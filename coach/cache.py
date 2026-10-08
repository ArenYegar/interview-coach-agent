"""打分结果缓存：同一（模型 + 提示版本 + 防护档 + 采样次数 + 题 + 答案）不重复花钱。

面试考点：缓存键必须包含**所有会改变输出的输入**，否则改了提示词还在吃旧分 ——
这是最危险的一类缓存 bug，且它看起来完全正常。所以键里带：
  model / PROMPT_VERSION / guard 档 / reps / qid / 答案哈希 / rubric 哈希。
**改了 score_answer 的提示词或防户口径，必须把 PROMPT_VERSION 往上改一位**（下面那行常量）。

命中会记在 ScoreCard.cached 上，也会让成本账里的 llm_calls 自然变少 —— 不隐藏、可审计。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

from . import config

# 提示词 / 防户口径 / 中位数聚合方式的版本号：改动其中任何一处都要改这里，否则旧分会继续被复用
PROMPT_VERSION = "score-v4-guard2-median-cache"

_LOCK = threading.Lock()


def enabled() -> bool:
    """**默认关闭**，要显式设 COACH_CACHE=1 才启用（评测重跑同一批答案时用）。

    为什么默认关：缓存会让"同一输入重复跑得到完全一样的分数"，而这恰好会**掩盖本项目要测量的
    模型抖动**（稳定性探针的结论就是"单次采样极差常态 10pp"）。默认开等于偷偷把测量对象换掉了。
    另外它默认开还会造成跨测试/跨运行的陈旧命中（见 tests/__init__.py 的说明）。
    """
    return os.getenv("COACH_CACHE", "0") == "1"


def _path() -> Path:
    """缓存文件位置：默认 runs/score_cache.json（不进版本库）；可用 COACH_CACHE_PATH 覆盖。"""
    return Path(os.getenv("COACH_CACHE_PATH") or (Path(config.RUNS_DIR) / "score_cache.json"))


def key_of(model: str, guard: int, reps: int, qid: str, answer: str, rubric_json: str) -> str:
    parts = [str(model), PROMPT_VERSION, str(guard), str(reps), str(qid),
             hashlib.sha1((answer or "").encode("utf-8")).hexdigest(),
             hashlib.sha1((rubric_json or "").encode("utf-8")).hexdigest()]
    return hashlib.sha1("\u0001".join(parts).encode("utf-8")).hexdigest()[:24]


def _load() -> dict:
    p = _path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get(key: str) -> dict | None:
    if not enabled():
        return None
    with _LOCK:
        return _load().get(key)


def put(key: str, payload: dict) -> None:
    if not enabled():
        return
    with _LOCK:
        d = _load()
        d[key] = payload
        config.ensure_dirs()
        tmp = _path().with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_path())


def stats() -> dict:
    with _LOCK:
        d = _load()
    return {"entries": len(d), "path": str(_path()), "enabled": enabled(),
            "prompt_version": PROMPT_VERSION}


def clear() -> int:
    with _LOCK:
        n = len(_load())
        p = _path()
        if p.exists():
            p.unlink()
    return n
