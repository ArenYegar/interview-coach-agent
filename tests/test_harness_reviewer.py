"""harness 复核路径的防护单测（2026-09-20 加）。

背景：照文档里的命令跑 harness 时环境里没有 API Key，机器复核逐题 LLMError，
旧实现仍然把 results/reviewer_scores.csv 覆盖成空值，冲掉了上一次的有效复核结果。
本文件锁住三条不变量：
  1) 无 Key / 离线时复核直接标记 reviewer_unavailable，不发起任何调用；
  2) 复核一条配对都没产生时，绝不覆盖既有的 reviewer_scores.csv；
  3) 人评未填时是 awaiting_scores，不用估算值填充。
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coach import config
from coach.eval import harness
from coach.graph import CoachEngine

JD = "- 熟悉 Python 与 FastAPI\n- 掌握 RAG 全链路与评测\n- 了解 Agent 编排（工具调用/状态机）"
RESUME = "熟悉 Python、FastAPI，做过 RAG 混合检索与重排，写过 Agent 工具调用，具备评测意识"
SENTINEL = "run_id,qid,auto,reviewer,delta,note\ndemo,技术-01,0.5,0.5,0.0,原有有效结果\n"
RUN_ID = "unittest-reviewer"


class TestReviewerGuard(unittest.TestCase):
    """把 RESULTS_DIR 指向临时目录，避免污染仓库里的 results/。"""

    def setUp(self):
        eng = CoachEngine(run_id=RUN_ID, max_questions=2, use_llm=False, follow_up=False)
        eng.run(JD, RESUME, answers=["答1", "答2"])
        self.tmp = Path(tempfile.mkdtemp(prefix="coach-eval-"))
        src = Path(config.RESULTS_DIR) / ("run_%s.json" % RUN_ID)
        self.assertTrue(src.exists(), "离线跑一场应当落盘 run_<id>.json")
        shutil.copy2(src, self.tmp / src.name)
        (self.tmp / "reviewer_scores.csv").write_text(SENTINEL, encoding="utf-8")
        self._orig_results = config.RESULTS_DIR
        config.RESULTS_DIR = self.tmp

    def tearDown(self):
        config.RESULTS_DIR = self._orig_results
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _csv(self):
        return (self.tmp / "reviewer_scores.csv").read_text(encoding="utf-8")

    def test_no_api_key_marks_unavailable_without_calling_model(self):
        called = []

        class Boom:
            def probe(self):
                called.append("probe")
                return True, ""

            def structured(self, *a, **k):
                called.append("structured")
                raise AssertionError("无 Key 时不应该发起复核调用")

        with mock.patch.object(config, "api_key", lambda: None), \
                mock.patch("coach.llm.get_client", lambda *a, **k: Boom()):
            r = harness.machine_agreement(RUN_ID)

        self.assertEqual(r["status"], "reviewer_unavailable")
        self.assertIn("API Key", r["reason"])
        self.assertEqual(called, [], "无 Key 时不得发起任何模型调用")
        self.assertTrue(r["kept_previous"])
        self.assertEqual(self._csv(), SENTINEL, "无 Key 路径不得触碰 reviewer_scores.csv")

    def test_all_reviews_failing_keeps_previous_csv(self):
        class Boom:
            def probe(self):
                return True, ""

            def structured(self, *a, **k):
                raise RuntimeError("模拟模型不可用")

        with mock.patch.object(config, "api_key", lambda: "fake-key-for-test"), \
                mock.patch.object(config, "offline", lambda: False), \
                mock.patch("coach.llm.get_client", lambda *a, **k: Boom()):
            r = harness.machine_agreement(RUN_ID)

        self.assertEqual(r["status"], "reviewer_failed")
        self.assertGreaterEqual(r["attempted"], 1)
        self.assertIn("RuntimeError", r["reason"])
        self.assertTrue(r["kept_previous"])
        self.assertEqual(self._csv(), SENTINEL, "复核全失败时不得覆盖上一次的有效结果")

    def test_probe_failure_keeps_previous_csv(self):
        class Dead:
            def probe(self):
                return False, "网络不可达"

            def structured(self, *a, **k):
                raise AssertionError("probe 已失败，不应继续调用")

        with mock.patch.object(config, "api_key", lambda: "fake-key-for-test"), \
                mock.patch.object(config, "offline", lambda: False), \
                mock.patch("coach.llm.get_client", lambda *a, **k: Dead()):
            r = harness.machine_agreement(RUN_ID)

        self.assertEqual(r["status"], "reviewer_unavailable")
        self.assertEqual(r["reason"], "网络不可达")
        self.assertEqual(self._csv(), SENTINEL)

    def test_human_agreement_reports_awaiting_scores_not_fabricated(self):
        (self.tmp / "human_scores.csv").write_text(
            "run_id,qid,human_score,comment\n%s,技术-01,,\n" % RUN_ID, encoding="utf-8")
        r = harness.human_agreement(RUN_ID, human_csv=self.tmp / "human_scores.csv")
        self.assertEqual(r["status"], "awaiting_scores")
        self.assertIsNone(r["agreement"])
        self.assertEqual(r["rows_template"], 1)


class TestHarnessCli(unittest.TestCase):
    """CLI 与 run_demo.py 对齐：支持 --env-file，且位置参数用法不变。"""

    def test_env_file_is_accepted(self):
        tmp = Path(tempfile.mkdtemp(prefix="coach-envfile-"))
        env = tmp / ".env"
        env.write_text("COACH_TEST_MARKER=1\n", encoding="utf-8")
        os.environ.pop("COACH_TEST_MARKER", None)
        try:
            with mock.patch.object(harness, "evaluate",
                                   lambda rid, out_path=None: {"run_id": rid}), \
                    mock.patch("sys.stdout"), mock.patch("sys.stderr"):
                harness.main(["demo-x", "--env-file", str(env)])
            self.assertEqual(os.environ.get("COACH_TEST_MARKER"), "1",
                             "--env-file 应当把变量载入环境")
        finally:
            os.environ.pop("COACH_TEST_MARKER", None)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_positional_run_id_still_works(self):
        seen = {}

        def fake_eval(rid, out_path=None):
            seen["rid"] = rid
            seen["out"] = out_path
            return {"run_id": rid}

        with mock.patch.object(harness, "evaluate", fake_eval), \
                mock.patch("sys.stdout"):
            harness.main(["demo-positional"])
        self.assertEqual(seen["rid"], "demo-positional")

    def test_reviewer_flag_does_not_require_run_id(self):
        seen = {}

        def fake_review(rid):
            seen["rid"] = rid
            return {"status": "reviewer_unavailable"}

        with mock.patch.object(harness, "machine_agreement", fake_review), \
                mock.patch.object(harness, "_latest_run", lambda: "latest-x"), \
                mock.patch("sys.stdout"):
            harness.main(["--reviewer"])
        self.assertEqual(seen["rid"], "latest-x")


if __name__ == "__main__":
    unittest.main()
