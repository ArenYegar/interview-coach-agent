"""工具 4：回答评分。规则打分为确定性核心，模型只在可用时做语义增强。"""
from __future__ import annotations

from ..schemas import Question, ScoreCard


def heuristic_score(question: Question, answer: str) -> ScoreCard:
    """按 rubric 的参考要点关键词覆盖率打分：维度分 = 命中要点数 / 该维度要点数。"""
    ans_low = (answer or "").lower()
    dim_scores: dict[str, float] = {}
    evidence: list[str] = []
    missing: list[str] = []

    for dim in question.rubric.dims:
        kws = dim.keywords or [dim.name]
        hit = [kw for kw in kws if kw.lower() in ans_low]
        miss = [kw for kw in kws if kw.lower() not in ans_low]
        dim_scores[dim.name] = round(len(hit) / len(kws), 4)
        evidence.extend(hit)
        missing.extend(miss)

    tot_w = sum(d.weight for d in question.rubric.dims) or 1.0
    total = sum(dim_scores[d.name] * d.weight for d in question.rubric.dims) / tot_w
    total = round(min(1.0, max(0.0, total)), 4)

    # 长度惩罚：问答过短一律标记；若要点命中较多则再下调分数，避免"关键词堆砌"得高分
    if len(answer or "") < 40:
        missing.append("回答过短，缺少具体背景与结果")
        if total > 0.4:
            total = round(total * 0.8, 4)

    verdict = "strong" if total >= 0.7 else ("ok" if total >= 0.4 else "weak")
    improv = ("补充缺失要点：" + "、".join(missing[:4])) if missing else "结构完整，可再补量化结果"
    return ScoreCard(qid=question.qid, dim_scores=dim_scores, total=total, evidence=sorted(set(evidence)),
                     missing=sorted(set(missing)), verdict=verdict, improvement=improv, method="heuristic")


def score_answer(question: Question, answer: str, llm=None) -> ScoreCard:
    """评分入口：模型可用时走模型（结构化 ScoreCard），异常一律回退到规则打分。"""
    base = heuristic_score(question, answer)
    if llm is None:
        return base
    try:
        dims = {d.name: {"标准": d.criteria, "参考要点": d.keywords} for d in question.rubric.dims}
        prompt = (
            f"【问题】{question.text}\n"
            f"【评分维度与参考要点】{dims}\n"
            f"【候选人回答】{answer}\n\n"
            "评分要求（严格遵守）：\n"
            "1) 每个维度打 0~1 分（保留两位小数）；total 为按权重加权后的总分，必须在 0~1；\n"
            "2) evidence 只能从【参考要点】里原样挑选回答中真正讲到的条目，不要自己造词；\n"
            "3) missing 只能从【参考要点】里挑选回答中没讲到的条目；\n"
            "4) 回答若明显在答非所问，直接给弱判定并说明。\n"
            "4+) verdict 取值 strong(>=0.7)/ok(>=0.4)/weak(其余)；improvement 一句可执行建议。\n"
            f"只输出 JSON，schema: {ScoreCard.model_json_schema()}"
        )
        card = llm.structured("你是严格但建设性的技术面试官，只输出 JSON。", prompt, ScoreCard)
        card.qid = question.qid
        if not 0.0 <= card.total <= 1.0:
            card.total = max(0.0, min(1.0, card.total))
        card.method = "llm"
        return card
    except Exception:
        return base
