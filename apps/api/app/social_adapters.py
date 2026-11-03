import hashlib
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

import httpx

from app.linkedin_oauth import (
    LinkedInConfigurationError,
    LinkedInRequestError,
    decrypt_token,
    encrypt_token,
    linkedin_settings,
    normalize_linkedin_scopes,
    refresh_access_token,
)
from app.models import (
    Approval,
    ContentVersion,
    LinkedInCredential,
    MockPublication,
    PublishJob,
    SocialAccount,
    SocialAccountCapability,
)


class SocialAdapter(Protocol):
    name: str

    def prepare(
        self,
        db: Session,
        job: PublishJob,
        approval: Approval,
        version: ContentVersion,
        account: SocialAccount,
    ) -> None:
        ...

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

    def prepare(
        self,
        db: Session,
        job: PublishJob,
        approval: Approval,
        version: ContentVersion,
        account: SocialAccount,
    ) -> None:
        return None

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


class LinkedInAdapter:
    name = "linkedin"

    def prepare(
        self,
        db: Session,
        job: PublishJob,
        approval: Approval,
        version: ContentVersion,
        account: SocialAccount,
    ) -> None:
        credential = self._credential(db, job.organization_id, account.id)
        try:
            settings = linkedin_settings()
        except LinkedInConfigurationError as error:
            raise ValueError(str(error)) from error
        try:
            access_token = decrypt_token(credential.access_token_ciphertext, settings.encryption_key)
            refresh_token = (
                decrypt_token(credential.refresh_token_ciphertext, settings.encryption_key)
                if credential.refresh_token_ciphertext else ""
            )
        except LinkedInConfigurationError as error:
            raise ValueError(str(error)) from error

        now = datetime.now(timezone.utc)
        expires_at = _as_utc(credential.access_token_expires_at)
        if expires_at > now + timedelta(minutes=2):
            return
        refresh_expires_at = (
            _as_utc(credential.refresh_token_expires_at)
            if credential.refresh_token_expires_at else None
        )
        if not refresh_token or (refresh_expires_at and refresh_expires_at <= now):
            self._disable_publish_capability(db, account, "LinkedIn token expired; reconnect the account.")
            db.commit()
            raise ValueError("LinkedIn authorization expired; reconnect the account before publishing")
        try:
            refreshed = refresh_access_token(
                settings,
                refresh_token,
                normalize_linkedin_scopes(credential.granted_scopes),
            )
        except LinkedInRequestError as error:
            self._disable_publish_capability(db, account, "LinkedIn token refresh failed; reconnect the account.")
            db.commit()
            raise ValueError("LinkedIn token refresh failed; reconnect the account before publishing") from error
        credential.access_token_ciphertext = encrypt_token(refreshed.access_token, settings.encryption_key)
        if refreshed.refresh_token:
            credential.refresh_token_ciphertext = encrypt_token(refreshed.refresh_token, settings.encryption_key)
        credential.granted_scopes = normalize_linkedin_scopes(refreshed.scopes)
        credential.access_token_expires_at = refreshed.expires_at
        credential.refresh_token_expires_at = refreshed.refresh_expires_at or credential.refresh_token_expires_at
        if "w_member_social" not in credential.granted_scopes:
            self._disable_publish_capability(db, account, "LinkedIn token no longer grants w_member_social.")
            db.commit()
            raise ValueError("LinkedIn publishing permission is missing; reconnect and grant the required permission")
        db.commit()

    def publish(
        self,
        db: Session,
        job: PublishJob,
        approval: Approval,
        version: ContentVersion,
        account: SocialAccount,
    ) -> str:
        if version.media_manifest:
            raise ValueError("LinkedIn adapter currently supports text-only posts")
        if approval.destination != account.external_account_id:
            raise ValueError("LinkedIn approval destination does not match the connected member")
        credential = self._credential(db, job.organization_id, account.id)
        if "w_member_social" not in normalize_linkedin_scopes(credential.granted_scopes):
            raise ValueError("LinkedIn publishing permission is missing; reconnect the account")
        try:
            settings = linkedin_settings()
        except LinkedInConfigurationError as error:
            raise ValueError(str(error)) from error
        try:
            access_token = decrypt_token(credential.access_token_ciphertext, settings.encryption_key)
        except LinkedInConfigurationError as error:
            raise ValueError(str(error)) from error
        try:
            response = httpx.post(
                "https://api.linkedin.com/rest/posts",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "X-Restli-Protocol-Version": "2.0.0",
                    "Linkedin-Version": settings.api_version,
                },
                json={
                    "author": account.external_account_id,
                    "commentary": version.body,
                    "visibility": "PUBLIC",
                    "distribution": {
                        "feedDistribution": "MAIN_FEED",
                        "targetEntities": [],
                        "thirdPartyDistributionChannels": [],
                    },
                    "lifecycleState": "PUBLISHED",
                    "isReshareDisabledByAuthor": False,
                },
                timeout=httpx.Timeout(30.0, connect=5.0),
            )
        except httpx.RequestError:
            raise
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "").strip()
            retry_detail = (
                f" Retry-After: {retry_after} seconds."
                if retry_after.isdecimal() and len(retry_after) <= 6
                else " Honor the provider Retry-After header before retrying."
            )
            raise ValueError(
                "LinkedIn rate limited this publish request (HTTP 429); no automatic retry was made."
                + retry_detail
            )
        if 400 <= response.status_code < 500:
            raise ValueError("LinkedIn rejected the post; verify account permission and content before retrying")
        if response.is_error:
            raise RuntimeError("LinkedIn publish outcome is uncertain; reconcile before retrying")
        post_id = response.headers.get("x-restli-id", "").strip()
        if not post_id:
            raise RuntimeError("LinkedIn accepted a post without a confirmation ID; reconcile before retrying")
        return post_id

    @staticmethod
    def _credential(db: Session, organization_id: int, account_id: int) -> LinkedInCredential:
        credential = db.scalar(select(LinkedInCredential).where(
            LinkedInCredential.organization_id == organization_id,
            LinkedInCredential.social_account_id == account_id,
        ))
        if credential is None:
            raise ValueError("LinkedIn account credentials are unavailable; reconnect the account")
        return credential

    @staticmethod
    def _disable_publish_capability(
        db: Session,
        account: SocialAccount,
        reason: str,
    ) -> None:
        capability = db.scalar(select(SocialAccountCapability).where(
            SocialAccountCapability.organization_id == account.organization_id,
            SocialAccountCapability.social_account_id == account.id,
            SocialAccountCapability.capability == "publish",
        ))
        if capability is not None:
            capability.enabled = False
            capability.status = "reauthorization_required"
            capability.reason = reason


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def adapter_for_account(account: SocialAccount) -> SocialAdapter:
    if account.is_mock is True:
        return MockSocialAdapter()
    if account.platform == "linkedin":
        return LinkedInAdapter()
    raise ValueError("No official social adapter is configured for this account")
