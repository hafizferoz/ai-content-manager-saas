import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fastapi.security import HTTPAuthorizationCredentials

from app.database import get_db
from app.models import (
    Approval, AuditLog, BrandProfile, Campaign, ContentItem, ContentVersion,
    Membership, Organization, PublishJob, QualityReview, SessionToken,
    SocialAccount, User,
)
from app.domain import fingerprint_brand
from app.publishing import dispatch_job
from app.security import bearer, create_session, current_user, password_hash, require_role, workspace_member


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
    platform: str = Field(min_length=1, max_length=32)
    display_name: str = Field(min_length=1, max_length=160)
    external_account_id: str = Field(min_length=1, max_length=160)
    is_mock: bool = True


class ContentRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    platform: str = Field(min_length=1, max_length=32)
    body: str = Field(min_length=1, max_length=3000)
    campaign_id: int | None = None
    media_manifest: list[dict[str, Any]] = Field(default_factory=list)


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
    return [as_dict(c) for c in db.scalars(select(Campaign).where(Campaign.organization_id == workspace_id).order_by(Campaign.id.desc()))]


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
    return [as_dict(a) for a in db.scalars(select(SocialAccount).where(SocialAccount.organization_id == workspace_id))]


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
    audit(db, workspace_id, user.id, "social_account.mock_created", "social_account", account.id)
    db.commit()
    return as_dict(account)


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
    item = ContentItem(organization_id=workspace_id, campaign_id=payload.campaign_id, title=payload.title,
                       platform=payload.platform.lower(), created_by=user.id)
    db.add(item)
    db.flush()
    version = make_version(db, item, payload.body, payload.media_manifest, user.id)
    item.current_version_id = version.id
    audit(db, workspace_id, user.id, "content.created", "content_item", item.id)
    db.commit()
    return {**as_dict(item), "version": as_dict(version)}


def make_version(db: Session, item: ContentItem, body: str, media_manifest: list, user_id: int) -> ContentVersion:
    previous = db.scalar(select(ContentVersion).where(ContentVersion.content_item_id == item.id)
                         .order_by(ContentVersion.number.desc()))
    number = 1 if previous is None else previous.number + 1
    fingerprint = hashlib.sha256(json.dumps({"body": body, "media": media_manifest},
                                               sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    version = ContentVersion(content_item_id=item.id, number=number, body=body,
                             media_manifest=media_manifest, fingerprint=fingerprint, created_by=user_id)
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
    item.title = payload.title
    item.platform = payload.platform.lower()
    item.campaign_id = payload.campaign_id
    version = make_version(db, item, payload.body, payload.media_manifest, user.id)
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
    ).order_by(QualityReview.id.desc()))
    if review is None or not review.passed:
        raise HTTPException(status_code=409, detail="Run a passing quality review before requesting approval")
    brand = db.scalar(select(BrandProfile).where(BrandProfile.organization_id == workspace_id))
    if review.brand_fingerprint != fingerprint_brand(brand):
        raise HTTPException(status_code=409, detail="Brand guidance changed; rerun the quality review")
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
        review = db.scalar(select(QualityReview).where(
            QualityReview.content_version_id == approval.content_version_id,
            QualityReview.organization_id == workspace_id,
        ).order_by(QualityReview.id.desc()))
        result.append({
            **as_dict(approval),
            "content_title": item.title if item else "",
            "content_body": version.body if version else "",
            "version_number": version.number if version else None,
            "platform": item.platform if item else "",
            "account_name": account.display_name if account else "",
            "quality_findings": review.findings if review else [],
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
