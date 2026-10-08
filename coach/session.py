"""Web 会话：把"一场面试"拆成可以一问一答、可刷新恢复的步进会话。

为什么不直接用 engine.run()：run() 需要一次拿到全部回答（或一个阻塞式 provider），
而 Web/前端是"上传简历 → 出题 → 逐题作答 → 汇总报告"的交互流程，必须能停在中途。

设计：
  · 复用 CoachEngine 的 plan / score / _followup_text / _build_report，不重写打分与追问逻辑；
  · 状态落盘到 runs/<run_id>/web_state.json（runs/ 已 gitignore，含用户作答内容）；
  · 每一步都是幂等的"读状态→改状态→写状态"，所以浏览器刷新/重开也能接着答。

与 engine.run() 的一致性由 tests/test_web_session.py 断言（同样的题、同样的追问层数与得分）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import config
from .schemas import Question, ScoreCard, SessionReport, Turn
from .tools import aggregate_weakness, cross_session_tags, export_report, load_bank, record_session

STRONG = 0.7
WEAK = 0.4


class SessionNotFound(KeyError):
    pass


def state_path(run_id: str) -> Path:
    return Path(config.RUNS_DIR) / run_id / "web_state.json"


def list_sessions(limit: int = 12) -> list[dict]:
    """列出最近的 Web 场次（按更新时间倒序），供首页"最近练过的几场"使用。

    只读 runs/*/web_state.json 的摘要字段，不返回简历/作答原文。
    """
    runs = Path(config.RUNS_DIR)
    if not runs.exists():
        return []
    items: list[dict] = []
    for sp in runs.glob("*/web_state.json"):
        try:
            d = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            continue
        items.append({
            "run_id": d.get("run_id", sp.parent.name),
            "jd_title": d.get("jd_title", ""),
            "resume_name": d.get("resume_name", ""),
            "total_questions": len(d.get("questions", []) or []),
            "answered_main": len([t for t in d.get("turns", []) if not t.get("follow_up")]),
            "finished": bool(d.get("finished")),
            "llm_model": d.get("llm_model", ""),
            "updated_at": d.get("updated_at", ""),
        })
    items.sort(key=lambda x: x["updated_at"], reverse=True)
    return items[:limit]


