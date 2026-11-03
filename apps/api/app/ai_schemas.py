from typing import Literal

from pydantic import BaseModel, Field


class ResearchFinding(BaseModel):
    statement: str = Field(min_length=1, max_length=500)
    evidence_summary: str = Field(min_length=1, max_length=1000)
    source_ids: list[int] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    classification: Literal["fact", "hypothesis"]


class ResearchOutput(BaseModel):
    findings: list[ResearchFinding] = Field(min_length=1, max_length=12)
    recommendations: list[str] = Field(default_factory=list, max_length=12)
    limitations: list[str] = Field(default_factory=list, max_length=12)


class StrategyPillar(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=1, max_length=500)
    example_topics: list[str] = Field(default_factory=list, max_length=6)


class StrategyOutput(BaseModel):
    audience: str = Field(min_length=1, max_length=500)
    message_pillars: list[StrategyPillar] = Field(min_length=1, max_length=8)
    content_angles: list[str] = Field(default_factory=list, max_length=12)
    call_to_action: str = Field(min_length=1, max_length=300)
    success_metrics: list[str] = Field(default_factory=list, max_length=8)
    cadence_suggestion: str = Field(min_length=1, max_length=300)
    source_ids: list[int] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list, max_length=8)


class CopyOutput(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    hook: str = Field(default="", max_length=500)
    body: str = Field(min_length=1, max_length=3000)
    call_to_action: str = Field(default="", max_length=300)
    hashtags: list[str] = Field(default_factory=list, max_length=15)
    source_ids: list[int] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list, max_length=8)


class CreativeConcept(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=800)
    visual_prompt: str = Field(min_length=1, max_length=1200)
    storyboard: list[str] = Field(default_factory=list, max_length=10)


class CreativeOutput(BaseModel):
    concepts: list[CreativeConcept] = Field(min_length=1, max_length=5)
    production_notes: list[str] = Field(default_factory=list, max_length=10)
    source_ids: list[int] = Field(default_factory=list)


class ReviewFinding(BaseModel):
    severity: Literal["blocking", "warning", "info"]
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)
    remediation: str = Field(default="", max_length=500)


class ReviewOutput(BaseModel):
    recommendation: Literal["pass", "revise"]
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=30)
    claims_requiring_sources: list[str] = Field(default_factory=list, max_length=12)
    limitations: list[str] = Field(default_factory=list, max_length=12)


class PlatformPreview(BaseModel):
    platform: str
    approximate: bool = True
    character_count: int
    character_limit: int
    within_limit: bool
    preview_text: str
    warnings: list[str] = Field(default_factory=list)
