"""评测 harness：把"效果"变成可复现的数字，而不是形容词。

四类指标（全部可从产物直接算，不编造）：
  1) 出题覆盖度 coverage_at_k：选出的题覆盖了多少条 JD 硬性要求；
  2) 工具调用成功率 tool_success_rate：trace 里 ok=True 的比例（含失败重试后的结果）；
  3) 要点覆盖率 point_coverage：模型/规则评分命中的参考要点 / 全部参考要点；
  4) 与人评一致性：读取人工评分表（human_scores.csv），没有则明确输出 pending，绝不编造。
"""
from __future__ import annotations

import argparse
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


def machine_agreement(run_id: str, bank_path: str | Path | None = None) -> dict:
    """独立复核一致性：用"逐条要点判定"这套不同协议重新评一遍，再与自动评分对齐。

    注意：这是**机器复核**，不是人评。人评一致性仍由 human_scores.csv 提供。
    """
    from ..llm import get_client
    from ..schemas import ReviewerCheck
    from ..tools import load_bank

    csv_path = Path(config.RESULTS_DIR) / "reviewer_scores.csv"
    if config.offline():
        # 没有可用 Key 或显式离线：Mock 客户端不支持 ReviewerCheck，
        # 提前返回，绝不进入"逐题调用 → 失败 → 覆盖旧结果"的路径。
        return {"agreement": None, "status": "reviewer_unavailable",
                "reason": "未配置可用的 API Key 或处于离线模式（可用 --env-file 指定）；机器复核需要真实模型",
                "kept_previous": csv_path.exists(), "detail_csv": str(csv_path),
                "label": "机器复核一致性（非人评）"}

    report = SessionReport(**json.loads(
        (Path(config.RESULTS_DIR) / f"run_{run_id}.json").read_text(encoding="utf-8")))
    client = get_client()
    if hasattr(client, "probe"):
        ok, why = client.probe()
        if not ok:
            return {"agreement": None, "status": "reviewer_unavailable", "reason": why[:120],
                    "kept_previous": csv_path.exists(), "detail_csv": str(csv_path)}
    bank = {q.qid: q for q in load_bank(bank_path)}

    rows, pairs = [], []
    for t in report.turns:
        base = t.qid.split("-F")[0]
        q = bank.get(base)
        if not q or not t.scorecard or t.follow_up:
            continue
        points = [k for dim in q.rubric.dims for k in dim.keywords]
        prompt = (
            f"【问题】{t.question}\n【候选人回答】{t.answer}\n\n"
            f"【参考要点】{points}\n\n"
            "请只做一件事：逐条判断上面的参考要点有没有被回答真正覆盖（要求有具体做法或结论，泛泛提到不算）。\n"
            "covered / missing 都必须从【参考要点】里原文照抄；score = 覆盖数 / 要点总数（0~1，保留两位小数）。\n"
            "只输出 JSON，schema: " + json.dumps(ReviewerCheck.model_json_schema(), ensure_ascii=False)
        )
        try:
            chk = client.structured("你是严格的技术面试复核员，只输出 JSON。", prompt, ReviewerCheck)
        except Exception as e:
            rows.append({"run_id": run_id, "qid": t.qid, "auto": t.scorecard.total,
                         "reviewer": "", "delta": "", "note": f"复核失败:{type(e).__name__}"})
            continue
        auto = round(t.scorecard.total, 4)
        rev = round(float(chk.score), 4)
        pairs.append((auto, rev))
        rows.append({"run_id": run_id, "qid": t.qid, "auto": auto, "reviewer": rev,
                     "delta": round(rev - auto, 4), "note": ""})

    if not pairs:
        # 一条配对都没产生（复核全部失败 / 没有可复核的题目）：
        # 保留上一次的 reviewer_scores.csv 不覆盖，只汇报状态与首个失败原因。
        first_err = next((r["note"] for r in rows if r.get("note")), "")
        return {"agreement": None, "status": "reviewer_failed" if first_err else "no_pairs",
                "attempted": len(rows), "reason": first_err[:120],
                "kept_previous": csv_path.exists(),
                "hint": "复核未产生任何配对，已保留原有的 reviewer_scores.csv 不被覆盖",
                "detail_csv": str(csv_path), "label": "机器复核一致性（非人评）"}

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["run_id", "qid", "auto", "reviewer", "delta", "note"])
        w.writeheader()
        w.writerows(rows)
    n = len(pairs)
    mae = sum(abs(a - r) for a, r in pairs) / n
    agree = sum(1 for a, r in pairs if abs(a - r) <= 0.2) / n
    return {"agreement": round(agree, 4), "mae": round(mae, 4), "n": n,
            "protocol": "逐条要点判定 @temp0.3 + 原协议整体打分（两套不同协议）",
            "label": "机器复核一致性（非人评）", "detail_csv": str(csv_path)}


