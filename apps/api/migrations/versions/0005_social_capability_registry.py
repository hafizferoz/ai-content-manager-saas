"""Add per-account social capability registry."""
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import select

from app.database import Base
from app import models
from app.social_capabilities import CAPABILITY_CATALOG

revision = "0005_social_capability_registry"
down_revision = "0004_campaign_strategy_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    capability_table = Base.metadata.tables["social_account_capabilities"]
    capability_table.create(bind=bind, checkfirst=True)
    accounts = Base.metadata.tables["social_accounts"]
    existing = {
        (row.social_account_id, row.capability)
        for row in bind.execute(select(
            capability_table.c.social_account_id,
            capability_table.c.capability,
        ))
    }
    checked_at = datetime.now(timezone.utc)
    rows = []
    for account in bind.execute(select(
        accounts.c.id,
        accounts.c.organization_id,
        accounts.c.is_mock,
    )):
        is_mock = bool(account.is_mock)
        for entry in CAPABILITY_CATALOG:
            if (account.id, entry["name"]) in existing:
                continue
            available = is_mock and entry["mock_status"] == "available"
            rows.append({
                "organization_id": account.organization_id,
                "social_account_id": account.id,
                "capability": entry["name"],
                "status": "available" if available else "not_implemented",
                "enabled": available,
                "reason": (
                    "Local mock adapter is available; no external post is sent."
                    if available else entry["unavailable_reason"]
                ),
                "required_permissions": [],
                "checked_at": checked_at,
            })
    if rows:
        bind.execute(capability_table.insert(), rows)


def downgrade() -> None:
    Base.metadata.tables["social_account_capabilities"].drop(bind=op.get_bind(), checkfirst=True)
