"""供应商 / 端点 / 模型链配置单测（2026-09-20 加）。

动机：端点与模型链是分开配的，"换成 DeepSeek 端点却还在请求 qwen-*"是最容易犯的错配。
本文件锁住：① 按端点选链；② COACH_MODEL 支持逗号分隔的降级链；③ 老名字 DEFAULT_MODEL_CHAIN 仍在。
"""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from coach import config

DEEPSEEK = "https://api.deepseek.com"
DASHSCOPE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class TestProvider(unittest.TestCase):
    def test_provider_from_base_url(self):
        self.assertEqual(config.provider(DEEPSEEK), "deepseek")
        self.assertEqual(config.provider(DEEPSEEK + "/v1"), "deepseek")
        self.assertEqual(config.provider(DASHSCOPE), "dashscope")
        self.assertEqual(config.provider("https://api.openai.com/v1"), "unknown")

    def test_chain_switches_with_endpoint(self):
        with mock.patch.dict(os.environ, {"COACH_BASE_URL": DEEPSEEK}, clear=False):
            os.environ.pop("COACH_MODEL", None)
            self.assertEqual(config.model_chain(), ["deepseek-flash", "deepseek-chat"])
        with mock.patch.dict(os.environ, {"COACH_BASE_URL": DASHSCOPE}, clear=False):
            os.environ.pop("COACH_MODEL", None)
            self.assertEqual(config.model_chain(), ["qwen-plus", "qwen-max", "qwen-turbo"])

    def test_default_endpoint_is_dashscope(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COACH_BASE_URL", None)
            os.environ.pop("BASE_URL", None)
            os.environ.pop("COACH_MODEL", None)
            self.assertEqual(config.provider(), "dashscope")

    def test_coach_model_overrides_and_parses_chain(self):
        with mock.patch.dict(os.environ, {"COACH_MODEL": " deepseek-flash , deepseek-v4-pro "}, clear=False):
            self.assertEqual(config.model_chain(), ["deepseek-flash", "deepseek-v4-pro"])
        with mock.patch.dict(os.environ, {"COACH_MODEL": "qwen-max"}, clear=False):
            self.assertEqual(config.model_chain(), ["qwen-max"])

    def test_legacy_name_still_exists(self):
        self.assertEqual(tuple(config.DEFAULT_MODEL_CHAIN),
                         ("qwen-plus", "qwen-max", "qwen-turbo"))

    def test_provider_summary_is_one_line(self):
        with mock.patch.dict(os.environ, {"COACH_BASE_URL": DEEPSEEK}, clear=False):
            os.environ.pop("COACH_MODEL", None)
            line = config.provider_summary()
        self.assertIn("deepseek", line)
        self.assertIn("deepseek-flash", line)
        self.assertNotIn("\n", line)

    def test_cli_overrides_env(self):
        """--model / --base-url 必须优先于 .env（run_demo.main 里显式写回环境变量）。"""
        import tempfile
        import run_demo
        # 入口现在有输入体检：必须喂真实存在的文件，否则 main 会先 return 2、根本不处理参数
        _t = Path(tempfile.mkdtemp(prefix="coach-cli-"))
        _jd = _t / "jd.md"; _jd.write_text("- 熟悉 Python", encoding="utf-8")
        _rs = _t / "resume.md"; _rs.write_text("熟悉 Python", encoding="utf-8")
        # 注意：必须 patch "run_demo.CoachEngine"（run_demo 里是 from coach.graph import CoachEngine），
        # 否则引擎会真跑一场并往 results/ 写随机命名的垃圾产物。
        with mock.patch.dict(os.environ, {"COACH_BASE_URL": DASHSCOPE, "COACH_MODEL": "qwen-plus"}, clear=False), \
                mock.patch("run_demo.load_text", lambda p: "文本"), \
                mock.patch("run_demo.CoachEngine"), \
                mock.patch("sys.stdout"):
            try:
                run_demo.main(["--jd", str(_jd), "--resume", str(_rs), "--questions", "1",
                               "--model", "deepseek-chat", "--base-url", DEEPSEEK])
            except Exception:
                pass                      # 这里只关心参数是否写回环境变量
            # 断言必须放在 with 块内：mock.patch.dict 退出时会把环境变量还原
            self.assertEqual(os.environ.get("COACH_MODEL"), "deepseek-chat")
            self.assertEqual(os.environ.get("COACH_BASE_URL"), DEEPSEEK)


class TestModelParamCompat(unittest.TestCase):
    """思维链模型的参数兼容：deepseek-reasoner 不接受 temperature / response_format。"""

    def test_param_filter_helpers(self):
        from coach.llm import _accepts_json_mode, _accepts_temperature
        self.assertTrue(_accepts_temperature("deepseek-chat"))
        self.assertTrue(_accepts_json_mode("qwen-plus"))
        self.assertFalse(_accepts_temperature("deepseek-reasoner"))
        self.assertFalse(_accepts_json_mode("deepseek-reasoner"))

    def test_reasoner_kwargs_drop_unsupported_params(self):
        import types
        from coach.llm import LLMClient

        seen = {}

        class FakeCompletions:
            def create(self, **kw):
                seen.clear()
                seen.update(kw)
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="{}"))])

        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-key-for-test"}, clear=False):
            c = LLMClient(model_chain=["deepseek-chat", "deepseek-reasoner"])
        c.client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=FakeCompletions()))

        c._raw("s", "u", json_mode=True)                       # 普通模型：两个参数都要带上
        self.assertEqual(seen["model"], "deepseek-chat")
        self.assertIn("temperature", seen)
        self.assertIn("response_format", seen)

        c.model_chain = ["deepseek-reasoner"]                  # 思维链模型：两个参数都要摘掉
        c._raw("s", "u", json_mode=True)
        self.assertEqual(seen["model"], "deepseek-reasoner")
        self.assertNotIn("temperature", seen)
        self.assertNotIn("response_format", seen)
        self.assertIn("max_tokens", seen)


