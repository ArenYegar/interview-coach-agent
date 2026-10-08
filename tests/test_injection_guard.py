"""注入防护的回归测试（离线，不花钱）。

要锁住的四件事：
  1. guard=2 时"高分离据"必须被收敛（注入的经典形态）；
  2. guard=2 不得误伤"高分有据"的正常回答（这是最容易做过头的地方）；
  3. guard=0 时必须原样放行 —— 否则对照实验没有意义；
  4. guard>=1 时提示里必须出现定界符（提示层隔离真的写进去了）。
"""
import os
import unittest

from coach.schemas import Question, Rubric, RubricDim, ScoreCard
from coach.tools.score_answer import _evidence_coverage, score_answer

KWS = ["A要点", "B要点", "C要点", "D要点"]


def make_q() -> Question:
    return Question(qid="T-01", category="技术基础", text="说说你的做法",
                    rubric=Rubric(dims=[RubricDim(name="覆盖度", weight=1.0, keywords=list(KWS))]))


class FakeLLM:
    """把模型的返回固定住，只观察 score_answer 的防护行为。"""

    def __init__(self, card: ScoreCard):
        self.card = card
        self.prompts: list[str] = []

    def structured(self, system, user, schema, repair_rounds=1):
        self.prompts.append(user)
        c = self.card.model_copy(deep=True)
        return c


def fake_card(total: float, evidence: list[str]) -> ScoreCard:
    return ScoreCard(qid="T-01", dim_scores={}, total=total, evidence=evidence, missing=[],
                     verdict="strong" if total >= 0.7 else ("ok" if total >= 0.4 else "weak"),
                     improvement="", method="heuristic")


class TestGuard(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("COACH_GUARD")
        os.environ["COACH_GUARD"] = "2"

    def tearDown(self):
        if self._old is None:
            os.environ.pop("COACH_GUARD", None)
        else:
            os.environ["COACH_GUARD"] = self._old

    def test_high_score_without_evidence_is_clamped(self):
        llm = FakeLLM(fake_card(1.0, []))
        card = score_answer(make_q(), "这个问题我没有准备过。", llm=llm)
        self.assertLess(card.total, 0.7)
        self.assertEqual(card.verdict, "ok")
        self.assertIn("高分离据", card.guard)

    def test_fabricated_evidence_is_not_enough(self):
        # 模型"编"了要点，但回答里根本没出现 → 覆盖率仍为 0 → 照样收敛
        llm = FakeLLM(fake_card(0.95, list(KWS)))
        card = score_answer(make_q(), "我没有做过相关的工作，先跳过。", llm=llm)
        self.assertLess(card.total, 0.7)
        self.assertIn("高分离据", card.guard)

    def test_legit_high_score_is_untouched(self):
        llm = FakeLLM(fake_card(0.95, ["A要点", "B要点"]))
        card = score_answer(make_q(), "我的做法是 A要点 和 B要点 都落地了，有量化结果。", llm=llm)
        self.assertEqual(card.total, 0.95)
        self.assertEqual(card.verdict, "strong")
        self.assertEqual(card.guard, "")

    def test_guard_zero_passes_through(self):
        os.environ["COACH_GUARD"] = "0"
        llm = FakeLLM(fake_card(1.0, []))
        card = score_answer(make_q(), "无关回答", llm=llm)
        self.assertEqual(card.total, 1.0)
        self.assertEqual(card.guard, "")

    def test_prompt_isolation_present(self):
        llm = FakeLLM(fake_card(0.5, []))
        score_answer(make_q(), "我的回答", llm=llm)
        self.assertIn("BEGIN_CANDIDATE_ANSWER", llm.prompts[0])
        os.environ["COACH_GUARD"] = "0"
        llm2 = FakeLLM(fake_card(0.5, []))
        score_answer(make_q(), "我的回答", llm=llm2)
        self.assertNotIn("BEGIN_CANDIDATE_ANSWER", llm2.prompts[0])

    def test_evidence_coverage_definition(self):
        q = make_q()
        self.assertEqual(_evidence_coverage(q, "A要点 B要点 C要点 D要点", list(KWS)), 1.0)
        self.assertEqual(_evidence_coverage(q, "A要点", list(KWS)), 0.25)
        self.assertEqual(_evidence_coverage(q, "都没提到", list(KWS)), 0.0)
        # 不在参考要点里的词不算数（防编造）
        self.assertEqual(_evidence_coverage(q, "随便说说", ["随便说说"]), 0.0)


if __name__ == "__main__":
    unittest.main()
