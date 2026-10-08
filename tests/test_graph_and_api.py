"""LangGraph 编排与 FastAPI 端点的冒烟测试（缺依赖时跳过）。"""
import unittest


class TestLangGraph(unittest.TestCase):
    RUNS = ("unittest-parity-a", "unittest-parity-b")

    def tearDown(self):
        import shutil
        from pathlib import Path
        from coach import config
        for r in self.RUNS:
            shutil.rmtree(Path(config.RUNS_DIR) / r, ignore_errors=True)
            for p in Path(config.RESULTS_DIR).glob(f"*{r}*"):
                p.unlink()

    def test_langgraph_and_engine_give_identical_turns(self):
        """两版必须逐轮给出同样的 qid 与分数（含追问层数）—— 这是"共用业务函数"的证明。"""
        import os
        from coach.graph import CoachEngine
        from coach.graph.langgraph_flow import langgraph_available, run_with_langgraph
        ok, why = langgraph_available()
        if not ok:
            self.skipTest(f"langgraph 不可用: {why}")
        jd = "- 熟悉 Python 与 RAG 评测\n- 了解 Agent 工具调用"
        rs = "熟悉 Python，做过 RAG 检索与评测"
        # 故意给"不会"这种弱回答，逼出追问层
        ans = ["不会", "不清楚", "没做过", "不了解"] * 3
        old = os.environ.get("COACH_OFFLINE")
        os.environ["COACH_OFFLINE"] = "1"          # 离线确定性：两版必须逐轮一致
        try:
            rep = CoachEngine(run_id=self.RUNS[0], max_questions=2).run(jd, rs, answers=ans)
            lg, _ = run_with_langgraph(jd, rs, ans, run_id=self.RUNS[1], max_questions=2)
        finally:
            if old is None:
                os.environ.pop("COACH_OFFLINE", None)
            else:
                os.environ["COACH_OFFLINE"] = old
        self.assertEqual([(t.qid, t.scorecard.total) for t in rep.turns],
                         [(t.qid, t.scorecard.total) for t in lg.turns])
        self.assertGreaterEqual(sum(1 for t in rep.turns if t.follow_up), 1)   # 追问确实被触发了

    def test_langgraph_matches_engine_behaviour(self):
        from coach.graph.langgraph_flow import langgraph_available, run_with_langgraph
        ok, why = langgraph_available()
        if not ok:
            self.skipTest(f"langgraph 不可用: {why}")
        report, run_id = run_with_langgraph(
            "- 熟悉 Python 与 RAG 评测\n- 了解 Agent 工具调用",
            "熟悉 Python，做过 RAG 检索与评测",
            answers=["回答一", "回答二"], run_id="unittest-langgraph", max_questions=2)
        # 注意语义：追问也会消耗一条回答（与 engine.run 完全一致），
        # 所以 2 条回答可能只够跑完「1 道主问题 + 1 层追问」；两版的逐轮一致性由
        # test_langgraph_and_engine_give_identical_turns 锁住，这里只做冒烟。
        self.assertGreaterEqual(len([t for t in report.turns if not t.follow_up]), 1)
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
        self.assertGreaterEqual(h["bank_size"], 100)   # 题库会扩充，别把条数写死
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
