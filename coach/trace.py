"""可观测层：每步写成 JSONL，支持事后回放；同时记下每一步消耗的 token。

面试考点：
  ① Agent 的失败定位靠 trace，不靠肉眼猜；
  ② 「一次问答花多少 token、多少延迟」必须能从产物文件算出来 ——
     所以 trace 里除了 ms，还带 prompt/completion/total tokens（由 llm 客户端的计量器做增量）。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .schemas import TraceEvent

# token 计量的零值模板：离线/Mock 模式下 token 数没有意义，一律记 0（不编数字）
ZERO_USAGE = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
TOKEN_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


def _delta(new: dict, old: dict) -> dict:
    """两次快照之间的增量。只取正数：计量器被替换/重置时不产生负数。"""
    out = {"calls": int(new.get("calls", 0) or 0) - int(old.get("calls", 0) or 0)}
    for k in TOKEN_KEYS:
        out[k] = max(0, int(new.get(k, 0) or 0) - int(old.get(k, 0) or 0))
    return out


class Tracer:
    def __init__(self, run_id: str, enabled: bool = True, meter=None, append: bool = False) -> None:
        self.run_id = run_id
        self.enabled = enabled
        # append=True 用于**恢复已有会话**：Web 界面每个 HTTP 请求都会新建引擎，
        # 若每次都清空 trace，回放与成本账就只剩最后一次请求的事件（这个 bug 真实发生过）。
        self.append = append
        self.meter = meter                 # 任何实现 usage_snapshot() 的对象（真实/Mock LLM 客户端）
        self.steps: dict[str, dict] = {}   # 按步骤聚合：事件数/失败数/耗时/token
        self.t0 = time.perf_counter()
        config.ensure_dirs()
        self.dir = Path(config.RUNS_DIR) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "trace.jsonl"
        if self.enabled and not self.append:
            self.path.write_text("", encoding="utf-8")

    # ---------- 计量器 ----------
    def set_meter(self, meter) -> None:
        self.meter = meter

    def usage_snapshot(self) -> dict:
        if self.meter is not None and hasattr(self.meter, "usage_snapshot"):
            try:
                return dict(self.meter.usage_snapshot())
            except Exception:                     # 计量器自己出错不该拖垮业务链路
                pass
        return dict(ZERO_USAGE)

    # ---------- 写事件 ----------
    def emit(self, step: str, ok: bool = True, ms: float = 0.0,
             model: str = "", detail: str = "", error: str = "",
             usage: dict | None = None) -> TraceEvent:
        u = usage or {}
        calls = int(u.get("calls", 0) or 0)
        ev = TraceEvent(
            ts=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            run_id=self.run_id, step=step, ok=ok, ms=round(ms, 1), model=model,
            detail=config.redact(detail), error=config.redact(error, 300),
            prompt_tokens=int(u.get("prompt_tokens", 0) or 0),
            completion_tokens=int(u.get("completion_tokens", 0) or 0),
            total_tokens=int(u.get("total_tokens", 0) or 0),
            calls=calls,
        )
        if self.enabled:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(ev.model_dump_json() + "\n")
        s = self.steps.setdefault(step, {"count": 0, "fail": 0, "ms": 0.0,
                                         "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
        s["count"] += 1
        s["fail"] += 0 if ok else 1
        s["ms"] = round(s["ms"] + ms, 1)
        for k in TOKEN_KEYS:
            s[k] += getattr(ev, k)
        return ev

    def span(self, step: str, detail: str = ""):
        """with tracer.span('tool.score_answer', qid): ... 自动记录耗时、异常与本次 token 增量。"""
        return _Span(self, step, detail)

    # ---------- 本场汇总 ----------
    def summary(self) -> dict:
        """给报告用的成本与延迟汇总。

        llm_ms 只统计"有 token 消耗的步骤"（即真的打了模型的步骤），
        这样它和 wall_ms 的差就是规则计算/落盘的耗时 —— 面试问"最长的一段在哪"时按这个拆。
        """
        usage = self.usage_snapshot()
        llm_ms = round(sum(s["ms"] for s in self.steps.values() if s["total_tokens"] > 0), 1)
        return {
            "source": "tracer-memory",
            "model": getattr(self.meter, "model", "") if self.meter is not None else "",
            "llm_calls": int(usage.get("calls", 0) or 0),
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
            "llm_ms": llm_ms,
            "steps_ms": round(sum(s["ms"] for s in self.steps.values()), 1),
            "wall_ms": round((time.perf_counter() - self.t0) * 1000, 1),
            "events": sum(s["count"] for s in self.steps.values()),
            "failures": sum(s["fail"] for s in self.steps.values()),
        }


class _Span:
    def __init__(self, tracer: Tracer, step: str, detail: str) -> None:
        self.tracer, self.step, self.detail = tracer, step, detail

    def __enter__(self):
        self.t0 = time.perf_counter()
        self.u0 = self.tracer.usage_snapshot()
        return self

    def __exit__(self, exc_type, exc, tb):
        ms = (time.perf_counter() - self.t0) * 1000
        du = _delta(self.tracer.usage_snapshot(), self.u0)
        if exc is None:
            self.tracer.emit(self.step, ok=True, ms=ms, detail=self.detail, usage=du)
        else:
            self.tracer.emit(self.step, ok=False, ms=ms, detail=self.detail,
                             error=f"{exc_type.__name__}: {exc}", usage=du)
        return False


def load_trace(run_id: str) -> list[TraceEvent]:
    p = Path(config.RUNS_DIR) / run_id / "trace.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(TraceEvent(**json.loads(line)))
            except Exception:
                continue          # 半截/损坏的行不该让整个回放挂掉
    return out


def cost_summary(run_id: str) -> dict:
    """只读 trace 文件的「本场成本与延迟」汇总，键与 Tracer.summary() 保持一致。

    为什么需要它：Web 界面是**每个 HTTP 请求新建一个引擎**（会话状态落盘、引擎不常驻），
    进程内的计数器跨不了请求，报告会显示 calls=0 —— 看起来正常，实际没测到。
    同一 run_id 的 trace 是追加写的，所以从文件算才是这条路径的正确口径。
    """
    events = load_trace(run_id)
    steps: dict[str, dict] = {}
    model = ""
    for e in events:
        s = steps.setdefault(e.step, {"count": 0, "fail": 0, "ms": 0.0,
                                      "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
        s["count"] += 1
        s["fail"] += 0 if e.ok else 1
        s["ms"] = round(s["ms"] + e.ms, 1)
        for k in TOKEN_KEYS:
            s[k] += getattr(e, k)
        if not model and e.model:
            model = e.model
    tot = {k: sum(s[k] for s in steps.values()) for k in TOKEN_KEYS}
    wall_ms = 0.0
    if events:
        try:
            wall_ms = round((datetime.fromisoformat(events[-1].ts)
                             - datetime.fromisoformat(events[0].ts)).total_seconds() * 1000, 1)
        except Exception:
            wall_ms = 0.0
    return {
        "model": model,
        "llm_calls": (sum(e.calls for e in events) or sum(1 for e in events if e.total_tokens > 0)),
        "prompt_tokens": tot["prompt_tokens"],
        "completion_tokens": tot["completion_tokens"],
        "total_tokens": tot["total_tokens"],
        "llm_ms": round(sum(s["ms"] for s in steps.values() if s["total_tokens"] > 0), 1),
        "steps_ms": round(sum(s["ms"] for s in steps.values()), 1),
        "wall_ms": wall_ms,
        "events": len(events),
        "failures": sum(s["fail"] for s in steps.values()),
        "source": "trace-file",
    }


def summarize_trace(run_id: str) -> dict:
    """只读 trace 文件做聚合 —— 对任何历史 run 都能复算成本账（不依赖进程内状态）。"""
    events = load_trace(run_id)
    steps: dict[str, dict] = {}
    for e in events:
        s = steps.setdefault(e.step, {"count": 0, "fail": 0, "ms": 0.0,
                                      "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
        s["count"] += 1
        s["fail"] += 0 if e.ok else 1
        s["ms"] = round(s["ms"] + e.ms, 1)
        for k in TOKEN_KEYS:
            s[k] += getattr(e, k)
    totals = {k: sum(s[k] for s in steps.values()) for k in TOKEN_KEYS}
    llm_ms = round(sum(s["ms"] for s in steps.values() if s["total_tokens"] > 0), 1)
    wall_ms = 0.0
    if events:
        try:
            wall_ms = round((datetime.fromisoformat(events[-1].ts)
                             - datetime.fromisoformat(events[0].ts)).total_seconds() * 1000, 1)
        except Exception:
            wall_ms = 0.0
    return {
        "run_id": run_id,
        "steps": steps,
        "totals": {**totals, "ms": round(sum(s["ms"] for s in steps.values()), 1),
                   "llm_ms": llm_ms, "wall_ms": wall_ms,
                   "events": len(events), "failures": sum(s["fail"] for s in steps.values())},
    }
