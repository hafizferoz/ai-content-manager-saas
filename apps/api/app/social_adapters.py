import hashlib
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Approval, ContentVersion, MockPublication, PublishJob, SocialAccount


class SocialAdapter(Protocol):
    name: str

    def publish(
        self,
        db: Session,
        job: PublishJob,
        approval: Approval,
        version: ContentVersion,
        account: SocialAccount,
    ) -> str:
        ...


class MockSocialAdapter:
    name = "mock"

    def publish(
        self,
        db: Session,
        job: PublishJob,
        approval: Approval,
        version: ContentVersion,
        account: SocialAccount,
    ) -> str:
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


def adapter_for_account(account: SocialAccount) -> SocialAdapter:
    if account.is_mock is True:
        return MockSocialAdapter()
    raise ValueError("No official social adapter is configured for this account")
