"""编排层：显式状态机（engine）+ LangGraph 版（可选、可降级）。"""
from .engine import CoachEngine, Plan
from .state import CoachState, new_state

__all__ = ["CoachEngine", "Plan", "CoachState", "new_state", "run_with_langgraph"]


def run_with_langgraph(*args, **kwargs):
    """延迟导入：langgraph 不可用时给出明确错误，不影响 engine 主路径。"""
    from .langgraph_flow import run_with_langgraph as _run
    return _run(*args, **kwargs)