class WebSession:
    # ---------- 创建 / 载入 ----------
    def __init__(self, engine, plan, jd_name: str = "", resume_name: str = "",
                 jd_text: str = "", resume_text: str = "") -> None:
        self.engine = engine
        self.plan = plan
        self.jd_name = jd_name
        self.resume_name = resume_name
        self.jd_text = jd_text
        self.resume_text = resume_text
        self.turns: list[Turn] = []
        self.qi = 0          # 当前主问题下标
        self.layer = 0       # 当前主问题的追问层数（0=主问题，1/2=深挖层）
        # 每个主问题"已经追问过的要点"：保证第二层追问不会重复第一层的问题（并随状态一起落盘）
        self.asked: dict[str, list[str]] = {}
        self.finished = False
        self.report: SessionReport | None = None

    @classmethod
    def start(cls, resume_text: str, jd_text: str, k: int = 5, run_id: str | None = None,
              resume_name: str = "", jd_name: str = "", use_llm: bool = True,
              variety: bool = True, avoid_repeat: bool = True,
              seed: int | None = None) -> "WebSession":
        """variety/avoid_repeat 默认 True：练习时不要每次都遇到同一批题（用户实测反馈）。

        注意：评测/回归场景应显式传 variety=False，保证同一输入得到同一套题。
        """
        from .graph import CoachEngine
        eng = CoachEngine(run_id=run_id, max_questions=k, use_llm=use_llm,
                          variety=variety, avoid_repeat=avoid_repeat, seed=seed)
        plan = eng.plan(jd_text, resume_text)
        s = cls(eng, plan, jd_name=jd_name, resume_name=resume_name,
                jd_text=jd_text, resume_text=resume_text)
        s.save()
        return s

    @classmethod
    def load(cls, run_id: str, use_llm: bool = True) -> "WebSession":
        p = state_path(run_id)
        if not p.exists():
            raise SessionNotFound(run_id)
        data = json.loads(p.read_text(encoding="utf-8"))
        from .graph import CoachEngine
        from .schemas import JDProfile, MatchResult
        eng = CoachEngine(run_id=run_id, max_questions=len(data["questions"] or []) or 1,
                          use_llm=use_llm, probe=False,     # probe=False：恢复会话不必再打一次网络探测
                          trace_append=True)                # 追加 trace：Web 每个请求都新建引擎，不能清空`
        from .graph.engine import Plan
        plan = Plan(profile=JDProfile(**data["profile"]), match=MatchResult(**data["match"]),
                    questions=[Question(**q) for q in data["questions"]])
        s = cls(eng, plan, jd_name=data.get("jd_name", ""), resume_name=data.get("resume_name", ""),
                jd_text=data.get("jd_text", ""), resume_text=data.get("resume_text", ""))
        s.turns = [Turn(**t) for t in data.get("turns", [])]
        s.asked = {k: list(v) for k, v in (data.get("asked") or {}).items()}
        s.qi = data.get("qi", 0)
        s.layer = data.get("layer", 0)
        s.finished = data.get("finished", False)
        if s.finished:                       # 已结束的场次：直接载入已有报告，避免重复汇总
            rp = Path(config.RESULTS_DIR) / f"run_{s.engine.run_id}.json"
            if rp.exists():
                s.report = SessionReport(**json.loads(rp.read_text(encoding="utf-8")))
        return s

    # ---------- 持久化 ----------
    def save(self) -> None:
        p = state_path(self.engine.run_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "run_id": self.engine.run_id,
            "jd_name": self.jd_name, "resume_name": self.resume_name,
            "jd_text": self.jd_text, "resume_text": self.resume_text,
            "jd_title": self.plan.profile.title,
            "profile": self.plan.profile.model_dump(),
            "match": self.plan.match.model_dump(),
            "questions": [q.model_dump() for q in self.plan.questions],
            "turns": [t.model_dump() for t in self.turns],
            "qi": self.qi, "layer": self.layer, "finished": self.finished,
            "asked": self.asked,
            "llm_model": self.engine.model, "llm_status": self.engine.degraded_reason or "真实模型可用",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- 视图 ----------
    def _q_dict(self, q: Question, follow_up: bool, base_qid: str | None = None) -> dict:
        """注意：qid 由调用方给全（追问题目形如 技术-01-F1），这里不再二次拼接后缀，
        否则会出现 技术-01-F1-F2 这种叠加 id。"""
        return {
            "qid": q.qid,
            "base_qid": base_qid or q.qid.split("-F")[0],
            "category": q.category, "difficulty": q.difficulty,
            "text": q.text, "target_req_id": q.target_req_id, "follow_up": follow_up,
            "layer": self.layer, "rubric": [{"name": d.name, "weight": d.weight,
                                            "keywords": list(d.keywords)} for d in q.rubric.dims],
        }

    def next_question(self) -> dict | None:
        """当前该答的题；追问由引擎按最低分维度即时生成。

        注意：若当前主问题"已无可追问的新要点"，这里会自动跳过它、推进到下一题
        （历史上曾出现"追问文案与上一层逐字相同"的问题；现在宁可不问也不重复问）。
        """
        if self.finished:
            return None
        while self.qi < len(self.plan.questions):
            q = self.plan.questions[self.qi]
            if self.layer == 0 or self._last_card(q.qid) is None:
                break
            text, _ = self.engine._followup_text(q, self._last_card(q.qid),
                                                 asked=set(self.asked.get(q.qid, [])), layer=self.layer)
            if text is not None:
                break
            self.qi += 1
            self.layer = 0
        if self.qi >= len(self.plan.questions):
            return None
        q = self.plan.questions[self.qi]
        if self.layer == 0:
            return self._q_dict(q, False)
        last = self._last_card(q.qid)
        if last is None:
            return self._q_dict(q, False)
        text, _points = self.engine._followup_text(q, last, asked=set(self.asked.get(q.qid, [])),
                                                   layer=self.layer)
        if text is None:                  # 没有新要点可问 → 这题就当答完了，不再追问
            return None
        fq = Question(qid=f"{q.qid}-F{self.layer}", category=q.category, text=text, rubric=q.rubric)
        return self._q_dict(fq, True, base_qid=q.qid)

    def _last_card(self, base_qid: str) -> ScoreCard | None:
        for t in reversed(self.turns):
            if t.qid.split("-F")[0] == base_qid and t.scorecard:
                return t.scorecard
        return None

    def state(self) -> dict:
        nxt = self.next_question()
        answered = [t for t in self.turns if not t.follow_up]
        return {
            "run_id": self.engine.run_id,
            "jd_title": self.plan.profile.title,
            "jd_name": self.jd_name, "resume_name": self.resume_name,
            "llm_model": self.engine.model, "llm_status": self.engine.degraded_reason or "真实模型可用",
            "requirements": [r.model_dump() for r in self.plan.profile.requirements],
            "match": self.plan.match.model_dump(),
            "total_questions": len(self.plan.questions),
            "answered_main": len(answered),
            "progress": round(len(answered) / max(1, len(self.plan.questions)), 3),
            "finished": self.finished,
            "next": nxt,
            "turns": [{"qid": t.qid, "question": t.question, "answer": t.answer,
                       "follow_up": t.follow_up, "layer": t.followup_layer,
                       "total": t.scorecard.total if t.scorecard else None,
                       "verdict": t.scorecard.verdict if t.scorecard else None,
                       "missing": t.scorecard.missing if t.scorecard else [],
                       "evidence": t.scorecard.evidence if t.scorecard else []} for t in self.turns],
        }

    # ---------- 一问一答 ----------
    def submit(self, answer: str) -> dict:
        if self.finished:
            raise ValueError("本场已经结束，请查看报告或另开一场")
        answer = (answer or "").strip()
        if not answer:
            raise ValueError("回答不能为空")
        nxt = self.next_question()
        if nxt is None:
            raise ValueError("已经没有待答的题了")
        base = self.plan.questions[self.qi]
        if nxt["follow_up"]:
            fqid = f"{base.qid}-F{self.layer}"
            # 记录这一层问的是哪些要点，下一层就不会再问同一批
            _txt, asked_points = self.engine._followup_text(
                base, self._last_card(base.qid), asked=set(self.asked.get(base.qid, [])), layer=self.layer)
            if asked_points:
                self.asked.setdefault(base.qid, [])
                self.asked[base.qid] = list(dict.fromkeys(self.asked[base.qid] + asked_points))
            card = self.engine.score(Question(qid=fqid, category=base.category, text=nxt["text"],
                                              rubric=base.rubric), answer)
            prev = self._last_card(base.qid)
            self.turns.append(Turn(qid=fqid, target_req_id=base.target_req_id, question=nxt["text"],
                                   answer=answer, scorecard=card, follow_up=True,
                                   followup_layer=self.layer))
            self.engine.followups_triggered += 1
            if prev is not None and card.total > prev.total + 0.05:
                self.engine.followups_improved += 1
        else:
            card = self.engine.score(base, answer)
            self.turns.append(Turn(qid=base.qid, target_req_id=base.target_req_id, question=base.text,
                                   answer=answer, scorecard=card))

        # 下一步：仍很弱、缺口 >=2、且"还有没问过的要点" → 再深挖一层；否则推进到下一题
        want_more = (self.engine.follow_up and card.total < WEAK and len(card.missing) >= 2
                     and self.layer < self.engine.max_followup)
        if want_more:
            nxt_text, _ = self.engine._followup_text(base, card, asked=set(self.asked.get(base.qid, [])),
                                                     layer=self.layer + 1)
            want_more = nxt_text is not None
        if want_more:
            self.layer += 1
        else:
            self.qi += 1
            self.layer = 0
        self.save()
        return {"scorecard": card.model_dump(), "next": self.next_question(), "state": self.state()}

    # ---------- 收尾 ----------
    def finish(self) -> dict:
        if self.finished and self.report is not None:
            return self._report_dict()
        self.engine._go("aggregate")
        record_session(self.engine.run_id, self.turns)
        weaknesses = aggregate_weakness(self.engine.run_id, top=5)
        history = cross_session_tags(run_id=self.engine.run_id, top=5)
        report = self.engine._build_report(self.plan, self.turns, weaknesses, history)
        # Web 路径每个 HTTP 请求都会新建引擎，进程内计数器跨不了请求（报告会显示 calls=0），
        # 所以这里改用"只读 trace 文件"的口径 —— 同一 run_id 的 trace 是追加写的。
        from .trace import cost_summary
        report.cost = cost_summary(self.engine.run_id)
        export_report(report)
        self.report = report
        self.finished = True
        self.save()
        return self._report_dict()

    def _report_dict(self) -> dict:
        r = self.report
        if r is None:
            raise ValueError("本场还没有生成报告，请先 finish()")
        return {
            "run_id": r.run_id, "jd_title": r.jd_title, "overall": r.overall,
            "llm_model": r.llm_model, "llm_status": r.llm_status,
            "match_score": r.match.score if r.match else None,
            "missing_keywords": r.match.missing_keywords if r.match else [],
            "highlights": r.highlights, "actions": r.actions, "resume_edits": r.resume_edits,
            "weaknesses": [{"tag": w.tag, "count": w.count, "severity": w.severity} for w in r.weaknesses],
            "turns": [{"qid": t.qid, "question": t.question, "answer": t.answer,
                       "follow_up": t.follow_up,
                       "total": t.scorecard.total if t.scorecard else None,
                       "verdict": t.scorecard.verdict if t.scorecard else None,
                       "missing": t.scorecard.missing if t.scorecard else [],
                       "evidence": t.scorecard.evidence if t.scorecard else []} for t in r.turns],
            "report_path": r.report_path,
            "markdown": Path(r.report_path).read_text(encoding="utf-8") if r.report_path and Path(r.report_path).exists() else "",
        }
