"""工具层单测（stdlib unittest，无需额外安装）。"""
import unittest

from coach.schemas import (JDProfile, JDRequirement, Question, Rubric, RubricDim, ScoreCard, Turn)
from coach.tools import (aggregate_weakness, heuristic_score, load_bank, match_resume, parse_jd,
                         record_session, search_bank)


class TestParseJd(unittest.TestCase):
    def test_heuristic_extracts_requirements(self):
        p = parse_jd("- 熟悉 Python 与 FastAPI\n- 掌握 RAG 全链路\n- 了解 MCP 优先", llm=None)
        self.assertEqual(p.source, "heuristic")
        self.assertGreaterEqual(len(p.requirements), 3)
        self.assertTrue(p.requirements[0].must_have)
        self.assertTrue(any("Python" in kw for kw in p.requirements[0].keywords))

    def test_empty_jd_raises(self):
        with self.assertRaises(ValueError):
            parse_jd("   ", llm=None)


class TestMatchResume(unittest.TestCase):
    def test_full_match_scores_one(self):
        prof = JDProfile(requirements=[JDRequirement(req_id="R1", text="Python", keywords=["Python"])])
        r = match_resume("我会用 Python 写服务", prof)
        self.assertEqual(r.score, 1.0)
        self.assertTrue(r.items[0].evidence)

    def test_missing_keyword_recorded(self):
        prof = JDProfile(requirements=[JDRequirement(req_id="R1", text="Kubernetes", keywords=["Kubernetes"])])
        r = match_resume("只用过 Python", prof)
        self.assertEqual(r.score, 0.0)
        self.assertIn("Kubernetes", r.missing_keywords)

    def test_must_have_weighted_higher(self):
        prof = JDProfile(requirements=[
            JDRequirement(req_id="R1", text="Python", must_have=True, keywords=["Python"]),
            JDRequirement(req_id="R2", text="Go", must_have=False, keywords=["Golang"]),
        ])
        r = match_resume("Python 熟练", prof)
        self.assertAlmostEqual(r.score, 2 / 3, places=4)


class TestBank(unittest.TestCase):
    def test_bank_invariants(self):
        """题库只锁"不变量"，不锁条数 —— 条数会随扩充变化（曾写死 100，扩充后一跑就挂）。"""
        bank = load_bank()
        self.assertGreaterEqual(len(bank), 100)
        self.assertEqual(len({q.category for q in bank}), 7)
        self.assertEqual(len({q.qid for q in bank}), len(bank))      # qid 唯一
        self.assertEqual(len({q.text for q in bank}), len(bank))     # 题目不重复
        self.assertTrue(all(q.rubric.dims for q in bank))            # 每题都有评分维度
        self.assertTrue(all(q.skills for q in bank))

    def test_search_returns_k_unique(self):
        qs = search_bank("RAG 向量检索 重排", k=5)
        self.assertEqual(len(qs), 5)
        self.assertEqual(len({q.qid for q in qs}), 5)


class TestScore(unittest.TestCase):
    def _q(self):
        return Question(qid="T-01", category="RAG", text="怎么防幻觉？",
                        rubric=Rubric(dims=[RubricDim(name="覆盖度",
                                                      keywords=["prompt约束", "后校验", "拒答"])]))

    def test_keyword_hit_increases_score(self):
        low = heuristic_score(self._q(), "不知道")
        high = heuristic_score(self._q(), "我用 prompt约束 加 后校验，并在低置信时 拒答。" + "细节" * 30)
        self.assertGreater(high.total, low.total)
        self.assertEqual(high.verdict, "strong")

    def test_short_answer_penalised(self):
        card = heuristic_score(self._q(), "prompt约束")
        self.assertLessEqual(card.total, 0.8)
        self.assertTrue(any("过短" in m for m in card.missing))


class TestWeakness(unittest.TestCase):
    def test_record_and_aggregate(self):
        turns = [
            Turn(qid="A-1", question="q1", answer="a",
                 scorecard=ScoreCard(qid="A-1", total=0.2, missing=["量化", "取舍"], verdict="weak")),
            Turn(qid="A-2", question="q2", answer="a",
                 scorecard=ScoreCard(qid="A-2", total=0.3, missing=["量化"], verdict="weak")),
        ]
        self.assertEqual(record_session("unittest-run", turns), 2)
        w = aggregate_weakness("unittest-run")
        self.assertEqual(w[0].tag, "量化")
        self.assertEqual(w[0].count, 2)


if __name__ == "__main__":
    unittest.main()
