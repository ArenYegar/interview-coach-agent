"""状态定义：一次面试会话的全部可序列化状态。"""
from __future__ import annotations

from typing import Any, TypedDict


class CoachState(TypedDict, total=False):
    run_id: str
    jd_text: str
    resume_text: str
    profile: Any          # JDProfile
    match: Any            # MatchResult
    questions: list       # list[Question]
    answers: list         # list[str]（人工/预置回答）
    idx: int
    turns: list           # list[Turn]
    weaknesses: list
    report: Any           # SessionReport
    log: list             # list[str] 步骤日志
    degraded: bool


def new_state(run_id: str, jd_text: str, resume_text: str, answers: list[str] | None = None) -> CoachState:
    return CoachState(run_id=run_id, jd_text=jd_text, resume_text=resume_text,
                      answers=list(answers or []), idx=0, turns=[], log=[], degraded=False)
