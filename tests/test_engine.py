"""编排层单测：离线（mock）也要能跑完一整场，并且产物落盘。"""
import unittest
from pathlib import Path

from coach import config
from coach.graph import CoachEngine

JD = "- 熟悉 Python 与 FastAPI\n- 掌握 RAG 全链路与评测\n- 了解 Agent 编排（工具调用/状态机）"
RESUME = "熟悉 Python、FastAPI，做过 RAG 混合检索与重排，写过 Agent 工具调用，具备评测意识"


class TestEngineOffline(unittest.TestCase):
    def test_run_completes_and_persists(self):
        eng = CoachEngine(run_id="unittest-engine", max_questions=3, use_llm=False, follow_up=False)
        report = eng.run(JD, RESUME, answers=["答1", "答2", "答3"])
        self.assertEqual(len([t for t in report.turns if not t.follow_up]), 3)
        self.assertTrue(0.0 <= report.overall <= 1.0)
        self.assertTrue(Path(report.report_path).exists())
        self.assertTrue((Path(config.RESULTS_DIR) / "run_unittest-engine.json").exists())
        trace = Path(config.RUNS_DIR) / "unittest-engine" / "trace.jsonl"
        self.assertTrue(trace.exists())
        self.assertGreaterEqual(len(trace.read_text(encoding="utf-8").splitlines()), 5)

    def test_followup_consumes_next_answer(self):
        """得分低时触发追问，追问会消耗回答列表里的下一条（语义明确、可预期）。"""
        eng = CoachEngine(run_id="unittest-engine-followup", max_questions=3, use_llm=False)
        report = eng.run(JD, RESUME, answers=["答1", "答2", "答3", "答4"])
        self.assertTrue(any(t.follow_up for t in report.turns))
        self.assertGreaterEqual(len(report.turns), 3)

    def test_missing_answers_stop_gracefully(self):
        eng = CoachEngine(run_id="unittest-engine-short", max_questions=3, use_llm=False)
        report = eng.run(JD, RESUME, answers=["只有一条回答"])
        self.assertEqual(len([t for t in report.turns if not t.follow_up]), 1)

    def test_state_transitions_recorded(self):
        eng = CoachEngine(run_id="unittest-engine-fsm", max_questions=2, use_llm=False)
        eng.run(JD, RESUME, answers=["a", "b"])
        self.assertTrue(any(x.startswith("plan.") for x in eng.transitions))
        self.assertTrue(any(x.startswith("done(") for x in eng.transitions))


if __name__ == "__main__":
    unittest.main()
