"""Web 会话（步进式一问一答）单测（2026-09-20 加）。

关键不变量：
  1) WebSession 的逐题流程必须与 engine.run() 的整场流程等价（同样的题、同样的追问层数、同样的分数序列）——
     否则"网页里练"和"命令行练"会给出两套结论。
  2) 状态落盘后可恢复（浏览器刷新接着答）。
  3) 空回答、重复结束等边界要有明确报错，且不产生半截状态。
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coach import config
from coach.graph import CoachEngine
from coach.ingest import IngestError, normalize, text_from_bytes
from coach.session import WebSession, state_path

JD = "- 熟悉 Python 与 FastAPI\n- 掌握 RAG 全链路与评测\n- 了解 Agent 编排（工具调用/状态机）"
RESUME = "熟悉 Python、FastAPI，做过 RAG 混合检索与重排，写过 Agent 工具调用"
ANSWERS = ["回答一：我在服务里做了 X、取舍是 Y、结果是 Z", "太短", "回答三：讲清做法与量化结果",
           "补答：具体做法 A、B，代价 C", "回答五：完整覆盖", "回答六"]


class TestWebSession(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coach-web-"))
        self._orig_runs = config.RUNS_DIR
        self._orig_results = config.RESULTS_DIR
        config.RUNS_DIR = self.tmp / "runs"
        config.RESULTS_DIR = self.tmp / "results"
        config.ensure_dirs()

    def tearDown(self):
        config.RUNS_DIR = self._orig_runs
        config.RESULTS_DIR = self._orig_results
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stepwise_matches_engine_run(self):
        """离线口径下，网页步进流程与 engine.run() 整场流程必须给出同一串分数。"""
        eng = CoachEngine(run_id="cmp-engine", max_questions=3, use_llm=False)
        report = eng.run(JD, RESUME, answers=list(ANSWERS))

        # 显式关闭"选题多样性"，否则两侧抽到的题不同，这条一致性断言就不成立
        s = WebSession.start(RESUME, JD, k=3, run_id="cmp-web", use_llm=False,
                             variety=False, avoid_repeat=False)
        seq = []
        # engine.run() 在"回答用尽"时会中断整场，这里必须用同一个停止条件才可比
        while s.next_question() is not None and len(seq) < len(ANSWERS):
            res = s.submit(ANSWERS[len(seq)])
            seq.append(round(res["scorecard"]["total"], 4))
        s.finish()

        self.assertEqual(seq, [round(t.scorecard.total, 4) for t in report.turns],
                         "步进流程与整场流程的分数序列必须一致")
        self.assertEqual(len(s.turns), len(report.turns), "轮次数（含追问）必须一致")

    def test_state_survives_reload(self):
        s = WebSession.start(RESUME, JD, k=3, run_id="reload-web", use_llm=False)
        first_q = s.next_question()["qid"]
        s.submit("我的做法是 A，取舍是 B，结果是 C（带数字）")

        again = WebSession.load("reload-web", use_llm=False)
        self.assertEqual(len(again.turns), 1)
        self.assertEqual(again.next_question()["qid"], s.next_question()["qid"])
        self.assertEqual(again.turns[0].qid, first_q)
        self.assertTrue(state_path("reload-web").exists())

    def test_empty_answer_rejected_without_side_effect(self):
        s = WebSession.start(RESUME, JD, k=2, run_id="empty-web", use_llm=False)
        with self.assertRaises(ValueError):
            s.submit("   ")
        self.assertEqual(len(s.turns), 0, "空回答不得写入任何轮次")

    def test_finish_writes_report_and_is_idempotent(self):
        s = WebSession.start(RESUME, JD, k=2, run_id="finish-web", use_llm=False)
        while s.next_question() is not None:
            s.submit("回答：做法 A、取舍 B、结果 C（量化）")
        r1 = s.finish()
        r2 = s.finish()
        self.assertEqual(r1["run_id"], r2["run_id"])
        self.assertTrue(Path(r1["report_path"]).exists())
        self.assertIn("面试复盘报告", r1["markdown"])
        self.assertTrue(r1["turns"])
        self.assertIsInstance(r1["overall"], float)

    def test_submit_after_finish_rejected(self):
        s = WebSession.start(RESUME, JD, k=1, run_id="after-web", use_llm=False)
        s.submit("回答：做法 A、取舍 B、结果 C")
        s.finish()
        with self.assertRaises(ValueError):
            s.submit("再答一次")

    def test_followup_qid_has_no_duplicated_suffix(self):
        """追问题号必须是 技术-01-F1 / -F2，不能叠加成 -F1-F2（前端与报告都按这个口径读）。"""
        s = WebSession.start(RESUME, JD, k=1, use_llm=False)
        seen = []
        for _ in range(3):
            q = s.next_question()
            if q is None:
                break
            seen.append(q["qid"])
            s.submit("太短")
        self.assertEqual(seen[0], s.plan.questions[0].qid)
        self.assertEqual(seen[1], f"{seen[0]}-F1")
        self.assertEqual(seen[2], f"{seen[0]}-F2")
        self.assertNotIn("-F1-F", "".join(seen))


class TestIngest(unittest.TestCase):
    def test_text_format_passthrough(self):
        self.assertIn("熟悉 Python", text_from_bytes("a.md", "熟悉 Python\n\n\n\nRAG".encode("utf-8")))

    def test_gbk_tolerated(self):
        raw = "熟悉 Python 与 FastAPI".encode("gb18030")
        self.assertIn("FastAPI", text_from_bytes("a.txt", raw))

    def test_unsupported_suffix_rejected(self):
        with self.assertRaises(IngestError):
            text_from_bytes("a.xlsx", b"data")

    def test_empty_and_oversize_rejected(self):
        with self.assertRaises(IngestError):
            text_from_bytes("a.txt", b"")
        with self.assertRaises(IngestError):
            text_from_bytes("a.txt", b"x" * (6 * 1024 * 1024))

    def test_normalize_collapses_blank_lines(self):
        self.assertEqual(normalize("a\n\n\n\nb\n"), "a\n\nb")

# 手搓的最小 PDF（ASCII 文本；缺 xref 表，pypdf 会拒绝、pdfminer 能读），
# 正好用来验证"多后端依次回退"这条逻辑是真的在工作。
MINIMAL_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 120]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 58>>stream
BT /F1 14 Tf 20 60 Td (Resume Fixture RAG Python) Tj ET
endstream
endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
trailer<</Root 1 0 R/Size 6>>
%%EOF
"""


