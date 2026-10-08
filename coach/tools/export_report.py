"""工具 6：报告导出（Markdown + JSON 落盘，产物即证据）。"""
from __future__ import annotations

import json
from pathlib import Path

from .. import config
from ..schemas import SessionReport


def _bar(v: float) -> str:
    n = int(round(v * 10))
    return "█" * n + "░" * (10 - n)


def export_report(report: SessionReport) -> tuple[Path, Path]:
    """写出 results/run_<run_id>.json 与 results/report_<run_id>.md。"""
    config.ensure_dirs()
    res = Path(config.RESULTS_DIR)
    json_path = res / f"run_{report.run_id}.json"
    md_path = res / f"report_{report.run_id}.md"

    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    lines: list[str] = [
        f"# 面试复盘报告 {report.run_id}",
        "",
        f"- 目标岗位：{report.jd_title or '（未解析）'}",
        f"- 模型：{report.llm_model or '未记录'}"
        + (f"（{report.llm_status}）" if report.llm_status and report.llm_status != "真实模型可用" else ""),
    ]
    if report.match:
        lines += [f"- 简历-岗位要求覆盖率：{report.match.score:.0%}（{'模型+规则' if report.match.method == 'llm+heuristic' else '规则'}口径）",
                  f"- 未覆盖关键词：{'、'.join(report.match.missing_keywords) or '无'}"]
    lines += [f"- 综合得分：{report.overall:.0%} {_bar(report.overall)}", ""]

    lines += ["## 逐题表现", "", "| 题号 | 分类 | 得分 | 判定 | 缺失要点 |", "| --- | --- | --- | --- | --- |"]
    for t in report.turns:
        sc = t.scorecard
        lines.append(f"| {t.qid} | {t.question[:14]}… | {sc.total:.0%} | {sc.verdict} | {'、'.join(sc.missing[:3]) or '-'} |" if sc
                     else f"| {t.qid} | {t.question[:14]}… | - | - | - |")
    lines += ["", "## 弱点画像（本场）"]
    lines += [f"- [{w.severity}] {w.tag} × {w.count}" for w in report.weaknesses] or ["- 无明显弱点"]
    if report.actions:
        lines += ["", "## 下一步行动"] + [f"- {a}" for a in report.actions]
    if report.highlights:
        lines += ["", "## 亮点"] + [f"- {h}" for h in report.highlights]
    if report.resume_edits:
        lines += ["", "## 简历改写建议"] + [f"- {r}" for r in report.resume_edits]

    # 成本与延迟：面试必问"一次问答多少 token、多少延迟"，所以这页报告自己带数字
    if report.cost:
        c = report.cost
        llm_ms = float(c.get("llm_ms", 0) or 0)
        wall_ms = float(c.get("wall_ms", 0) or 0)
        n = max(1, len(report.turns))
        lines += [
            "", "## 成本与延迟（本场）",
            f"- LLM 调用 {c.get('llm_calls', 0)} 次（模型 {c.get('model') or report.llm_model or '未记录'}）",
            f"- token 合计 {c.get('total_tokens', 0)}（prompt {c.get('prompt_tokens', 0)} / completion {c.get('completion_tokens', 0)}）",
            f"- 总墙钟 {wall_ms/1000:.1f} s，其中 LLM 等待 {llm_ms/1000:.1f} s"
            + (f"，占 {llm_ms/wall_ms*100:.0f}%" if wall_ms else ""),
            f"- 每轮均值（含首题解析 JD 的固定开销）：token {round(c.get('total_tokens', 0)/n, 1)} / LLM 耗时 {round(llm_ms/n, 1)} ms"
            "（只看评分/追问轮的 per_turn 见 results/cost_<run_id>.json）",
            "- 口径：token 取模型响应的 usage 字段；耗时取 trace 每步 span；分阶段明细见 results/cost_<run_id>.json",
        ]

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report.report_path = str(md_path)
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return json_path, md_path
