import hashlib
import csv
import io
import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from pydantic import AnyHttpUrl, BaseModel, EmailStr, Field, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fastapi.security import HTTPAuthorizationCredentials

from app.database import get_db
from app.ai_schemas import (
    CopyOutput,
    CreativeOutput,
    PlatformPreview,
    ResearchOutput,
    ReviewOutput,
    StrategyOutput,
)
from app.ai_credentials import decrypt_api_key, encrypt_api_key
from app.ai_provider import AIProviderError, create_named_provider
from app.ai_workflows import WorkflowExecutionError, execute_workflow
from app.models import (
    AIProviderConfiguration, AgentRun, AnalyticsSnapshot, Approval, AuditLog, BrandProfile, Campaign, CampaignStrategy, CommunityMessage,
    CommunityReply, ContentItem, ContentVersion, Experiment,
    LinkedInCredential, Membership, OAuthState, Organization, PublishJob, QualityReview,
    ResearchRecord, ResearchSource, SessionToken, SocialAccount, SocialAccountCapability,
    UsageEvent, User,
)
from app.domain import fingerprint_brand
from app.linkedin_oauth import (
    LINKEDIN_SCOPES,
    LinkedInConfigurationError,
    LinkedInRequestError,
    authorization_url,
    configuration_status,
    create_state,
    encrypt_token,
    exchange_code,
    fetch_profile,
    linkedin_publishing_enabled,
    linkedin_settings,
    normalize_linkedin_scopes,
)
from app.publishing import dispatch_job
from app.security import bearer, create_session, current_user, password_hash, require_role, workspace_member
from app.social_capabilities import (
    capability_catalog_response,
    capability_rows_for_account,
    has_enabled_capability,
    reconcile_linkedin_publish_capability,
)


app = FastAPI(title="AI Content Manager API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ANALYTICS_METRICS = {
    "impressions": {"label": "Impressions", "unit": "count", "aggregation": "sum", "definition": "Provider-reported impressions for the selected period; availability and counting vary by platform."},
    "reach": {"label": "Reach", "unit": "count", "aggregation": "not_comparable", "definition": "Provider-reported unique accounts reached; platform definitions and deduplication windows differ."},
    "clicks": {"label": "Clicks", "unit": "count", "aggregation": "sum", "definition": "Provider-reported clicks; click types and attribution rules are platform-specific."},
    "reactions": {"label": "Reactions", "unit": "count", "aggregation": "sum", "definition": "Provider-reported reactions or likes; reaction types differ across platforms."},
    "comments": {"label": "Comments", "unit": "count", "aggregation": "sum", "definition": "Provider-reported comments for the selected post and period."},
    "shares": {"label": "Shares", "unit": "count", "aggregation": "sum", "definition": "Provider-reported shares or reposts; platform semantics differ."},
    "engagement_rate": {"label": "Engagement rate", "unit": "percent", "aggregation": "not_comparable", "definition": "Provider-reported percentage. Formula, denominator, and attribution are platform-specific; do not compare across platforms."},
    "followers_gained": {"label": "Followers gained", "unit": "count", "aggregation": "not_comparable", "definition": "Provider-reported follower change; attribution to an individual post may not be available."},
}

AI_PROVIDER_CATALOG = [
    {"id": "gemini", "name": "Google Gemini", "model_discovery": True},
    {"id": "groq", "name": "Groq", "model_discovery": True},
    {"id": "openrouter", "name": "OpenRouter", "model_discovery": True},
    {"id": "mistral", "name": "Mistral AI", "model_discovery": True},
    {"id": "openai", "name": "OpenAI", "model_discovery": True},
]

class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    organization_name: str = Field(min_length=1, max_length=160)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class AIProviderConfigurationRequest(BaseModel):
    api_key: str | None = Field(default=None, max_length=8192)
    model: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    priority: int = Field(default=0, ge=0, le=20)

    @field_validator("model")
    @classmethod
    def trim_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A model identifier is required")
        return normalized


class BrandRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    value_proposition: str = ""
    audience: str = ""
    tone: str = ""
    preferred_vocabulary: list[str] = Field(default_factory=list)
    prohibited_claims: list[str] = Field(default_factory=list)


class CampaignRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    objective: str = ""
    audience: str = ""
    starts_at: str | None = None
    ends_at: str | None = None


class AccountRequest(BaseModel):
    platform: str = Field(pattern="^(linkedin|instagram|facebook|youtube|x|tiktok)$")
    display_name: str = Field(min_length=1, max_length=160)
    external_account_id: str = Field(min_length=1, max_length=160)
    is_mock: bool = True


class ContentRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    platform: str = Field(min_length=1, max_length=32)
    body: str = Field(min_length=1, max_length=3000)
    campaign_id: int | None = None
    media_manifest: list[dict[str, Any]] = Field(default_factory=list)
    source_references: list[int] = Field(default_factory=list, max_length=30)


