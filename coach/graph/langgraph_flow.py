"""同一流程的 LangGraph 表达（可选依赖；不可用时回落到 engine）。

为什么要这一层：面试官会问"你怎么编排" —— 用显式状态机而不是黑盒 AgentExecutor。
注意：本文件只做编排，所有业务逻辑复用 engine 的方法，避免两套实现漂移。
"""
from __future__ import annotations

from .engine import CoachEngine
from .state import CoachState, new_state


def langgraph_available() -> tuple[bool, str]:
    try:
        import langgraph  # noqa: F401
        from langgraph.graph import StateGraph  # noqa: F401
        return True, "ok"
    except Exception as e:  # pragma: no cover
        return False, f"{type(e).__name__}: {e}"


def build_graph(engine: CoachEngine, preset_questions: list | None = None):
    """构建状态机：parse → match → plan → loop(ask+score) → aggregate → report。"""
    from langgraph.graph import END, StateGraph

    def n_parse(state: CoachState) -> CoachState:
        from ..tools import parse_jd
        state["profile"] = parse_jd(state["jd_text"], llm=engine.client)
        state["log"] = state.get("log", []) + ["parse_jd"]
        return state

    def n_match(state: CoachState) -> CoachState:
        from ..tools import match_resume
        state["match"] = match_resume(state["resume_text"], state["profile"], llm=None)
        state["log"] = state.get("log", []) + ["match_resume"]
        return state

    def n_plan(state: CoachState) -> CoachState:
        from ..tools import plan_questions
        state["questions"] = list(preset_questions) if preset_questions else plan_questions(
            state["profile"], k=engine.max_questions, bank=engine.bank, categories=engine.categories)
        state["idx"] = 0
        state["turns"] = []
        state["log"] = state.get("log", []) + ["search_bank"]
        return state

    def n_answer(state: CoachState) -> CoachState:
        from ..schemas import Turn
        i = state["idx"]
        q = state["questions"][i]
        answers = state.get("answers") or []
        if i >= len(answers):
            return state
        ans = answers[i]
        card = engine.score(q, ans)
        state["turns"] = state.get("turns", []) + [Turn(qid=q.qid, target_req_id=q.target_req_id,
                                                        question=q.text, answer=ans, scorecard=card)]
        state["idx"] = i + 1
        state["log"] = state.get("log", []) + [f"ask+score {q.qid} -> {card.total:.2f}"]
        return state

    def n_aggregate(state: CoachState) -> CoachState:
        from ..tools import aggregate_weakness, record_session
        record_session(state["run_id"], state["turns"])
        state["weaknesses"] = aggregate_weakness(state["run_id"], top=5)
        state["log"] = state.get("log", []) + ["aggregate"]
        return state

    def n_report(state: CoachState) -> CoachState:
        from ..schemas import SessionReport
        from ..tools import export_report
        plan = type("P", (), {"profile": state["profile"], "match": state["match"]})()
        scored = [t.scorecard.total for t in state["turns"] if t.scorecard]
        report = SessionReport(
            run_id=state["run_id"], jd_title=state["profile"].title, match=state["match"],
            turns=state["turns"], weaknesses=state["weaknesses"],
            overall=round(sum(scored) / len(scored), 4) if scored else 0.0,
            actions=[f"补强：{w.tag}" for w in state["weaknesses"][:3]],
        )
        export_report(report)
        state["report"] = report
        state["log"] = state.get("log", []) + ["report"]
        return state

    def route_loop(state: CoachState) -> str:
        return "answer" if state["idx"] < len(state["questions"]) and state["idx"] < len(state.get("answers") or []) else "aggregate"

    g = StateGraph(CoachState)
    g.add_node("node_parse", n_parse)
    g.add_node("node_match", n_match)
    g.add_node("node_plan", n_plan)
    g.add_node("node_answer", n_answer)
    g.add_node("node_aggregate", n_aggregate)
    g.add_node("node_report", n_report)
    g.set_entry_point("node_parse")
    g.add_edge("node_parse", "node_match")
    g.add_edge("node_match", "node_plan")
    g.add_conditional_edges("node_plan", route_loop, {"answer": "node_answer", "aggregate": "node_aggregate"})
    g.add_conditional_edges("node_answer", route_loop, {"answer": "node_answer", "aggregate": "node_aggregate"})
    g.add_edge("node_aggregate", "node_report")
    g.add_edge("node_report", END)
    return g.compile()


def run_with_langgraph(jd_text: str, resume_text: str, answers: list[str] | None = None,
                       run_id: str | None = None, max_questions: int | None = None,
                       preset_questions: list | None = None):
    """用 LangGraph 跑一遍（返回 (SessionReport, run_id)）。langgraph 缺失时抛 RuntimeError。"""
    ok, why = langgraph_available()
    if not ok:
        raise RuntimeError(f"langgraph 不可用: {why}")
    engine = CoachEngine(run_id=run_id, max_questions=max_questions)
    app = build_graph(engine, preset_questions=preset_questions)
    state = new_state(engine.run_id, jd_text, resume_text, answers)
    out = app.invoke(state)
    engine.transitions = [f"langgraph.{x}" for x in out.get("log", [])]
    return out["report"], engine.run_id
