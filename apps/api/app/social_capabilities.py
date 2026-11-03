from typing import Any
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.linkedin_oauth import (
    configuration_status,
    linkedin_publishing_enabled,
    normalize_linkedin_scopes,
)
from app.models import LinkedInCredential, SocialAccount, SocialAccountCapability

SUPPORTED_PLATFORMS = ("linkedin", "instagram", "facebook", "youtube", "x", "tiktok")

CAPABILITY_CATALOG = (
    {
        "name": "publish",
        "description": "Publish an approved post",
        "mock_status": "available",
        "unavailable_status": "not_implemented",
        "unavailable_reason": "No official platform adapter is configured.",
    },
    {
        "name": "analytics",
        "description": "Read post and account analytics",
        "mock_status": "available",
        "unavailable_status": "not_implemented",
        "mock_reason": "Mock accounts support labelled mock or manually imported analytics snapshots; no provider sync is performed.",
        "unavailable_reason": "Provider analytics sync is not implemented.",
    },
    {
        "name": "community",
        "description": "Read and respond to community interactions",
        "mock_status": "available",
        "unavailable_status": "not_implemented",
        "mock_reason": "Mock/manual inbox records and reviewed reply drafts are available; no external replies are sent.",
        "unavailable_reason": "Provider community API workflows are not implemented.",
    },
    {
        "name": "oauth",
        "description": "Connect an official platform account",
        "mock_status": "not_implemented",
        "unavailable_status": "not_implemented",
        "unavailable_reason": "Official OAuth integration is not configured.",
    },
)


def capability_rows_for_account(
    account: SocialAccount,
) -> list[SocialAccountCapability]:
    is_mock = account.is_mock is True
    return [
        SocialAccountCapability(
            organization_id=account.organization_id,
            social_account_id=account.id,
            capability=entry["name"],
            status=entry["mock_status"] if is_mock else entry["unavailable_status"],
            enabled=is_mock and entry["mock_status"] == "available",
            reason=(
                entry.get("mock_reason", "Local mock adapter is available; no external post is sent.")
                if is_mock and entry["mock_status"] == "available"
                else entry["unavailable_reason"]
            ),
            required_permissions=[],
        )
        for entry in CAPABILITY_CATALOG
    ]


def capability_catalog_response() -> list[dict[str, Any]]:
    linkedin_config = configuration_status()
    result = []
    for platform in SUPPORTED_PLATFORMS:
        capabilities = []
        for entry in CAPABILITY_CATALOG:
            real_status = "not_implemented"
            reason = entry["unavailable_reason"]
            if platform == "linkedin" and linkedin_config["configured"]:
                if entry["name"] == "oauth":
                    real_status = "available"
                    reason = "OAuth connection can be started; actual access depends on granted LinkedIn products and scopes."
                elif entry["name"] == "publish":
                    if linkedin_config["publishing_enabled"]:
                        real_status = "requires_member_permission"
                        reason = "Requires granted w_member_social scope and a valid member account."
                    else:
                        real_status = "disabled_by_operator"
                        reason = "Set PUBLISHING_ENABLED=true before enabling LinkedIn publishing."
            elif platform == "linkedin" and entry["name"] == "oauth":
                reason = linkedin_config["reason"]
            capabilities.append({
                "name": entry["name"],
                "description": entry["description"],
                "mock_status": entry["mock_status"],
                "real_status": real_status,
                "reason": reason,
                "required_permissions": (
                    ["openid", "profile"] if entry["name"] == "oauth" and platform == "linkedin"
                    else ["w_member_social"] if entry["name"] == "publish" and platform == "linkedin"
                    else []
                ),
            })
        result.append({
            "platform": platform,
            "connection_mode": (
                "oauth_available" if platform == "linkedin" and linkedin_config["configured"]
                else "mock_only"
            ),
            "real_connection_status": (
                "available" if platform == "linkedin" and linkedin_config["configured"]
                else "not_configured" if platform == "linkedin"
                else "not_implemented"
            ),
            "capabilities": capabilities,
        })
    return result


def has_enabled_capability(db: Session, account: SocialAccount, capability: str) -> bool:
    return db.scalar(select(SocialAccountCapability.id).where(
        SocialAccountCapability.organization_id == account.organization_id,
        SocialAccountCapability.social_account_id == account.id,
        SocialAccountCapability.capability == capability,
        SocialAccountCapability.enabled.is_(True),
        SocialAccountCapability.status == "available",
    ).limit(1)) is not None


def reconcile_linkedin_publish_capability(db: Session, account: SocialAccount) -> bool:
    if account.platform != "linkedin" or account.is_mock:
        return False
    credential = db.scalar(select(LinkedInCredential).where(
        LinkedInCredential.organization_id == account.organization_id,
        LinkedInCredential.social_account_id == account.id,
    ))
    if credential is None:
        return False

    scopes = normalize_linkedin_scopes(credential.granted_scopes)
    has_permission = "w_member_social" in scopes
    publishing_enabled = linkedin_publishing_enabled()
    status = (
        "available" if has_permission and publishing_enabled
        else "disabled_by_operator" if has_permission
        else "missing_permission"
    )
    reason = (
        "Member publishing is enabled for approved text-only posts."
        if has_permission and publishing_enabled
        else "Set PUBLISHING_ENABLED=true to enable LinkedIn publishing."
        if has_permission
        else "Request the w_member_social permission in the LinkedIn Developer Portal."
    )
    capability = db.scalar(select(SocialAccountCapability).where(
        SocialAccountCapability.organization_id == account.organization_id,
        SocialAccountCapability.social_account_id == account.id,
        SocialAccountCapability.capability == "publish",
    ))
    if capability is None:
        capability = SocialAccountCapability(
            organization_id=account.organization_id,
            social_account_id=account.id,
            capability="publish",
            status=status,
            enabled=has_permission and publishing_enabled,
            reason=reason,
            required_permissions=["w_member_social"],
        )
        account.capabilities.append(capability)
        changed = True
    else:
        changed = (
            credential.granted_scopes != scopes
            or capability.status != status
            or capability.enabled != (has_permission and publishing_enabled)
            or capability.reason != reason
            or capability.required_permissions != ["w_member_social"]
        )
        capability.status = status
        capability.enabled = has_permission and publishing_enabled
        capability.reason = reason
        capability.required_permissions = ["w_member_social"]
        if changed:
            capability.checked_at = datetime.now(timezone.utc)
    if credential.granted_scopes != scopes:
        credential.granted_scopes = scopes
    return changed