class TestRunRecordsModel(unittest.TestCase):
    """跨模型分数不可直接比较 ⇒ 每场必须记录"是谁评的"；旧 run 文件缺字段也能加载。"""

    RUN_ID = "unittest-llmmodel"

    def setUp(self):
        # 先把这一场跑出来，后面三个测试都基于同一份产物（否则测试次序会互相依赖）
        from coach.graph import CoachEngine
        self.eng = CoachEngine(run_id=self.RUN_ID, max_questions=1, use_llm=False, follow_up=False)
        self.eng.run("- 熟悉 Python", "熟悉 Python", answers=["答1"])

    def test_offline_run_records_model_name(self):
        from coach.schemas import SessionReport
        eng = self.eng
        p = Path(config.RESULTS_DIR) / "run_unittest-llmmodel.json"
        self.assertTrue(p.exists(), "run json 必须落盘")
        rep = SessionReport(**json.loads(p.read_text(encoding="utf-8")))
        self.assertTrue(rep.llm_model, "报告必须记录本场模型名")
        self.assertEqual(rep.llm_model, eng.model)
        md = Path(config.RESULTS_DIR) / "report_unittest-llmmodel.md"
        self.assertIn("模型：", md.read_text(encoding="utf-8"))

    def test_old_run_file_without_model_field_still_loads(self):
        from coach.schemas import SessionReport
        old = {"run_id": "legacy", "llm_status": "真实模型可用", "overall": 0.5}
        rep = SessionReport(**old)                 # 旧产物没有 llm_model，不能因此报错
        self.assertEqual(rep.llm_model, "")

    def test_evaluate_exposes_model_name(self):
        from coach.eval import harness
        with mock.patch.object(harness, "machine_agreement", lambda rid: {"status": "skipped"}):
            m = harness.evaluate("unittest-llmmodel", out_path=Path(config.RESULTS_DIR) / "eval_unittest-llmmodel.json")
        self.assertTrue(m["llm_model"])


