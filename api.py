"""FastAPI 服务层：把工具、编排与会话暴露成 HTTP + SSE（本地运行，无需公网）。

两组接口：
  1) 无状态工具接口：/health /plan /score /report/{run_id} /stream  —— 原有接口，保持不变；
  2) Web 会话接口（前端用）：/api/session*  —— 上传简历→出题→逐题作答→汇总报告，
     状态落在 runs/<run_id>/web_state.json，浏览器刷新可接着答。
另外 / 直接返回 web/index.html（单文件前端，无构建步骤、无 CDN 依赖）。
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from coach import config
from coach.graph import CoachEngine
from coach.ingest import IngestError, text_from_bytes, text_from_path
from coach.session import SessionNotFound, WebSession
from coach.tools import load_bank

app = FastAPI(title="interview-coach-agent", version="0.3.0")


@app.middleware("http")
async def _auth_gate(request, call_next):
    """可选门禁：设了 COACH_API_TOKEN 就要求 Bearer Token（只挡 /api/* 与 /report/*）。

    /health 保持开放便于探活；首页不挡（没有数据的页面看到也没意义）。
    """
    token = config.api_token()
    path = request.url.path
    if token and (path.startswith("/api/") or path.startswith("/report/")):
        if request.headers.get("authorization", "") != f"Bearer {token}":
            return JSONResponse(
                {"detail": "未授权：需要 Authorization: Bearer <COACH_API_TOKEN>"}, status_code=401)
    return await call_next(request)
WEB_DIR = Path(__file__).resolve().parent / "web"
DEFAULT_JD = Path(__file__).resolve().parent / "data" / "jd_sample.md"


class PlanReq(BaseModel):
    jd_text: str
    resume_text: str
    k: int = 3


class ScoreReq(BaseModel):
    qid: str
    answer: str


class AnswerReq(BaseModel):
    answer: str


# ---------------- 前端页面 ----------------
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    page = WEB_DIR / "index.html"
    if not page.exists():
        raise HTTPException(status_code=500, detail="web/index.html 不存在")
    return FileResponse(page, media_type="text/html")


# ---------------- 原有工具接口 ----------------
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


# ---------------- Web 会话接口 ----------------
@app.post("/api/session")
async def create_session(resume: UploadFile = File(...),
                         jd: UploadFile | None = File(None),
                         jd_text: str = Form(""),
                         k: int = Form(5),
                         run_id: str = Form(""),
                         seed: str = Form("")) -> dict:
    """上传简历（必填）+ JD（可选，缺省用示例 JD），立刻解析并出题。"""
    k = max(1, min(int(k or 5), 10))
    try:
        resume_text = text_from_bytes(resume.filename or "resume", await resume.read())
    except IngestError as e:
        raise HTTPException(status_code=400, detail=f"简历解析失败：{e}") from e

    if jd is not None and jd.filename:
        try:
            jd_source = text_from_bytes(jd.filename, await jd.read())
            jd_name = jd.filename
        except IngestError as e:
            raise HTTPException(status_code=400, detail=f"JD 解析失败：{e}") from e
    elif (jd_text or "").strip():
        jd_source, jd_name = jd_text.strip(), "（粘贴的 JD）"
    else:
        jd_source, jd_name = text_from_path(DEFAULT_JD), f"示例 JD（{DEFAULT_JD.name}）"

    try:
        s = WebSession.start(resume_text=resume_text, jd_text=jd_source, k=k,
                             run_id=(run_id or None), resume_name=resume.filename or "", jd_name=jd_name,
                             seed=int(seed) if (seed or "").strip().isdigit() else None)
    except Exception as e:                      # 模型/解析异常也要给前端一句人话
        raise HTTPException(status_code=500, detail=f"出题失败：{type(e).__name__}: {e}") from e
    return s.state()


@app.get("/api/sessions")
def list_sessions(limit: int = 12) -> dict:
    """最近的 Web 场次摘要（首页"最近练过的几场"）——只给摘要，不含简历与作答原文。"""
    from coach.session import list_sessions as _ls
    return {"sessions": _ls(limit=max(1, min(int(limit), 50)))}


@app.get("/api/session/{run_id}")
def get_session(run_id: str) -> dict:
    try:
        return WebSession.load(run_id).state()
    except SessionNotFound as e:
        raise HTTPException(status_code=404, detail="会话不存在（可能已清理）") from e


@app.post("/api/session/{run_id}/answer")
def submit_answer(run_id: str, req: AnswerReq) -> dict:
    try:
        s = WebSession.load(run_id)
    except SessionNotFound as e:
        raise HTTPException(status_code=404, detail="会话不存在") from e
    try:
        return s.submit(req.answer)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/session/{run_id}/finish")
def finish_session(run_id: str) -> dict:
    try:
        s = WebSession.load(run_id)
    except SessionNotFound as e:
        raise HTTPException(status_code=404, detail="会话不存在") from e
    return s.finish()


@app.get("/api/session/{run_id}/report", response_class=PlainTextResponse)
def session_report_md(run_id: str) -> PlainTextResponse:
    """直接返回 Markdown 文本，方便前端渲染与复制。"""
    try:
        s = WebSession.load(run_id)
    except SessionNotFound as e:
        raise HTTPException(status_code=404, detail="会话不存在") from e
    if not s.finished:
        raise HTTPException(status_code=409, detail="本场还没结束，请先提交全部回答")
    md_path = Path(config.RESULTS_DIR) / f"report_{run_id}.md"
    if not md_path.exists():                 # 状态说结束了但文件不在：明确报错，不返回空内容
        raise HTTPException(status_code=404, detail="报告文件不存在，请重新 finish 一次")
    return PlainTextResponse(md_path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")


# ---------------- HITL：人工确认闸门 ----------------
class ReviewReq(BaseModel):
    approved: list[str] = []


@app.post("/api/review/{run_id}")
def review_pending(run_id: str, req: ReviewReq) -> dict:
    """把本场弱点画像写入跨场次记忆 —— 但只写 `approved` 里列出的要点。

    传空列表 = 全部驳回（这一场不进画像）。这是"Agent 提议、人来放行"里人放行的那个动作。
    """
    from coach import review as review_mod
    try:
        s = WebSession.load(run_id, use_llm=False)
    except SessionNotFound:
        raise HTTPException(status_code=404, detail=f"会话不存在: {run_id}")
    prop = review_mod.load_proposal(run_id)
    if prop is None:
        raise HTTPException(status_code=409, detail="本场没有待确认的提议（未开 HITL，或已经处理过）")
    if prop.get("applied"):
        return {"ok": True, "already_applied": True, "approved": prop.get("approved", [])}
    res = review_mod.commit(run_id, s.turns, req.approved)
    return {"ok": True, **res}


@app.get("/api/session/{run_id}/report.html", include_in_schema=False)
def report_html(run_id: str):
    """可打印的报告页：浏览器打开后 Ctrl+P 直接存 PDF（零依赖方案）。"""
    md = Path(config.RESULTS_DIR) / f"report_{run_id}.md"
    if not md.exists():
        raise HTTPException(status_code=404, detail=f"报告不存在: {run_id}（先 finish 一次）")
    from coach.tools.export_html import export_html
    return FileResponse(export_html(md), media_type="text/html; charset=utf-8")
