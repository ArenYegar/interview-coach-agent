"""选题对比：关键词匹配 vs 混合检索（稠密 bge + BM25 + RRF）。

诚实口径：两个代理指标都不能证明"题目更好"，只能说明选题更贴 JD 要求；
  · overlap：题目文本/技能与所分配 JD 要求关键词的重叠（对两种方法都公平，主口径）
  · cosine ：题目与 JD 的最优语义相似度（对混合法有利，仅参考）
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .. import config
from ..retrieval import Embedder, build_selector
from ..schemas import JDProfile
from ..tools import load_bank, parse_jd, plan_questions


def _kw(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9+#._/-]{1,20}|[\u4e00-\u9fa5]{2,6}", text)
            if len(w) >= 2}


def _overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a) if a else 0.0


def compare(jd_text: str, k: int = 7, out_name: str = "select_compare.json") -> dict:
    bank = load_bank()
    profile: JDProfile = parse_jd(jd_text, llm=None)
    sel = build_selector(bank)
    pick_kw = plan_questions(profile, k=k, bank=bank)
    pick_hy = plan_questions(profile, k=k, bank=bank, selector=sel)

    def score_selection(picks) -> dict:
        reqs = {r.req_id: _kw(r.text) for r in profile.requirements}
        ov, cos = [], []
        if sel.embedder.available:
            emb = sel.embedder
            jd_vecs = emb.encode([r.text for r in profile.requirements], is_query=True)
        for q in picks:
            want = reqs.get(q.target_req_id, set())
            ov.append(_overlap(want, _kw(q.text) | {s.lower() for s in q.skills}))
            if sel.embedder.available:
                qv = emb.encode([q.text])[0]
                cos.append(float(max(jd_vecs @ qv)))
        return {"overlap_mean": round(sum(ov) / len(ov), 4) if ov else None,
                "cosine_max_mean": round(sum(cos) / len(cos), 4) if cos else None,
                "qids": [q.qid for q in picks]}

    result = {
        "jd_requirements": len(profile.requirements), "k": k,
        "selector_mode": sel.mode,
        "embed_backend": getattr(sel.embedder, "backend", "none"),
        "embed_error": (sel.embedder.error or "")[:200],
        "keyword": score_selection(pick_kw),
        "hybrid": score_selection(pick_hy),
    }
    result["delta_overlap"] = (None if result["hybrid"]["overlap_mean"] is None
                               or result["keyword"]["overlap_mean"] is None else
                               round(result["hybrid"]["overlap_mean"] - result["keyword"]["overlap_mean"], 4))
    config.ensure_dirs()
    out = Path(config.RESULTS_DIR) / out_name
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["out_path"] = str(out)
    return result


if __name__ == "__main__":
    import sys
    jd_path = sys.argv[1] if len(sys.argv) > 1 else str(Path(config.ROOT) / "data" / "jd_sample.md")
    k = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    print(json.dumps(compare(Path(jd_path).read_text(encoding="utf-8"), k), ensure_ascii=False, indent=2))
