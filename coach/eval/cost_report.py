"""成本与延迟账：把 trace 里的每步耗时与 token 聚合成一张能直接报出来的表。

面试考点（F5）：「一次问答花多少 token、多少延迟？最长的一段在哪？」
答案必须来自产物文件，而不是"感觉挺快"。
所有数字都从 runs/<run_id>/trace.jsonl 复算 —— 对任何历史 run 都能重算，
所以这张表不会因为脚本重跑而过期（这也是"指标必须可复现"的一部分）。

用法：
    python -m coach.eval.cost_report <run_id>              # 打印并写盘
    python -m coach.eval.cost_report <run_id> --no-write
单价（可选，元/百万 token）：
    COACH_PRICE_IN / COACH_PRICE_OUT 环境变量；不配置就只报 token，不报钱。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .. import config
from ..trace import summarize_trace

# 步骤 → 阶段（延迟要分段讲，不能只报一个总数）
PHASES = (
    ("tool.parse_jd", "① 解析 JD（固定开销）"),
    ("tool.match_resume", "② 简历匹配（规则，不花 token）"),
    ("tool.plan_questions", "③ 选题（规则/检索，不花 token）"),
    ("tool.score_answer", "④ 逐题评分与追问（LLM 主战场）"),
    ("tool.record_session", "⑤ 弱点入库（SQLite）"),
    ("tool.export_report", "⑥ 报告落盘"),
)


def classify(step: str) -> str:
    for prefix, name in PHASES:
        if step.startswith(prefix):
            return name
    if step.startswith("tool."):
        return "⑦ 其他工具"
    return "⑧ 编排/状态"


def _price(name: str):
    v = os.getenv(name)
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def aggregate(run_id: str) -> dict:
    base = summarize_trace(run_id)
    steps = base["steps"]

    phases: dict[str, dict] = {}
    for step, s in steps.items():
        ph = classify(step)
        p = phases.setdefault(ph, {"count": 0, "ms": 0.0, "total_tokens": 0,
                                   "prompt_tokens": 0, "completion_tokens": 0})
        p["count"] += s["count"]
        for k in ("ms", "total_tokens", "prompt_tokens", "completion_tokens"):
            p[k] = round(p[k] + s[k], 1)

    # 轮次：优先读本场 run json（含追问轮），读不到就退回评分事件数
    turns = None
    rp = Path(config.RESULTS_DIR) / f"run_{run_id}.json"
    if rp.exists():
        try:
            turns = len(json.loads(rp.read_text(encoding="utf-8")).get("turns") or []) or None
        except Exception:
            turns = None
    if not turns:
        turns = sum(s["count"] for st, s in steps.items() if st.startswith("tool.score_answer")) or None

    score_steps = {st: s for st, s in steps.items() if st.startswith("tool.score_answer")}
    score_tokens = sum(s["total_tokens"] for s in score_steps.values())
    score_ms = round(sum(s["ms"] for s in score_steps.values()), 1)
    fixed_tokens = steps.get("tool.parse_jd", {}).get("total_tokens", 0)
    fixed_ms = steps.get("tool.parse_jd", {}).get("ms", 0.0)

    pi, po = _price("COACH_PRICE_IN"), _price("COACH_PRICE_OUT")
    cost = None
    if pi is not None and po is not None:
        cost = round(base["totals"].get("prompt_tokens", 0) / 1e6 * pi
                     + base["totals"].get("completion_tokens", 0) / 1e6 * po, 6)

    return {
        "run_id": run_id,
        "turns": turns,
        "totals": base["totals"],
        "phases": phases,
        "steps": steps,
        "per_turn": {
            "total_tokens": round(score_tokens / turns, 1) if turns else None,
            "ms": round(score_ms / turns, 1) if turns else None,
        },
        "fixed": {"tokens": fixed_tokens, "ms": round(fixed_ms, 1)},
        "price": {"in": pi, "out": po},
        "cost": cost,
    }


def format_text(agg: dict, model: str = "") -> str:
    t = agg["totals"]
    llm_ms = t.get("llm_ms", 0.0)
    wall_ms = t.get("wall_ms", 0.0)
    steps_ms = t.get("ms", 0.0)
    L = []
    L.append("成本与延迟账  run=%s%s" % (agg["run_id"], f"  模型={model}" if model else ""))
    L.append("  轮次（含追问）: %s" % (agg["turns"] or "-"))
    L.append("  token 合计   : %s（prompt %s / completion %s）"
             % (t.get("total_tokens", 0), t.get("prompt_tokens", 0), t.get("completion_tokens", 0)))
    pct = (llm_ms / wall_ms * 100) if wall_ms else 0
    # 上限截到 100%：span 可以嵌套/重叠，加上 trace 时间戳是秒级精度，直接相除会冒出 101% 这种数字
    L.append("  总墙钟       : %.1f s（步骤耗时合计 %.1f s；其中 LLM 等待 %.1f s，占 %.0f%%）"
             % (wall_ms / 1000, steps_ms / 1000, llm_ms / 1000, min(pct, 100.0)))
    L.append("")
    L.append("  分段延迟                       事件   耗时(s)   占比    token")
    for ph, s in sorted(agg["phases"].items()):
        pct = (s["ms"] / steps_ms * 100) if steps_ms else 0
        L.append("  %-30s %4d %8.1f %5.1f%% %8d" % (ph, s["count"], s["ms"] / 1000, pct, s["total_tokens"]))
    L.append("")
    pt = agg["per_turn"]
    L.append("  每次问答（评分/追问轮）: token %s  |  LLM 耗时 %s ms" % (pt["total_tokens"], pt["ms"]))
    L.append("  首题固定开销（解析 JD，每场一次）: token %s  |  %s ms"
             % (agg["fixed"]["tokens"], agg["fixed"]["ms"]))
    if agg["cost"] is not None:
        L.append("  估算成本: %.6f 元（单价 入 %.2f / 出 %.2f 元每百万 token）"
                 % (agg["cost"], agg["price"]["in"], agg["price"]["out"]))
    else:
        L.append("  估算成本: 未配置单价 —— 设 COACH_PRICE_IN / COACH_PRICE_OUT（元/百万 token）后自动计算")
    return "\n".join(L)


def write(run_id: str) -> Path:
    agg = aggregate(run_id)
    config.ensure_dirs()
    p = Path(config.RESULTS_DIR) / f"cost_{run_id}.json"
    p.write_text(json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="成本与延迟账（读 runs/<run_id>/trace.jsonl 复算）")
    ap.add_argument("run_id")
    ap.add_argument("--no-write", action="store_true", help="只打印，不写 results/cost_<run_id>.json")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而不是表格")
    args = ap.parse_args(list(sys.argv[1:]) if argv is None else list(argv))

    agg = aggregate(args.run_id)
    model = ""
    rp = Path(config.RESULTS_DIR) / f"run_{args.run_id}.json"
    if rp.exists():
        try:
            model = json.loads(rp.read_text(encoding="utf-8")).get("llm_model", "")
        except Exception:
            model = ""
    if args.json:
        print(json.dumps(agg, ensure_ascii=False, indent=2))
    else:
        print(format_text(agg, model=model))
    if not args.no_write:
        print("产物: %s" % write(args.run_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
