"""工具 1：JD 结构化解析。LLM 优先，失败/离线时退化为规则解析。"""
from __future__ import annotations

import re

from ..schemas import JDProfile, JDRequirement

_BULLET = re.compile(r"^\s*(?:[-*•·]|\d+[.、)]|[（(]\d+[)）])\s*(.+)$")
_HARD = ("熟悉", "掌握", "精通", "必备", "要求", "具备", "must", "proficient", "required")
_TECH = re.compile(r"[A-Za-z][A-Za-z0-9+#._/-]{1,20}|[\u4e00-\u9fa5]{2,8}")
_STOP = {"以及", "相关", "经验", "能力", "优先", "工作", "负责", "我们", "岗位", "要求", "熟练", "了解"}


def _keywords(text: str) -> list[str]:
    out: list[str] = []
    for kw in _TECH.findall(text):
        if kw in _STOP or len(kw) < 2:
            continue
        if kw not in out:
            out.append(kw)
    return out[:8]


def heuristic_parse_jd(jd_text: str, max_reqs: int = 12) -> JDProfile:
    """规则解析：按行抽要求，前 3 条默认视为硬性要求。"""
    title = "未知岗位"
    for line in jd_text.splitlines():
        s = line.strip()
        if s and ("岗位" in s or "职位" in s or "实习生" in s or "工程师" in s):
            title = re.sub(r"^[#\s]*", "", s)[:40]
            break

    reqs: list[JDRequirement] = []
    for raw in jd_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _BULLET.match(line)
        body = m.group(1).strip() if m else (line if any(k in line for k in _HARD) else "")
        if not body or len(body) < 4:
            continue
        reqs.append(JDRequirement(
            req_id=f"R{len(reqs) + 1}",
            text=body[:160],
            must_have=(len(reqs) < 3) or any(k in body for k in _HARD),
            keywords=_keywords(body),
        ))
        if len(reqs) >= max_reqs:
            break

    if not reqs:  # 极端兜底：把整段当一条要求
        reqs = [JDRequirement(req_id="R1", text=jd_text.strip()[:160], must_have=True,
                              keywords=_keywords(jd_text))]
    return JDProfile(title=title, requirements=reqs,
                     digest=f"规则解析出 {len(reqs)} 条要求", source="heuristic")


def parse_jd(jd_text: str, llm=None) -> JDProfile:
    """JD 解析入口：有模型走模型（结构化输出），否则走规则。"""
    if not jd_text or not jd_text.strip():
        raise ValueError("JD 文本为空")
    if llm is None:
        return heuristic_parse_jd(jd_text)

    prompt = (
        "请把下面的招聘 JD 解析成结构化要求列表。要求：\n"
        "1) 每条要求给出 req_id（R1、R2...）、原文压缩后的 text、是否硬性要求 must_have、以及 2~8 个用于匹配简历的关键词 keywords；\n"
        "2) 只输出 JSON，字段为 title / company / requirements / digest；\n"
        f"3) JSON schema: {JDProfile.model_json_schema()}\n\n【JD 原文】\n{jd_text[:4000]}"
    )
    try:
        profile = llm.structured("你是招聘信息结构化解析器，只输出 JSON。", prompt, JDProfile)
        profile.source = "llm"
        return profile
    except Exception:
        return heuristic_parse_jd(jd_text)
