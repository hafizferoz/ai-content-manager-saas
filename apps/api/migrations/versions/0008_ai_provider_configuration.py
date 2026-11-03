"""Add encrypted workspace AI provider configuration and fallback metadata."""
import sqlalchemy as sa
from alembic import op

from app.database import Base
from app import models

revision = "0008_ai_provider_configuration"
down_revision = "0007_analytics_community"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.tables["ai_provider_configurations"].create(bind=bind, checkfirst=True)

    agent_columns = {column["name"] for column in sa.inspect(bind).get_columns("agent_runs")}
    if "provider_attempts" not in agent_columns:
        op.add_column("agent_runs", sa.Column("provider_attempts", sa.JSON(), nullable=False, server_default="[]"))
    if "fallback_used" not in agent_columns:
        op.add_column("agent_runs", sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "attempt_count" not in agent_columns:
        op.add_column("agent_runs", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))

    usage_columns = {column["name"] for column in sa.inspect(bind).get_columns("usage_events")}
    if "fallback_used" not in usage_columns:
        op.add_column("usage_events", sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "attempt_count" not in usage_columns:
        op.add_column("usage_events", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    bind = op.get_bind()
    usage_columns = {column["name"] for column in sa.inspect(bind).get_columns("usage_events")}
    if "attempt_count" in usage_columns:
        op.drop_column("usage_events", "attempt_count")
    if "fallback_used" in usage_columns:
        op.drop_column("usage_events", "fallback_used")
    agent_columns = {column["name"] for column in sa.inspect(bind).get_columns("agent_runs")}
    if "attempt_count" in agent_columns:
        op.drop_column("agent_runs", "attempt_count")
    if "fallback_used" in agent_columns:
        op.drop_column("agent_runs", "fallback_used")
    if "provider_attempts" in agent_columns:
        op.drop_column("agent_runs", "provider_attempts")
    Base.metadata.tables["ai_provider_configurations"].drop(bind=bind, checkfirst=True)
