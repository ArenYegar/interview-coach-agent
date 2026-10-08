"""把报告渲染成**可打印的 HTML**（零依赖，浏览器直接"打印成 PDF"）。

为什么不引 PDF 库：本机环境里没有 reportlab / weasyprint / fpdf（实测），
为了导出一份 PDF 引入一个重依赖不划算。用 @page + 打印样式让浏览器出 PDF，
清晰度更高、可复制、还能保留中文排版。真要在服务端出 PDF，接 weasyprint 是替换适配层的事。

用法：
    python run_demo.py ... --html          # 跑完额外写一份 report_<run_id>.html
    GET /api/session/{run_id}/report.html  # Web 端直接打开即可打印
"""
from __future__ import annotations

import html
import re
from pathlib import Path

_CSS = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body { font-family: "Microsoft YaHei", "PingFang SC", system-ui, sans-serif;
       font-size: 11.5pt; line-height: 1.65; color: #1a1a1a; margin: 0 auto; max-width: 820px; padding: 8px; }
h1 { font-size: 19pt; border-bottom: 2px solid #1a1a1a; padding-bottom: 6px; margin: 0 0 14px; }
h2 { font-size: 14pt; margin: 22px 0 8px; padding-left: 8px; border-left: 4px solid #3b6ea5; }
h3 { font-size: 12.5pt; margin: 16px 0 6px; color: #24486e; }
ul { margin: 6px 0 6px 2px; padding-left: 20px; }
li { margin: 3px 0; }
code { background: #f2f4f7; padding: 1px 4px; border-radius: 3px; font-size: 10.5pt; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 10.5pt; }
th, td { border: 1px solid #c9d2dd; padding: 5px 8px; text-align: left; }
th { background: #eef3f9; }
hr { border: none; border-top: 1px dashed #b9c4d0; margin: 18px 0; }
blockquote { border-left: 4px solid #d8c27a; background: #fdfaf1; margin: 8px 0; padding: 6px 10px; }
.pending { border: 1px solid #d8c27a; background: #fdfaf1; padding: 8px 10px; border-radius: 4px; }
.hint { background: #eef3f9; border: 1px solid #c9d2dd; border-radius: 4px;
        padding: 6px 10px; font-size: 10pt; color: #33475b; margin-bottom: 14px; }
@media print { h2 { page-break-after: avoid; } table, blockquote { page-break-inside: avoid; }
               .hint { display: none; } }        /* 提示只在屏幕上出现，不进 PDF */
"""


def _inline(t: str) -> str:
    t = html.escape(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
    return t


def md_to_html(md: str) -> str:
    """够用就好的 Markdown 子集：标题 / 列表 / 加粗 / 行内码 / 表格 / 分隔线 / 引用。"""
    out, in_ul, in_table = [], False, False

    def close_blocks():
        nonlocal in_ul, in_table
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_table:
            out.append("</table>")
            in_table = False

    for raw in md.splitlines():
        line = raw.rstrip()
        if line.startswith("|") and line.count("|") >= 2:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):        # 分隔行
                continue
            if not in_table:
                close_blocks()
                out.append("<table>")
                in_table = True
                out.append("<tr>" + "".join(f"<th>{_inline(c)}</th>" for c in cells) + "</tr>")
            else:
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
            continue
        close_blocks()
        if not line.strip():
            continue
        if line.startswith("#"):
            lvl = min(6, len(line) - len(line.lstrip("#")))
            out.append(f"<h{lvl}>{_inline(line.lstrip('#').strip())}</h{lvl}>")
        elif re.match(r"^\s*[-*]\s+", line):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append("<li>" + _inline(re.sub(r"^\s*[-*]\s+", "", line)) + "</li>")
        elif line.strip() == "---":
            out.append("<hr/>")
        elif line.startswith(">"):
            out.append("<blockquote>" + _inline(line.lstrip("> ")) + "</blockquote>")
        else:
            out.append("<p>" + _inline(line) + "</p>")
    close_blocks()
    return "\n".join(out)


def export_html(md_path: str | Path, title: str = "面试复盘报告") -> Path:
    md_path = Path(md_path)
    md = md_path.read_text(encoding="utf-8")
    body = md_to_html(md)
    doc = (f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
           f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
           f"<body><div class='hint'>用浏览器「打印 / Ctrl+P」即可存成 PDF"
           f"（页面尺寸 A4、已设好分页规避；这条提示不会被打印出来）</div>{body}</body></html>")
    out = md_path.with_suffix(".html")
    out.write_text(doc, encoding="utf-8")
    return out
