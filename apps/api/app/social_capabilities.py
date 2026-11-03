from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.linkedin_oauth import configuration_status
from app.models import SocialAccount, SocialAccountCapability

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
