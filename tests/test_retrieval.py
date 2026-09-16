"""检索层单测：切词、BM25 分支、混合选器与 plan_questions 的集成。"""
import unittest

from coach.retrieval import HybridSelector, _tok
from coach.schemas import JDProfile, JDRequirement
from coach.tools import load_bank, plan_questions


class _FakeSelector:
    """假的混合选器：只认给定 qid 顺序，用来验证 plan_questions 的接口。"""
    def __init__(self, order):
        self.order = order
    def score(self, query):
        return {qid: 1.0 / (i + 1) for i, qid in enumerate(self.order)}


class TestTokenizer(unittest.TestCase):
    def test_chinese_bigrams_and_ascii_words(self):
        t = _tok("熟悉 RAG 全链路")
        self.assertIn("rag", t)
        self.assertTrue(any(len(x) == 2 for x in t))


class TestSelector(unittest.TestCase):
    def test_bm25_ranks_related_question_first(self):
        sel = HybridSelector(load_bank())      # 稠密可能不可用，但 BM25 一定要能跑
        self.assertTrue(sel.bm25 is not None)
        top = sel.rank("RAG 评测分几层，指标怎么定")[:3]
        self.assertTrue(top)
        self.assertTrue(any("评测" in q.text for _, q in top), [q.text for _, q in top])


class TestPlanWithSelector(unittest.TestCase):
    def test_selector_drives_selection(self):
        bank = load_bank()
        prof = JDProfile(requirements=[JDRequirement(req_id="R1", text="RAG 评测", must_have=True,
                                                     keywords=["RAG", "评测"])])
        picks = plan_questions(prof, k=2, bank=bank, selector=_FakeSelector([q.qid for q in bank]))
        self.assertEqual(len(picks), 2)
        self.assertEqual(picks[0].target_req_id, "R1")


if __name__ == "__main__":
    unittest.main()
