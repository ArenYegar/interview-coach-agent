"""LLM 接入层：OpenAI 兼容协议 + JSON 强约束 + 重试 + 模型降级 + 离线兜底。

面试考点：多供应商可切、结构化输出校验、失败恢复。
"""
from __future__ import annotations

import json
import re
from typing import Any, Type, TypeVar

from pydantic import BaseModel, ValidationError

from . import config

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> str:
    """模型偶尔会带 ```json 围栏或前后废话，这里做一次保守抽取。"""
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    if not t.startswith("{"):
        i, j = t.find("{"), t.rfind("}")
        if i != -1 and j > i:
            t = t[i:j + 1]
    return t


class LLMClient:
    """真实模型客户端。"""

    def __init__(self, model_chain: list[str] | None = None, temperature: float | None = None) -> None:
        from openai import OpenAI  # 延迟导入：离线模式不需要
        key = config.api_key()
        if not key:
            raise LLMError("未找到可用的 API Key（COACH_API_KEY / DASHSCOPE_API_KEY / QWEN_API_KEY ...）")
        self.client = OpenAI(api_key=key, base_url=config.base_url(), timeout=config.TIMEOUT)
        self.model_chain = model_chain or config.model_chain()
        self.temperature = config.TEMPERATURE if temperature is None else temperature
        self.model = self.model_chain[0]
        self.calls = 0
        self.last_error = ""

    def probe(self) -> tuple[bool, str]:
        """一次极轻量的可用性探测（列模型），用于避免"静默降级"。"""
        try:
            self.client.models.list()
            return True, "ok"
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return False, self.last_error

    def _raw(self, system: str, user: str, json_mode: bool) -> str:
        last_err: Exception | None = None
        for model in list(self.model_chain):
            for attempt in range(config.RETRIES):
                try:
                    kwargs: dict[str, Any] = dict(
                        model=model,
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": user}],
                        temperature=self.temperature,
                        max_tokens=config.MAX_TOKENS,
                    )
                    if json_mode:
                        kwargs["response_format"] = {"type": "json_object"}
                    resp = self.client.chat.completions.create(**kwargs)
                    self.model = model
                    self.calls += 1
                    return resp.choices[0].message.content or ""
                except Exception as e:  # 网络/限流/模型不存在
                    last_err = e
                    msg = str(e).lower()
                    retryable = any(k in msg for k in ("rate", "timeout", "500", "502", "503", "504", "connection"))
                    if not retryable:
                        break  # 模型不存在/无权限 → 换下一个模型
        self.last_error = f"{type(last_err).__name__}: {last_err}"
        raise LLMError(f"LLM 调用失败: {self.last_error}")

    def text(self, system: str, user: str) -> str:
        return self._raw(system, user, json_mode=False).strip()

    def structured(self, system: str, user: str, schema: Type[T], repair_rounds: int = 1) -> T:
        raw = self._raw(system, user, json_mode=True)
        for _ in range(repair_rounds + 1):
            try:
                return schema(**json.loads(_extract_json(raw)))
            except (json.JSONDecodeError, ValidationError) as e:
                raw = self._raw(
                    "你是严格的 JSON 修复器。只输出符合要求的 JSON，不要任何解释。",
                    f"下面的内容不符合目标 schema，请修复后仅输出 JSON。\n错误：{e}\n原内容：\n{raw}",
                    json_mode=True,
                )
        raise LLMError(f"结构化输出解析失败（已重试 {repair_rounds} 次）")


class MockLLMClient:
    """离线/测试用：确定性输出，保证流水线在没有 Key 时也能跑通与断言。

    注意：Mock 只用于自测与降级演示，不代表任何真实效果指标。
    """

    def __init__(self, *_, **__) -> None:
        self.model = "mock-deterministic"
        self.calls = 0

    def text(self, system: str, user: str) -> str:
        self.calls += 1
        return "[mock] 这是离线模式下的确定性回复，用于验证流水线。"

    def structured(self, system: str, user: str, schema: Type[T], repair_rounds: int = 1) -> T:
        self.calls += 1
        name = schema.__name__
        if name == "JDProfile":
            reqs = re.findall(r"(?:^|\n)\s*(?:[-*•]|\d+[.、)])\s*(.+)", user)[:6]
            return schema(
                title="（mock）AI 应用开发实习生",
                company="（mock）示例公司",
                requirements=[{"req_id": f"R{i+1}", "text": r.strip()[:80], "must_have": i < 3,
                               "keywords": re.findall(r"[A-Za-z+#.]{2,}|[\u4e00-\u9fa5]{2,}", r)[:4]}
                              for i, r in enumerate(reqs)] or
                             [{"req_id": "R1", "text": "熟悉 Python", "must_have": True, "keywords": ["Python"]}],
                digest="（mock）JD 结构化结果",
                source="heuristic",
            )
        if name == "ScoreCard":
            return schema(qid="mock", dim_scores={"覆盖度": 0.6}, total=0.6, evidence=["（mock）命中要点"],
                          missing=["（mock）缺失要点"], verdict="ok", improvement="（mock）补充量化结果",
                          method="heuristic")
        raise LLMError(f"MockLLMClient 未实现 schema: {name}")


def get_client(prefer_real: bool = True):
    """优先真实模型；无 Key 或显式离线时退化为 Mock。"""
    if prefer_real and not config.offline():
        try:
            return LLMClient()
        except LLMError:
            pass
    return MockLLMClient()