class TestPdfIngest(unittest.TestCase):
    """PDF 解析必须多后端回退：本机 llm 环境没有 PyMuPDF，但有 pypdf/pdfplumber/pdfminer。"""

    def test_pdf_extracted_via_fallback_chain(self):
        text = text_from_bytes("resume.pdf", MINIMAL_PDF)
        self.assertIn("Resume Fixture RAG Python", text)

    def test_short_pdf_text_gets_hint(self):
        text = text_from_bytes("resume.pdf", MINIMAL_PDF)      # 只有 25 个字符 → 应附提示
        self.assertIn("图片版简历", text)

    def test_unreadable_pdf_lists_backends_and_suggests_docx(self):
        with self.assertRaises(IngestError) as ctx:
            text_from_bytes("resume.pdf", b"%PDF-1.4\nnot a real pdf at all\n%%EOF")
        msg = str(ctx.exception)
        self.assertIn("各解析后端结果", msg)
        self.assertIn(".docx", msg)

    def test_chain_continues_when_first_backend_fails(self):
        """把 pypdf 打成必失败，结果仍应拿到文本（证明回退生效、不是靠单一后端）。"""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "pypdf":
                raise ImportError("模拟未安装 pypdf")
            return real_import(name, *a, **k)

        with mock.patch.object(builtins, "__import__", fake_import):
            text = text_from_bytes("resume.pdf", MINIMAL_PDF)
        self.assertIn("Resume Fixture", text)


class TestWebApi(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coach-api-web-"))
        self._orig_runs = config.RUNS_DIR
        self._orig_results = config.RESULTS_DIR
        config.RUNS_DIR = self.tmp / "runs"
        config.RESULTS_DIR = self.tmp / "results"
        config.ensure_dirs()

    def tearDown(self):
        config.RUNS_DIR = self._orig_runs
        config.RESULTS_DIR = self._orig_results
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _client(self):
        from fastapi.testclient import TestClient
        from api import app
        return TestClient(app)

    def test_upload_then_answer_then_report(self):
        try:
            c = self._client()
        except Exception as e:
            self.skipTest(f"TestClient 不可用: {e}")
        r = c.post("/api/session",
                   files={"resume": ("resume.md", RESUME.encode("utf-8"), "text/markdown")},
                   data={"jd_text": JD, "k": "2"})
        self.assertEqual(r.status_code, 200, r.text)
        s = r.json()
        self.assertTrue(s["run_id"])
        self.assertEqual(s["total_questions"], 2)
        self.assertIsNotNone(s["next"])

        while True:
            nxt = s["next"]
            if nxt is None:
                break
            rr = c.post(f"/api/session/{s['run_id']}/answer",
                        json={"answer": "回答：做法 A、取舍 B、结果 C（带数字）"})
            self.assertEqual(rr.status_code, 200, rr.text)
            s = rr.json()["state"]

        fin = c.post(f"/api/session/{s['run_id']}/finish")
        self.assertEqual(fin.status_code, 200, fin.text)
        rep = fin.json()
        self.assertIn("面试复盘报告", rep["markdown"])
        self.assertTrue(rep["turns"])

        md = c.get(f"/api/session/{s['run_id']}/report")
        self.assertEqual(md.status_code, 200)
        self.assertIn("综合得分", md.text)

        got = c.get(f"/api/session/{s['run_id']}")
        self.assertEqual(got.status_code, 200)
        self.assertTrue(got.json()["finished"])

    def test_upload_requires_resume(self):
        try:
            c = self._client()
        except Exception as e:
            self.skipTest(f"TestClient 不可用: {e}")
        self.assertEqual(c.post("/api/session", data={"jd_text": JD}).status_code, 422)

    def test_bad_suffix_returns_400_with_reason(self):
        try:
            c = self._client()
        except Exception as e:
            self.skipTest(f"TestClient 不可用: {e}")
        r = c.post("/api/session", files={"resume": ("bad.xlsx", b"x", "application/octet-stream")})
        self.assertEqual(r.status_code, 400)
        self.assertIn("不支持的格式", r.json()["detail"])

    def test_unknown_session_404(self):
        try:
            c = self._client()
        except Exception as e:
            self.skipTest(f"TestClient 不可用: {e}")
        self.assertEqual(c.get("/api/session/nope").status_code, 404)
        self.assertEqual(c.post("/api/session/nope/answer", json={"answer": "x"}).status_code, 404)

    def test_index_page_served(self):
        try:
            c = self._client()
        except Exception as e:
            self.skipTest(f"TestClient 不可用: {e}")
        r = c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("面试陪练 Agent", r.text)
        self.assertIn("拖到这里", r.text)


if __name__ == "__main__":
    unittest.main()