class ResearchSourceInput(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    url: AnyHttpUrl
    source_type: str = Field(default="web", pattern="^(web|document|interview|internal)$")
    excerpt: str = Field(min_length=1, max_length=6000)

    @field_validator("url")
    @classmethod
    def reject_url_credentials(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.username or value.password:
            raise ValueError("Source URLs must not contain credentials")
        return value


class ResearchRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    campaign_id: int | None = None
    sources: list[ResearchSourceInput] = Field(default_factory=list, max_length=8)

    @field_validator("sources")
    @classmethod
    def limit_total_source_text(cls, sources: list[ResearchSourceInput]) -> list[ResearchSourceInput]:
        if sum(len(source.excerpt) for source in sources) > 12000:
            raise ValueError("Combined source excerpts must be 12,000 characters or fewer")
        return sources


class ResearchContextRequest(BaseModel):
    research_record_ids: list[int] = Field(default_factory=list, max_length=8)


class CopyRequest(ResearchContextRequest):
    campaign_id: int | None = None
    platform: str = Field(pattern="^(linkedin|instagram|facebook|youtube|x|tiktok)$")
    brief: str = Field(min_length=3, max_length=2000)


class CreativeRequest(ResearchContextRequest):
    brief: str = Field(min_length=3, max_length=2000)


class SubmissionRequest(BaseModel):
    social_account_id: int
    destination: str = Field(min_length=1, max_length=240)
    action: str = Field(default="publish", pattern="^publish$")


class DecisionRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected|changes_requested)$")
    comment: str = Field(default="", max_length=2000)


class ScheduleRequest(BaseModel):
    approval_id: int
    idempotency_key: str = Field(min_length=8, max_length=128)


class PublishReconciliationRequest(BaseModel):
    outcome: Literal["confirmed_published", "confirmed_not_published", "retry_after_rate_limit"]
    evidence_summary: str = Field(min_length=20, max_length=500)
    provider_post_id: str | None = Field(
        default=None,
        min_length=16,
        max_length=160,
        pattern=r"^urn:li:(share|ugcPost):[A-Za-z0-9_-]+$",
    )
    retry_confirmation: Literal[
        "I confirmed no LinkedIn post was created",
        "I waited for Retry-After and confirmed no LinkedIn post was created",
    ] | None = None

    @model_validator(mode="after")
    def validate_reconciliation_evidence(self):
        if self.outcome == "confirmed_published" and not self.provider_post_id:
            raise ValueError("A confirmed LinkedIn post ID is required")
        if self.outcome != "confirmed_published" and self.provider_post_id is not None:
            raise ValueError("A provider post ID is valid only for a confirmed published outcome")
        if self.outcome == "confirmed_not_published" and self.retry_confirmation is None:
            raise ValueError("Explicit no-post confirmation is required before retry")
        if self.outcome == "confirmed_not_published" and self.retry_confirmation != (
            "I confirmed no LinkedIn post was created"
        ):
            raise ValueError("Explicit no-post confirmation is required before retry")
        if self.outcome == "retry_after_rate_limit" and self.retry_confirmation != (
            "I waited for Retry-After and confirmed no LinkedIn post was created"
        ):
            raise ValueError("Explicit rate-limit wait and no-post confirmation is required")
        if self.outcome == "confirmed_published" and self.retry_confirmation is not None:
            raise ValueError("Retry confirmation is not valid for a published outcome")
        return self


class AnalyticsSnapshotRequest(BaseModel):
    social_account_id: int
    content_item_id: int | None = None
    campaign_id: int | None = None
    period_start: datetime
    period_end: datetime
    metrics: dict[str, int | float] = Field(default_factory=dict)
    unavailable_metrics: list[str] = Field(default_factory=list)
    source: Literal["manual_import", "mock"]
    source_reference: str = Field(min_length=8, max_length=160)
    source_note: str = Field(min_length=8, max_length=500)

    @field_validator("metrics", mode="before")
    @classmethod
    def validate_metric_values(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            raise ValueError("Metrics must be an object")
        for key, value in values.items():
            if key not in ANALYTICS_METRICS:
                raise ValueError(f"Unsupported analytics metric: {key}")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"Metric {key} must be a number")
            if value < 0 or value > 1_000_000_000_000_000:
                raise ValueError(f"Metric {key} must be between zero and 1 quadrillion")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"Metric {key} must be finite")
        return values

    @field_validator("unavailable_metrics")
    @classmethod
    def validate_unavailable_metrics(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values) or any(value not in ANALYTICS_METRICS for value in values):
            raise ValueError("Unavailable metrics must be unique supported metric names")
        return values

    @field_validator("period_start", "period_end")
    @classmethod
    def require_aware_dates(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamps must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_period(self):
        if self.period_start >= self.period_end:
            raise ValueError("period_start must be before period_end")
        if set(self.metrics).intersection(self.unavailable_metrics):
            raise ValueError("A metric cannot be both supplied and unavailable")
        return self


class ExperimentRequest(BaseModel):
    campaign_id: int | None = None
    platform: str | None = Field(default=None, pattern="^(linkedin|instagram|facebook|youtube|x|tiktok)$")
    name: str = Field(min_length=1, max_length=200)
    hypothesis: str = Field(min_length=3, max_length=2000)
    variant: str = Field(min_length=1, max_length=2000)
    primary_metric: str = Field(pattern="^(impressions|reach|clicks|reactions|comments|shares|engagement_rate|followers_gained)$")
    period_start: datetime
    period_end: datetime
    status: Literal["planned", "running", "completed", "paused"] = "planned"
    outcome: str = Field(default="", max_length=2000)
    next_action: str = Field(default="", max_length=2000)

    @field_validator("period_start", "period_end")
    @classmethod
    def require_aware_dates(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamps must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_period(self):
        if self.period_start >= self.period_end:
            raise ValueError("period_start must be before period_end")
        return self


class CommunityMessageRequest(BaseModel):
    social_account_id: int
    external_message_id: str = Field(min_length=1, max_length=160)
    conversation_id: str = Field(min_length=1, max_length=160)
    author_name: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=5000)
    source: Literal["manual_import", "mock"]
    received_at: datetime

    @field_validator("received_at")
    @classmethod
    def require_aware_received_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at must include a timezone")
        return value.astimezone(timezone.utc)


class CommunityAssignmentRequest(BaseModel):
    assigned_to: int | None


class CommunityStatusRequest(BaseModel):
    status: Literal["open", "resolved"]


class CommunityReplyRequest(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class CommunityReplyDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected", "changes_requested"]
    comment: str = Field(default="", max_length=2000)


def as_dict(model: Any, omit: tuple[str, ...] = ()) -> dict:
    return {column.name: getattr(model, column.name) for column in model.__table__.columns if column.name not in omit}


def audit(db: Session, organization_id: int, actor_id: int, action: str,
          resource_type: str, resource_id: int, details: dict | None = None) -> None:
    db.add(AuditLog(
        organization_id=organization_id,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        details=details or {},
    ))


def get_scoped_item(db: Session, organization_id: int, item_id: int) -> ContentItem:
    item = db.scalar(select(ContentItem).where(
        ContentItem.id == item_id, ContentItem.organization_id == organization_id
    ))
    if item is None:
        raise HTTPException(status_code=404, detail="Content not found")
    return item


def scoped_research_sources(db: Session, organization_id: int, record_ids: list[int]) -> list[ResearchSource]:
    unique_ids = list(dict.fromkeys(record_ids))
    if not unique_ids:
        return []
    records = db.scalars(select(ResearchRecord.id).where(
        ResearchRecord.organization_id == organization_id,
        ResearchRecord.id.in_(unique_ids),
    )).all()
    if set(records) != set(unique_ids):
        raise HTTPException(status_code=404, detail="Research record not found")
    return db.scalars(select(ResearchSource).where(
        ResearchSource.organization_id == organization_id,
        ResearchSource.research_record_id.in_(unique_ids),
    ).order_by(ResearchSource.id)).all()


def validate_source_ids(db: Session, organization_id: int, source_ids: list[int]) -> list[int]:
    unique_ids = list(dict.fromkeys(source_ids))
    if not unique_ids:
        return []
    rows = db.scalars(select(ResearchSource.id).where(
        ResearchSource.organization_id == organization_id,
        ResearchSource.id.in_(unique_ids),
    )).all()
    if set(rows) != set(unique_ids):
        raise HTTPException(status_code=404, detail="Research source not found")
    return unique_ids


def research_context(sources: list[ResearchSource]) -> list[dict[str, Any]]:
    return [{
        "id": source.id,
        "title": source.title,
        "url": source.url,
        "source_type": source.source_type,
        "excerpt": source.excerpt,
    } for source in sources]


def brand_context(brand: BrandProfile | None) -> dict[str, Any]:
    if brand is None:
        return {}
    return {
        "name": brand.name,
        "value_proposition": brand.value_proposition,
        "audience": brand.audience,
        "tone": brand.tone,
        "preferred_vocabulary": brand.preferred_vocabulary,
        "prohibited_claims": brand.prohibited_claims,
    }


def execute_ai(db: Session, organization_id: int, user_id: int, agent_name: str,
               schema: type[BaseModel], input_data: dict[str, Any],
               input_refs: dict[str, Any]) -> tuple[BaseModel, AgentRun]:
    try:
        return execute_workflow(
            db, organization_id, user_id, agent_name, schema, input_data, input_refs
        )
    except WorkflowExecutionError as error:
        db.commit()
        raise HTTPException(
            status_code=502,
            detail=f"AI workflow failed: {str(error)} (code: {error.code}); run {error.run.id} is available in Agent Activity.",
        ) from error


@app.get("/health/live")
def live() -> dict:
    return {"status": "ok"}


@app.get("/health/ready")
def ready(db: Session = Depends(get_db)) -> dict:
    db.execute(select(1))
    return {"status": "ready"}


@app.post("/api/v1/auth/register", status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> dict:
    email = payload.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(name=payload.name.strip(), email=email, password_hash=password_hash.hash(payload.password))
    db.add(user)
    db.flush()
    organization = Organization(name=payload.organization_name.strip())
    db.add(organization)
    db.flush()
    db.add(Membership(organization_id=organization.id, user_id=user.id, role="owner"))
    db.add(BrandProfile(organization_id=organization.id, name=organization.name))
    token = create_session(db, user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    return {"access_token": token, "token_type": "bearer", "user": {"id": user.id, "name": user.name, "email": user.email},
            "workspace": {"id": organization.id, "name": organization.name, "role": "owner"}}


@app.post("/api/v1/auth/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or not password_hash.verify(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email or password is incorrect")
    token = create_session(db, user)
    db.commit()
    memberships = db.scalars(select(Membership).where(Membership.user_id == user.id, Membership.active.is_(True))).all()
    workspaces = []
    for membership in memberships:
        workspace = db.get(Organization, membership.organization_id)
        if workspace is not None:
            workspaces.append({"id": workspace.id, "name": workspace.name, "role": membership.role})
    return {"access_token": token, "token_type": "bearer", "user": {"id": user.id, "name": user.name, "email": user.email},
            "workspaces": workspaces}


@app.get("/api/v1/auth/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    memberships = db.scalars(select(Membership).where(Membership.user_id == user.id, Membership.active.is_(True))).all()
    return {"user": {"id": user.id, "name": user.name, "email": user.email},
            "workspaces": [{"id": m.organization_id, "role": m.role, "name": db.get(Organization, m.organization_id).name}
                           for m in memberships]}


@app.delete("/api/v1/auth/session", status_code=204)
def logout(user: User = Depends(current_user),
           credentials: HTTPAuthorizationCredentials = Depends(bearer),
           db: Session = Depends(get_db)):
    from app.security import hash_token
    db.execute(delete(SessionToken).where(SessionToken.token_hash == hash_token(credentials.credentials)))
    db.commit()


@app.get("/api/v1/workspaces/{workspace_id}/members")
def list_members(workspace_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    workspace_member(db, user, workspace_id)
    memberships = db.scalars(select(Membership).where(Membership.organization_id == workspace_id)).all()
    return [{"user_id": m.user_id, "name": db.get(User, m.user_id).name, "email": db.get(User, m.user_id).email,
             "role": m.role, "active": m.active} for m in memberships]


class MembershipRequest(BaseModel):
    email: EmailStr
    role: str = Field(pattern="^(admin|strategist|creator|approver|analyst|viewer)$")


@app.post("/api/v1/workspaces/{workspace_id}/members", status_code=201)
def add_member(workspace_id: int, payload: MembershipRequest, user: User = Depends(current_user),
               db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin")
    invited_user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if invited_user is None:
        raise HTTPException(status_code=404, detail="User must register before being added to a workspace")
    existing = db.scalar(select(Membership).where(
        Membership.organization_id == workspace_id, Membership.user_id == invited_user.id
    ))
    if existing is not None:
        if existing.active:
            raise HTTPException(status_code=409, detail="User is already a workspace member")
        existing.active = True
        existing.role = payload.role
        member = existing
    else:
        member = Membership(organization_id=workspace_id, user_id=invited_user.id, role=payload.role)
        db.add(member)
        db.flush()
    audit(db, workspace_id, user.id, "membership.added", "membership", member.id, {"role": member.role})
    db.commit()
    return {"user_id": invited_user.id, "email": invited_user.email, "role": member.role, "active": member.active}


class RoleUpdateRequest(BaseModel):
    role: str = Field(pattern="^(admin|strategist|creator|approver|analyst|viewer)$")


@app.patch("/api/v1/workspaces/{workspace_id}/members/{member_user_id}")
def update_member(workspace_id: int, member_user_id: int, payload: RoleUpdateRequest,
                  user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin")
    target = db.scalar(select(Membership).where(
        Membership.organization_id == workspace_id, Membership.user_id == member_user_id
    ))
    if target is None:
        raise HTTPException(status_code=404, detail="Workspace member not found")
    if target.role == "owner":
        raise HTTPException(status_code=409, detail="The workspace owner role cannot be changed")
    target.role = payload.role
    audit(db, workspace_id, user.id, "membership.role_updated", "membership", target.id, {"role": target.role})
    db.commit()
    return {"user_id": target.user_id, "role": target.role}


@app.delete("/api/v1/workspaces/{workspace_id}/members/{member_user_id}", status_code=204)
def remove_member(workspace_id: int, member_user_id: int, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> None:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin")
    target = db.scalar(select(Membership).where(
        Membership.organization_id == workspace_id, Membership.user_id == member_user_id
    ))
    if target is None:
        raise HTTPException(status_code=404, detail="Workspace member not found")
    if target.role == "owner" or target.user_id == user.id:
        raise HTTPException(status_code=409, detail="The workspace owner cannot be removed or remove themselves")
    target.active = False
    audit(db, workspace_id, user.id, "membership.deactivated", "membership", target.id)
    db.commit()


@app.get("/api/v1/workspaces/{workspace_id}/brand")
def get_brand(workspace_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    workspace_member(db, user, workspace_id)
    brand = db.scalar(select(BrandProfile).where(BrandProfile.organization_id == workspace_id))
    return as_dict(brand)


@app.put("/api/v1/workspaces/{workspace_id}/brand")
def update_brand(workspace_id: int, payload: BrandRequest, user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "strategist")
    brand = db.scalar(select(BrandProfile).where(BrandProfile.organization_id == workspace_id))
    if brand is None:
        brand = BrandProfile(organization_id=workspace_id)
        db.add(brand)
    previous_fingerprint = fingerprint_brand(brand)
    for key, value in payload.model_dump().items():
        setattr(brand, key, value)
    if previous_fingerprint != fingerprint_brand(brand):
        from sqlalchemy import update
        db.execute(update(Approval).where(
            Approval.organization_id == workspace_id,
            Approval.status.in_(("pending", "approved")),
        ).values(status="invalidated", comment="Invalidated by brand guidance change"))
    audit(db, workspace_id, user.id, "brand.updated", "brand_profile", brand.id)
    db.commit()
    return as_dict(brand)


@app.get("/api/v1/workspaces/{workspace_id}/campaigns")
def list_campaigns(workspace_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    workspace_member(db, user, workspace_id)
    campaigns = db.scalars(select(Campaign).where(
        Campaign.organization_id == workspace_id
    ).order_by(Campaign.id.desc())).all()
    campaign_ids = [campaign.id for campaign in campaigns]
    strategies = db.scalars(select(CampaignStrategy).where(
        CampaignStrategy.organization_id == workspace_id,
        CampaignStrategy.campaign_id.in_(campaign_ids or [-1]),
    ).order_by(CampaignStrategy.created_at.desc(), CampaignStrategy.id.desc())).all()
    latest_strategies = {}
    for strategy in strategies:
        latest_strategies.setdefault(strategy.campaign_id, strategy)
    return [
        {
            **as_dict(campaign),
            "latest_strategy": as_dict(latest_strategies[campaign.id])
            if campaign.id in latest_strategies else None,
        }
        for campaign in campaigns
    ]


@app.post("/api/v1/workspaces/{workspace_id}/campaigns", status_code=201)
def create_campaign(workspace_id: int, payload: CampaignRequest, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "strategist", "creator")
    campaign = Campaign(organization_id=workspace_id, created_by=user.id, **payload.model_dump())
    db.add(campaign)
    db.flush()
    audit(db, workspace_id, user.id, "campaign.created", "campaign", campaign.id)
    db.commit()
    return as_dict(campaign)


@app.get("/api/v1/workspaces/{workspace_id}/social-accounts")
def list_accounts(workspace_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    workspace_member(db, user, workspace_id)
    accounts = db.scalars(select(SocialAccount).where(
        SocialAccount.organization_id == workspace_id
    ).order_by(SocialAccount.id)).all()
    capabilities_changed = False
    for account in accounts:
        if reconcile_linkedin_publish_capability(db, account):
            capabilities_changed = True
            audit(db, workspace_id, user.id, "linkedin.publish_capability_reconciled",
                  "social_account", account.id)
    if capabilities_changed:
        db.commit()
    return [
        {
            **as_dict(account),
            "capabilities": [
                as_dict(capability)
                for capability in sorted(account.capabilities, key=lambda row: row.capability)
            ],
        }
        for account in accounts
    ]


@app.get("/api/v1/workspaces/{workspace_id}/integrations/capabilities")
def list_platform_capabilities(workspace_id: int, user: User = Depends(current_user),
                               db: Session = Depends(get_db)) -> list[dict]:
    workspace_member(db, user, workspace_id)
    return capability_catalog_response()


@app.post("/api/v1/workspaces/{workspace_id}/integrations/linkedin/connect")
def start_linkedin_connection(workspace_id: int, user: User = Depends(current_user),
                              db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin")
    try:
        settings = linkedin_settings()
    except LinkedInConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    raw_state, state_hash, expires_at = create_state()
    now = datetime.now(timezone.utc)
    db.execute(delete(OAuthState).where(
        (OAuthState.expires_at <= now)
        | (OAuthState.consumed_at.is_not(None) & (OAuthState.consumed_at <= now - timedelta(days=1)))
    ))
    db.add(OAuthState(
        state_hash=state_hash,
        provider="linkedin",
        organization_id=workspace_id,
        actor_id=user.id,
        expires_at=expires_at,
    ))
    audit(db, workspace_id, user.id, "linkedin.oauth_started", "integration", "linkedin")
    db.commit()
    return {
        "authorization_url": authorization_url(settings, raw_state),
        "expires_in_seconds": 600,
        "requested_scopes": list(LINKEDIN_SCOPES),
    }


@app.get("/api/v1/integrations/linkedin/callback")
def linkedin_callback(
    code: str | None = Query(default=None, max_length=4096),
    state: str | None = Query(default=None, max_length=256),
    error: str | None = Query(default=None, max_length=120),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if not state:
        raise HTTPException(status_code=400, detail="LinkedIn connection state is missing")
    try:
        settings = linkedin_settings()
    except LinkedInConfigurationError as configuration_error:
        raise HTTPException(status_code=503, detail=str(configuration_error)) from configuration_error
    now = datetime.now(timezone.utc)
    oauth_state = db.scalar(select(OAuthState).where(
        OAuthState.provider == "linkedin",
        OAuthState.state_hash == hashlib.sha256(state.encode()).hexdigest(),
        OAuthState.consumed_at.is_(None),
        OAuthState.expires_at > now,
    ).with_for_update())
    if oauth_state is None:
        raise HTTPException(status_code=400, detail="LinkedIn connection state is invalid or expired")
    oauth_state.consumed_at = now
    membership = db.scalar(select(Membership).where(
        Membership.organization_id == oauth_state.organization_id,
        Membership.user_id == oauth_state.actor_id,
        Membership.active.is_(True),
        Membership.role.in_(("owner", "admin")),
    ))
    if membership is None:
        audit(db, oauth_state.organization_id, oauth_state.actor_id,
              "linkedin.oauth_rejected", "integration", "linkedin",
              {"reason": "initiating_membership_inactive"})
        db.commit()
        return linkedin_result_redirect("connection_failed")
    if error == "user_cancelled_login":
        audit(db, oauth_state.organization_id, oauth_state.actor_id,
              "linkedin.oauth_cancelled", "integration", "linkedin",
              {"reason": "user_cancelled_login"})
        db.commit()
        return linkedin_result_redirect("cancelled")
    if error == "user_cancelled_authorize":
        audit(db, oauth_state.organization_id, oauth_state.actor_id,
              "linkedin.oauth_permission_declined", "integration", "linkedin",
              {"reason": "user_cancelled_authorize"})
        db.commit()
        return linkedin_result_redirect("permission_denied")
    if error:
        audit(db, oauth_state.organization_id, oauth_state.actor_id,
              "linkedin.oauth_failed", "integration", "linkedin",
              {"reason": "provider_oauth_error"})
        db.commit()
        return linkedin_result_redirect("connection_failed")
    if not code:
        audit(db, oauth_state.organization_id, oauth_state.actor_id,
              "linkedin.oauth_failed", "integration", "linkedin",
              {"reason": "authorization_code_missing"})
        db.commit()
        return linkedin_result_redirect("connection_failed")
    db.commit()

    try:
        token = exchange_code(settings, code)
        profile = fetch_profile(token.access_token)
    except LinkedInRequestError as request_error:
        audit(db, oauth_state.organization_id, oauth_state.actor_id,
              "linkedin.oauth_failed", "integration", "linkedin",
              {"reason": str(request_error)[:160]})
        db.commit()
        return linkedin_result_redirect("connection_failed")

    external_account_id = f"urn:li:person:{profile['subject']}"
    account = db.scalar(select(SocialAccount).where(
        SocialAccount.organization_id == oauth_state.organization_id,
        SocialAccount.platform == "linkedin",
        SocialAccount.external_account_id == external_account_id,
    ))
    if account is None:
        account = SocialAccount(
            organization_id=oauth_state.organization_id,
            platform="linkedin",
            display_name=profile["name"],
            external_account_id=external_account_id,
            is_mock=False,
            active=True,
        )
        db.add(account)
        db.flush()
    else:
        account.display_name = profile["name"]
        account.is_mock = False
        account.active = True

    granted_scopes = normalize_linkedin_scopes(token.scopes)
    credential = db.scalar(select(LinkedInCredential).where(
        LinkedInCredential.organization_id == oauth_state.organization_id,
        LinkedInCredential.social_account_id == account.id,
    ))
    if credential is None:
        credential = LinkedInCredential(
            organization_id=oauth_state.organization_id,
            social_account_id=account.id,
            connected_by=oauth_state.actor_id,
            access_token_ciphertext=encrypt_token(token.access_token, settings.encryption_key),
            refresh_token_ciphertext=(
                encrypt_token(token.refresh_token, settings.encryption_key)
                if token.refresh_token else ""
            ),
            granted_scopes=granted_scopes,
            access_token_expires_at=token.expires_at,
            refresh_token_expires_at=token.refresh_expires_at,
        )
        db.add(credential)
    else:
        credential.connected_by = oauth_state.actor_id
        credential.access_token_ciphertext = encrypt_token(token.access_token, settings.encryption_key)
        if token.refresh_token:
            credential.refresh_token_ciphertext = encrypt_token(token.refresh_token, settings.encryption_key)
        credential.granted_scopes = granted_scopes
        credential.access_token_expires_at = token.expires_at
        if token.refresh_expires_at:
            credential.refresh_token_expires_at = token.refresh_expires_at

    capabilities = db.scalars(select(SocialAccountCapability).where(
        SocialAccountCapability.organization_id == oauth_state.organization_id,
        SocialAccountCapability.social_account_id == account.id,
    )).all()
    by_name = {capability.capability: capability for capability in capabilities}
    for capability in capability_rows_for_account(account):
        if capability.capability not in by_name:
            db.add(capability)
            by_name[capability.capability] = capability
    oauth_capability = by_name["oauth"]
    oauth_capability.status = "connected"
    oauth_capability.enabled = True
    oauth_capability.reason = "LinkedIn OAuth connection is active."
    oauth_capability.required_permissions = ["openid", "profile"]
    publish_capability = by_name["publish"]
    has_publish_permission = "w_member_social" in granted_scopes
    publishing_enabled = linkedin_publishing_enabled()
    publish_capability.status = (
        "available" if has_publish_permission and publishing_enabled
        else "disabled_by_operator" if has_publish_permission
        else "missing_permission"
    )
    publish_capability.enabled = has_publish_permission and publishing_enabled
    publish_capability.reason = (
        "Member publishing is enabled for approved text-only posts."
        if has_publish_permission and publishing_enabled
        else "Set PUBLISHING_ENABLED=true to enable LinkedIn publishing."
        if has_publish_permission
        else "Request the w_member_social permission in the LinkedIn Developer Portal."
    )
    publish_capability.required_permissions = ["w_member_social"]
    for name in ("analytics", "community"):
        capability = by_name[name]
        capability.status = "not_implemented"
        capability.enabled = False
        capability.reason = "This LinkedIn API workflow is not implemented."

    audit(db, oauth_state.organization_id, oauth_state.actor_id,
          "linkedin.connected", "social_account", account.id,
          {"scopes": granted_scopes, "publish_enabled": publish_capability.enabled})
    db.commit()
    return linkedin_result_redirect("connected")


@app.delete("/api/v1/workspaces/{workspace_id}/social-accounts/{account_id}/connection")
def disconnect_social_account(workspace_id: int, account_id: int, user: User = Depends(current_user),
                              db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin")
    account = db.scalar(select(SocialAccount).where(
        SocialAccount.id == account_id,
        SocialAccount.organization_id == workspace_id,
    ))
    if account is None:
        raise HTTPException(status_code=404, detail="Social account not found")
    if account.is_mock:
        raise HTTPException(status_code=409, detail="Mock accounts do not have an OAuth connection")
    if account.platform != "linkedin":
        raise HTTPException(status_code=409, detail="Disconnect is not implemented for this provider")
    credential = db.scalar(select(LinkedInCredential).where(
        LinkedInCredential.organization_id == workspace_id,
        LinkedInCredential.social_account_id == account.id,
    ))
    if credential is not None:
        db.delete(credential)
    account.active = False
    for capability in account.capabilities:
        capability.enabled = False
        capability.status = "disconnected"
        capability.reason = "LinkedIn connection was disconnected."
    audit(db, workspace_id, user.id, "linkedin.disconnected", "social_account", account.id)
    db.commit()
    return {"status": "disconnected"}


def linkedin_result_redirect(status: str) -> RedirectResponse:
    web_base_url = os.getenv("WEB_BASE_URL", "http://localhost:3000").rstrip("/")
    return RedirectResponse(
        f"{web_base_url}/?{urlencode({'linkedin': status})}",
        status_code=303,
    )


@app.post("/api/v1/workspaces/{workspace_id}/social-accounts", status_code=201)
def create_account(workspace_id: int, payload: AccountRequest, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin")
    if not payload.is_mock:
        raise HTTPException(status_code=400, detail="Real social integrations are not enabled; use the mock adapter")
    account = SocialAccount(organization_id=workspace_id, **payload.model_dump())
    db.add(account)
    db.flush()
    capabilities = capability_rows_for_account(account)
    db.add_all(capabilities)
    audit(db, workspace_id, user.id, "social_account.mock_created", "social_account", account.id)
    db.commit()
    return {
        **as_dict(account),
        "capabilities": [
            as_dict(capability)
            for capability in sorted(capabilities, key=lambda row: row.capability)
        ],
    }


@app.get("/api/v1/workspaces/{workspace_id}/content")
def list_content(workspace_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    workspace_member(db, user, workspace_id)
    items = db.scalars(select(ContentItem).where(ContentItem.organization_id == workspace_id).order_by(ContentItem.id.desc())).all()
    result = []
    for item in items:
        version = db.get(ContentVersion, item.current_version_id) if item.current_version_id else None
        result.append({**as_dict(item), "version": as_dict(version) if version else None})
    return result


@app.post("/api/v1/workspaces/{workspace_id}/content", status_code=201)
def create_content(workspace_id: int, payload: ContentRequest, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "strategist", "creator")
    if payload.campaign_id and db.scalar(select(Campaign).where(
        Campaign.id == payload.campaign_id, Campaign.organization_id == workspace_id
    )) is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    source_references = validate_source_ids(db, workspace_id, payload.source_references)
    item = ContentItem(organization_id=workspace_id, campaign_id=payload.campaign_id, title=payload.title,
                       platform=payload.platform.lower(), created_by=user.id)
    db.add(item)
    db.flush()
    version = make_version(db, item, payload.body, payload.media_manifest, user.id, source_references)
    item.current_version_id = version.id
    audit(db, workspace_id, user.id, "content.created", "content_item", item.id)
    db.commit()
    return {**as_dict(item), "version": as_dict(version)}


def make_version(db: Session, item: ContentItem, body: str, media_manifest: list, user_id: int,
                 source_references: list[int] | None = None) -> ContentVersion:
    previous = db.scalar(select(ContentVersion).where(ContentVersion.content_item_id == item.id)
                         .order_by(ContentVersion.number.desc()))
    number = 1 if previous is None else previous.number + 1
    source_references = source_references or []
    fingerprint = hashlib.sha256(json.dumps({
        "body": body,
        "media": media_manifest,
        "source_references": source_references,
    },
                                               sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    version = ContentVersion(content_item_id=item.id, number=number, body=body,
                             media_manifest=media_manifest, source_references=source_references,
                             fingerprint=fingerprint, created_by=user_id)
    db.add(version)
    db.flush()
    item.current_version_id = version.id
    from sqlalchemy import update
    db.execute(update(Approval).where(
        Approval.content_item_id == item.id, Approval.status.in_(("pending", "approved"))
    ).values(status="invalidated", comment="Invalidated by a new content version"))
    return version


@app.post("/api/v1/workspaces/{workspace_id}/content/{item_id}/versions", status_code=201)
def create_version(workspace_id: int, item_id: int, payload: ContentRequest, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "strategist", "creator")
    item = get_scoped_item(db, workspace_id, item_id)
    if payload.campaign_id and db.scalar(select(Campaign).where(
        Campaign.id == payload.campaign_id, Campaign.organization_id == workspace_id
    )) is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    source_references = validate_source_ids(db, workspace_id, payload.source_references)
    item.title = payload.title
    item.platform = payload.platform.lower()
    item.campaign_id = payload.campaign_id
    version = make_version(db, item, payload.body, payload.media_manifest, user.id, source_references)
    audit(db, workspace_id, user.id, "content.version_created", "content_item", item.id, {"version": version.number})
    db.commit()
    return {**as_dict(item), "version": as_dict(version)}


@app.get("/api/v1/workspaces/{workspace_id}/content/{item_id}/versions")
def list_versions(workspace_id: int, item_id: int, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> list[dict]:
    workspace_member(db, user, workspace_id)
    item = get_scoped_item(db, workspace_id, item_id)
    return [as_dict(v) for v in db.scalars(select(ContentVersion).where(
        ContentVersion.content_item_id == item.id
    ).order_by(ContentVersion.number.desc()))]


@app.post("/api/v1/workspaces/{workspace_id}/content/{item_id}/quality-check")
def quality_check(workspace_id: int, item_id: int, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "strategist", "creator")
    item = get_scoped_item(db, workspace_id, item_id)
    version = db.get(ContentVersion, item.current_version_id)
    brand = db.scalar(select(BrandProfile).where(BrandProfile.organization_id == workspace_id))
    findings = []
    if not version.body.strip():
        findings.append({"severity": "blocking", "code": "empty_body", "message": "Content body is required."})
    if len(version.body) > 3000:
        findings.append({"severity": "blocking", "code": "body_too_long", "message": "Content exceeds 3,000 characters."})
    for claim in (brand.prohibited_claims if brand else []):
        if claim and claim.casefold() in version.body.casefold():
            findings.append({"severity": "blocking", "code": "prohibited_claim", "message": f"Contains configured prohibited claim: {claim}"})
    findings.append({"severity": "warning", "code": "human_fact_check", "message": "Verify factual claims and media rights before approval."})
    result = QualityReview(organization_id=workspace_id, content_version_id=version.id,
                           brand_fingerprint=fingerprint_brand(brand),
                           review_type="deterministic",
                           passed=not any(f["severity"] == "blocking" for f in findings), findings=findings)
    db.add(result)
    audit(db, workspace_id, user.id, "quality.checked", "content_version", version.id, {"passed": result.passed})
    db.commit()
    return as_dict(result)


@app.post("/api/v1/workspaces/{workspace_id}/content/{item_id}/submit-for-approval", status_code=201)
def submit_approval(workspace_id: int, item_id: int, payload: SubmissionRequest,
                    user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "strategist", "creator")
    item = get_scoped_item(db, workspace_id, item_id)
    version = db.get(ContentVersion, item.current_version_id)
    account = db.scalar(select(SocialAccount).where(
        SocialAccount.id == payload.social_account_id,
        SocialAccount.organization_id == workspace_id,
        SocialAccount.active.is_(True),
        SocialAccount.platform == item.platform,
    ))
    if account is None:
        raise HTTPException(status_code=404, detail="Active connected account for this platform not found")
    if account.is_mock is not True and reconcile_linkedin_publish_capability(db, account):
        audit(db, workspace_id, user.id, "linkedin.publish_capability_reconciled",
              "social_account", account.id)
        db.commit()
    if not has_enabled_capability(db, account, "publish"):
        raise HTTPException(status_code=409, detail="The selected account does not have publish permission")
    if account.is_mock is not True:
        if account.platform != "linkedin" or payload.destination != account.external_account_id:
            raise HTTPException(status_code=409, detail="LinkedIn approval destination must be the connected member")
        credential = db.scalar(select(LinkedInCredential).where(
            LinkedInCredential.organization_id == workspace_id,
            LinkedInCredential.social_account_id == account.id,
        ))
        if credential is None or "w_member_social" not in normalize_linkedin_scopes(credential.granted_scopes):
            raise HTTPException(status_code=409, detail="LinkedIn publishing requires the w_member_social permission")
    review = db.scalar(select(QualityReview).where(
        QualityReview.organization_id == workspace_id,
        QualityReview.content_version_id == version.id,
        QualityReview.review_type == "deterministic",
    ).order_by(QualityReview.id.desc()))
    if review is None or not review.passed:
        raise HTTPException(status_code=409, detail="Run a passing quality review before requesting approval")
    brand = db.scalar(select(BrandProfile).where(BrandProfile.organization_id == workspace_id))
    if review.brand_fingerprint != fingerprint_brand(brand):
        raise HTTPException(status_code=409, detail="Brand guidance changed; rerun the quality review")
    ai_review = db.scalar(select(QualityReview).where(
        QualityReview.organization_id == workspace_id,
        QualityReview.content_version_id == version.id,
        QualityReview.review_type == "ai",
    ).order_by(QualityReview.id.desc()))
    if ai_review is not None and (
        not ai_review.passed or ai_review.brand_fingerprint != fingerprint_brand(brand)
    ):
        raise HTTPException(status_code=409, detail="The latest AI review requires changes or is out of date")
    approval = Approval(organization_id=workspace_id, content_item_id=item.id, content_version_id=version.id,
                        version_fingerprint=version.fingerprint, brand_fingerprint=review.brand_fingerprint,
                        social_account_id=account.id,
                        destination=payload.destination.strip(), action=payload.action, requested_by=user.id)
    db.add(approval)
    db.flush()
    audit(db, workspace_id, user.id, "approval.requested", "approval", approval.id,
          {"version_id": version.id, "account_id": account.id, "destination": approval.destination, "action": approval.action})
    db.commit()
    return as_dict(approval)


@app.get("/api/v1/workspaces/{workspace_id}/approvals")
def list_approvals(workspace_id: int, status: str | None = None, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> list[dict]:
    workspace_member(db, user, workspace_id)
    query = select(Approval).where(Approval.organization_id == workspace_id).order_by(Approval.id.desc())
    if status:
        query = query.where(Approval.status == status)
    approvals = db.scalars(query).all()
    result = []
    for approval in approvals:
        item = db.get(ContentItem, approval.content_item_id)
        version = db.get(ContentVersion, approval.content_version_id)
        account = db.get(SocialAccount, approval.social_account_id)
        reviews = db.scalars(select(QualityReview).where(
            QualityReview.content_version_id == approval.content_version_id,
            QualityReview.organization_id == workspace_id,
        ).order_by(QualityReview.id.desc())).all()
        latest_reviews = {}
        for review in reviews:
            latest_reviews.setdefault(review.review_type, review)
        source_rows = []
        if version and version.source_references:
            source_rows = db.scalars(select(ResearchSource).where(
                ResearchSource.organization_id == workspace_id,
                ResearchSource.id.in_(version.source_references),
            )).all()
        result.append({
            **as_dict(approval),
            "content_title": item.title if item else "",
            "content_body": version.body if version else "",
            "version_number": version.number if version else None,
            "platform": item.platform if item else "",
            "account_name": account.display_name if account else "",
            "account_is_mock": account.is_mock if account else True,
            "source_references": [as_dict(source) for source in source_rows],
            "quality_findings": [
                {"review_type": review.review_type, **finding}
                for review in latest_reviews.values()
                for finding in review.findings
            ],
        })
    return result


@app.post("/api/v1/workspaces/{workspace_id}/approvals/{approval_id}/decision")
def decide_approval(workspace_id: int, approval_id: int, payload: DecisionRequest,
                    user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "approver")
    approval = db.scalar(select(Approval).where(
        Approval.id == approval_id, Approval.organization_id == workspace_id
    ).with_for_update())
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=409, detail="Approval is no longer pending")
    if approval.requested_by == user.id:
        raise HTTPException(status_code=403, detail="Authors cannot approve their own content")
    item = get_scoped_item(db, workspace_id, approval.content_item_id)
    version = db.get(ContentVersion, approval.content_version_id)
    if item.current_version_id != version.id or version.fingerprint != approval.version_fingerprint:
        approval.status = "invalidated"
        db.commit()
        raise HTTPException(status_code=409, detail="Approval was invalidated by a content edit")
    brand = db.scalar(select(BrandProfile).where(BrandProfile.organization_id == workspace_id))
    if fingerprint_brand(brand) != approval.brand_fingerprint:
        approval.status = "invalidated"
        db.commit()
        raise HTTPException(status_code=409, detail="Approval was invalidated by a brand guidance change")
    approval.status = payload.decision
    approval.decided_by = user.id
    approval.comment = payload.comment
    approval.decided_at = datetime.now(timezone.utc)
    audit(db, workspace_id, user.id, f"approval.{payload.decision}", "approval", approval.id)
    db.commit()
    return as_dict(approval)


@app.post("/api/v1/workspaces/{workspace_id}/publish-jobs", status_code=201)
def schedule_publish(workspace_id: int, payload: ScheduleRequest, user: User = Depends(current_user),
                     db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "strategist", "creator", "approver")
    approval = db.scalar(select(Approval).where(
        Approval.id == payload.approval_id,
        Approval.organization_id == workspace_id,
        Approval.status == "approved",
        Approval.action == "publish",
    ))
    if approval is None:
        raise HTTPException(status_code=409, detail="A valid human approval is required to schedule publishing")
    item = get_scoped_item(db, workspace_id, approval.content_item_id)
    version = db.get(ContentVersion, approval.content_version_id)
    if item.current_version_id != version.id or approval.version_fingerprint != version.fingerprint:
        raise HTTPException(status_code=409, detail="Approval was invalidated by a content edit")
    job = db.scalar(select(PublishJob).where(
        PublishJob.organization_id == workspace_id,
        PublishJob.idempotency_key == payload.idempotency_key,
    ))
    if job is not None and job.approval_id != approval.id:
        raise HTTPException(status_code=409, detail="Idempotency key is already associated with another approval")
    if job is None:
        job = PublishJob(organization_id=workspace_id, approval_id=approval.id,
                         idempotency_key=payload.idempotency_key, status="queued")
        db.add(job)
        db.flush()
        audit(db, workspace_id, user.id, "publish.queued", "publish_job", job.id, {"approval_id": approval.id})
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            job = db.scalar(select(PublishJob).where(
                PublishJob.organization_id == workspace_id,
                PublishJob.idempotency_key == payload.idempotency_key,
            ))
            if job is None or job.approval_id != approval.id:
                raise HTTPException(status_code=409, detail="Idempotency key is already associated with another approval")
    return as_dict(job)


@app.get("/api/v1/workspaces/{workspace_id}/publish-jobs")
def list_publish_jobs(workspace_id: int, user: User = Depends(current_user),
                      db: Session = Depends(get_db)) -> list[dict]:
    workspace_member(db, user, workspace_id)
    jobs = db.scalars(select(PublishJob).where(
        PublishJob.organization_id == workspace_id
    ).order_by(PublishJob.id.desc())).all()
    result = []
    for job in jobs:
        approval = db.get(Approval, job.approval_id)
        item = db.get(ContentItem, approval.content_item_id) if approval else None
        account = db.get(SocialAccount, approval.social_account_id) if approval else None
        result.append({
            **as_dict(job),
            "destination": approval.destination if approval else "",
            "content_title": item.title if item else "",
            "platform": item.platform if item else "",
            "account_name": account.display_name if account else "",
            "account_is_mock": account.is_mock if account else True,
        })
    return result


@app.post("/api/v1/workspaces/{workspace_id}/publish-jobs/{job_id}/dispatch")
def dispatch_publish(workspace_id: int, job_id: int, user: User = Depends(current_user),
                     db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "approver")
    job = db.scalar(select(PublishJob).where(PublishJob.id == job_id, PublishJob.organization_id == workspace_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Publish job not found")
    try:
        post_id = dispatch_job(job_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except Exception as error:
        raise HTTPException(
            status_code=409,
            detail="Publishing outcome is uncertain; reconcile before retrying",
        ) from error
    return {"status": "published", "provider_post_id": post_id}


@app.post("/api/v1/workspaces/{workspace_id}/publish-jobs/{job_id}/reconcile")
def reconcile_publish_job(
    workspace_id: int,
    job_id: int,
    payload: PublishReconciliationRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin")
    job = db.scalar(select(PublishJob).where(
        PublishJob.id == job_id,
        PublishJob.organization_id == workspace_id,
    ).with_for_update())
    if job is None:
        raise HTTPException(status_code=404, detail="Publish job not found")
    rate_limited = (
        job.status == "blocked"
        and job.error_message.startswith("LinkedIn rate limited this publish request (HTTP 429)")
    )
    if job.status != "reconciliation_required" and not rate_limited:
        raise HTTPException(status_code=409, detail="Only uncertain or rate-limited LinkedIn jobs can be reconciled")
    approval = db.scalar(select(Approval).where(
        Approval.id == job.approval_id,
        Approval.organization_id == workspace_id,
    ))
    if approval is None:
        raise HTTPException(status_code=409, detail="Publish job approval is unavailable")
    account = db.scalar(select(SocialAccount).where(
        SocialAccount.id == approval.social_account_id,
        SocialAccount.organization_id == workspace_id,
    ))
    if account is None:
        raise HTTPException(status_code=409, detail="Publish job account or approval is unavailable")
    if account.is_mock or account.platform != "linkedin":
        raise HTTPException(status_code=409, detail="Manual reconciliation is available only for uncertain LinkedIn posts")
    if approval.action != "publish" or approval.destination.strip() == "":
        raise HTTPException(status_code=409, detail="A valid approved publish action is required")
    if approval.destination != account.external_account_id:
        raise HTTPException(status_code=409, detail="Publish job destination does not match the connected LinkedIn member")
    if payload.outcome != "confirmed_published" and approval.status != "approved":
        raise HTTPException(status_code=409, detail="A current human approval is required before retry")
    if rate_limited and payload.outcome != "retry_after_rate_limit":
        raise HTTPException(status_code=409, detail="A rate-limited request can only be retried after the provider wait period")
    if not rate_limited and payload.outcome == "retry_after_rate_limit":
        raise HTTPException(status_code=409, detail="This job was not classified as rate-limited")

    previous_status = job.status
    if payload.outcome == "confirmed_published":
        job.status = "published"
        job.provider_post_id = payload.provider_post_id
        job.error_message = ""
        job.completed_at = datetime.now(timezone.utc)
        action = "publish.reconciled_published"
        details = {
            "provider": "linkedin",
            "provider_post_id": payload.provider_post_id,
            "evidence_summary": payload.evidence_summary,
        }
    else:
        job.status = "queued"
        job.provider_post_id = None
        job.error_message = (
            "Operator confirmed no LinkedIn post was created after the rate-limit wait; queued for a retry."
            if payload.outcome == "retry_after_rate_limit"
            else "Operator confirmed no LinkedIn post was created; queued for a retry."
        )
        job.completed_at = None
        action = (
            "publish.rate_limit_retry_authorized"
            if payload.outcome == "retry_after_rate_limit"
            else "publish.reconciled_retry_authorized"
        )
        details = {
            "provider": "linkedin",
            "evidence_summary": payload.evidence_summary,
            "retry_confirmation": payload.retry_confirmation,
        }
    audit(
        db,
        workspace_id,
        user.id,
        action,
        "publish_job",
        job.id,
        {"previous_status": previous_status, **details},
    )
    db.commit()
    return as_dict(job)


def analytics_snapshot_query(
    db: Session,
    workspace_id: int,
    start: datetime | None,
    end: datetime | None,
    platform: str | None,
    social_account_id: int | None,
    campaign_id: int | None,
    content_item_id: int | None,
):
    statement = select(
        AnalyticsSnapshot,
        SocialAccount.display_name,
        ContentItem.title,
        Campaign.name,
    ).join(
        SocialAccount,
        (SocialAccount.id == AnalyticsSnapshot.social_account_id)
        & (SocialAccount.organization_id == workspace_id),
    ).outerjoin(
        ContentItem,
        (ContentItem.id == AnalyticsSnapshot.content_item_id)
        & (ContentItem.organization_id == workspace_id),
    ).outerjoin(
        Campaign,
        (Campaign.id == AnalyticsSnapshot.campaign_id)
        & (Campaign.organization_id == workspace_id),
    ).where(AnalyticsSnapshot.organization_id == workspace_id)
    if start is not None:
        statement = statement.where(AnalyticsSnapshot.period_end >= start)
    if end is not None:
        statement = statement.where(AnalyticsSnapshot.period_start <= end)
    if platform is not None:
        statement = statement.where(AnalyticsSnapshot.platform == platform)
    if social_account_id is not None:
        statement = statement.where(AnalyticsSnapshot.social_account_id == social_account_id)
    if campaign_id is not None:
        statement = statement.where(AnalyticsSnapshot.campaign_id == campaign_id)
    if content_item_id is not None:
        statement = statement.where(AnalyticsSnapshot.content_item_id == content_item_id)
    return db.execute(statement.order_by(AnalyticsSnapshot.period_end.desc(), AnalyticsSnapshot.id.desc()).limit(500)).all()


@app.get("/api/v1/workspaces/{workspace_id}/analytics/definitions")
def analytics_definitions(workspace_id: int, user: User = Depends(current_user),
                          db: Session = Depends(get_db)) -> dict:
    workspace_member(db, user, workspace_id)
    return {
        "definitions": [{"key": key, **definition} for key, definition in ANALYTICS_METRICS.items()],
        "freshness_note": "Snapshots show when data was captured. Manual imports are not live provider syncs.",
    }


@app.get("/api/v1/workspaces/{workspace_id}/analytics")
def list_analytics(
    workspace_id: int,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    platform: str | None = Query(default=None, pattern="^(linkedin|instagram|facebook|youtube|x|tiktok)$"),
    social_account_id: int | None = Query(default=None, gt=0),
    campaign_id: int | None = Query(default=None, gt=0),
    content_item_id: int | None = Query(default=None, gt=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    workspace_member(db, user, workspace_id)
    if start and start.tzinfo is None or end and end.tzinfo is None:
        raise HTTPException(status_code=422, detail="Analytics date filters must include a timezone")
    if start and end and start > end:
        raise HTTPException(status_code=422, detail="start must be before end")
    rows = analytics_snapshot_query(
        db, workspace_id, start, end, platform, social_account_id, campaign_id, content_item_id
    )
    snapshots = []
    for snapshot, account_name, content_title, campaign_name in rows:
        snapshots.append({
            **as_dict(snapshot),
            "account_name": account_name,
            "content_title": content_title or "",
            "campaign_name": campaign_name or "",
        })
    latest = max((snapshot["fetched_at"] for snapshot in snapshots), default=None)
    return {
        "snapshots": snapshots,
        "definitions": [{"key": key, **definition} for key, definition in ANALYTICS_METRICS.items()],
        "freshness": {
            "latest_snapshot_at": latest,
            "snapshot_count": len(snapshots),
            "note": "Manual imports are not live provider syncs. Metric definitions and platform availability can differ.",
        },
    }


@app.post("/api/v1/workspaces/{workspace_id}/analytics/snapshots", status_code=201)
def create_analytics_snapshot(
    workspace_id: int,
    payload: AnalyticsSnapshotRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "analyst")
    account = db.scalar(select(SocialAccount).where(
        SocialAccount.id == payload.social_account_id,
        SocialAccount.organization_id == workspace_id,
        SocialAccount.active.is_(True),
    ))
    if account is None:
        raise HTTPException(status_code=404, detail="Active social account not found")
    if payload.source == "mock" and not account.is_mock:
        raise HTTPException(status_code=409, detail="Mock analytics data can only be attached to a mock account")
    item = None
    if payload.content_item_id is not None:
        item = db.scalar(select(ContentItem).where(
            ContentItem.id == payload.content_item_id,
            ContentItem.organization_id == workspace_id,
        ))
        if item is None:
            raise HTTPException(status_code=404, detail="Content item not found")
        if item.platform != account.platform:
            raise HTTPException(status_code=409, detail="Analytics account platform does not match the content platform")
    campaign_id = payload.campaign_id
    if campaign_id is not None:
        campaign = db.scalar(select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.organization_id == workspace_id,
        ))
        if campaign is None:
            raise HTTPException(status_code=404, detail="Campaign not found")
        if item is not None and item.campaign_id not in (None, campaign_id):
            raise HTTPException(status_code=409, detail="Campaign does not match the selected content item")
    elif item is not None:
        campaign_id = item.campaign_id
    duplicate = db.scalar(select(AnalyticsSnapshot.id).where(
        AnalyticsSnapshot.organization_id == workspace_id,
        AnalyticsSnapshot.social_account_id == account.id,
        AnalyticsSnapshot.source_reference == payload.source_reference,
    ))
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="This analytics source reference was already imported")
    snapshot = AnalyticsSnapshot(
        organization_id=workspace_id,
        social_account_id=account.id,
        content_item_id=item.id if item else None,
        campaign_id=campaign_id,
        platform=account.platform,
        period_start=payload.period_start,
        period_end=payload.period_end,
        metrics=payload.metrics,
        unavailable_metrics=payload.unavailable_metrics,
        source=payload.source,
        source_reference=payload.source_reference,
        source_note=payload.source_note,
        fetched_at=datetime.now(timezone.utc),
        created_by=user.id,
    )
    db.add(snapshot)
    try:
        db.flush()
        audit(db, workspace_id, user.id, "analytics.snapshot_imported", "analytics_snapshot", snapshot.id, {
            "social_account_id": account.id,
            "platform": account.platform,
            "source": payload.source,
            "period_start": payload.period_start.isoformat(),
            "period_end": payload.period_end.isoformat(),
        })
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This analytics source reference was already imported")
    return as_dict(snapshot)


@app.get("/api/v1/workspaces/{workspace_id}/analytics/export.csv")
def export_analytics_csv(
    workspace_id: int,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    platform: str | None = Query(default=None, pattern="^(linkedin|instagram|facebook|youtube|x|tiktok)$"),
    social_account_id: int | None = Query(default=None, gt=0),
    campaign_id: int | None = Query(default=None, gt=0),
    content_item_id: int | None = Query(default=None, gt=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    workspace_member(db, user, workspace_id)
    if start and start.tzinfo is None or end and end.tzinfo is None:
        raise HTTPException(status_code=422, detail="Analytics date filters must include a timezone")
    if start and end and start > end:
        raise HTTPException(status_code=422, detail="start must be before end")
    rows = analytics_snapshot_query(
        db, workspace_id, start, end, platform, social_account_id, campaign_id, content_item_id
    )
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    metric_names = list(ANALYTICS_METRICS)
    writer.writerow([
        "snapshot_id", "platform", "account", "content", "campaign", "period_start",
        "period_end", "fetched_at", "source", "source_note", *metric_names,
        "unavailable_metrics",
    ])
    for snapshot, account_name, content_title, campaign_name in rows:
        safe = lambda value: "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value
        writer.writerow([
            snapshot.id, snapshot.platform, safe(account_name), safe(content_title or ""),
            safe(campaign_name or ""), snapshot.period_start.isoformat(),
            snapshot.period_end.isoformat(), snapshot.fetched_at.isoformat(),
            snapshot.source, safe(snapshot.source_note),
            *(snapshot.metrics.get(name, "") for name in metric_names),
            ",".join(snapshot.unavailable_metrics),
        ])
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=analytics.csv"},
    )


@app.get("/api/v1/workspaces/{workspace_id}/experiments")
def list_experiments(workspace_id: int, user: User = Depends(current_user),
                     db: Session = Depends(get_db)) -> list[dict]:
    workspace_member(db, user, workspace_id)
    return [as_dict(row) for row in db.scalars(
        select(Experiment).where(Experiment.organization_id == workspace_id)
        .order_by(Experiment.created_at.desc(), Experiment.id.desc()).limit(200)
    ).all()]


@app.post("/api/v1/workspaces/{workspace_id}/experiments", status_code=201)
def create_experiment(workspace_id: int, payload: ExperimentRequest,
                      user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "strategist", "analyst")
    if payload.campaign_id is not None and db.scalar(select(Campaign.id).where(
        Campaign.id == payload.campaign_id,
        Campaign.organization_id == workspace_id,
    )) is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    experiment = Experiment(
        organization_id=workspace_id,
        created_by=user.id,
        **payload.model_dump(),
    )
    db.add(experiment)
    db.flush()
    audit(db, workspace_id, user.id, "experiment.created", "experiment", experiment.id, {
        "primary_metric": experiment.primary_metric,
        "status": experiment.status,
    })
    db.commit()
    return as_dict(experiment)


@app.put("/api/v1/workspaces/{workspace_id}/experiments/{experiment_id}")
def update_experiment(workspace_id: int, experiment_id: int, payload: ExperimentRequest,
                      user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "strategist", "analyst")
    experiment = db.scalar(select(Experiment).where(
        Experiment.id == experiment_id,
        Experiment.organization_id == workspace_id,
    ).with_for_update())
    if experiment is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    if payload.campaign_id is not None and db.scalar(select(Campaign.id).where(
        Campaign.id == payload.campaign_id,
        Campaign.organization_id == workspace_id,
    )) is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    for key, value in payload.model_dump().items():
        setattr(experiment, key, value)
    audit(db, workspace_id, user.id, "experiment.updated", "experiment", experiment.id, {
        "primary_metric": experiment.primary_metric,
        "status": experiment.status,
    })
    db.commit()
    return as_dict(experiment)


def community_message_response(db: Session, message: CommunityMessage) -> dict:
    account = db.get(SocialAccount, message.social_account_id)
    replies = db.scalars(select(CommunityReply).where(
        CommunityReply.organization_id == message.organization_id,
        CommunityReply.community_message_id == message.id,
    ).order_by(CommunityReply.created_at, CommunityReply.id)).all()
    return {
        **as_dict(message),
        "platform": account.platform if account else "",
        "account_name": account.display_name if account else "",
        "replies": [as_dict(reply) for reply in replies],
        "external_send_available": False,
    }


@app.get("/api/v1/workspaces/{workspace_id}/community")
def list_community_messages(
    workspace_id: int,
    status: Literal["open", "resolved"] | None = Query(default=None),
    assigned_to: int | None = Query(default=None, gt=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    workspace_member(db, user, workspace_id)
    statement = select(CommunityMessage).where(
        CommunityMessage.organization_id == workspace_id,
    )
    if status:
        statement = statement.where(CommunityMessage.status == status)
    if assigned_to:
        statement = statement.where(CommunityMessage.assigned_to == assigned_to)
    messages = db.scalars(statement.order_by(
        CommunityMessage.received_at.desc(), CommunityMessage.id.desc()
    ).limit(200)).all()
    return [community_message_response(db, message) for message in messages]


@app.post("/api/v1/workspaces/{workspace_id}/community", status_code=201)
def create_community_message(workspace_id: int, payload: CommunityMessageRequest,
                             user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "creator", "strategist")
    account = db.scalar(select(SocialAccount).where(
        SocialAccount.id == payload.social_account_id,
        SocialAccount.organization_id == workspace_id,
        SocialAccount.active.is_(True),
    ))
    if account is None:
        raise HTTPException(status_code=404, detail="Active social account not found")
    if payload.source == "mock" and not account.is_mock:
        raise HTTPException(status_code=409, detail="Mock interactions can only be attached to a mock account")
    message = CommunityMessage(
        organization_id=workspace_id,
        social_account_id=account.id,
        external_message_id=payload.external_message_id,
        conversation_id=payload.conversation_id,
        author_name=payload.author_name,
        body=payload.body,
        source=payload.source,
        received_at=payload.received_at,
        created_by=user.id,
    )
    db.add(message)
    try:
        db.flush()
        audit(db, workspace_id, user.id, "community.message_imported", "community_message", message.id, {
            "social_account_id": account.id,
            "source": payload.source,
        })
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This message was already imported")
    return community_message_response(db, message)


@app.patch("/api/v1/workspaces/{workspace_id}/community/{message_id}/assignment")
def assign_community_message(
    workspace_id: int,
    message_id: int,
    payload: CommunityAssignmentRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "approver", "creator", "strategist")
    message = db.scalar(select(CommunityMessage).where(
        CommunityMessage.id == message_id,
        CommunityMessage.organization_id == workspace_id,
    ).with_for_update())
    if message is None:
        raise HTTPException(status_code=404, detail="Community message not found")
    if payload.assigned_to is not None and db.scalar(select(Membership.id).where(
        Membership.organization_id == workspace_id,
        Membership.user_id == payload.assigned_to,
        Membership.active.is_(True),
    )) is None:
        raise HTTPException(status_code=404, detail="Active workspace assignee not found")
    message.assigned_to = payload.assigned_to
    audit(db, workspace_id, user.id, "community.message_assigned", "community_message", message.id, {
        "assigned_to": payload.assigned_to,
    })
    db.commit()
    return community_message_response(db, message)


@app.patch("/api/v1/workspaces/{workspace_id}/community/{message_id}/status")
def update_community_status(
    workspace_id: int,
    message_id: int,
    payload: CommunityStatusRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "approver", "creator", "strategist")
    message = db.scalar(select(CommunityMessage).where(
        CommunityMessage.id == message_id,
        CommunityMessage.organization_id == workspace_id,
    ).with_for_update())
    if message is None:
        raise HTTPException(status_code=404, detail="Community message not found")
    previous_status = message.status
    message.status = payload.status
    audit(db, workspace_id, user.id, "community.message_status_changed", "community_message", message.id, {
        "from": previous_status,
        "to": payload.status,
    })
    db.commit()
    return community_message_response(db, message)


@app.post("/api/v1/workspaces/{workspace_id}/community/{message_id}/replies", status_code=201)
def create_community_reply(
    workspace_id: int,
    message_id: int,
    payload: CommunityReplyRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "creator", "strategist")
    message = db.scalar(select(CommunityMessage).where(
        CommunityMessage.id == message_id,
        CommunityMessage.organization_id == workspace_id,
    ))
    if message is None:
        raise HTTPException(status_code=404, detail="Community message not found")
    if message.status != "open":
        raise HTTPException(status_code=409, detail="Reopen the community message before drafting a reply")
    reply = CommunityReply(
        organization_id=workspace_id,
        community_message_id=message.id,
        body=payload.body,
        status="draft",
        created_by=user.id,
    )
    db.add(reply)
    db.flush()
    audit(db, workspace_id, user.id, "community.reply_drafted", "community_reply", reply.id, {
        "community_message_id": message.id,
    })
    db.commit()
    return as_dict(reply)


@app.post("/api/v1/workspaces/{workspace_id}/community/replies/{reply_id}/submit")
def submit_community_reply(workspace_id: int, reply_id: int, user: User = Depends(current_user),
                           db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "creator", "strategist")
    reply = db.scalar(select(CommunityReply).where(
        CommunityReply.id == reply_id,
        CommunityReply.organization_id == workspace_id,
    ).with_for_update())
    if reply is None:
        raise HTTPException(status_code=404, detail="Community reply not found")
    if reply.created_by != user.id:
        raise HTTPException(status_code=403, detail="Only the reply author can submit this draft for review")
    if reply.status not in ("draft", "changes_requested"):
        raise HTTPException(status_code=409, detail="Only a draft or changes-requested reply can be submitted")
    reply.status = "pending_review"
    audit(db, workspace_id, user.id, "community.reply_submitted", "community_reply", reply.id)
    db.commit()
    return as_dict(reply)


@app.post("/api/v1/workspaces/{workspace_id}/community/replies/{reply_id}/decision")
def decide_community_reply(
    workspace_id: int,
    reply_id: int,
    payload: CommunityReplyDecisionRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "approver")
    reply = db.scalar(select(CommunityReply).where(
        CommunityReply.id == reply_id,
        CommunityReply.organization_id == workspace_id,
    ).with_for_update())
    if reply is None:
        raise HTTPException(status_code=404, detail="Community reply not found")
    if reply.created_by == user.id:
        raise HTTPException(status_code=403, detail="Authors cannot review their own community replies")
    if reply.status != "pending_review":
        raise HTTPException(status_code=409, detail="Only pending community replies can be reviewed")
    reply.status = payload.decision
    reply.reviewed_by = user.id
    reply.review_comment = payload.comment
    audit(db, workspace_id, user.id, f"community.reply_{payload.decision}", "community_reply", reply.id, {
        "external_send_available": False,
    })
    db.commit()
    return as_dict(reply)


@app.get("/api/v1/workspaces/{workspace_id}/audit-logs")
def list_audit_logs(workspace_id: int, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> list[dict]:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "approver")
    return [as_dict(row) for row in db.scalars(
        select(AuditLog).where(AuditLog.organization_id == workspace_id).order_by(AuditLog.id.desc()).limit(200)
    )]


def research_response(db: Session, record: ResearchRecord) -> dict[str, Any]:
    sources = db.scalars(select(ResearchSource).where(
        ResearchSource.research_record_id == record.id,
        ResearchSource.organization_id == record.organization_id,
    ).order_by(ResearchSource.id)).all()
    run = db.get(AgentRun, record.agent_run_id) if record.agent_run_id else None
    return {
        **as_dict(record),
        "sources": [as_dict(source) for source in sources],
        "agent_run": as_dict(run) if run is not None else None,
    }


@app.post("/api/v1/workspaces/{workspace_id}/research", status_code=201)
def create_research(workspace_id: int, payload: ResearchRequest, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "strategist", "creator")
    if payload.campaign_id and db.scalar(select(Campaign).where(
        Campaign.id == payload.campaign_id, Campaign.organization_id == workspace_id
    )) is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    record = ResearchRecord(
        organization_id=workspace_id,
        campaign_id=payload.campaign_id,
        created_by=user.id,
        question=payload.question,
        status="running",
    )
    db.add(record)
    db.flush()
    sources = []
    for source_input in payload.sources:
        source = ResearchSource(
            organization_id=workspace_id,
            research_record_id=record.id,
            title=source_input.title,
            url=str(source_input.url),
            source_type=source_input.source_type,
            excerpt=source_input.excerpt,
        )
        db.add(source)
        sources.append(source)
    db.flush()
    source_ids = [source.id for source in sources]
    source_data = research_context(sources)
    try:
        result, run = execute_workflow(
            db,
            workspace_id,
            user.id,
            "market_intelligence",
            ResearchOutput,
            {"question": payload.question, "sources": source_data},
            {"research_record_id": record.id, "campaign_id": payload.campaign_id,
             "source_ids": source_ids},
        )
    except WorkflowExecutionError as error:
        record.status = "failed"
        record.agent_run_id = error.run.id
        audit(db, workspace_id, user.id, "research.failed", "research_record", record.id,
              {"run_id": error.run.id, "error_code": error.code})
        db.commit()
        raise HTTPException(
            status_code=502,
            detail=f"Research workflow failed: {str(error)} (code: {error.code}); run {error.run.id} is available in Agent Activity.",
        ) from error
    record.status = "completed"
    record.agent_run_id = run.id
    record.findings = [finding.model_dump(mode="json") for finding in result.findings]
    record.recommendations = result.recommendations
    record.completed_at = datetime.now(timezone.utc)
    audit(db, workspace_id, user.id, "research.completed", "research_record", record.id,
          {"run_id": run.id, "source_count": len(sources)})
    db.commit()
    return research_response(db, record)


@app.get("/api/v1/workspaces/{workspace_id}/research")
def list_research(workspace_id: int, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> list[dict]:
    workspace_member(db, user, workspace_id)
    records = db.scalars(select(ResearchRecord).where(
        ResearchRecord.organization_id == workspace_id
    ).order_by(ResearchRecord.id.desc()).limit(100)).all()
    return [research_response(db, record) for record in records]


@app.post("/api/v1/workspaces/{workspace_id}/campaigns/{campaign_id}/strategy")
def create_campaign_strategy(workspace_id: int, campaign_id: int, payload: ResearchContextRequest,
                             user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "strategist", "creator")
    campaign = db.scalar(select(Campaign).where(
        Campaign.id == campaign_id, Campaign.organization_id == workspace_id
    ))
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    brand = db.scalar(select(BrandProfile).where(BrandProfile.organization_id == workspace_id))
    sources = scoped_research_sources(db, workspace_id, payload.research_record_ids)
    source_ids = [source.id for source in sources]
    output, run = execute_ai(
        db,
        workspace_id,
        user.id,
        "content_strategist",
        StrategyOutput,
        {
            "campaign": {
                "id": campaign.id, "name": campaign.name, "objective": campaign.objective,
                "audience": campaign.audience, "starts_at": campaign.starts_at, "ends_at": campaign.ends_at,
            },
            "brand": brand_context(brand),
            "research": research_context(sources),
            "source_ids": source_ids,
        },
        {"campaign_id": campaign.id, "research_record_ids": payload.research_record_ids,
         "source_ids": source_ids},
    )
    audit(db, workspace_id, user.id, "campaign.strategy_generated", "campaign", campaign.id,
          {"run_id": run.id})
    strategy_record = CampaignStrategy(
        organization_id=workspace_id,
        campaign_id=campaign.id,
        agent_run_id=run.id,
        created_by=user.id,
        output=output.model_dump(mode="json"),
        source_ids=source_ids,
    )
    db.add(strategy_record)
    db.commit()
    return {
        "strategy": output.model_dump(mode="json"),
        "strategy_record": as_dict(strategy_record),
        "agent_run": as_dict(run),
    }


@app.get("/api/v1/workspaces/{workspace_id}/campaigns/{campaign_id}/strategies")
def list_campaign_strategies(workspace_id: int, campaign_id: int, user: User = Depends(current_user),
                             db: Session = Depends(get_db)) -> list[dict]:
    workspace_member(db, user, workspace_id)
    campaign = db.scalar(select(Campaign).where(
        Campaign.id == campaign_id,
        Campaign.organization_id == workspace_id,
    ))
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return [as_dict(strategy) for strategy in db.scalars(select(CampaignStrategy).where(
        CampaignStrategy.organization_id == workspace_id,
        CampaignStrategy.campaign_id == campaign_id,
    ).order_by(CampaignStrategy.created_at.desc(), CampaignStrategy.id.desc()).limit(50))]


@app.post("/api/v1/workspaces/{workspace_id}/content/generate", status_code=201)
def generate_content(workspace_id: int, payload: CopyRequest, user: User = Depends(current_user),
                     db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "strategist", "creator")
    campaign = None
    if payload.campaign_id:
        campaign = db.scalar(select(Campaign).where(
            Campaign.id == payload.campaign_id, Campaign.organization_id == workspace_id
        ))
        if campaign is None:
            raise HTTPException(status_code=404, detail="Campaign not found")
    sources = scoped_research_sources(db, workspace_id, payload.research_record_ids)
    source_ids = [source.id for source in sources]
    brand = db.scalar(select(BrandProfile).where(BrandProfile.organization_id == workspace_id))
    output, run = execute_ai(
        db,
        workspace_id,
        user.id,
        "cross_platform_copywriter",
        CopyOutput,
        {
            "brief": payload.brief,
            "platform": payload.platform,
            "campaign": ({"id": campaign.id, "name": campaign.name, "objective": campaign.objective,
                          "audience": campaign.audience} if campaign else {}),
            "brand": brand_context(brand),
            "research": research_context(sources),
            "source_ids": source_ids,
        },
        {"campaign_id": campaign.id if campaign else None,
         "research_record_ids": payload.research_record_ids, "source_ids": source_ids},
    )
    parts = [part for part in (output.hook, output.body, output.call_to_action) if part]
    if output.hashtags:
        parts.append(" ".join(f"#{tag.lstrip('#')}" for tag in output.hashtags))
    body = "\n\n".join(parts)
    if len(body) > 3000:
        run.status = "failed"
        run.error_code = "draft_exceeds_limit"
        db.commit()
        raise HTTPException(status_code=422, detail="Generated draft exceeded the content limit; adjust the brief and try again.")
    item = ContentItem(
        organization_id=workspace_id,
        campaign_id=campaign.id if campaign else None,
        title=output.title,
        platform=payload.platform,
        status="draft",
        created_by=user.id,
    )
    db.add(item)
    db.flush()
    version = make_version(db, item, body, [], user.id, output.source_ids)
    item.current_version_id = version.id
    run.content_version_id = version.id
    audit(db, workspace_id, user.id, "content.ai_draft_created", "content_item", item.id,
          {"run_id": run.id, "version_id": version.id, "platform": payload.platform})
    db.commit()
    return {
        **as_dict(item),
        "version": as_dict(version),
        "draft_notes": {"call_to_action": output.call_to_action, "hashtags": output.hashtags,
                        "assumptions": output.assumptions},
        "agent_run": as_dict(run),
    }


@app.post("/api/v1/workspaces/{workspace_id}/campaigns/{campaign_id}/creative-concepts")
def create_creative_concepts(workspace_id: int, campaign_id: int, payload: CreativeRequest,
                             user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "strategist", "creator")
    campaign = db.scalar(select(Campaign).where(
        Campaign.id == campaign_id, Campaign.organization_id == workspace_id
    ))
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    sources = scoped_research_sources(db, workspace_id, payload.research_record_ids)
    source_ids = [source.id for source in sources]
    output, run = execute_ai(
        db, workspace_id, user.id, "creative_director", CreativeOutput,
        {"brief": payload.brief, "campaign": {"id": campaign.id, "name": campaign.name},
         "sources": research_context(sources), "source_ids": source_ids},
        {"campaign_id": campaign.id, "research_record_ids": payload.research_record_ids,
         "source_ids": source_ids},
    )
    audit(db, workspace_id, user.id, "creative.concepts_generated", "campaign", campaign.id,
          {"run_id": run.id})
    db.commit()
    return {"concepts": output.model_dump(mode="json"), "agent_run": as_dict(run)}


@app.post("/api/v1/workspaces/{workspace_id}/content/{item_id}/ai-review")
def review_content_with_ai(workspace_id: int, item_id: int, user: User = Depends(current_user),
                           db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin", "strategist", "creator")
    item = get_scoped_item(db, workspace_id, item_id)
    version = db.get(ContentVersion, item.current_version_id)
    if version is None:
        raise HTTPException(status_code=409, detail="Content has no current version")
    brand = db.scalar(select(BrandProfile).where(BrandProfile.organization_id == workspace_id))
    sources = db.scalars(select(ResearchSource).where(
        ResearchSource.organization_id == workspace_id,
        ResearchSource.id.in_(version.source_references or [-1]),
    )).all()
    source_ids = [source.id for source in sources]
    output, run = execute_ai(
        db, workspace_id, user.id, "quality_brand_safety_reviewer", ReviewOutput,
        {"body": version.body, "platform": item.platform,
         "brand": brand_context(brand),
         "sources": research_context(sources), "source_ids": source_ids},
        {"content_item_id": item.id, "content_version_id": version.id, "source_ids": source_ids},
    )
    findings = [finding.model_dump(mode="json") for finding in output.findings]
    findings.extend({
        "severity": "warning",
        "code": "claim_needs_source",
        "message": f"Verify this claim against an authoritative source: {claim}",
        "remediation": "Attach a source reference or revise the claim.",
    } for claim in output.claims_requiring_sources)
    findings.extend({
        "severity": "info",
        "code": "review_limitation",
        "message": limitation,
        "remediation": "",
    } for limitation in output.limitations)
    result = QualityReview(
        organization_id=workspace_id,
        content_version_id=version.id,
        brand_fingerprint=fingerprint_brand(brand),
        review_type="ai",
        agent_run_id=run.id,
        passed=output.recommendation == "pass" and not any(
            finding["severity"] == "blocking" for finding in findings
        ),
        findings=findings,
    )
    db.add(result)
    audit(db, workspace_id, user.id, "quality.ai_reviewed", "content_version", version.id,
          {"run_id": run.id, "passed": result.passed})
    db.commit()
    return {**as_dict(result), "recommendation": output.recommendation,
            "limitations": output.limitations, "agent_run_id": run.id}


PLATFORM_TEXT_LIMITS = {
    "linkedin": 3000,
    "instagram": 2200,
    "facebook": 63206,
    "youtube": 5000,
    "x": 280,
    "tiktok": 2200,
}


@app.get("/api/v1/workspaces/{workspace_id}/content/{item_id}/platform-preview")
def get_platform_preview(workspace_id: int, item_id: int, user: User = Depends(current_user),
                         db: Session = Depends(get_db)) -> dict:
    workspace_member(db, user, workspace_id)
    item = get_scoped_item(db, workspace_id, item_id)
    version = db.get(ContentVersion, item.current_version_id)
    if version is None:
        raise HTTPException(status_code=409, detail="Content has no current version")
    limit = PLATFORM_TEXT_LIMITS.get(item.platform)
    if limit is None:
        raise HTTPException(status_code=422, detail="No local preview limit is configured for this platform.")
    warnings = ["Approximate text-only preview; actual platform rendering and account capabilities may differ."]
    if not version.media_manifest:
        warnings.append("No media is attached; confirm whether the selected platform format requires media.")
    preview = PlatformPreview(
        platform=item.platform,
        character_count=len(version.body),
        character_limit=limit,
        within_limit=len(version.body) <= limit,
        preview_text=version.body,
        warnings=warnings,
    )
    return preview.model_dump()


@app.get("/api/v1/workspaces/{workspace_id}/agent-runs")
def list_agent_runs(workspace_id: int, limit: int = 50, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> list[dict]:
    workspace_member(db, user, workspace_id)
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail="Limit must be between 1 and 100")
    runs = db.scalars(select(AgentRun).where(
        AgentRun.organization_id == workspace_id
    ).order_by(AgentRun.id.desc()).limit(limit)).all()
    return [as_dict(run) for run in runs]


@app.get("/api/v1/workspaces/{workspace_id}/usage")
def get_ai_usage(workspace_id: int, user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> dict:
    workspace_member(db, user, workspace_id)
    events = db.scalars(select(UsageEvent).where(
        UsageEvent.organization_id == workspace_id
    ).order_by(UsageEvent.id.desc()).limit(1000)).all()
    providers: dict[str, dict[str, Any]] = {}
    daily: dict[str, dict[str, int]] = {}
    for event in events:
        provider_summary = providers.setdefault(event.provider, {
            "provider": event.provider,
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "failed_requests": 0,
            "fallback_requests": 0,
        })
        provider_summary["requests"] += 1
        provider_summary["input_tokens"] += event.input_tokens
        provider_summary["output_tokens"] += event.output_tokens
        provider_summary["failed_requests"] += int(event.status == "failed")
        provider_summary["fallback_requests"] += int(event.fallback_used)
        day = event.created_at.date().isoformat()
        daily_summary = daily.setdefault(day, {
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "failed_requests": 0,
            "fallback_requests": 0,
        })
        daily_summary["requests"] += 1
        daily_summary["input_tokens"] += event.input_tokens
        daily_summary["output_tokens"] += event.output_tokens
        daily_summary["failed_requests"] += int(event.status == "failed")
        daily_summary["fallback_requests"] += int(event.fallback_used)
    return {
        "event_count": len(events),
        "successful_requests": sum(event.status == "succeeded" for event in events),
        "failed_requests": sum(event.status == "failed" for event in events),
        "fallback_requests": sum(event.fallback_used for event in events),
        "input_tokens": sum(event.input_tokens for event in events),
        "output_tokens": sum(event.output_tokens for event in events),
        "estimated_events": sum(1 for event in events if event.is_estimated),
        "known_cost_usd": round(sum(event.cost_usd or 0 for event in events), 6),
        "cost_note": "Provider cost is unknown unless the provider supplies billing data; local mock token counts are estimates.",
        "by_provider": sorted(providers.values(), key=lambda summary: summary["provider"]),
        "daily": [{"date": date, **values} for date, values in sorted(daily.items())],
        "events": [as_dict(event) for event in events],
    }


def _saved_ai_provider(db: Session, workspace_id: int, provider_name: str):
    configuration = db.scalar(select(AIProviderConfiguration).where(
        AIProviderConfiguration.organization_id == workspace_id,
        AIProviderConfiguration.provider == provider_name,
    ))
    if configuration is None:
        raise HTTPException(status_code=404, detail="AI provider is not configured for this workspace")
    try:
        provider = create_named_provider(
            configuration.provider,
            decrypt_api_key(configuration.api_key_ciphertext),
            configuration.model,
        )
    except AIProviderError as error:
        raise HTTPException(status_code=503, detail=f"{error} (code: {error.code})") from error
    return configuration, provider


@app.get("/api/v1/workspaces/{workspace_id}/ai/providers")
def list_ai_providers(workspace_id: int, user: User = Depends(current_user),
                      db: Session = Depends(get_db)) -> dict:
    workspace_member(db, user, workspace_id)
    configurations = db.scalars(select(AIProviderConfiguration).where(
        AIProviderConfiguration.organization_id == workspace_id
    ).order_by(AIProviderConfiguration.priority, AIProviderConfiguration.id)).all()
    enabled = next((item.provider for item in configurations if item.enabled), None)
    if enabled is None:
        enabled = os.getenv("LLM_PROVIDER", "mock").strip().lower()
    return {
        "providers": AI_PROVIDER_CATALOG,
        "primary_provider": enabled,
        "configurations": [{
            "provider": item.provider,
            "model": item.model,
            "enabled": item.enabled,
            "priority": item.priority,
            "api_key_configured": bool(item.api_key_ciphertext),
        } for item in configurations],
    }


@app.put("/api/v1/workspaces/{workspace_id}/ai/providers/{provider_name}")
def configure_ai_provider(workspace_id: int, provider_name: str, payload: AIProviderConfigurationRequest,
                          user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin")
    supported = {item["id"] for item in AI_PROVIDER_CATALOG}
    if provider_name not in supported:
        raise HTTPException(status_code=404, detail="AI provider is not supported")
    configuration = db.scalar(select(AIProviderConfiguration).where(
        AIProviderConfiguration.organization_id == workspace_id,
        AIProviderConfiguration.provider == provider_name,
    ))
    submitted_key = (payload.api_key or "").strip()
    if configuration is None and not submitted_key:
        raise HTTPException(status_code=422, detail="An API key is required when configuring a provider")
    if configuration is None:
        configuration = AIProviderConfiguration(
            organization_id=workspace_id,
            provider=provider_name,
            api_key_ciphertext="",
            model=payload.model.strip(),
            enabled=payload.enabled,
            priority=payload.priority,
            configured_by=user.id,
        )
        db.add(configuration)
    if submitted_key:
        try:
            configuration.api_key_ciphertext = encrypt_api_key(submitted_key)
        except AIProviderError as error:
            raise HTTPException(status_code=503, detail=f"{error} (code: {error.code})") from error
    configuration.model = payload.model.strip()
    configuration.enabled = payload.enabled
    configuration.priority = payload.priority
    configuration.configured_by = user.id
    audit(db, workspace_id, user.id, "ai.provider_configured", "ai_provider", provider_name,
          {"provider": provider_name, "enabled": payload.enabled, "priority": payload.priority})
    db.commit()
    return {
        "provider": configuration.provider,
        "model": configuration.model,
        "enabled": configuration.enabled,
        "priority": configuration.priority,
        "api_key_configured": bool(configuration.api_key_ciphertext),
    }


@app.delete("/api/v1/workspaces/{workspace_id}/ai/providers/{provider_name}", status_code=204)
def delete_ai_provider(workspace_id: int, provider_name: str, user: User = Depends(current_user),
                       db: Session = Depends(get_db)) -> Response:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin")
    configuration = db.scalar(select(AIProviderConfiguration).where(
        AIProviderConfiguration.organization_id == workspace_id,
        AIProviderConfiguration.provider == provider_name,
    ))
    if configuration is None:
        raise HTTPException(status_code=404, detail="AI provider is not configured for this workspace")
    db.delete(configuration)
    audit(db, workspace_id, user.id, "ai.provider_removed", "ai_provider", provider_name,
          {"provider": provider_name})
    db.commit()
    return Response(status_code=204)


@app.get("/api/v1/workspaces/{workspace_id}/ai/providers/{provider_name}/models")
def list_configured_ai_models(workspace_id: int, provider_name: str, user: User = Depends(current_user),
                              db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin")
    _, provider = _saved_ai_provider(db, workspace_id, provider_name)
    try:
        return {"provider": provider_name, "models": provider.list_models()}
    except AIProviderError as error:
        raise HTTPException(status_code=502, detail=f"{error} (code: {error.code})") from error


@app.post("/api/v1/workspaces/{workspace_id}/ai/providers/{provider_name}/validate")
def validate_ai_provider(workspace_id: int, provider_name: str, user: User = Depends(current_user),
                         db: Session = Depends(get_db)) -> dict:
    membership = workspace_member(db, user, workspace_id)
    require_role(membership, "owner", "admin")
    configuration, provider = _saved_ai_provider(db, workspace_id, provider_name)
    try:
        models = provider.list_models()
    except AIProviderError as error:
        raise HTTPException(status_code=502, detail=f"{error} (code: {error.code})") from error
    return {
        "status": "valid",
        "provider": provider_name,
        "configured_model": configuration.model,
        "configured_model_available": configuration.model in models,
        "available_model_count": len(models),
    }
