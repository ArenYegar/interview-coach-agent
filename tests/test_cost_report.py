"""成本与延迟账的回归测试。

四条线：
  1. 每步 span 记录的是 token 增量，且汇总正确；
  2. 没有计量器（离线）时不报错，记 0 而不是编数；
  3. 旧格式 trace（没有 token 字段）+ 坏行，仍能回放 —— 加字段不能把历史产物读坏；
  4. 离线真跑一场：报告带 cost，并产出 results/cost_<run_id>.json。
"""
import json
import os
import shutil
import unittest
from pathlib import Path

from coach import config
from coach.eval import cost_report
from coach.trace import Tracer, load_trace, summarize_trace


class FakeMeter:
    """假装成 LLM 客户端：只实现 usage_snapshot()（真实/Mock 客户端都有这个方法）。"""

    def __init__(self, **kw):
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.model = "fake-model"
        self.usage.update(kw)

    def usage_snapshot(self):
        return dict(self.usage)


class TestUsageAccounting(unittest.TestCase):
    RUN = "unittest-cost"

    def tearDown(self):
        shutil.rmtree(Path(config.RUNS_DIR) / self.RUN, ignore_errors=True)
        for p in Path(config.RESULTS_DIR).glob(f"*{self.RUN}*"):
            p.unlink()

    def test_span_records_token_delta(self):
        m = FakeMeter()                       # 从 0 开始：计量器只在 span 内部变化（和真实调用一致）
        tr = Tracer(self.RUN, meter=m)
        with tr.span("tool.score_answer", "q1"):
            m.usage = {"calls": 1, "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        with tr.span("tool.score_answer", "q2"):
            m.usage = {"calls": 2, "prompt_tokens": 300, "completion_tokens": 120, "total_tokens": 420}
        self.assertEqual([e.total_tokens for e in load_trace(self.RUN)], [150, 270])
        s = tr.summary()
        self.assertEqual(s["total_tokens"], 420)
        self.assertEqual(s["llm_calls"], 2)
        self.assertGreater(s["wall_ms"], 0)

    def test_no_meter_records_zero(self):
        tr = Tracer(self.RUN)
        with tr.span("tool.parse_jd"):
            pass
        self.assertEqual(load_trace(self.RUN)[0].total_tokens, 0)
        self.assertEqual(tr.summary()["total_tokens"], 0)

    def test_old_trace_and_broken_line_still_load(self):
        d = Path(config.RUNS_DIR) / self.RUN
        d.mkdir(parents=True, exist_ok=True)
        (d / "trace.jsonl").write_text(
            json.dumps({"ts": "2026-09-20T10:00:00+08:00", "run_id": self.RUN, "step": "tool.parse_jd",
                        "ok": True, "ms": 12.0, "model": "old", "detail": "d", "error": ""}) + "\n"
            + "{ 坏行 \n", encoding="utf-8")
        evs = load_trace(self.RUN)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].total_tokens, 0)

    def test_summarize_trace_aggregates(self):
        m = FakeMeter()
        tr = Tracer(self.RUN, meter=m)
        with tr.span("tool.parse_jd"):
            m.usage = {"calls": 1, "prompt_tokens": 150, "completion_tokens": 50, "total_tokens": 200}
        with tr.span("tool.score_answer", "q1"):
            m.usage = {"calls": 2, "prompt_tokens": 900, "completion_tokens": 300, "total_tokens": 1200}
        agg = summarize_trace(self.RUN)
        self.assertEqual(agg["totals"]["total_tokens"], 1200)
        self.assertEqual(agg["totals"]["events"], 2)
        self.assertIn("tool.score_answer", agg["steps"])
        self.assertEqual(cost_report.classify("tool.score_answer"), "④ 逐题评分与追问（LLM 主战场）")

    def test_offline_run_writes_cost_json(self):
        os.environ["COACH_OFFLINE"] = "1"
        try:
            from coach.graph import CoachEngine
            jd = (Path(config.ROOT) / "data" / "jd_sample.md").read_text(encoding="utf-8")
            rs = (Path(config.ROOT) / "data" / "resume_sample.md").read_text(encoding="utf-8")
            eng = CoachEngine(run_id=self.RUN, max_questions=2, follow_up=False)
            rep = eng.run(jd, rs, answers=["a", "b"])
            self.assertEqual(rep.cost.get("total_tokens"), 0)      # mock 不产生真实 token，不编数
            self.assertGreater(rep.cost.get("wall_ms", 0), 0)
            p = cost_report.write(self.RUN)
            self.assertTrue(p.exists())
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data["turns"], 2)
            self.assertIn("phases", data)
        finally:
            os.environ.pop("COACH_OFFLINE", None)


