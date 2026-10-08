"""把整场得分做"归因拆分"：分数是丢在选题层还是答题层？

面试考点：拿到一个 0.55 的整场分，先回答"分丢在哪"再谈优化。
拆法（乘法关系，口径写死在输出里）：

    整场均分 ≈ 覆盖系数 × 答题均分
    覆盖系数 = 有对口题的硬性要求数 / 硬性要求总数      ← 选题/题库层的损失
    答题均分 = 已评分轮次的平均分                        ← 答题层的损失

于是"题库里没有对口题"造成的损失与"答得不好"造成的损失被分开。
第三段是**过程成本**：各工具的成功率、耗时、token —— 用于回答"哪个工具在拖后腿"。

用法：
    python -m coach.eval.attribute <run_id>
    python -m coach.eval.attribute <run_id> --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import config
from ..trace import summarize_trace


def attribute(run_id: str) -> dict:
    rp = Path(config.RESULTS_DIR) / f"run_{run_id}.json"
    if not rp.exists():
        raise SystemExit(f"找不到 {rp}")
    rep = json.loads(rp.read_text(encoding="utf-8"))
    turns = [t for t in (rep.get("turns") or []) if t.get("scorecard")]
    main = [t for t in turns if not t.get("follow_up")]
    scored = [t["scorecard"]["total"] for t in turns]
    cov = None
    must_total = must_covered = 0
    jd = (rep.get("match") or {})
    # 覆盖率：优先用报告里的 uncovered + 已被出题命中的要求集合
    if rep.get("uncovered") is not None:
        covered_ids = {t.get("target_req_id") for t in main if t.get("target_req_id")}
        must_total = len(covered_ids) + len(rep["uncovered"])
        must_covered = len(covered_ids)
        cov = round(must_covered / must_total, 3) if must_total else None
    tr = summarize_trace(run_id)
    steps = tr.get("steps") or {}
    tools = []
    for k in sorted(steps):
        if not k.startswith("tool."):
            continue
        s = steps[k]
        tools.append({"tool": k, "events": s["count"], "fail": s["fail"],
                      "ms": s["ms"], "tokens": s["total_tokens"]})
    mean_score = round(sum(scored) / len(scored), 4) if scored else None
    return {
        "run_id": run_id, "overall": rep.get("overall"), "turns": len(turns), "main_turns": len(main),
        "coverage": cov, "must_total": must_total, "must_covered": must_covered,
        "answer_mean": mean_score, "uncovered": rep.get("uncovered") or [],
        "tools": tools, "trace_totals": tr.get("totals"),
        "reading": ("分数丢在**选题层**：有硬性要求没有对口题（题库缺口），这部分根本没机会拿分。"
                    if (cov is not None and cov < 0.9) else
                    "覆盖没问题，分数主要丢在**答题层**：覆盖到的题也没答到位。"),
    }


def format_text(r: dict) -> str:
    L = ["效果归因  run=%s" % r["run_id"],
         "  整场均分: %s（%d 轮，其中主问题 %d 道）" % (r["overall"], r["turns"], r["main_turns"]),
         "  覆盖系数: %s（%d/%d 条硬性要求有对口题）  ← 选题/题库层" % (r["coverage"], r["must_covered"], r["must_total"]),
         "  答题均分: %s  ← 答题层" % r["answer_mean"],
         "  拆解: 整场 ≈ 覆盖 %s × 答题 %s = %s" % (
             r["coverage"], r["answer_mean"],
             round((r["coverage"] or 0) * (r["answer_mean"] or 0), 3))]
    if r["uncovered"]:
        L.append("  题库缺口（未出题的要求）:")
        L += ["    - " + u for u in r["uncovered"][:6]]
    L.append("  过程成本（按工具）:")
    L.append("    %-24s 事件  失败   耗时(ms)   token" % "工具")
    for t in r["tools"]:
        L.append("    %-24s %4d %5d %10.1f %7d" % (t["tool"], t["events"], t["fail"], t["ms"], t["tokens"]))
    L.append("")
    L.append("  结论: " + r["reading"])
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="效果归因：分丢在选题层还是答题层")
    ap.add_argument("run_id")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(list(sys.argv[1:]) if argv is None else list(argv))
    r = attribute(a.run_id)
    print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else format_text(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
