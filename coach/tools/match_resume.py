"""工具 2：简历 × JD 匹配打分（确定性覆盖率为核心，模型只做补充说明）。"""
from __future__ import annotations

import re

from ..schemas import JDProfile, MatchItem, MatchResult

_SENT_SPLIT = re.compile(r"[。；;\n]")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]


def _hit(keyword: str, text_low: str) -> bool:
    k = keyword.lower()
    if not k:
        return False
    return k in text_low  # 中英文统一用子串匹配，避免分词误差放大


def match_resume(resume_text: str, profile: JDProfile, llm=None) -> MatchResult:
    """按"硬性要求权重 2、加分项权重 1"计算覆盖率，并给出证据句与缺口。"""
    if not resume_text or not resume_text.strip():
        raise ValueError("简历文本为空")

    resume_low = resume_text.lower()
    sents = _sentences(resume_text)
    items: list[MatchItem] = []
    missing_kw: list[str] = []
    got_w = tot_w = 0.0

    for req in profile.requirements:
        weight = 2.0 if req.must_have else 1.0
        tot_w += weight
        kws = req.keywords or [req.text[:8]]
        evidence, matched_kws = "", []
        for kw in kws:
            if _hit(kw, resume_low):
                matched_kws.append(kw)
                if not evidence:
                    for s in sents:
                        if kw.lower() in s.lower():
                            evidence = s[:120]
                            break
        if matched_kws:
            got_w += weight
            items.append(MatchItem(req_id=req.req_id, requirement=req.text, must_have=req.must_have,
                                   matched=True, evidence=evidence or "（关键词命中）"))
        else:
            missing_kw.extend(kws[:2])
            items.append(MatchItem(req_id=req.req_id, requirement=req.text, must_have=req.must_have,
                                   matched=False, gap=f"简历中未出现：{'/'.join(kws[:3])}"))

    score = round(got_w / tot_w, 4) if tot_w else 0.0
    result = MatchResult(score=score, items=items,
                         missing_keywords=sorted(set(missing_kw))[:12], method="heuristic")

    if llm is not None:
        try:
            gaps = [i.requirement for i in items if not i.matched][:6]
            if gaps:
                tip = llm.text(
                    "你是简历顾问，只输出 3 条以内、每条不超过 40 字的补充建议，不要客套话。",
                    "岗位要求：" + "；".join(gaps) + "\n我的简历片段：" + resume_text[:1500],
                )
                result.items.append(MatchItem(req_id="ADVICE", requirement="模型补充建议",
                                              must_have=False, matched=False, gap=tip[:300]))
                result.method = "llm+heuristic"
        except Exception:
            pass
    return result
