"""并发执行相互独立的调用：把"等模型"的时间叠起来。

面试考点：模型调用是等待型 IO，GIL 在等待时释放，所以线程池就够并行（不用上多进程）。
但必须分清**哪些调用真的互相独立**：
  · 同一题的多次重复采样 —— 独立（取中位数与顺序无关）→ 可以并发；
  · 追问 —— 依赖上一轮的分数 → 不能并发；
  · 选题 / 规则计算 —— 本来就是毫秒级，不值得并发。
所以并发只加在"独立且贵"的地方，不乱铺。
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

DEFAULT_WORKERS = 4


def workers() -> int:
    """并发度（COACH_PARALLEL，默认 4；设 1 关闭）。别设太大，下游 API 有限流。"""
    v = os.getenv("COACH_PARALLEL")
    if not v:
        return DEFAULT_WORKERS
    try:
        return max(1, min(16, int(v)))
    except ValueError:
        return DEFAULT_WORKERS


def run_many(func, items, n: int | None = None) -> list:
    """按顺序返回结果（顺序与 items 一致，便于复现）。n<=1 时退化为串行。"""
    items = list(items)
    n = workers() if n is None else n
    if n <= 1 or len(items) <= 1:
        return [func(x) for x in items]
    with ThreadPoolExecutor(max_workers=min(n, len(items))) as ex:
        return list(ex.map(func, items))
