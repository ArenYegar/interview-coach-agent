"""一条命令跑通一场面试陪练（产物落 results/ 与 runs/）。

用法示例：
  python run_demo.py --jd data/jd_sample.md --resume data/resume_sample.md \
      --answers data/answers_demo.json --questions 3 --env-file <你的 .env 路径>
  python run_demo.py --jd ... --resume ... --interactive        # 真人练习模式
  python run_demo.py --jd ... --resume ... --engine langgraph   # 用 LangGraph 编排
  COACH_OFFLINE=1 python run_demo.py ... --questions 2          # 离线确定性模式（不调模型）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from coach import config
from coach.eval import harness
from coach.graph import CoachEngine


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def _docx_text(path: str) -> str:
    """简历一般是 .docx，这里直接抽文本（不依赖外部服务）。"""
    import docx  # python-docx
    d = docx.Document(path)
    parts = [p.text.strip() for p in d.paragraphs if p.text.strip()]
    for tb in d.tables:
        for row in tb.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def load_text(path: str) -> str:
    p = Path(path)
    if p.suffix.lower() == ".docx":
        return _docx_text(str(p))
    return _read(str(p))


def load_answers(spec: str | None) -> tuple[list[str], dict]:
    """返回 (顺序回答列表, 按类别回答映射)。两种输入都支持，便于演示与真人练习。"""
    if not spec:
        return [], {}
    p = Path(spec)
    if not p.exists():
        return [], {}
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if p.suffix.lower() == ".json":
        data = json.loads(txt)
        if isinstance(data, dict):
            if "by_category" in data:
                merged = {str(k): str(v) for k, v in data["by_category"].items()}
                merged.update({str(k): str(v) for k, v in data.get("by_qid", {}).items()})
                return [], merged
            data = data.get("answers", [])
        return [str(x) for x in data], {}
    return [blk.strip() for blk in txt.split("\n\n") if blk.strip()], {}


def interactive_provider(q) -> str:
    print("\n" + "=" * 70)
    print(f"[{q.qid} | {q.category} | {q.difficulty}] {q.text}")
    print("自评分参考要点：")
    for dim in q.rubric.dims:
        print(f"  - {dim.name}: {'、'.join(dim.keywords)}")
    print("-" * 70 + "（输入 END 结束本题）")
    lines: list[str] = []
    while True:
        try:
            line = input("> ")
        except EOFError:
            break
        if line.strip() == "END":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="多智能体求职陪练 Agent - 本地演示入口")
    ap.add_argument("--jd", required=True, help="JD 文本文件路径（.md/.txt/.docx）")
    ap.add_argument("--resume", required=True, help="简历文件路径（.docx/.md/.txt）")
    ap.add_argument("--answers", help="预置回答（.json 列表，或按空行分隔的 txt）")
    ap.add_argument("--interactive", action="store_true", help="真人练习模式（END 结束一题）")
    ap.add_argument("--questions", type=int, default=config.MAX_QUESTIONS)
    ap.add_argument("--engine", choices=["local", "langgraph"], default="local")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--env-file", default=None, help="可选：读取 .env（不会写入仓库）")
    ap.add_argument("--eval", action="store_true", help="跑完自动执行评测 harness")
    args = ap.parse_args(argv)

    loaded = config.load_env_file(args.env_file)
    config.ensure_dirs()
    jd_text = load_text(args.jd)
    resume_text = load_text(args.resume)
    print(f"[env] 载入变量 {loaded} 个 | 模式: {'离线(mock)' if config.offline() else '在线(真实模型)'}"
          f" | base_url 已配置: {bool(config.base_url())}")

    answers, by_cat = load_answers(args.answers)

    if args.engine == "langgraph":
        from coach.graph import run_with_langgraph
        preset = None
        if by_cat and not answers:      # 按类别回答需要先知道选题，物化成有序回答
            planner = CoachEngine(run_id=args.run_id, max_questions=args.questions, use_llm=False)
            preset = planner.plan(jd_text, resume_text).questions
            answers = [by_cat.get(q.qid) or by_cat.get(q.category, by_cat.get("default", ""))
                       for q in preset]
        report, run_id = run_with_langgraph(jd_text, resume_text, answers, run_id=args.run_id,
                                            max_questions=args.questions, preset_questions=preset)
    else:
        eng = CoachEngine(run_id=args.run_id, max_questions=args.questions)
        print(f"[model] {eng.model} | 题库 {len(eng.bank)} 条")
        provider = None
        if args.interactive:
            provider = interactive_provider
        elif by_cat:
            # 按类别作答：追问（-F 结尾）不再作答，避免用同一段话重复打分
            def provider(q):
                base = q.qid.split("-F")[0]
                if q.qid != base:            # 追问题目：优先用专门的补充回答，没有就跳过
                    return by_cat.get(q.qid) or by_cat.get("__followup__", "")
                return by_cat.get(q.qid) or by_cat.get(q.category, by_cat.get("default", ""))
        report = eng.run(jd_text, resume_text, answers=answers, answer_provider=provider)
        run_id = eng.run_id

    print("\n" + "=" * 70)
    print(f"run_id        : {run_id}")
    print(f"岗位          : {report.jd_title}")
    print(f"要求覆盖率    : {report.match.score:.0%}" if report.match else "要求覆盖率    : -")
    print(f"综合得分      : {report.overall:.0%}")
    print(f"答题轮次      : {len(report.turns)}")
    print("逐题：")
    for t in report.turns:
        sc = t.scorecard
        if sc:
            print(f"  - {t.qid:9s} {sc.total:.0%} {sc.verdict:6s} 缺失: {'、'.join(sc.missing[:3]) or '-'}")
        else:
            print(f"  - {t.qid} 未评分")
    print(f"报告          : {report.report_path}")
    print(f"JSON          : {config.RESULTS_DIR}/run_{run_id}.json")
    print(f"trace         : {config.RUNS_DIR}/{run_id}/trace.jsonl")

    if args.eval:
        print("\n[eval] " + json.dumps(harness.evaluate(run_id), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
