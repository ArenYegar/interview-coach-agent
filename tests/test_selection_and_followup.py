"""选题策略与追问策略的单测（2026-09-20 加，源自用户实测反馈）。

用户反馈三条，逐条锁住：
  1) "每次生成的题目都一样"  → 练习模式必须能换题，且同 seed 可复现；
  2) "题目偏向后端"          → AI 应用方向的 JD，非方向类目（技术基础/算法模型/行为动机）要有配额上限，
                               且逐条要求本身仍要被覆盖（不能为了躲后端而答不对岗）；
  3) "不会回答就一直追问，甚至重复同一个问题" → 追问必须换维度、只问没问过的要点，
                               没有新要点就停（宁可不问，也不重复问）。
"""
import unittest

from coach.schemas import JDProfile, JDRequirement, MatchResult, Question, Rubric, RubricDim
from coach.schemas import ScoreCard
from coach.tools import load_bank, plan_questions
from coach.tools.search_bank import DIRECTION_CATEGORIES, is_direction_jd

AI_JD = JDProfile(
    title="AI 应用开发实习生（大模型应用 / Agent 方向）",
    requirements=[
        JDRequirement(req_id="R1", text="熟悉 Python，具备完整项目开发与线上部署经验",
                      keywords=["Python", "部署"], must_have=True),
        JDRequirement(req_id="R2", text="掌握大模型应用开发：Prompt 工程、结构化输出、Function Calling",
                      keywords=["大模型", "Function Calling", "Prompt"], must_have=True),
        JDRequirement(req_id="R3", text="熟悉 RAG 全链路：文档解析、Embedding、向量检索、重排、评测",
                      keywords=["RAG", "检索", "重排", "评测"], must_have=True),
        JDRequirement(req_id="R4", text="了解 Agent 编排：工具调用、状态机、失败恢复",
                      keywords=["Agent", "工具调用", "状态机"], must_have=True),
        JDRequirement(req_id="R5", text="熟悉 FastAPI 接口开发与 SSE 流式响应",
                      keywords=["FastAPI", "SSE"], must_have=True),
    ],
)

OFF_DIRECTION = {"技术基础", "算法模型", "行为动机"}


def _q(qid="技术-01", dims=None):
    dims = dims or [RubricDim(name="覆盖度", weight=1.0, keywords=["CPU密集", "IO密集", "多进程"],
                              criteria="覆盖关键要点"),
                    RubricDim(name="深度与取舍", weight=0.6, keywords=["实测", "代价"],
                              criteria="深度与取舍")]
    return Question(qid=qid, category="技术基础", difficulty="normal", skills=["Python"],
                    text="Python 的 GIL 对你写的服务有什么影响？", rubric=Rubric(dims=dims))


class TestSelectionVariety(unittest.TestCase):
    def test_same_input_is_deterministic_without_variety(self):
        a = plan_questions(AI_JD, k=5, bank=load_bank(), variety=False)
        b = plan_questions(AI_JD, k=5, bank=load_bank(), variety=False)
        self.assertEqual([q.qid for q in a], [q.qid for q in b])

    def test_variety_changes_questions_but_seed_reproduces(self):
        import random
        runs = [[q.qid for q in plan_questions(AI_JD, k=5, bank=load_bank(),
                                              variety=True, rng=random.Random())] for _ in range(6)]
        self.assertTrue(any(r != runs[0] for r in runs[1:]), "开启多样后应当能抽到不同的题")
        s1 = [q.qid for q in plan_questions(AI_JD, k=5, bank=load_bank(), variety=True, rng=random.Random(42))]
        s2 = [q.qid for q in plan_questions(AI_JD, k=5, bank=load_bank(), variety=True, rng=random.Random(42))]
        s3 = [q.qid for q in plan_questions(AI_JD, k=5, bank=load_bank(), variety=True, rng=random.Random(7))]
        self.assertEqual(s1, s2, "同 seed 必须可复现")
        self.assertNotEqual(s1, s3, "不同 seed 应当给出不同组合")

    def test_exclude_qids_avoids_recent_questions(self):
        base = [q.qid for q in plan_questions(AI_JD, k=5, bank=load_bank(), variety=False)]
        banned = {base[0]}
        new = [q.qid for q in plan_questions(AI_JD, k=5, bank=load_bank(), variety=False,
                                            exclude_qids=banned)]
        self.assertNotIn(base[0], new, "最近做过的题不该立刻再出现")
        self.assertEqual(len(new), 5)

    def test_direction_jd_recognised(self):
        self.assertTrue(is_direction_jd(AI_JD))
        backend = JDProfile(title="Java 后端开发", requirements=[
            JDRequirement(req_id="R1", text="熟悉 Java 与 Spring", keywords=["Java", "Spring"])])
        self.assertFalse(is_direction_jd(backend))

    def test_off_direction_quota_for_ai_jd(self):
        for k in (2, 5, 7):
            qs = plan_questions(AI_JD, k=k, bank=load_bank(), variety=False)
            off = [q for q in qs if q.category in OFF_DIRECTION]
            self.assertLessEqual(len(off), max(1, round(k * 0.4)),
                                 f"k={k} 时非方向题超标：{[q.qid for q in off]}")
            self.assertGreaterEqual(len({q.category for q in qs}), 2)
            self.assertTrue(any(q.category in DIRECTION_CATEGORIES for q in qs))

    def test_requirement_coverage_still_holds(self):
        """躲后端不能躲掉岗位要求：k 足够大时，硬性要求应各有一道对口题（target_req_id 打上）。"""
        qs = plan_questions(AI_JD, k=5, bank=load_bank(), variety=False)
        targets = {q.target_req_id for q in qs}
        self.assertIn("R1", targets)
        self.assertGreaterEqual(len([t for t in targets if t]), 5)


