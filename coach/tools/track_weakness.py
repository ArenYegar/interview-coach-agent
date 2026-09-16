"""工具 5：弱点追踪（SQLite 持久化，支持跨场次聚合 —— 这是本项目的差异化点）。"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone

from .. import config
from ..schemas import Turn, WeaknessStat


def _conn() -> sqlite3.Connection:
    config.ensure_dirs()
    c = sqlite3.connect(config.DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS answers(
        run_id TEXT, ts TEXT, qid TEXT, category TEXT, question TEXT,
        total REAL, missing TEXT, verdict TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS turns(
        run_id TEXT, ts TEXT, qid TEXT, question TEXT, answer TEXT, scorecard TEXT)""")
    return c


def record_session(run_id: str, turns: list[Turn]) -> int:
    """把本次面试的所有轮次写入库，返回写入条数。"""
    c = _conn()
    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    # 幂等：同一 run_id 重复写入时先清空旧行，避免统计被重复累计
    c.execute("DELETE FROM turns WHERE run_id=?", (run_id,))
    c.execute("DELETE FROM answers WHERE run_id=?", (run_id,))
    n = 0
    for t in turns:
        sc = t.scorecard
        c.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)",
                  (run_id, ts, t.qid, t.question, t.answer,
                   sc.model_dump_json() if sc else ""))
        c.execute("INSERT INTO answers VALUES(?,?,?,?,?,?,?,?)",
                  (run_id, ts, t.qid, getattr(sc, "category", "") or t.qid.split("-")[0],
                   t.question, sc.total if sc else 0.0,
                   json.dumps(sc.missing, ensure_ascii=False) if sc else "[]",
                   sc.verdict if sc else ""))
        n += 1
    c.commit()
    c.close()
    return n


def aggregate_weakness(run_id: str, top: int = 5) -> list[WeaknessStat]:
    """本场弱点：按缺失要点聚合成标签，再按出现次数排序。"""
    c = _conn()
    rows = c.execute("SELECT missing, verdict, total FROM answers WHERE run_id=?", (run_id,)).fetchall()
    c.close()
    cnt: Counter[str] = Counter()
    for missing_json, verdict, total in rows:
        try:
            items = json.loads(missing_json or "[]")
        except json.JSONDecodeError:
            items = []
        if not items:
            if verdict == "weak":
                cnt["整题答不上来"] += 1
            continue
        for tag in items[:4]:
            cnt[tag] += 1

    out: list[WeaknessStat] = []
    for tag, n in cnt.most_common(top):
        sev = "high" if n >= 3 else ("mid" if n == 2 else "low")
        out.append(WeaknessStat(tag=tag, count=n, severity=sev))
    return out


def cross_session_tags(run_id: str | None = None, top: int = 8) -> list[WeaknessStat]:
    """跨场次弱点：同名标签在所有历史场次里累计（用于"弱点追踪"叙事）。"""
    c = _conn()
    rows = c.execute("SELECT run_id, missing FROM answers").fetchall()
    c.close()
    per_run: dict[str, Counter] = {}
    for rid, missing_json in rows:
        if run_id and rid == run_id:
            continue
        try:
            items = json.loads(missing_json or "[]")
        except json.JSONDecodeError:
            items = []
        for tag in items:
            per_run.setdefault(tag, Counter())[rid] += 1

    out: list[WeaknessStat] = []
    for tag, runs_counter in per_run.items():
        total = sum(runs_counter.values())
        sev = "high" if len(runs_counter) >= 2 else "mid"
        out.append(WeaknessStat(tag=tag, count=total, severity=sev,
                                last_seen=max(runs_counter.keys())))
    out.sort(key=lambda w: (-w.count, w.tag))
    return out[:top]
