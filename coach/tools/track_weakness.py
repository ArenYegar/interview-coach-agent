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


def _count_weakness(rows, top: int = 5) -> list[WeaknessStat]:
    """把 (缺失要点, 判定, 分数) 三元组聚合成弱点标签。DB 行与内存轮次共用这一份逻辑。"""
    cnt: Counter[str] = Counter()
    for missing, verdict, _total in rows:
        if isinstance(missing, str):
            try:
                items = json.loads(missing or "[]")
            except json.JSONDecodeError:
                items = []
        else:
            items = list(missing or [])
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


def aggregate_weakness(run_id: str, top: int = 5) -> list[WeaknessStat]:
    """本场弱点：按缺失要点聚合成标签，再按出现次数排序。"""
    c = _conn()
    rows = c.execute("SELECT missing, verdict, total FROM answers WHERE run_id=?", (run_id,)).fetchall()
    c.close()
    return _count_weakness(rows, top=top)


def propose_weakness(turns: list[Turn], top: int = 5) -> list[WeaknessStat]:
    """从内存里的轮次直接算出弱点提议（HITL 模式还没写库时用，与 aggregate_weakness 同一套口径）。"""
    rows = [((t.scorecard.missing if t.scorecard else []),
             (t.scorecard.verdict if t.scorecard else ""),
             (t.scorecard.total if t.scorecard else 0.0)) for t in turns]
    return _count_weakness(rows, top=top)


def _age_days(ts: str) -> float:
    """把库里的时间戳换成"距今天数"，用于时间衰减。解析失败按 0 天（当作最新）处理。"""
    try:
        t = datetime.fromisoformat(str(ts))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - t).total_seconds() / 86400.0)
    except Exception:
        return 0.0


def cross_session_tags(run_id: str | None = None, top: int = 8,
                       half_life_days: float = 21.0) -> list[WeaknessStat]:
    """跨场次弱点：同名标签在所有历史场次累计，并做**时间衰减**与**跨场次置信度**。

    为什么要衰减：一个半年前出现过一次的弱点，和最近连输三场都犯的弱点，不该被同等对待 ——
    不衰减的话，画像会被陈年数据长期占位，练习重点永远指着老问题。
    衰减口径：每次出现的权重 = 0.5 ** (距今天数 / half_life_days)，默认半衰期 21 天。
    置信度口径：出现在几**场**（distinct run），跨场次出现才算真弱点 —— 与单场内多题重复区分开。
    """
    c = _conn()
    rows = c.execute("SELECT run_id, missing, ts FROM answers").fetchall()
    c.close()
    per_run: dict[str, dict] = {}          # tag -> {run_id: 该场出现次数}
    weight: dict[str, float] = {}          # tag -> 衰减后强度
    latest: dict[str, str] = {}
    for rid, missing_json, ts in rows:
        if run_id and rid == run_id:
            continue
        try:
            items = json.loads(missing_json or "[]")
        except json.JSONDecodeError:
            items = []
        w = 0.5 ** (_age_days(ts) / max(1e-6, half_life_days))
        for tag in items:
            per_run.setdefault(tag, {})
            per_run[tag][rid] = per_run[tag].get(rid, 0) + 1
            weight[tag] = weight.get(tag, 0.0) + w
            if str(ts) > latest.get(tag, ""):
                latest[tag] = str(ts)

    out: list[WeaknessStat] = []
    for tag, runs_map in per_run.items():
        total = sum(runs_map.values())
        decayed = round(weight.get(tag, 0.0), 3)
        # 严重度看"跨场次 + 衰减后强度"：只在多场出现且衰减后仍有分量的才算 high
        sev = "high" if (len(runs_map) >= 2 and decayed >= 2.0) else ("mid" if len(runs_map) >= 1 else "low")
        out.append(WeaknessStat(tag=tag, count=total, severity=sev, last_seen=latest.get(tag, ""),
                                runs=len(runs_map), decayed=decayed))
    out.sort(key=lambda w: (-w.decayed, -w.runs, w.tag))
    return out[:top]

def recent_question_ids(limit_runs: int = 3) -> set[str]:
    """最近 N 场出过的题号（用于练习模式"别每次都遇到同一批题"）。

    只读 qid，不碰作答内容；返回空集表示没有历史场次或读取失败——
    失败不该阻断出题，所以这里吞掉异常。
    """
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT run_id FROM turns GROUP BY run_id ORDER BY MAX(ts) DESC LIMIT ?",
                (max(1, int(limit_runs)),)).fetchall()
            if not rows:
                return set()
            ids = [r[0] for r in rows]
            marks = ",".join("?" * len(ids))
            qids = c.execute(f"SELECT DISTINCT qid FROM turns WHERE run_id IN ({marks})",
                             ids).fetchall()
        out: set[str] = set()
        for (qid,) in qids:
            out.add(str(qid).split("-F")[0])          # 追问题号归一到主问题
        return out
    except Exception:
        return set()
