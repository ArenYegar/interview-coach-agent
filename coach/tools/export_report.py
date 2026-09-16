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

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report.report_path = str(md_path)
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return json_path, md_path
