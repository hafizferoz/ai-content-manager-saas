import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import AnyHttpUrl, BaseModel, EmailStr, Field, field_validator
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
from app.ai_workflows import WorkflowExecutionError, execute_workflow
from app.models import (
    AgentRun, Approval, AuditLog, BrandProfile, Campaign, CampaignStrategy, ContentItem, ContentVersion,
    Membership, Organization, PublishJob, QualityReview, ResearchRecord, ResearchSource,
    SessionToken, SocialAccount, UsageEvent, User,
)
from app.domain import fingerprint_brand
from app.publishing import dispatch_job
from app.security import bearer, create_session, current_user, password_hash, require_role, workspace_member
from app.social_capabilities import (
    capability_catalog_response,
    capability_rows_for_account,
)


app = FastAPI(title="AI Content Manager API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    organization_name: str = Field(min_length=1, max_length=160)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


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
            detail=f"AI workflow failed ({error.code}); run {error.run.id} is available in Agent Activity.",
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
        SocialAccount.is_mock.is_(True),
    ))
    if account is None:
        raise HTTPException(status_code=404, detail="Active mock account for this platform not found")
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
    return {"status": "published", "provider_post_id": post_id}


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
            detail=f"Research workflow failed ({error.code}); run {error.run.id} is available in Agent Activity.",
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
    return {
        "event_count": len(events),
        "input_tokens": sum(event.input_tokens for event in events),
        "output_tokens": sum(event.output_tokens for event in events),
        "estimated_events": sum(1 for event in events if event.is_estimated),
        "known_cost_usd": round(sum(event.cost_usd or 0 for event in events), 6),
        "cost_note": "Provider cost is unknown unless the provider supplies billing data; local mock token counts are estimates.",
        "events": [as_dict(event) for event in events],
    }
