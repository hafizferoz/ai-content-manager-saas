from datetime import datetime, timezone
import hashlib

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.domain import fingerprint_brand
from app.models import (
    Approval, AuditLog, BrandProfile, ContentItem, ContentVersion, MockPublication,
    Membership, PublishJob, SocialAccount,
)


def validate_publish_authorization(db: Session, job: PublishJob) -> tuple[Approval, ContentVersion, SocialAccount]:
    approval = db.scalar(select(Approval).where(
        Approval.id == job.approval_id,
        Approval.organization_id == job.organization_id,
    ).with_for_update())
    if approval is None or approval.status != "approved" or approval.action != "publish":
        raise ValueError("A current human approval for this publish action is required")
    if approval.decided_by is None or approval.decided_by == approval.requested_by:
        raise ValueError("The recorded approver is no longer authorized")
    reviewer_membership = db.scalar(select(Membership).where(
        Membership.organization_id == job.organization_id,
        Membership.user_id == approval.decided_by,
        Membership.active.is_(True),
        Membership.role.in_(("owner", "admin", "approver")),
    ))
    if reviewer_membership is None:
        raise ValueError("The recorded approver is no longer authorized")
    item = db.scalar(select(ContentItem).where(
        ContentItem.id == approval.content_item_id,
        ContentItem.organization_id == job.organization_id,
    ).with_for_update())
    version = db.get(ContentVersion, approval.content_version_id)
    account = db.scalar(select(SocialAccount).where(
        SocialAccount.id == approval.social_account_id,
        SocialAccount.organization_id == job.organization_id,
        SocialAccount.active.is_(True),
    ).with_for_update())
    if item is None or version is None or account is None:
        raise ValueError("Approved content or connected account is unavailable")
    if item.current_version_id != version.id or approval.version_fingerprint != version.fingerprint:
        raise ValueError("Approval was invalidated by a content edit")
    brand = db.scalar(select(BrandProfile).where(BrandProfile.organization_id == job.organization_id))
    if fingerprint_brand(brand) != approval.brand_fingerprint:
        raise ValueError("Approval was invalidated by a brand guidance change")
    if approval.destination.strip() == "" or account.is_mock is not True or account.platform != item.platform:
        raise ValueError("Only active mock accounts with a valid destination are supported")
    return approval, version, account


def mock_provider_publish(db: Session, job: PublishJob, approval: Approval,
                          version: ContentVersion, account: SocialAccount) -> str:
    previous = db.scalar(select(MockPublication).where(
        MockPublication.organization_id == job.organization_id,
        MockPublication.idempotency_key == job.idempotency_key,
    ))
    if previous is not None:
        return previous.provider_post_id
    post_id = "mock_" + hashlib.sha256(
        f"{job.organization_id}:{job.idempotency_key}".encode()
    ).hexdigest()[:24]
    db.add(MockPublication(
        organization_id=job.organization_id,
        idempotency_key=job.idempotency_key,
        provider_post_id=post_id,
        content_version_id=version.id,
        social_account_id=account.id,
        destination=approval.destination,
    ))
    db.flush()
    return post_id


def dispatch_job(job_id: int) -> str:
    with SessionLocal() as db:
        job = db.scalar(select(PublishJob).where(PublishJob.id == job_id).with_for_update())
        if job is None:
            raise ValueError("Publish job not found")
        if job.status == "published":
            return job.provider_post_id or ""
        if job.status != "queued":
            raise ValueError(f"Publish job cannot be dispatched from {job.status}")
        result = db.execute(update(PublishJob).where(
            PublishJob.id == job.id, PublishJob.status == "queued"
        ).values(status="dispatching"))
        if result.rowcount != 1:
            raise ValueError("Publish job is already being dispatched")
        db.commit()

    try:
        with SessionLocal() as db:
            job = db.scalar(select(PublishJob).where(PublishJob.id == job_id).with_for_update())
            if job is None:
                raise ValueError("Publish job not found")
            # Revalidate at dispatch and again immediately before the provider call.
            validate_publish_authorization(db, job)
            approval, version, account = validate_publish_authorization(db, job)
            post_id = mock_provider_publish(db, job, approval, version, account)
            job.provider_post_id = post_id
            job.status = "published"
            job.completed_at = datetime.now(timezone.utc)
            db.add(AuditLog(
                organization_id=job.organization_id,
                actor_id=None,
                action="publish.confirmed",
                resource_type="publish_job",
                resource_id=str(job.id),
                details={"provider": "mock", "post_id": post_id},
            ))
            db.commit()
            return post_id
    except Exception as error:
        with SessionLocal() as db:
            job = db.get(PublishJob, job_id)
            if job is not None and job.status != "published":
                job.status = "blocked" if isinstance(error, ValueError) else "reconciliation_required"
                job.error_message = (
                    str(error)[:500] if isinstance(error, ValueError)
                    else "Publishing outcome is uncertain; reconcile before retrying"
                )
                db.add(AuditLog(
                    organization_id=job.organization_id,
                    actor_id=None,
                    action="publish.blocked" if job.status == "blocked" else "publish.uncertain",
                    resource_type="publish_job",
                    resource_id=str(job.id),
                    details={"reason": job.error_message},
                ))
                db.commit()
        raise


def dispatch_next() -> bool:
    with SessionLocal() as db:
        job_id = db.scalar(select(PublishJob.id).where(PublishJob.status == "queued").order_by(PublishJob.id))
    if job_id is None:
        return False
    dispatch_job(job_id)
    return True
