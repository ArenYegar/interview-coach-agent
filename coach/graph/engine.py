"""核心编排：plan → ask/score（含追问）→ aggregate → report。

设计原则（面试可讲）：
  1. 每一步都走 tracer.span，失败可定位、可回放；
  2. 每个工具都有确定性兜底，模型不可用时整条链路依然能跑完（降级而非崩溃）；
  3. 与框架解耦：langgraph_flow.py 只是同一流程的另一种表达。
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from .. import config
from ..llm import get_client
from ..schemas import (JDProfile, MatchResult, Question, ScoreCard, SessionReport, Turn,
                       WeaknessStat)
from ..tools import (aggregate_weakness, cross_session_tags, export_report, load_bank,
                     match_resume, parse_jd, plan_questions, record_session, score_answer,
                     search_bank)
from ..trace import Tracer

FOLLOWUP_TPL = "你在「{dim}」上少讲了：{points}。请补充具体做法、取舍和结果。"
STRONG = 0.7
WEAK = 0.4


@dataclass
class Plan:
    profile: JDProfile
    match: MatchResult
    questions: list[Question] = field(default_factory=list)


class CoachEngine:
    def __init__(self, run_id: str | None = None, max_questions: int | None = None,
                 categories: list[str] | None = None, use_llm: bool = True,
                 follow_up: bool = True, max_followup: int = 2) -> None:
        self.run_id = run_id or f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
        self.tracer = Tracer(self.run_id)
        self.max_questions = max_questions or config.MAX_QUESTIONS
        self.categories = categories
        self.follow_up = follow_up
        self.max_followup = max_followup
        self.followups_triggered = 0
        self.followups_improved = 0
        self.client = get_client(prefer_real=use_llm)
        self.model = getattr(self.client, "model", "unknown")
        self.degraded_reason = ""
        if hasattr(self.client, "probe"):          # 真实客户端才探测
            ok, why = self.client.probe()
            if not ok:
                self.degraded_reason = f"模型不可用（{why[:160]}）→ 本次运行使用规则打分"
                self.tracer.emit("llm.unavailable", ok=False, error=why)
            else:
                self.tracer.emit("llm.available", ok=True, model=self.model)
        elif config.offline():
            self.degraded_reason = "离线模式（未配置 API Key）→ 使用规则打分"
        self.degraded = bool(self.degraded_reason)
        self.bank = load_bank()            # 题库常驻内存（100 条）
        self.selector = None               # COACH_SELECTOR=hybrid 时启用稠密+BM25 混合检索
        if os.getenv("COACH_SELECTOR", "keyword").lower() == "hybrid":
            try:
                from ..retrieval import build_selector
                self.selector = build_selector(self.bank)
                self.tracer.emit("selector.ready", detail=f"mode={self.selector.mode}")
            except Exception as e:
                self.tracer.emit("selector.unavailable", ok=False, error=str(e)[:160])
        self.transitions: list[str] = []   # 状态迁移轨迹（供演示/汇报）
        self.degraded_steps: list[str] = []  # 降级过的步骤（评估时不能算作"成功"）

    # ---------- 1. 规划 ----------
    def plan(self, jd_text: str, resume_text: str) -> Plan:
        self._go("plan.start")
        with self.tracer.span("tool.parse_jd", jd_text[:60]):
            profile = parse_jd(jd_text, llm=self.client)
        if profile.source != "llm" and not self.degraded:
            self.degraded_steps.append("parse_jd")
            self.tracer.emit("tool.parse_jd.llm_failed", ok=False,
                             error=getattr(self.client, "last_error", "llm unavailable"))
        self._go(f"plan.jd_parsed(reqs={len(profile.requirements)},source={profile.source})")

        with self.tracer.span("tool.match_resume"):
            match = match_resume(resume_text, profile, llm=None)  # 覆盖率走确定性核心
        self._go(f"plan.matched(cover={match.score:.2f},missing={len(match.missing_keywords)})")

        with self.tracer.span("tool.plan_questions", f"k={self.max_questions}"):
            questions = plan_questions(profile, k=self.max_questions, bank=self.bank,
                                       categories=self.categories, selector=self.selector)
        self._go(f"plan.questions_picked({[q.qid for q in questions]})")
        return Plan(profile=profile, match=match, questions=questions)

    # ---------- 2. 打分（模型优先，失败即降级） ----------
    def score(self, q: Question, answer: str) -> ScoreCard:
        with self.tracer.span("tool.score_answer", q.qid) as span:
            card = score_answer(q, answer, llm=None if self.degraded else self.client)
            span.detail = f"{q.qid} total={card.total:.2f} method={card.method}"
        if card.method == "heuristic" and not self.degraded:
            self.degraded_steps.append(f"score_answer:{q.qid}")
            self.tracer.emit("tool.score_answer.llm_failed", ok=False, detail=q.qid,
                             error=getattr(self.client, "last_error", "llm unavailable"))
            self._go(f"score.degraded_to_heuristic({q.qid})")
        return card

    def _followup_text(self, q: Question, card: ScoreCard) -> str:
        """按最低分维度追问：把该维度没讲到的参考要点点出来，逼出具体做法与结果。"""
        dims = {d.name: d for d in q.rubric.dims}
        ranked = sorted(card.dim_scores.items(), key=lambda kv: kv[1]) if card.dim_scores else []
        dim = ranked[0][0] if ranked else "覆盖度"
        keys = [k for k in (dims[dim].keywords if dim in dims else []) if k in card.missing] or card.missing
        return FOLLOWUP_TPL.format(dim=dim, points="、".join(keys[:3]))

    # ---------- 3. 一场面试 ----------
    def run(self, jd_text: str, resume_text: str, answers: list[str] | None = None,
            answer_provider: Callable[[Question], str] | None = None) -> SessionReport:
        answers = list(answers or [])
        plan = self.plan(jd_text, resume_text)
        turns: list[Turn] = []
        ai = 0

        def take(answer_provider, qid: str, category: str, text: str, rubric):
            """取一条回答：先按顺序用 answers，再退回 provider；都没有则返回 None。"""
            nonlocal ai
            if ai < len(answers):
                a = answers[ai]; ai += 1
                return a
            if answer_provider is None:
                return None
            return answer_provider(Question(qid=qid, category=category, text=text, rubric=rubric))

        for q in plan.questions:
            ans = take(answer_provider, q.qid, q.category, q.text, q.rubric)
            if ans is None:
                self._go(f"ask.{q.qid}.skipped(no_answer)")
                break
            self._go(f"ask.{q.qid}")
            card = self.score(q, ans)
            turns.append(Turn(qid=q.qid, target_req_id=q.target_req_id, question=q.text,
                              answer=ans, scorecard=card))

            # 多轮深挖：低分且缺口 >=2 时按维度追问，最多 self.max_followup 层
            layer, cur = 0, card
            while (self.follow_up and layer < self.max_followup
                   and cur.total < WEAK and len(cur.missing) >= 2):
                layer += 1
                fq = self._followup_text(q, cur)
                fqid = f"{q.qid}-F{layer}"
                fans = take(answer_provider, fqid, q.category, fq, q.rubric)
                if fans is None or not fans.strip():
                    self._go(f"followup.{fqid}.skipped(no_answer)")
                    break
                self._go(f"followup.{fqid}")
                fcard = self.score(Question(qid=fqid, category=q.category, text=fq, rubric=q.rubric), fans)
                turns.append(Turn(qid=fqid, target_req_id=q.target_req_id, question=fq, answer=fans,
                                  scorecard=fcard, follow_up=True, followup_layer=layer))
                self.followups_triggered += 1
                if fcard.total > cur.total + 0.05:
                    self.followups_improved += 1
                cur = fcard

        self._go("aggregate")
        with self.tracer.span("tool.record_session"):
            n = record_session(self.run_id, turns)
        weaknesses = aggregate_weakness(self.run_id, top=5)
        history = cross_session_tags(run_id=self.run_id, top=5)

        report = self._build_report(plan, turns, weaknesses, history)
        with self.tracer.span("tool.export_report"):
            export_report(report)
        self._go(f"done(report={report.report_path})")
        return report

    # ---------- 4. 报告 ----------
    def _build_report(self, plan: Plan, turns: list[Turn], weaknesses: list[WeaknessStat],
                      history: list[WeaknessStat]) -> SessionReport:
        scored = [t.scorecard.total for t in turns if t.scorecard]
        overall = round(sum(scored) / len(scored), 4) if scored else 0.0
        highlights = [f"{t.qid} 答得较扎实（{t.scorecard.total:.0%}）"
                      for t in turns if t.scorecard and t.scorecard.total >= STRONG][:3]
        actions = [f"针对「{w.tag}」（出现 {w.count} 次）：先补一段可复述的具体做法与量化结果"
                   for w in weaknesses[:3]]
        if history:
            actions.append("跨场次反复出现的弱点：" + "、".join(w.tag for w in history[:3]))
        edits = []
        for kw in plan.match.missing_keywords[:3]:
            edits.append(f"简历里补上「{kw}」相关的具体动作与结果（岗位要求里有，但简历没体现）")
        weak_turns = [t for t in turns if t.scorecard and t.scorecard.verdict == "weak"]
        if weak_turns:
            edits.append(f"优先准备 {weak_turns[0].qid} 这类问题的答法（当前最弱）")
        return SessionReport(run_id=self.run_id, llm_status=(self.degraded_reason or "真实模型可用"),
                             degraded_steps=list(self.degraded_steps),
                             jd_title=plan.profile.title, match=plan.match,
                             turns=turns, weaknesses=weaknesses + history, overall=overall,
                             highlights=highlights or ["（暂无 >=70% 的题）"], actions=actions,
                             resume_edits=edits)

    def _go(self, event: str) -> None:
        self.transitions.append(event)
        self.tracer.emit(f"state.{event}")
