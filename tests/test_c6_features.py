"""C6 三项功能的回归测试：可打印 HTML / 按 JD 定制权重 / 接口门禁（全部离线）。"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from coach import config
from coach.tools.export_html import export_html, md_to_html


class TestHtmlExport(unittest.TestCase):
    def test_md_to_html_handles_headings_bold_lists_tables(self):
        md = "# 标题\n\n## 小节\n\n- **要点一**\n- 要点二\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n"
        h = md_to_html(md)
        self.assertIn("<h1>标题</h1>", h)
        self.assertIn("<h2>小节</h2>", h)
        self.assertIn("<strong>要点一</strong>", h)
        self.assertIn("<table>", h)
        self.assertIn("<td>1</td>", h)

    def test_export_html_writes_printable_file(self):
        tmp = Path(tempfile.mkdtemp(prefix="coach_html_"))
        try:
            md = tmp / "report_x.md"
            md.write_text("# 报告\n\n- 综合得分：41%\n", encoding="utf-8")
            out = export_html(md, title="测试报告")
            self.assertTrue(out.exists())
            txt = out.read_text(encoding="utf-8")
            self.assertIn("@page", txt)                      # 打印样式必须带
            self.assertIn("打印", txt)                        # 说明怎么出 PDF
            self.assertIn("<h1>报告</h1>", txt)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestReqWeights(unittest.TestCase):
    """按 JD 定制权重：高权重的要求必须先挑题 —— 否则权重就是个没有效果的旋钮。"""

    JD = "- 熟悉 Python\n- 熟悉 RAG 检索与重排\n- 了解 Docker"

    def _plan(self, weights=None):
        os.environ["COACH_OFFLINE"] = "1"
        from coach.tools import load_bank, parse_jd, plan_questions
        prof = parse_jd(self.JD, llm=None)
        return plan_questions(prof, k=3, bank=load_bank(), variety=False, req_weights=weights)

    def tearDown(self):
        os.environ.pop("COACH_OFFLINE", None)

    def test_weight_changes_pick_order(self):
        base = self._plan(None)
        weighted = self._plan({"RAG": 5.0})
        # 加权重后，第一条被挑中的题应该属于被加权的那条要求
        self.assertEqual(weighted[0].target_req_id, "R2")
        self.assertNotEqual([q.qid for q in base], [q.qid for q in weighted])

    def test_no_weights_keeps_default(self):
        base = self._plan(None)
        self.assertEqual(base[0].target_req_id, "R1")        # 默认按 JD 顺序


class TestApiAuthGate(unittest.TestCase):
    """接口门禁：设了 COACH_API_TOKEN 后 /api/* 与 /report/* 必须带 Bearer Token。"""

    def setUp(self):
        os.environ["COACH_API_TOKEN"] = "test-token-123"
        os.environ["COACH_OFFLINE"] = "1"

    def tearDown(self):
        for k in ("COACH_API_TOKEN", "COACH_OFFLINE"):
            os.environ.pop(k, None)

    def test_401_without_token_200_health(self):
        from fastapi.testclient import TestClient
        from api import app
        c = TestClient(app)
        self.assertEqual(c.get("/health").status_code, 200)              # 探活不挡
        self.assertEqual(c.get("/api/session/none/state").status_code, 401)
        self.assertEqual(c.get("/api/session/none/state",
                               headers={"Authorization": "Bearer wrong"}).status_code, 401)
        # 带对 token：不是 401（具体状态由业务决定，这里是"门开了"）
        self.assertNotEqual(c.get("/api/session/none/state",
                                  headers={"Authorization": "Bearer test-token-123"}).status_code, 401)

    def test_open_when_unset(self):
        os.environ.pop("COACH_API_TOKEN", None)
        from fastapi.testclient import TestClient
        from api import app
        c = TestClient(app)
        self.assertNotEqual(c.get("/api/session/none/state").status_code, 401)


if __name__ == "__main__":
    unittest.main()
