"""人工确认闸门（HITL）：Agent 的结论进"跨场次记忆"之前，必须由人放行。

为什么闸门设在这一步：跨场次弱点画像是**长期状态** —— 它会被后续每一场反复引用
（"你这类题反复答不好"），并影响练习重点与简历改写建议。它一旦写错，就会持续误导后面所有场次，
是典型的"错误落库后难以回收"。所以把这一步拆成两段：

  1. Agent 只**提议**（proposal 落盘到 runs/<run_id>/pending_review.json）；
  2. 人确认后才 **commit**（写入 SQLite，且只有被批准的要点进入画像）。

状态是落盘的不是内存里的挂起，所以中断可以跨进程恢复（CLI 跑一半关掉、明天再确认都行）。
生产上可以把中断点交给框架（LangGraph 的 `interrupt` + checkpointer，本机装的是 0.4.5，支持）；
本机版选择文件落盘，理由是：会话状态本来就要落盘（`web_state.json`），
单机单用户场景下引入 checkpointer 只是把同一件事换个地方存，复杂度大于收益。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import config


def proposal_path(run_id: str) -> Path:
    return Path(config.RUNS_DIR) / run_id / "pending_review.json"


def save_proposal(run_id: str, proposals: list[dict], note: str = "") -> Path:
    p = proposal_path(run_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "note": note or "以下弱点画像将写入跨场次记忆，需要人工确认",
        "proposals": proposals,
        "approved": [],
        "applied": False,
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_proposal(run_id: str) -> dict | None:
    p = proposal_path(run_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def commit(run_id: str, turns: list, approved_tags: list[str]) -> dict:
    """人工确认后提交：写库，但**只保留被批准的弱点要点** —— 驳回必须真的生效。

    做法是过滤写入用的副本（不动内存里的原始报告）：被驳回的要点根本不进库，
    于是它也不会出现在后续场次的「跨场次弱点画像」里。
    """
    from .tools.track_weakness import record_session      # 延迟导入避免环
    approved = set(approved_tags)
    kept = []
    for t in turns:
        if t.scorecard is None:
            kept.append(t)
            continue
        sc = t.scorecard.model_copy(deep=True)
        sc.missing = [m for m in sc.missing if m in approved]
        kept.append(t.model_copy(update={"scorecard": sc}))
    n = record_session(run_id, kept)
    d = mark_applied(run_id, sorted(approved), committed=n)
    return {"committed_turns": n, "approved": sorted(approved), "applied": d["applied"],
            "proposal": str(proposal_path(run_id))}


def mark_applied(run_id: str, approved: list[str], committed: int = 0) -> dict:
    d = load_proposal(run_id) or {"run_id": run_id, "proposals": []}
    d["approved"] = list(approved)
    d["applied"] = True
    d["committed_turns"] = committed
    d["applied_ts"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    proposal_path(run_id).write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return d