class TestEmptyContentGuard(unittest.TestCase):
    """推理型模型可能把 token 预算全用在 reasoning 上而返回空 content —— 必须记为失败并换下一个模型，
    绝不能把 "" 当成正常返回（那会伪装成 JSON 解析失败，掩盖真实原因）。"""

    def test_empty_content_switches_model_and_raises_clearly(self):
        import types
        from coach.llm import LLMClient, LLMError

        tried = []

        class FakeCompletions:
            def create(self, **kw):
                tried.append(kw["model"])
                return types.SimpleNamespace(choices=[types.SimpleNamespace(
                    message=types.SimpleNamespace(content=""), finish_reason="length")])

        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-key-for-test"}, clear=False):
            c = LLMClient(model_chain=["deepseek-flash", "deepseek-chat"])
        c.client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=FakeCompletions()))

        with self.assertRaises(LLMError) as ctx:
            c._raw("s", "u", json_mode=True)
        self.assertIn("空内容", str(ctx.exception))
        self.assertEqual(tried, ["deepseek-flash", "deepseek-chat"], "两个模型都应被尝试过")

    def test_non_empty_content_still_returned(self):
        import types
        from coach.llm import LLMClient

        class FakeCompletions:
            def create(self, **kw):
                return types.SimpleNamespace(choices=[types.SimpleNamespace(
                    message=types.SimpleNamespace(content="  {\"ok\": true}  "))])

        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-key-for-test"}, clear=False):
            c = LLMClient(model_chain=["deepseek-flash"])
        c.client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=FakeCompletions()))
        self.assertEqual(c._raw("s", "u", json_mode=True), '{"ok": true}')


class TestRuntimeParams(unittest.TestCase):
    """参数必须"每次调用现读"：原先 import 时快照，导致 .env 里的 COACH_MAX_TOKENS / COACH_MAX_QUESTIONS
    全部失效（import 发生在 load_env_file 之前）。"""

    def test_env_values_are_honored_at_runtime(self):
        with mock.patch.dict(os.environ, {"COACH_MAX_TOKENS": "4096", "COACH_TEMPERATURE": "0.9",
                                          "COACH_MAX_QUESTIONS": "9"}, clear=False):
            self.assertEqual(config.max_tokens(), 4096)
            self.assertEqual(config.temperature(), 0.9)
            self.assertEqual(config.max_questions(), 9)

    def test_module_constants_are_import_snapshots(self):
        # 常量保留只为兼容旧引用，它们不会随运行时环境变化——这正是原来那个 bug 的形态
        with mock.patch.dict(os.environ, {"COACH_MAX_TOKENS": "123"}, clear=False):
            self.assertNotEqual(config.MAX_TOKENS, 123)

    def test_defaults_when_unset(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("COACH_MAX_TOKENS", "COACH_TEMPERATURE", "COACH_MAX_QUESTIONS")}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(config.max_tokens(), 900)
            self.assertEqual(config.temperature(), 0.3)
            self.assertEqual(config.max_questions(), 5)

    def test_preload_env_file_before_parser_defaults(self):
        import tempfile
        import run_demo
        tmp = Path(tempfile.mkdtemp(prefix="coach-preload-"))
        env = tmp / ".env"
        env.write_text("COACH_MAX_QUESTIONS=9\n", encoding="utf-8")
        os.environ.pop("COACH_MAX_QUESTIONS", None)
        try:
            path, n = run_demo.preload_env_file(["--env-file", str(env), "--jd", "x.md"])
            self.assertEqual(path, str(env))
            self.assertEqual(n, 1)
            self.assertEqual(config.max_questions(), 9, "--env-file 必须在解析参数前生效")
            path2, _ = run_demo.preload_env_file(["--env-file=" + str(env)])
            self.assertEqual(path2, str(env), "应支持 --env-file=path 形式")
        finally:
            os.environ.pop("COACH_MAX_QUESTIONS", None)


if __name__ == "__main__":
    unittest.main()
