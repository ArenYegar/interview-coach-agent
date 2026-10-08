"""HITL（人工确认闸门）的回归测试（离线）。

锁住三件事：
  1. 开了闸门时，本场不写库 —— 弱点画像不能未经确认就进跨场次记忆；
  2. 提议会落盘（跨进程可恢复），不是内存里的挂起；
  3. **驳回真的生效**：只批准第一条时，其它要点不得出现在库里。
"""
import json
import os
import shutil
import unittest
from pathlib import Path

from coach import config, review


class TestHitl(unittest.TestCase):
    RUN = "unittest-hitl"

    def setUp(self):
        self.tmp = __import__("tempfile").mkdtemp(prefix="coach_hitl_")
        self._old = {k: os.environ.get(k) for k in ("COACH_OFFLINE", "COACH_HITL", "COACH_CACHE", "COACH_PARALLEL")}
        os.environ["COACH_OFFLINE"] = "1"
        os.environ["COACH_HITL"] = "1"
        os.environ["COACH_CACHE"] = "0"

    def tearDown(self):
        from coach.tools.track_weakness import _conn
        with _conn() as c:
            c.execute("DELETE FROM answers WHERE run_id=?", (self.RUN,))
            c.execute("DELETE FROM turns WHERE run_id=?", (self.RUN,))
        shutil.rmtree(Path(config.RUNS_DIR) / self.RUN, ignore_errors=True)
        for p in Path(config.RESULTS_DIR).glob(f"*{self.RUN}*"):
            p.unlink()
        for k, v in self._old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def _count(self, run_id):
        from coach.tools.track_weakness import _conn
        with _conn() as c:
            return c.execute("SELECT COUNT(*) FROM answers WHERE run_id=?", (run_id,)).fetchone()[0]

    def test_gate_then_approve(self):
        from coach.graph import CoachEngine
        jd = (Path(config.ROOT) / "data" / "jd_sample.md").read_text(encoding="utf-8")
        rs = (Path(config.ROOT) / "data" / "resume_sample.md").read_text(encoding="utf-8")
        eng = CoachEngine(run_id=self.RUN, max_questions=1, follow_up=False)
        rep = eng.run(jd, rs, answers=["不会"])
        # 1) 报告标出待确认，且库里没有这一场
        self.assertTrue(rep.pending_review)
        self.assertIn("待人工确认", rep.review_note)
        self.assertEqual(self._count(self.RUN), 0)
        # 2) 提议落盘可恢复
        prop = review.load_proposal(self.RUN)
        self.assertIsNotNone(prop)
        self.assertFalse(prop["applied"])
        if not prop["proposals"]:
            self.skipTest("本场没有产出弱点提议（换个回答再试）")
        first = prop["proposals"][0]["tag"]
        others = [p["tag"] for p in prop["proposals"][1:]]
        # 3) 只批准第一条 → 只有它进库，其它被驳回的要点不得出现
        res = eng.resume_review([first])
        self.assertGreaterEqual(res["committed_turns"], 1)
        self.assertEqual(self._count(self.RUN), res["committed_turns"])
        from coach.tools.track_weakness import _conn
        with _conn() as c:
            rows = c.execute("SELECT missing FROM answers WHERE run_id=?", (self.RUN,)).fetchall()
        seen = set()
        for (mj,) in rows:
            seen |= set(json.loads(mj or "[]"))
        self.assertIn(first, seen)
        for tag in others:
            self.assertNotIn(tag, seen)
        self.assertTrue(review.load_proposal(self.RUN)["applied"])

    def test_off_switch_writes_immediately(self):
        """关掉 HITL 时必须照旧直接写库（默认行为不能被改坏）。"""
        os.environ["COACH_HITL"] = "0"
        from coach.graph import CoachEngine
        jd = (Path(config.ROOT) / "data" / "jd_sample.md").read_text(encoding="utf-8")
        rs = (Path(config.ROOT) / "data" / "resume_sample.md").read_text(encoding="utf-8")
        rep = CoachEngine(run_id=self.RUN, max_questions=1, follow_up=False).run(jd, rs, answers=["不会"])
        self.assertFalse(rep.pending_review)
        self.assertGreater(self._count(self.RUN), 0)


if __name__ == "__main__":
    unittest.main()
