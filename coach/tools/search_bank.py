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


# 方向类目：AI 应用岗真正想考的题目类型；其余三类视为"非方向"（通用工程/算法/HR）
DIRECTION_CATEGORIES = {"LLM应用", "Agent", "RAG", "项目深挖"}
OFF_DIRECTION_CATEGORIES = {"技术基础", "算法模型", "行为动机"}
# 命中这些词就认为这份 JD 是"AI 应用"方向（用于方向加权与配额）
DIRECTION_HINTS = ("大模型", "llm", "agent", "智能体", "rag", "检索增强", "prompt", "多模态",
                   "ai 应用", "ai应用", "aigc", "模型应用")


def is_direction_jd(profile: JDProfile) -> bool:
    text = " ".join([profile.title or ""] + [r.text for r in profile.requirements]).lower()
    return any(h in text for h in DIRECTION_HINTS)


def plan_questions(profile: JDProfile, k: int = 5, bank: list[Question] | None = None,
                   categories: list[str] | None = None, selector=None,
                   exclude_qids: set[str] | None = None, rng=None,
                   variety: bool = False, direction_boost: bool = True,
                   req_weights: dict[str, float] | None = None) -> list[Question]:
    """覆盖优先选题：先保证每条硬性要求都有一道对口题，再按相关性补齐到 k 题。

    为什么这么做：评测里的 coverage_at_k 就是按这个目标设计的 —— 选题要打到岗位要求上，
    而不是随便挑几道热门题。面试可讲：我先定义"覆盖率"目标，再让选题策略服务这个目标。

    2026-09-20 增加了三个开关（都是因为用户实测反馈"每次题目都一样 / 感觉偏后端"）：
      · exclude_qids：排除近期场次已出过的题 —— 练习时不再每次遇到同一批题；
      · rng + variety：在"分数接近前列"的候选里随机挑一个（同 seed 可复现），避免固定一套题；
      · direction_boost：AI 应用方向的 JD，对方向类目加权，并给非方向类目设上限
        （避免"Python 经验"这条要求每次都把 Python GIL / Docker 瘦身这类后端基础题顶上来）。

    2026-10-08 增加 req_weights：**按 JD 定制某条要求的权重**。
    键可以是 req_id（"R1"）或要求原文里的片段（"RAG"）；命中则该要求的相关性乘这个倍数。
    用途：同一份题库，不同 JD 的考察重点不同 —— 投 Agent 岗就把 Agent 那条要求权重调高，
    出题自然向它倾斜（而不是改题库、也不是改全局默认值）。
    """
    bank = bank if bank is not None else load_bank()
    pool = [q for q in bank if (not categories or q.category in categories)]
    picked: list[Question] = []
    used: set[str] = set()
    skip = {q for q in (exclude_qids or set())}
    direction = is_direction_jd(profile) and direction_boost
    # k 小的场次也别把额度全用光：k=5 → 非方向最多 2 题、单类最多 1 题
    off_quota = max(1, round(k * 0.4)) if direction else 10 ** 6
    off_cat_cap = 1 if (direction and k >= 4) else 10 ** 6
    off_used = 0
    off_by_cat: dict[str, int] = {}

    def req_weight(req) -> float:
        """该要求在本份 JD 下的权重倍数（未命中任何键时为 1.0）。"""
        if not req_weights:
            return 1.0
        for key, val in req_weights.items():
            if key == req.req_id or (key and key in (req.text or "")):
                try:
                    return float(val)
                except (TypeError, ValueError):
                    return 1.0
        return 1.0

    def relevance(req, q: Question) -> float:
        """相关性打分：有混合检索器时用 RRF 分（稠密+BM25），否则退回关键词重叠。"""
        base = (selector.score(req.text).get(q.qid, 0.0) if selector is not None
                else _score_question(q, {x.lower() for x in (req.keywords or [req.text[:6]])}))
        base *= req_weight(req)
        if direction and q.category in DIRECTION_CATEGORIES:
            base *= 1.25                      # 同等相关时，方向类目的题优先（观感不偏后端）
        return base

    def pick_from(ranked, req_id: str | None) -> Question | None:
        """从候选里选一个：优先"没做过的题"，其次在分数接近前列的候选里随机（variety）。"""
        if not ranked:
            return None
        fresh = [q for q in ranked if q.qid not in skip and q.qid not in used]
        cand = fresh or [q for q in ranked if q.qid not in used]
        if not cand:
            return None
        top = cand[0]
        if variety and rng is not None and len(cand) > 1:
            # 只在前列（相关性不低于最高分 85%）里随机，保证质量不掉档
            head = [q for q in cand if relevance_of(q) >= relevance_of(top) * 0.85][:4]
            chosen = rng.choice(head or [top])
        else:
            chosen = top
        return chosen.model_copy(update={"target_req_id": req_id}) if req_id else chosen

    def relevance_of(q: Question) -> float:
        return score_by_id.get(q.qid, 0.0)

    musts = [r for r in profile.requirements if r.must_have] or profile.requirements
    # 权重的作用点一：高权重的要求**先挑题**（先占有最相关的题，而不是被前面的要求抢走）
    order = sorted(range(len(musts)), key=lambda i: (-req_weight(musts[i]), i))
    musts = [musts[i] for i in order]
    # 权重的作用点二：补齐阶段用"跨全部要求、加权后取最大"，而不是只看最后一条要求
    all_rel: dict[str, float] = {}
    for req in musts:
        for q in pool:
            all_rel[q.qid] = max(all_rel.get(q.qid, 0.0), relevance(req, q))
    score_by_id: dict[str, float] = {}
    for req in musts:
        want = {x.lower() for x in (req.keywords or [req.text[:6]])}
        ranked = sorted(pool, key=lambda q: (-relevance(req, q), q.qid))
        score_by_id = {q.qid: relevance(req, q) for q in ranked}
        # 逐条要求仍按"最相关"选（不硬性偏袒方向类目，否则 R1"熟悉 Python"这类要求会被塞进
        # 不相干的 AI 题，覆盖率反而失真）；"不偏后端"靠 relevance 里的方向加权 + 配额实现。
        # 同时在这里也做一次类别上限，避免 k 小的时候 5 道题里有 3 道都是 Agent 类。
        cat_cap = max(1, k // 3)
        seen_now: dict[str, int] = {}
        for x in picked:
            seen_now[x.category] = seen_now.get(x.category, 0) + 1
        within = [x for x in ranked if seen_now.get(x.category, 0) < cat_cap]
        # 相关性为 0 的要求**不许强行对口**：宁可不出题（报告会把它列为"题库未覆盖"），
        # 也不能把不相干的题标成"覆盖了这条要求" —— 那会让覆盖率虚高。
        # 实测踩到过：JD 里"熟练使用 Kubernetes"被强行对口成一道 ReAct 题。
        if relevance_of(ranked[0]) <= 0:
            continue
        q = pick_from(within, req.req_id) or pick_from(ranked, req.req_id)
        if q is not None:
            picked.append(q); used.add(q.qid)
            if q.category in OFF_DIRECTION_CATEGORIES:
                off_used += 1
                off_by_cat[q.category] = off_by_cat.get(q.category, 0) + 1
        if len(picked) >= k:
            break

    if len(picked) < k:  # 补齐：类别平衡 + 相关性 + 方向配额
        want_all = {x.lower() for r in profile.requirements for x in r.keywords}
        seen_cat: dict[str, int] = {}
        for q in picked:
            seen_cat[q.category] = seen_cat.get(q.category, 0) + 1
        for q in sorted(pool, key=lambda q: (-(relevance_of(q) or all_rel.get(q.qid) or _score_question(q, want_all)), q.qid)):
            if q.qid in used:
                continue
            if seen_cat.get(q.category, 0) >= max(1, k // 3):
                continue
            if q.category in OFF_DIRECTION_CATEGORIES and direction:
                if off_used >= off_quota or off_by_cat.get(q.category, 0) >= off_cat_cap:
                    continue
            if q.qid in skip and any(x.qid not in skip for x in pool if x.qid not in used):
                continue                       # 还有"没做过"的题就别重复
            picked.append(q); used.add(q.qid)
            seen_cat[q.category] = seen_cat.get(q.category, 0) + 1
            if q.category in OFF_DIRECTION_CATEGORIES:
                off_used += 1
                off_by_cat[q.category] = off_by_cat.get(q.category, 0) + 1
            if len(picked) >= k:
                break
        for q in sorted(pool, key=lambda q: (-(relevance_of(q) or all_rel.get(q.qid) or _score_question(q, want_all)), q.qid)):
            if len(picked) >= k:
                break
            if q.qid not in used:
                picked.append(q); used.add(q.qid)
    return picked[:k]
