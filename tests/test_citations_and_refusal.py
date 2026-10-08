"""引用（可溯源）与拒答（知识不足显式声明）的回归测试（离线）。

锁住三件事：
  1. 引用必须**真的出自作答原文**（不是模型再总结一遍），所以断言 quote 是回答的子串；
  2. 回答里找不到对应句子时**不给引用**（不硬凑）；
  3. 题库没有对口题的硬性要求要被显式写进报告（拒答），而不是沉默或编题凑覆盖。
"""
import os
import shutil
import unittest
from pathlib import Path

from coach import config
from coach.schemas import Question, Rubric, RubricDim
from coach.tools.score_answer import heuristic_score, score_answer


def _q() -> Question:
    return Question(qid="T-01", category="技术基础", text="说说你的做法",
                    rubric=Rubric(dims=[RubricDim(name="覆盖度", weight=1.0,
                                                  keywords=["混合检索", "重排", "离线评测"])]))


class TestCitations(unittest.TestCase):
    def setUp(self):
        # 注意：本类会改 COACH_GUARD / COACH_CACHE，必须还原 ——
        # 实测踩过：COACH_GUARD=0 会改掉 parse_jd 的分隔符写法，
        # 让后面某个测试里 Mock 解析出完全不同的 requirements（"单独跑过、一起跑就挂"）。
        self._old = {k: os.environ.get(k) for k in ("COACH_GUARD", "COACH_CACHE")}

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def test_quote_comes_from_answer(self):
        ans = "我先做混合检索把候选召回上来。然后用重排把最相关的放到前面。最后在离线评测上量了指标。"
        card = heuristic_score(_q(), ans)
        self.assertTrue(card.citations)
        for c in card.citations:
            self.assertIn(c["quote"], ans)              # 引用必须出自原文
        self.assertEqual({c["point"] for c in card.citations}, {"混合检索", "重排", "离线评测"})

    def test_no_quote_when_point_absent(self):
        card = heuristic_score(_q(), "我没做过这块。")
        self.assertEqual(card.citations, [])             # 不硬凑

    def test_llm_path_also_gets_citations(self):
        from coach.schemas import ScoreCard

        class FakeLLM:
            model = "fake"

            def structured(self, s, u, schema, repair_rounds=1):
                return ScoreCard(qid="T-01", dim_scores={}, total=0.8,
                                 evidence=["混合检索"], missing=[], verdict="ok",
                                 improvement="", method="heuristic")

        os.environ["COACH_GUARD"] = "0"
        os.environ["COACH_CACHE"] = "0"
        card = score_answer(_q(), "我用混合检索做的召回。", llm=FakeLLM())
        self.assertEqual(len(card.citations), 1)
        self.assertIn("混合检索", card.citations[0]["quote"])


class TestRefusal(unittest.TestCase):
    RUN = "unittest-refusal"

    def tearDown(self):
        shutil.rmtree(Path(config.RUNS_DIR) / self.RUN, ignore_errors=True)
        for p in Path(config.RESULTS_DIR).glob(f"*{self.RUN}*"):
            p.unlink()

    def test_uncovered_must_have_is_reported(self):
        """JD 里有一条明显没有对口题的硬性要求 → 必须出现在报告的 uncovered 里。"""
        os.environ["COACH_OFFLINE"] = "1"
        os.environ["COACH_HITL"] = "0"
        try:
            from coach.graph import CoachEngine
            jd = "- 熟悉 Python\n- 熟练使用 Kubernetes 做多集群编排与故障演练\n- 了解 RAG 检索"
            rs = "熟悉 Python，做过 RAG 检索"
            rep = CoachEngine(run_id=self.RUN, max_questions=2, follow_up=False).run(jd, rs, answers=["不知道"])
            self.assertTrue(rep.uncovered, "题库缺口没有被报告出来")
            self.assertTrue(any("Kubernetes" in u for u in rep.uncovered))
        finally:
            os.environ.pop("COACH_OFFLINE", None)


if __name__ == "__main__":
    unittest.main()
