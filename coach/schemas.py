"""Pydantic 契约层：所有工具的输入/输出都必须落在这里。

面试考点：结构化输出（structured output）+ 工具契约（tool contract）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class JDRequirement(BaseModel):
    """JD 中的单条要求。"""
    req_id: str = Field(description="要求编号，如 R1")
    text: str = Field(description="要求原文（可压缩）")
    must_have: bool = Field(default=True, description="是否硬性要求")
    keywords: list[str] = Field(default_factory=list, description="用于匹配的关键词")


class JDProfile(BaseModel):
    title: str = "未知岗位"
    company: str = "未知公司"
    requirements: list[JDRequirement] = Field(default_factory=list)
    digest: str = Field(default="", description="JD 摘要")
    source: Literal["llm", "heuristic"] = "heuristic"


class MatchItem(BaseModel):
    req_id: str
    requirement: str
    must_have: bool = True
    matched: bool
    evidence: str = Field(default="", description="简历中的证据句")
    gap: str = Field(default="", description="缺口说明")


class MatchResult(BaseModel):
    score: float = Field(ge=0.0, le=1.0, description="要求覆盖率（0~1）")
    items: list[MatchItem] = Field(default_factory=list)
    missing_keywords: list[str] = Field(default_factory=list)
    method: Literal["llm+heuristic", "heuristic"] = "heuristic"


class RubricDim(BaseModel):
    name: str
    weight: float = 1.0
    criteria: str = ""
    keywords: list[str] = Field(default_factory=list, description="参考要点关键词，用于离线打分")


class Rubric(BaseModel):
    dims: list[RubricDim] = Field(default_factory=list)


class Question(BaseModel):
    qid: str
    category: str = Field(description="项目深挖/技术基础/LLM应用/RAG/Agent/算法/行为")
    text: str
    target_req_id: str = ""
    difficulty: Literal["easy", "normal", "hard"] = "normal"
    rubric: Rubric = Field(default_factory=Rubric)
    skills: list[str] = Field(default_factory=list, description="题目考察的技能标签")
    source: Literal["bank", "llm", "bank+llm"] = "bank"


class ScoreCard(BaseModel):
    qid: str
    dim_scores: dict[str, float] = Field(default_factory=dict)
    total: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list, description="回答中命中的要点")
    missing: list[str] = Field(default_factory=list, description="缺失的要点")
    verdict: Literal["strong", "ok", "weak"] = "ok"
    improvement: str = ""
    method: Literal["llm", "heuristic"] = "heuristic"
    # 防护触发说明：为空表示没触发（注入防护见 coach/tools/score_answer.py 的 _guard_*）
    guard: str = ""
    # 重复采样：reps>1 表示这一题打了多次模型、取中位数（降低单次采样抖动）
    reps: int = 1
    sample_totals: list[float] = Field(default_factory=list)


class Turn(BaseModel):
    qid: str
    target_req_id: str = Field(default="", description="该题对应的 JD 要求编号")
    question: str
    answer: str
    scorecard: ScoreCard | None = None
    follow_up: bool = False
    followup_layer: int = 0


class WeaknessStat(BaseModel):
    tag: str
    count: int = 1
    severity: Literal["high", "mid", "low"] = "mid"
    last_seen: str = ""


class SessionReport(BaseModel):
    run_id: str
    llm_status: str = ""
    # 本场实际用的模型名（跨模型分数不可直接比较，所以每场必须记录"是谁评的"）
    llm_model: str = ""
    degraded_steps: list[str] = Field(default_factory=list)
    jd_title: str = ""
    match: MatchResult | None = None
    turns: list[Turn] = Field(default_factory=list)
    weaknesses: list[WeaknessStat] = Field(default_factory=list)
    overall: float = Field(default=0.0, ge=0.0, le=1.0)
    highlights: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    resume_edits: list[str] = Field(default_factory=list)
    report_path: str = ""
    # 本场成本与延迟汇总（llm_calls / *_tokens / llm_ms / wall_ms），来自 tracer.summary()
    cost: dict = Field(default_factory=dict)


class ReviewerCheck(BaseModel):
    """独立复核协议：只逐条判定参考要点是否被回答覆盖（不整体打分），用于算复核一致性。"""
    qid: str
    covered: list[str] = Field(default_factory=list, description="被覆盖的参考要点（原文照抄）")
    missing: list[str] = Field(default_factory=list, description="未覆盖的参考要点（原文照抄）")
    score: float = Field(ge=0.0, le=1.0, description="覆盖要点数 / 全部要点数")


class TraceEvent(BaseModel):
    ts: str
    run_id: str
    step: str
    ok: bool = True
    ms: float = 0.0
    model: str = ""
    detail: str = ""
    error: str = ""
    # 单步的 token 用量（由 tracer 从 LLM 客户端读增量写进来）。
    # 必须给默认值：旧 trace 文件里没有这几个字段，load_trace 还要能加载。
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # 这一步里实际发生的模型调用次数（重复采样时 >1）；旧 trace 没这个字段，记 0
    calls: int = 0
