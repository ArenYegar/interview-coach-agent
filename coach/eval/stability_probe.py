"""评测稳定性探针：把「同一份输入重复打分，分数能差多少」量出来，并分清差从哪来。

背景：同一模型、同一份 JD/简历/预设回答连跑两场，单题最多差 30pp（见 README「重复运行的分数字抖动」）。
这个差至少有两个可能来源，混在一起就无法判断该怎么修，必须分组对照：

  ① 采样随机性（temperature + 每次独立采样）
  ② prompt 本身变了（例如加入注入防护的定界符、评分维度描述改写）

探针按条件分组、对同一题同一答案重复打分，报每组的均值 / 极差 / 标准差，再做跨条件对比。
判定用的答案直接取自 data/answers_by_category.json —— 与出问题的那两场用同一份输入，可复现现场。

用法：
    python -m coach.eval.stability_probe --reps 3 --env-file .env
    python -m coach.eval.stability_probe --qids 技术-03,技术-01 --reps 5
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

from .. import config

# 条件组：先测"纯采样抖动"，再测"prompt 变更"，最后测两档缓解手段
CONDITIONS = (
    ("A 原始口径 temp=0.3 guard=0", {"COACH_TEMPERATURE": "0.3", "COACH_GUARD": "0", "COACH_SEED": "", "COACH_SCORE_REPS": "1"}),
    ("B 加防护 prompt temp=0.3 guard=2", {"COACH_TEMPERATURE": "0.3", "COACH_GUARD": "2", "COACH_SEED": "", "COACH_SCORE_REPS": "1"}),
    ("C temp=0 guard=2", {"COACH_TEMPERATURE": "0.0", "COACH_GUARD": "2", "COACH_SEED": "", "COACH_SCORE_REPS": "1"}),
    ("D temp=0 guard=2 + seed", {"COACH_TEMPERATURE": "0.0", "COACH_GUARD": "2", "COACH_SEED": "42", "COACH_SCORE_REPS": "1"}),
    ("E 中位数 x3 (temp=0.3 guard=2)", {"COACH_TEMPERATURE": "0.3", "COACH_GUARD": "2", "COACH_SEED": "", "COACH_SCORE_REPS": "3"}),
)


def _apply_env(env: dict[str, str]) -> None:
    for k, v in env.items():
        if v == "":
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _load_answer_map() -> dict:
    p = Path(config.ROOT) / "data" / "answers_by_category.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    merged = dict(data.get("by_category") or {})
    merged.update(data.get("by_qid") or {})
    return merged


def answer_for(q, amap: dict) -> str:
    return (amap.get(q.qid) or amap.get(q.category) or amap.get("default")
            or "我的回答：先说结论，再讲做法与取舍，最后给量化结果。")


def run(qids: list[str] | None = None, reps: int = 3, conditions=None, only: list[str] | None = None) -> dict:
    from ..llm import LLMClient, LLMError, get_client
    from ..tools import load_bank
    from ..tools.score_answer import score_answer

    bank = {q.qid: q for q in load_bank()}
    picked = [bank[q] for q in (qids or []) if q in bank] or list(bank.values())[:2]
    amap = _load_answer_map()

    # 先确认有真模型：没有就明确标注，不拿 Mock 的数字充数
    probe_client = get_client()
    real = hasattr(probe_client, "probe")
    ok, why = probe_client.probe() if real else (False, "无可用模型（Mock 模式，本次结果无意义）")

    keys = ("COACH_TEMPERATURE", "COACH_GUARD", "COACH_SEED", "COACH_SCORE_REPS", "COACH_CACHE")
    saved = {k: os.environ.get(k, "") for k in keys}      # 跑完还原
    out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "reps": reps,
           "model_available": bool(real and ok), "model_note": "" if (real and ok) else why,
           "questions": [{"qid": q.qid, "category": q.category} for q in picked],
           "conditions": {}, "calls": 0}

    todo = conditions or CONDITIONS
    if only:
        todo = [c for c in todo if any(c[0].startswith(t) for t in only)]
    for name, env in todo:
        _apply_env(env)
        _apply_env({"COACH_CACHE": "0"})      # 探针故意重复测同一输入，必须关缓存
        _apply_env({"COACH_SCORE_REPS": env.get("COACH_SCORE_REPS", "1")})
        try:
            client = LLMClient()
        except LLMError as e:
            out["conditions"][name] = {"error": f"客户端构造失败: {e}"}
            continue
        out["conditions"][name] = {"env": env, "per_question": {}, }
        from .. import parallel
        for q in picked:
            ans = answer_for(q, amap)

            def _one(_i: int, _q=q, _ans=ans):
                _c = score_answer(_q, _ans, llm=client)
                return round(_c.total, 4), _c.method

            got = parallel.run_many(_one, range(reps))       # 独立采样 → 并发；顺序保持
            vals = [g[0] for g in got]
            methods = [g[1] for g in got]
            out["calls"] += reps * (int(env.get("COACH_SCORE_REPS", "1")) or 1)
            rng = round((max(vals) - min(vals)) * 100, 1)
            out["conditions"][name]["per_question"][q.qid] = {
                "values": vals, "methods": methods,
                "mean": round(statistics.fmean(vals), 4),
                "range_pp": rng,
                "stdev_pp": round(statistics.pstdev(vals) * 100, 1) if len(vals) > 1 else 0.0,
                "invalid": all(m == "heuristic" for m in methods),   # 全走规则 = 本次调用没用上模型
            }
        pq = out["conditions"][name]["per_question"]
        out["conditions"][name]["mean_range_pp"] = round(
            statistics.fmean(v["range_pp"] for v in pq.values()), 1) if pq else None
        out["conditions"][name]["max_range_pp"] = max((v["range_pp"] for v in pq.values()), default=None)
    _apply_env(saved)          # 跑完把环境还原，避免污染同一进程里的后续调用
    return out


def format_text(res: dict) -> str:
    L = ["评测稳定性探针  reps=%d  模型可用=%s" % (res["reps"], res["model_available"]),
         "  题目: %s" % "、".join(q["qid"] for q in res["questions"]), ""]
    L.append("  条件                              平均极差   最大极差   逐题（均值 / 极差 pp）")
    for name, d in res["conditions"].items():
        if "error" in d:
            L.append("  %-32s %s" % (name, d["error"]))
            continue
        pq = d["per_question"]
        detail = "  ".join("%s %.2f/%.1f" % (k, v["mean"], v["range_pp"]) for k, v in pq.items())
        if pq and all(v["invalid"] for v in pq.values()):
            detail += "   ⚠ 本次调用全部走了规则分，说明模型没被用上"
        L.append("  %-32s %6s %8s   %s" % (name, d["mean_range_pp"], d["max_range_pp"], detail))
    if not res["model_available"]:
        L.append("")
        L.append("  ⚠ 没有可用真实模型（%s）：以上数字来自 Mock，不可用于结论。" % res["model_note"])
    L.append("")
    L.append("  本次消耗: %d 次模型调用" % res["calls"])
    if any("中位数" in k for k in res["conditions"]):
        L.append("  注：E 档每次「测量」内部要打 3 次模型取中位数，所以它的调用数约为其他档的 3 倍；")
        L.append("      比的是「报出来的那个数的极差」，不是单次调用的极差 —— 这才是要稳定的东西。")
    return "\n".join(L)


def write(res: dict, run_id: str) -> tuple[Path, Path]:
    config.ensure_dirs()
    d = Path(config.RESULTS_DIR)
    jp, mp = d / f"stability_{run_id}.json", d / f"stability_{run_id}.md"
    jp.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text("# 评测稳定性探针\n\n```\n" + format_text(res) + "\n```\n", encoding="utf-8")
    return jp, mp


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="评测稳定性探针（分组重复打分，量出分数字抖动）")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--qids", default="", help="逗号分隔的题号，默认取题库前两题")
    ap.add_argument("--run-id", default=time.strftime("stability-%Y%m%d-%H%M%S"))
    ap.add_argument("--env-file", default=None)
    ap.add_argument("--only", default="", help="只跑名字以这些前缀开头的档位，如 A,C,E")
    a = ap.parse_args(list(sys.argv[1:]) if argv is None else list(argv))
    if a.env_file:
        print("[env] 载入变量 %d 个" % config.load_env_file(a.env_file))
    res = run([x for x in a.qids.split(",") if x.strip()], reps=a.reps,
              only=[x.strip() for x in a.only.split(",") if x.strip()])
    print("\n" + format_text(res))
    jp, mp = write(res, a.run_id)
    print("\n产物: %s\n      %s" % (jp, mp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
