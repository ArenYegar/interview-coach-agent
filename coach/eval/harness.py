"""评测 harness：把"效果"变成可复现的数字，而不是形容词。

四类指标（全部可从产物直接算，不编造）：
  1) 出题覆盖度 coverage_at_k：选出的题覆盖了多少条 JD 硬性要求；
  2) 工具调用成功率 tool_success_rate：trace 里 ok=True 的比例（含失败重试后的结果）；
  3) 要点覆盖率 point_coverage：模型/规则评分命中的参考要点 / 全部参考要点；
  4) 与人评一致性：读取人工评分表（human_scores.csv），没有则明确输出 pending，绝不编造。
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from .. import config
from ..schemas import SessionReport
from ..trace import load_trace


def _latest_run() -> str:
    runs = sorted([p for p in Path(config.RUNS_DIR).glob("run-*") if p.is_dir()],
                  key=lambda p: p.stat().st_mtime)
    if not runs:
        raise FileNotFoundError("runs/ 下没有任何运行记录，请先跑 run_demo.py")
    return runs[-1].name


def tool_success_rate(run_id: str) -> dict:
    ev = load_trace(run_id)
    if not ev:
        return {"tool_success_rate": None, "events": 0}
    ok = sum(1 for e in ev if e.ok)
    return {"tool_success_rate": round(ok / len(ev), 4), "events": len(ev),
            "failed_steps": [e.step for e in ev if not e.ok]}


def coverage_at_k(report: SessionReport, bank_path: str | Path | None = None) -> dict:
    """出题是否打在 JD 的硬性要求上：命中的 req_id 去重后 / 硬性要求总数。"""
    if not report.match:
        return {"coverage_at_k": None}
    hard = {i.req_id for i in report.match.items if i.req_id != "ADVICE" and i.must_have}
    hit_ids = {t.target_req_id for t in report.turns if t.target_req_id and not t.follow_up}
    hit = hit_ids & hard
    denom = len(hard) or 1
    return {"coverage_at_k": round(len(hit) / denom, 4), "hit_requirements": sorted(hit),
            "requirements_total": len(hard), "covered_by_questions": len(hit_ids)}


def _kws(text: str) -> list[str]:
    import re
    return [w for w in re.findall(r"[A-Za-z][A-Za-z0-9+#._/-]{1,20}|[\u4e00-\u9fa5]{2,6}", text)
            if len(w) >= 2][:6]


def point_coverage(report: SessionReport, bank_path: str | Path | None = None) -> dict:
    """所有题目的参考要点命中率（分数口径的直接来源）。"""
    p = Path(bank_path or config.BANK_PATH)
    bank = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            bank[d["qid"]] = d
    hit = total = 0
    for t in report.turns:
        base = t.qid.replace("-F", "")
        q = bank.get(base)
        if not q or not t.scorecard:
            continue
        kws = [k for dim in q["rubric"] for k in dim["keywords"]]
        total += len(kws)
        hit += len(set(t.scorecard.evidence) & set(kws))
    return {"point_coverage": round(hit / total, 4) if total else None, "points_hit": hit,
            "points_total": total}


def human_agreement(run_id: str, human_csv: str | Path | None = None) -> dict:
    """与人评的一致性：需要人工填写 results/human_scores.csv（列：run_id,qid,human_score）。"""
    csv_path = Path(human_csv or (Path(config.RESULTS_DIR) / "human_scores.csv"))
    if not csv_path.exists():
        return {"agreement": None, "status": "pending_human_scores",
                "hint": f"请填写 {csv_path}（列：run_id,qid,human_score），再重跑本 harness"}
    rows = [r for r in csv.DictReader(csv_path.open(encoding="utf-8")) if r.get("run_id") == run_id]
    if not rows:
        return {"agreement": None, "status": "pending_human_scores", "rows_for_run": 0,
                "hint": "该 run 还没有人工评分行"}
    run_path = Path(config.RESULTS_DIR) / f"run_{run_id}.json"
    report = SessionReport(**json.loads(run_path.read_text(encoding="utf-8")))
    auto = {t.qid: t.scorecard.total for t in report.turns if t.scorecard}
    pairs = [(float(r["human_score"]), auto[r["qid"]]) for r in rows if r["qid"] in auto]
    if not pairs:
        return {"agreement": None, "status": "no_matching_qids"}
    n = len(pairs)
    mae = sum(abs(h - a) for h, a in pairs) / n
    agree = sum(1 for h, a in pairs if abs(h - a) <= 0.2) / n     # 容差 0.2 视为一致
    return {"agreement": round(agree, 4), "mae": round(mae, 4), "n": n}


def evaluate(run_id: str | None = None, out_path: str | Path | None = None) -> dict:
    run_id = run_id or _latest_run()
    report = SessionReport(**json.loads(
        (Path(config.RESULTS_DIR) / f"run_{run_id}.json").read_text(encoding="utf-8")))
    metrics = {
        "run_id": run_id,
        "overall_score": report.overall,
        "questions_asked": len([t for t in report.turns if not t.follow_up]),
        "turns": len(report.turns),
        **tool_success_rate(run_id),
        **coverage_at_k(report),
        **point_coverage(report),
        "human": human_agreement(run_id),
    }
    config.ensure_dirs()
    out = Path(out_path or (Path(config.RESULTS_DIR) / f"eval_{run_id}.json"))
    out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics["out_path"] = str(out)
    return metrics


def main() -> None:
    run_id = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(evaluate(run_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