class TestFollowupNotRepeating(unittest.TestCase):
    def _card(self, missing, dims=None):
        return ScoreCard(qid="技术-01", dim_scores={"覆盖度": 0.0, "深度与取舍": 0.0}, total=0.0,
                         evidence=[], missing=list(missing), verdict="weak", improvement="",
                         method="llm")

    def test_second_followup_differs_from_first(self):
        from coach.graph.engine import CoachEngine
        eng = CoachEngine(use_llm=False, probe=False)
        q = _q()
        card = self._card(["CPU密集", "IO密集", "多进程", "实测", "代价"])
        t1, p1 = eng._followup_text(q, card, asked=set(), layer=1)
        t2, p2 = eng._followup_text(q, card, asked=set(p1), layer=2)
        self.assertIsNotNone(t1)
        self.assertIsNotNone(t2)
        self.assertNotEqual(t1, t2, "第二层追问不能与第一层逐字相同")
        self.assertFalse(set(p1) & set(p2), f"第二层不该重复第一层问过的要点：{p1} vs {p2}")

    def test_stops_when_no_new_point_left(self):
        """所有缺失要点都被问过之后，必须返回 None（停止追问），而不是换个说法再问一遍。"""
        from coach.graph.engine import CoachEngine
        eng = CoachEngine(use_llm=False, probe=False)
        q = _q(dims=[RubricDim(name="覆盖度", weight=1.0, keywords=["CPU密集"], criteria=""),
                     RubricDim(name="深度与取舍", weight=0.6, keywords=["实测"], criteria="")])
        card = self._card(["CPU密集", "实测"])
        t1, p1 = eng._followup_text(q, card, asked=set(), layer=1)
        self.assertIsNotNone(t1)
        t2, p2 = eng._followup_text(q, card, asked={"CPU密集", "实测"}, layer=2)
        self.assertIsNone(t2, "要点都问过了就必须停，不能重复问")
        # 恰好问完最后一个要点的那一层之后，再问也要停
        # 注意 _followup_text 返回 (文案, 要点) 元组，"停" 的哨兵是 (None, [])，断言要解开再判
        last_txt, pl = eng._followup_text(q, card, asked=set(p1), layer=2)
        self.assertIsNotNone(last_txt)
        nxt_txt, nxt_pts = eng._followup_text(q, card, asked=set(p1) | set(pl), layer=2)
        self.assertIsNone(nxt_txt)
        self.assertEqual(nxt_pts, [])

    def test_engine_run_never_asks_same_followup_twice(self):
        from coach.graph.engine import CoachEngine
        jd = "- 熟悉 Python\n- 掌握 RAG 检索与评测\n- 了解 Agent 工具调用"
        eng = CoachEngine(run_id="unittest-followup-dedup", max_questions=2, use_llm=False)
        report = eng.run(jd, "熟悉 Python 与 RAG", answers=["不会"] * 8)
        fus = [t for t in report.turns if t.follow_up]
        for base in {t.qid.split("-F")[0] for t in fus}:
            texts = [t.question for t in fus if t.qid.startswith(base + "-F")]
            self.assertEqual(len(texts), len(set(texts)), f"{base} 的追问文案出现重复：{texts}")


if __name__ == "__main__":
    unittest.main()
