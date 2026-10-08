"""把上传的文件（简历/JD）变成纯文本 —— Web 上传入口与 CLI 共用的清洗层。

设计原则：任何格式都不"静默失败"——解析不了的格式要给出明确原因，
并且明确告诉用户支持哪些格式，而不是抛一个空文本让后面的流程误解。
"""
from __future__ import annotations

import io
from pathlib import Path

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text", ".json", ".csv"}
DOCX_SUFFIX = {".docx"}
PDF_SUFFIX = {".pdf"}
MAX_BYTES = 5 * 1024 * 1024          # 单文件上限 5MB，够放简历与 JD


class IngestError(ValueError):
    """输入文件无法解析时抛出，带可读原因。"""


def _docx_text(data: bytes) -> str:
    try:
        import docx                                    # python-docx
    except Exception as e:                             # pragma: no cover
        raise IngestError(f"缺少 python-docx，无法解析 .docx（{type(e).__name__}）") from e
    d = docx.Document(io.BytesIO(data))
    parts = [p.text.strip() for p in d.paragraphs if p.text.strip()]
    for tb in d.tables:
        for row in tb.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _pdf_text(data: bytes) -> str:
    """PDF 取文：按"快→稳"依次尝试多个后端，全都不行才报错。

    为什么要多后端：本机 llm 环境里没有 PyMuPDF（fitz），但有 pypdf / pdfplumber / pdfminer.six，
    原先只认 fitz 会导致"明明能读的 PDF 直接报缺依赖"。多后端 + 明确列出各后端结果，才能既省事又不骗人。
    """
    attempts: list[str] = []

    def _pypdf() -> str:
        import io
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)

    def _pdfplumber() -> str:
        import io
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)

    def _pdfminer() -> str:
        import io
        from pdfminer.high_level import extract_text
        return extract_text(io.BytesIO(data))

    def _fitz() -> str:
        import fitz                                    # PyMuPDF（有就用，最快）
        with fitz.open(stream=data, filetype="pdf") as doc:
            return "\n".join(page.get_text() for page in doc)

    for name, fn in (("pypdf", _pypdf), ("pdfplumber", _pdfplumber),
                     ("pdfminer.six", _pdfminer), ("PyMuPDF", _fitz)):
        try:
            text = fn()
        except ImportError:
            attempts.append(f"{name}：未安装")
            continue
        except Exception as e:
            attempts.append(f"{name}：{type(e).__name__}")
            continue
        if text and text.strip():
            return text
        attempts.append(f"{name}：取到 0 字")

    detail = "；".join(attempts) or "无可用后端"
    raise IngestError(
        "这份 PDF 里没有可提取的文字（可能是扫描件/图片版简历，或文件已加密）。"
        f"各解析后端结果：{detail}。请把简历另存为 .docx 或 .txt 再上传。")


def _looks_too_short(text: str, suffix: str) -> str | None:
    """解析出来只有零星文字时给一句提醒（不拦，让用户自己判断）。"""
    if suffix in PDF_SUFFIX and len(text.strip()) < 60:
        return ("（提示：这份 PDF 只取到很少的文字，可能是图片版简历；"
                "关键信息若缺失，建议改用 .docx/.txt 上传）")
    return None


def text_from_bytes(filename: str, data: bytes) -> str:
    """按扩展名解析上传内容；返回规范化后的文本。"""
    if not data:
        raise IngestError("文件是空的")
    if len(data) > MAX_BYTES:
        raise IngestError(f"文件超过 {MAX_BYTES // 1024 // 1024}MB，请只上传简历/JD 正文")
    suffix = Path(filename or "").suffix.lower()
    if suffix in DOCX_SUFFIX:
        text = _docx_text(data)
    elif suffix in PDF_SUFFIX:
        text = _pdf_text(data)
    elif suffix in TEXT_SUFFIXES or suffix == "":
        text = _decode(data)
    else:
        raise IngestError(f"不支持的格式 {suffix or '(无扩展名)'}；支持 "
                          + "、".join(sorted(TEXT_SUFFIXES | DOCX_SUFFIX | PDF_SUFFIX)))
    text = normalize(text)
    if not text.strip():
        raise IngestError("解析后没有取到任何文字（可能是扫描件或纯图片简历，请改用文本版）")
    warn = _looks_too_short(text, suffix)
    if warn:
        text = text + "\n" + warn          # 让下游（前端/用户）看得到这句提醒
    return text


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "gb18030", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def normalize(text: str) -> str:
    """统一换行、压掉多余空行，避免不同来源的文本把要点切碎。"""
    lines = [ln.rstrip() for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if ln.strip():
            blank = 0
            out.append(ln)
        else:
            blank += 1
            if blank <= 1:
                out.append("")
    return "\n".join(out).strip()


def text_from_path(path: str | Path) -> str:
    p = Path(path)
    if not p.exists():
        raise IngestError(f"找不到文件 {path}")
    return text_from_bytes(p.name, p.read_bytes())
