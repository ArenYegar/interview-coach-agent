"""工具 3：金标准题库检索（确定性打分，可断言、可复现）。"""
from __future__ import annotations

import json
from pathlib import Path

from .. import config
from ..schemas import JDProfile, Question, Rubric, RubricDim


def load_bank(path: str | Path | None = None) -> list[Question]:
    p = Path(path or config.BANK_PATH)
    if not p.exists():
        raise FileNotFoundError(f"题库不存在: {p}")
    out: list[Question] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        dims = [RubricDim(**dim) for dim in d.pop("rubric")]
        out.append(Question(**d, rubric=Rubric(dims=dims)))
    return out


def _overlap(a: set[str], b: set[str]) -> float:
    if not a:
        return 0.0
    return len(a & b) / len(a)


def search_bank(query: str, profile: JDProfile | None = None, k: int = 5,
                bank: list[Question] | None = None, categories: list[str] | None = None
                ) -> list[Question]:
    """按"JD 关键词 ∩ 题目技能 + 分类平衡"排序取 top-k（同分按 qid 稳定排序）。"""
    bank = bank if bank is not None else load_bank()
    want: set[str] = set()
    if profile:
        for r in profile.requirements:
            want.update(x.lower() for x in r.keywords)
    want.update(w.lower() for w in query.split() if len(w) > 1)

    scored: list[tuple[float, str, Question]] = []
    for q in bank:
        if categories and q.category not in categories:
            continue
        qs = {s.lower() for s in q.skills} | {w.lower() for w in q.text.split()}
        sc = _overlap(want, qs) * 2 + _overlap(qs, want)
        scored.append((sc, q.qid, q))
    scored.sort(key=lambda x: (-x[0], x[1]))

    # 分类平衡：避免 5 题全落在同一类
    picked: list[Question] = []
    seen_cat: dict[str, int] = {}
    for sc, _, q in scored:
        if seen_cat.get(q.category, 0) >= max(1, k // 3):
            continue
        picked.append(q)
        seen_cat[q.category] = seen_cat.get(q.category, 0) + 1
        if len(picked) >= k:
            break
    if len(picked) < k:
        for sc, _, q in scored:
            if q not in picked:
                picked.append(q)
            if len(picked) >= k:
                break
    return picked


def _score_question(q: Question, want: set[str]) -> float:
    qs = {s.lower() for s in q.skills} | {w.lower() for w in q.text.split()}
    return _overlap(want, qs) * 2 + _overlap(qs, want)


def plan_questions(profile: JDProfile, k: int = 5, bank: list[Question] | None = None,
                   categories: list[str] | None = None) -> list[Question]:
    """覆盖优先选题：先保证每条硬性要求都有一道对口题，再按相关性补齐到 k 题。

    为什么这么做：评测里的 coverage_at_k 就是按这个目标设计的 —— 选题要打到岗位要求上，
    而不是随便挑几道热门题。面试可讲：我先定义"覆盖率"目标，再让选题策略服务这个目标。
    """
    bank = bank if bank is not None else load_bank()
    pool = [q for q in bank if (not categories or q.category in categories)]
    picked: list[Question] = []
    used: set[str] = set()

    for req in [r for r in profile.requirements if r.must_have] or profile.requirements:
        want = {x.lower() for x in (req.keywords or [req.text[:6]])}
        ranked = sorted(pool, key=lambda q: (-_score_question(q, want), q.qid))
        for q in ranked:
            if q.qid in used:
                continue
            if _score_question(q, want) <= 0 and len(picked) >= 1:
                continue
            picked.append(q.model_copy(update={"target_req_id": req.req_id})); used.add(q.qid)
            break
        if len(picked) >= k:
            break

    if len(picked) < k:  # 补齐：类别平衡 + 相关性
        want_all = {x.lower() for r in profile.requirements for x in r.keywords}
        seen_cat: dict[str, int] = {}
        for q in picked:
            seen_cat[q.category] = seen_cat.get(q.category, 0) + 1
        for q in sorted(pool, key=lambda q: (-_score_question(q, want_all), q.qid)):
            if q.qid in used:
                continue
            if seen_cat.get(q.category, 0) >= max(1, k // 3):
                continue
            picked.append(q); used.add(q.qid)
            seen_cat[q.category] = seen_cat.get(q.category, 0) + 1
            if len(picked) >= k:
                break
        for q in sorted(pool, key=lambda q: (-_score_question(q, want_all), q.qid)):
            if len(picked) >= k:
                break
            if q.qid not in used:
                picked.append(q); used.add(q.qid)
    return picked[:k]