def followup_effectiveness(report: SessionReport) -> dict:
    """追问有效率：触发追问的题里，追问后得分提升(>5pp)的比例；只统计真有作答的追问。"""
    by_base: dict[str, list] = {}
    for t in report.turns:
        base = t.qid.split("-F")[0]
        by_base.setdefault(base, []).append(t)
    triggered = improved = 0
    gains: list[float] = []
    for base, ts in by_base.items():
        primary = next((t for t in ts if not t.follow_up), None)
        fus = [t for t in ts if t.follow_up and t.scorecard]
        if primary is None or not fus or primary.scorecard is None or not fus:
            continue
        if primary.scorecard.total >= 0.4:      # 未触发追问
            continue
        triggered += 1
        best = max(t.scorecard.total for t in fus)
        gains.append(round(best - primary.scorecard.total, 4))
        if best > primary.scorecard.total + 0.05:
            improved += 1
    return {"followup_triggered": triggered, "followup_improved": improved,
            "followup_effectiveness": round(improved / triggered, 4) if triggered else None,
            "avg_score_gain": round(sum(gains) / len(gains), 4) if gains else None}


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
    with csv_path.open(encoding="utf-8") as f:          # 用 with 收口，避免未关闭文件句柄的 ResourceWarning
        rows = [r for r in csv.DictReader(f) if r.get("run_id") == run_id]
    if not rows:
        return {"agreement": None, "status": "pending_human_scores", "rows_for_run": 0,
                "hint": "该 run 还没有人工评分行"}
    run_path = Path(config.RESULTS_DIR) / f"run_{run_id}.json"
    report = SessionReport(**json.loads(run_path.read_text(encoding="utf-8")))
    auto = {t.qid: t.scorecard.total for t in report.turns if t.scorecard}
    filled = [r for r in rows if (r.get("human_score") or "").strip() != ""]
    if not filled:
        return {"agreement": None, "status": "awaiting_scores", "rows_template": len(rows),
                "hint": "模板已就绪但还没打分：把 results/human_scores.csv 的 human_score 列填 0~1，再重跑 harness"}
    pairs = [(float(r["human_score"]), auto[r["qid"]]) for r in filled if r["qid"] in auto]
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
        "llm_model": report.llm_model or "未记录",
        "overall_score": report.overall,
        "questions_asked": len([t for t in report.turns if not t.follow_up]),
        "turns": len(report.turns),
        **tool_success_rate(run_id),
        **coverage_at_k(report),
        **point_coverage(report),
        **followup_effectiveness(report),
        "reviewer": machine_agreement(run_id),
        "human": human_agreement(run_id),
    }
    config.ensure_dirs()
    out = Path(out_path or (Path(config.RESULTS_DIR) / f"eval_{run_id}.json"))
    out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics["out_path"] = str(out)
    return metrics


def main(argv: list[str] | None = None) -> None:
    """用法（与 run_demo.py 的 --env-file 口径一致）：

      python -m coach.eval.harness <run_id> [--env-file .env] [--out results/eval_x.json]
      python -m coach.eval.harness --reviewer [run_id] [--env-file .env]
    """
    ap = argparse.ArgumentParser(description="评测 harness：把效果变成可复现的数字")
    ap.add_argument("run_id", nargs="?", default=None, help="不填则取最近一次 run-*")
    ap.add_argument("--reviewer", action="store_true", help="只跑机器复核一致性")
    ap.add_argument("--env-file", default=None, help="可选：读取 .env（与 run_demo.py 对齐）")
    ap.add_argument("--out", default=None, help="评测结果落盘路径（默认 results/eval_<run_id>.json）")
    a = ap.parse_args(argv)

    loaded = config.load_env_file(a.env_file)
    if a.env_file:
        print(f"[env] 载入变量 {loaded} 个 | 模式: {'离线(mock)' if config.offline() else '在线(真实模型)'}",
              file=sys.stderr)

    if a.reviewer:
        print(json.dumps(machine_agreement(a.run_id or _latest_run()), ensure_ascii=False, indent=2))
        return
    print(json.dumps(evaluate(a.run_id, out_path=a.out), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
