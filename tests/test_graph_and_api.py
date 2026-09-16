"""LangGraph 编排与 FastAPI 端点的冒烟测试（缺依赖时跳过）。"""
import unittest


class TestLangGraph(unittest.TestCase):
    def test_langgraph_matches_engine_behaviour(self):
        from coach.graph.langgraph_flow import langgraph_available, run_with_langgraph
        ok, why = langgraph_available()
        if not ok:
            self.skipTest(f"langgraph 不可用: {why}")
        report, run_id = run_with_langgraph(
            "- 熟悉 Python 与 RAG 评测\n- 了解 Agent 工具调用",
            "熟悉 Python，做过 RAG 检索与评测",
            answers=["回答一", "回答二"], run_id="unittest-langgraph", max_questions=2)
        self.assertEqual(len([t for t in report.turns if not t.follow_up]), 2)
        self.assertTrue(report.report_path)


class TestApi(unittest.TestCase):
    def test_health_plan_score(self):
        try:
            from fastapi.testclient import TestClient
        except Exception as e:
            self.skipTest(f"TestClient 不可用: {e}")
        from api import app
        c = TestClient(app)
        h = c.get("/health").json()
        self.assertTrue(h["ok"])
        self.assertEqual(h["bank_size"], 100)
        p = c.post("/plan", json={"jd_text": "- 熟悉 Python\n- 掌握 RAG", "resume_text": "Python RAG", "k": 2}).json()
        self.assertEqual(len(p["questions"]), 2)
        qid = p["questions"][0]["qid"]
        s = c.post("/score", json={"qid": qid, "answer": "我的做法是……" * 10}).json()
        self.assertIn("total", s)
        self.assertEqual(c.get("/report/nonexistent-run").status_code, 404)

    def test_stream_sse(self):
        from fastapi.testclient import TestClient
        from api import app
        c = TestClient(app)
        with c.stream("GET", "/stream", params={"jd": "- 熟悉 Python", "resume": "Python",
                                                "answers": "回答A||回答B", "k": 2}) as r:
            body = "".join(chunk for chunk in r.iter_text())
        self.assertIn("event: plan", body)
        self.assertIn("event: done", body)


if __name__ == "__main__":
    unittest.main()
