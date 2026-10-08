"""入口输入体检单测（2026-09-20 加）。

动机：路径写错时原实现直接抛 PackageNotFoundError 一屏 traceback。
现在必须返回码 2 + 人话提示，且不能出现 traceback。
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run_demo


class TestCheckInputs(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coach-inputs-"))
        self.jd = self.tmp / "jd.md"
        self.jd.write_text("- 熟悉 Python", encoding="utf-8")
        self.resume = self.tmp / "resume.md"
        self.resume.write_text("熟悉 Python", encoding="utf-8")

    def test_ok_inputs_produce_no_problems(self):
        self.assertEqual(run_demo.check_inputs(str(self.jd), str(self.resume), None), [])

    def test_missing_resume_is_reported_with_hint(self):
        bad = str(self.tmp / "不存在.docx")
        problems = run_demo.check_inputs(str(self.jd), bad, None)
        self.assertEqual(len(problems), 1)
        self.assertIn("找不到", problems[0])
        self.assertIn("引号", problems[0])

    def test_empty_file_is_reported(self):
        empty = self.tmp / "empty.md"
        empty.write_text("", encoding="utf-8")
        problems = run_demo.check_inputs(str(empty), str(self.resume), None)
        self.assertTrue(any("空的" in p for p in problems))

    def test_missing_required_arguments_are_reported(self):
        problems = run_demo.check_inputs("", "", None)
        self.assertEqual(len(problems), 2)

    def test_main_exits_2_without_traceback(self):
        with mock.patch("sys.stdout"):
            rc = run_demo.main(["--jd", str(self.tmp / "nope.md"),
                                "--resume", str(self.tmp / "nope2.md")])
        self.assertEqual(rc, 2)
