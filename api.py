"""FastAPI 服务层：把工具与编排暴露成 HTTP + SSE（本地运行，无需公网）。"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from coach import config
from coach.graph import CoachEngine
from coach.tools import load_bank

app = FastAPI(title="interview-coach-agent", version="0.1.0")


class PlanReq(BaseModel):
    jd_text: str
    resume_text: str
    k: int = 3


class ScoreReq(BaseModel):
    qid: str
    answer: str


@app.get("/health")
def health() -> dict:
    return {"ok": True, "offline": config.offline(), "model_chain": config.model_chain(),
            "bank_size": len(load_bank()), "results_dir": str(config.RESULTS_DIR)}


@app.post("/plan")
def plan(req: PlanReq) -> dict:
    eng = CoachEngine(max_questions=req.k)
    p = eng.plan(req.jd_text, req.resume_text)
    return {"run_id": eng.run_id, "profile": p.profile.model_dump(), "match": p.match.model_dump(),
            "questions": [q.model_dump() for q in p.questions]}


@app.post("/score")
def score(req: ScoreReq) -> dict:
    bank = {q.qid: q for q in load_bank()}
    q = bank.get(req.qid)
    if not q:
        raise HTTPException(status_code=404, detail=f"题库中没有 {req.qid}")
    return CoachEngine(use_llm=False).score(q, req.answer).model_dump()


@app.get("/report/{run_id}")
def report(run_id: str) -> dict:
    p = Path(config.RESULTS_DIR) / f"run_{run_id}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="报告不存在")
    return json.loads(p.read_text(encoding="utf-8"))


@app.get("/report/{run_id}/markdown")
def report_md(run_id: str) -> FileResponse:
    p = Path(config.RESULTS_DIR) / f"report_{run_id}.md"
    if not p.exists():
        raise HTTPException(status_code=404, detail="报告不存在")
    return FileResponse(p, media_type="text/markdown")


@app.get("/stream")
def stream(jd: str, resume: str, answers: str = "", k: int = 3) -> StreamingResponse:
    """SSE：一题一行推送（start → plan → question → score → done），过程可观测。"""
    ans = [a for a in answers.split("||") if a.strip()]
    eng = CoachEngine(max_questions=k)

    def gen():
        yield f"event: start\ndata: {json.dumps({'run_id': eng.run_id, 'model': eng.model}, ensure_ascii=False)}\n\n"
        plan_obj = eng.plan(jd, resume)
        yield ("event: plan\ndata: " + json.dumps(
            {"title": plan_obj.profile.title, "coverage": plan_obj.match.score,
             "questions": [q.model_dump() for q in plan_obj.questions]}, ensure_ascii=False) + "\n\n")
        for i, q in enumerate(plan_obj.questions):
            yield f"event: question\ndata: {json.dumps({'qid': q.qid, 'text': q.text}, ensure_ascii=False)}\n\n"
            if i >= len(ans):
                yield f"event: skip\ndata: {json.dumps({'qid': q.qid, 'reason': 'no_answer'}, ensure_ascii=False)}\n\n"
                continue
            card = eng.score(q, ans[i])
            yield f"event: score\ndata: {card.model_dump_json()}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
