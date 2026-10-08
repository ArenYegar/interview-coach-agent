"""跨场次画像的时间衰减/置信度，以及效果归因的回归测试（离线）。"""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from coach import config


class TestCrossSessionDecay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="coach_db_")
        self._old_db = config.DB_PATH
        config.DB_PATH = Path(self.tmp) / "t.db"

    def tearDown(self):
        config.DB_PATH = self._old_db
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ins(self, run_id, ts, missing):
        from coach.tools.track_weakness import _conn
        with _conn() as c:
            c.execute("INSERT INTO answers VALUES(?,?,?,?,?,?,?,?)",
                      (run_id, ts, "q1", "技术基础", "题", 0.2, json.dumps(missing, ensure_ascii=False), "weak"))

    def test_recent_outweighs_old(self):
        from coach.tools.track_weakness import cross_session_tags
        now = datetime.now(timezone.utc).astimezone()
        old_ts = (now - timedelta(days=180)).isoformat(timespec="seconds")
        new_ts = now.isoformat(timespec="seconds")
        self._ins("run-old", old_ts, ["陈年弱点"])
        self._ins("run-a", new_ts, ["新弱点", "新弱点"])
        self._ins("run-b", new_ts, ["新弱点"])
        tags = cross_session_tags(half_life_days=21.0)
        first = tags[0]
        self.assertEqual(first.tag, "新弱点")
        self.assertEqual(first.runs, 2)                 # 置信度：出现在 2 场
        self.assertGreater(first.decayed, 2.0)
        self.assertEqual(first.severity, "high")
        old = next(t for t in tags if t.tag == "陈年弱点")
        self.assertLess(old.decayed, 0.1)               # 半年前的一次 → 几乎归零
        self.assertEqual(old.runs, 1)

    def test_half_life_changes_ranking(self):
        from coach.tools.track_weakness import cross_session_tags
        now = datetime.now(timezone.utc).astimezone()
        self._ins("run-old", (now - timedelta(days=90)).isoformat(timespec="seconds"), ["旧"])
        self._ins("run-new", now.isoformat(timespec="seconds"), ["新"])
        short = {t.tag: t.decayed for t in cross_session_tags(half_life_days=7.0)}
        long_ = {t.tag: t.decayed for t in cross_session_tags(half_life_days=3650.0)}
        # 同样的"90 天前出现一次"，半衰期越短衰减越狠
        self.assertLess(short["旧"], long_["旧"])
        self.assertEqual(long_["新"], 1.0)


class TestAttribution(unittest.TestCase):
    RUN = "unittest-attr"

    def tearDown(self):
        shutil.rmtree(Path(config.RUNS_DIR) / self.RUN, ignore_errors=True)
        for p in Path(config.RESULTS_DIR).glob(f"*{self.RUN}*"):
            p.unlink()

    def test_split_and_tool_costs(self):
        from coach.eval.attribute import attribute
        d = Path(config.RUNS_DIR) / self.RUN
        d.mkdir(parents=True, exist_ok=True)
        (d / "trace.jsonl").write_text("\n".join([
            json.dumps({"ts": "2026-10-08T10:00:00+08:00", "run_id": self.RUN, "step": "tool.parse_jd",
                        "ok": True, "ms": 100.0, "model": "m", "detail": "", "error": "",
                        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "calls": 1}),
            json.dumps({"ts": "2026-10-08T10:00:02+08:00", "run_id": self.RUN, "step": "tool.score_answer",
                        "ok": False, "ms": 900.0, "model": "m", "detail": "q1", "error": "boom",
                        "prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30, "calls": 1}),
        ]), encoding="utf-8")
        (Path(config.RESULTS_DIR) / f"run_{self.RUN}.json").write_text(json.dumps({
            "run_id": self.RUN, "overall": 0.5,
            "turns": [{"qid": "技术-01", "target_req_id": "R1", "follow_up": False,
                       "scorecard": {"total": 0.6}},
                      {"qid": "技术-01-F1", "target_req_id": "R1", "follow_up": True,
                       "scorecard": {"total": 0.4}}],
            "uncovered": ["R2：背到岗要求"],
        }, ensure_ascii=False), encoding="utf-8")
        r = attribute(self.RUN)
        self.assertEqual(r["coverage"], 0.5)            # 1 覆盖 /（1 覆盖 + 1 未覆盖）
        self.assertEqual(r["answer_mean"], 0.5)
        self.assertTrue(any(t["tool"] == "tool.score_answer" and t["fail"] == 1 for t in r["tools"]))
        self.assertIn("题库", r["reading"])


if __name__ == "__main__":
    unittest.main()
