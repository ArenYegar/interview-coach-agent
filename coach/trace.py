"""可观测层：把每个步骤写成 JSONL，支持事后回放。

面试考点：Agent 的失败定位靠 trace，而不是靠肉眼猜。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .schemas import TraceEvent


class Tracer:
    def __init__(self, run_id: str, enabled: bool = True) -> None:
        self.run_id = run_id
        self.enabled = enabled
        config.ensure_dirs()
        self.dir = Path(config.RUNS_DIR) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "trace.jsonl"
        if self.enabled:
            self.path.write_text("", encoding="utf-8")

    def emit(self, step: str, ok: bool = True, ms: float = 0.0,
             model: str = "", detail: str = "", error: str = "") -> TraceEvent:
        ev = TraceEvent(
            ts=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            run_id=self.run_id, step=step, ok=ok, ms=round(ms, 1), model=model,
            detail=config.redact(detail), error=config.redact(error, 300),
        )
        if self.enabled:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(ev.model_dump_json() + "\n")
        return ev

    def span(self, step: str, detail: str = ""):
        """with tracer.span('parse_jd', jd_text[:80]): ... 自动记录耗时与异常。"""
        return _Span(self, step, detail)


class _Span:
    def __init__(self, tracer: Tracer, step: str, detail: str) -> None:
        self.tracer, self.step, self.detail = tracer, step, detail

    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        ms = (time.perf_counter() - self.t0) * 1000
        if exc is None:
            self.tracer.emit(self.step, ok=True, ms=ms, detail=self.detail)
        else:
            self.tracer.emit(self.step, ok=False, ms=ms, detail=self.detail,
                             error=f"{exc_type.__name__}: {exc}")
        return False


def load_trace(run_id: str) -> list[TraceEvent]:
    p = Path(config.RUNS_DIR) / run_id / "trace.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(TraceEvent(**json.loads(line)))
    return out