class TestTracerAppend(unittest.TestCase):
    """恢复会话时 trace 必须追加而不是清空（Web 每个 HTTP 请求新建引擎，清空=回放与成本账残缺）。"""

    RUN = "unittest-append"

    def tearDown(self):
        shutil.rmtree(Path(config.RUNS_DIR) / self.RUN, ignore_errors=True)

    def test_append_keeps_existing_events(self):
        Tracer(self.RUN)                                   # 新建：清空
        with Tracer(self.RUN + "-tmp").span("x"):
            pass
        tr = Tracer(self.RUN)
        with tr.span("tool.parse_jd"):
            pass
        tr2 = Tracer(self.RUN, append=True)                # 恢复：追加
        with tr2.span("tool.score_answer", "q1"):
            pass
        self.assertEqual([e.step for e in load_trace(self.RUN)], ["tool.parse_jd", "tool.score_answer"])
        Tracer(self.RUN)                                   # 默认仍清空（CLI 复用 run_id 不该混旧事件）
        self.assertEqual(load_trace(self.RUN), [])


class TestWebSessionCost(unittest.TestCase):
    """Web 路径必须也从 trace 文件算成本：每个 HTTP 请求新建引擎，进程内计数器跨不了请求。"""

    RUN = "unittest-webcost"

    def tearDown(self):
        shutil.rmtree(Path(config.RUNS_DIR) / self.RUN, ignore_errors=True)
        for p in Path(config.RESULTS_DIR).glob(f"*{self.RUN}*"):
            p.unlink()

    def test_web_session_report_has_trace_based_cost(self):
        os.environ["COACH_OFFLINE"] = "1"
        try:
            from coach.session import WebSession
            jd = (Path(config.ROOT) / "data" / "jd_sample.md").read_text(encoding="utf-8")
            rs = (Path(config.ROOT) / "data" / "resume_sample.md").read_text(encoding="utf-8")
            s = WebSession.start(rs, jd, k=1, run_id=self.RUN, use_llm=False,
                                 variety=False, avoid_repeat=False)
            ans = "我的回答：先说结论，再讲做法与取舍，最后给量化结果。" * 3
            s.submit(ans)
            s.finish()
            self.assertIsNotNone(s.report)
            cost = s.report.cost
            self.assertEqual(cost.get("source"), "trace-file")
            self.assertGreater(cost.get("events", 0), 0)      # 离线也有事件，不能是空
            self.assertIn("llm_calls", cost)
            # 落盘的报告 JSON 里也要带
            data = json.loads((Path(config.RESULTS_DIR) / f"run_{self.RUN}.json").read_text(encoding="utf-8"))
            self.assertEqual(data["cost"].get("source"), "trace-file")
        finally:
            os.environ.pop("COACH_OFFLINE", None)


class TestMedianReps(unittest.TestCase):
    """重复采样取中位数：单次打分是随机测量，这里锁住聚合逻辑本身。"""

    def setUp(self):
        self._old = {k: os.environ.get(k) for k in ("COACH_SCORE_REPS", "COACH_GUARD", "COACH_PARALLEL")}
        os.environ["COACH_GUARD"] = "0"      # 先隔离掉防护，只测中位数聚合
        os.environ["COACH_SCORE_REPS"] = "3"
        os.environ["COACH_PARALLEL"] = "1"   # 本节测聚合逻辑，固定串行才谈得上"第几次采样"

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def test_median_of_three(self):
        from coach.schemas import Question, Rubric, RubricDim, ScoreCard
        from coach.tools.score_answer import score_answer

        class SeqLLM:
            def __init__(self, vals):
                self.vals, self.i = list(vals), 0

            def structured(self, s, u, schema, repair_rounds=1):
                v = self.vals[min(self.i, len(self.vals) - 1)]
                self.i += 1
                return ScoreCard(qid="T-01", dim_scores={}, total=v, evidence=[], missing=[],
                                 verdict="ok", improvement="", method="heuristic")

        q = Question(qid="T-01", category="技术基础", text="说说你的做法",
                     rubric=Rubric(dims=[RubricDim(name="覆盖度", weight=1.0, keywords=["A", "B"])]))
        llm = SeqLLM([0.2, 0.9, 0.4])
        card = score_answer(q, "我的回答" * 30, llm=llm)
        self.assertEqual(llm.i, 3)                       # 确实打了 3 次
        self.assertEqual(card.reps, 3)
        self.assertEqual(card.sample_totals, [0.2, 0.9, 0.4])
        self.assertEqual(card.total, 0.4)                # 中位数而不是某一次
        self.assertEqual(card.verdict, "ok")             # 中位数 0.4 → ok（>=0.4 的边界）

    def test_all_calls_fail_falls_back_to_heuristic(self):
        from coach.schemas import Question, Rubric, RubricDim
        from coach.tools.score_answer import score_answer

        class Boom:
            def structured(self, *a, **k):
                raise RuntimeError("boom")

        q = Question(qid="T-01", category="技术基础", text="x",
                     rubric=Rubric(dims=[RubricDim(name="覆盖度", weight=1.0, keywords=["A"])]))
        card = score_answer(q, "答" * 80, llm=Boom())
        self.assertEqual(card.method, "heuristic")
        self.assertEqual(card.reps, 1)


if __name__ == "__main__":
    unittest.main()
