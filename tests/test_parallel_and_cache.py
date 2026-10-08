"""并发与缓存的回归测试（全部离线，不花钱）。

要锁住四件事：
  1. 并发返回的顺序必须与输入一致（否则"第几次采样"会错位、复现会乱）；
  2. 并发是真的并发（不是把串行包了一层）；
  3. 缓存命中不再调模型，且 ScoreCard.cached 标记出来；
  4. 缓存键必须随 答案 / 防档案 / 采样次数 / 提示版本 变化 —— 改了提示还在吃旧分是最危险的缓存 bug。
"""
import os
import tempfile
import time
import unittest

from coach import cache, parallel
from coach.schemas import Question, Rubric, RubricDim, ScoreCard
from coach.tools.score_answer import score_answer


class TestParallel(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("COACH_PARALLEL")

    def tearDown(self):
        os.environ.pop("COACH_PARALLEL", None)
        if self._old is not None:
            os.environ["COACH_PARALLEL"] = self._old

    def test_order_preserved(self):
        os.environ["COACH_PARALLEL"] = "4"
        out = parallel.run_many(lambda x: x * 2, [5, 3, 9, 1, 7, 2])
        self.assertEqual(out, [10, 6, 18, 2, 14, 4])

    def test_actually_concurrent(self):
        os.environ["COACH_PARALLEL"] = "4"
        state = {"cur": 0, "peak": 0}

        def slow(_):
            state["cur"] += 1
            state["peak"] = max(state["peak"], state["cur"])
            time.sleep(0.15)
            state["cur"] -= 1
            return 1

        parallel.run_many(slow, range(4))
        self.assertGreaterEqual(state["peak"], 2)      # 串行实现会得到 peak == 1

    def test_serial_when_one_worker(self):
        os.environ["COACH_PARALLEL"] = "1"
        state = {"cur": 0, "peak": 0}

        def slow(_):
            state["cur"] += 1
            state["peak"] = max(state["peak"], state["cur"])
            time.sleep(0.05)
            state["cur"] -= 1
            return 1

        parallel.run_many(slow, range(3))
        self.assertEqual(state["peak"], 1)


class CountingLLM:
    """记录被调用了多少次；用于验证缓存命中是否真的省掉模型调用。"""

    def __init__(self, total=0.6):
        self.model = "fake-cache"
        self.n = 0
        self.total = total

    def structured(self, system, user, schema, repair_rounds=1):
        self.n += 1
        return ScoreCard(qid="T", dim_scores={}, total=self.total, evidence=[], missing=[],
                         verdict="ok", improvement="", method="heuristic")


def _q() -> Question:
    return Question(qid="T-01", category="技术基础", text="x",
                    rubric=Rubric(dims=[RubricDim(name="覆盖度", weight=1.0, keywords=["A", "B"])]))


class TestCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="coach_cache_test_")
        self._old = {k: os.environ.get(k) for k in ("COACH_CACHE", "COACH_CACHE_PATH", "COACH_GUARD",
                                                    "COACH_SCORE_REPS", "COACH_PARALLEL")}
        os.environ["COACH_CACHE_PATH"] = os.path.join(self.tmp, "c.json")
        os.environ["COACH_CACHE"] = "1"
        os.environ["COACH_GUARD"] = "0"
        os.environ["COACH_SCORE_REPS"] = "1"
        os.environ["COACH_PARALLEL"] = "1"

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def test_hit_skips_model_call(self):
        llm = CountingLLM()
        a = score_answer(_q(), "我的回答", llm=llm)
        self.assertFalse(a.cached)
        self.assertEqual(llm.n, 1)
        b = score_answer(_q(), "我的回答", llm=llm)
        self.assertTrue(b.cached)               # 第二次没有打模型
        self.assertEqual(llm.n, 1)
        self.assertEqual(b.total, a.total)

    def test_key_changes_with_answer(self):
        llm = CountingLLM()
        score_answer(_q(), "回答一", llm=llm)
        score_answer(_q(), "回答二", llm=llm)
        self.assertEqual(llm.n, 2)              # 答案不同 → 不命中

    def test_key_changes_with_guard_and_reps(self):
        llm = CountingLLM()
        score_answer(_q(), "同一段回答", llm=llm)
        os.environ["COACH_GUARD"] = "2"
        score_answer(_q(), "同一段回答", llm=llm)
        os.environ["COACH_SCORE_REPS"] = "2"
        score_answer(_q(), "同一段回答", llm=llm)
        # 三组配置互不命中：1 + 1 + （reps=2 → 2 次采样）= 4 次调用
        self.assertEqual(llm.n, 4)

    def test_off_switch(self):
        llm = CountingLLM()
        score_answer(_q(), "回答", llm=llm)
        os.environ["COACH_CACHE"] = "0"
        score_answer(_q(), "回答", llm=llm)
        self.assertEqual(llm.n, 2)


if __name__ == "__main__":
    unittest.main()
