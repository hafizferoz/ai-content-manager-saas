"""Add research records, structured AI runs and usage events."""
import sqlalchemy as sa
from alembic import op

from app.database import Base
from app import models

revision = "0003_ai_workflows"
down_revision = "0002_brand_bound_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in ("research_records", "research_sources", "agent_runs", "usage_events"):
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)

    version_columns = {column["name"] for column in sa.inspect(bind).get_columns("content_versions")}
    if "source_references" not in version_columns:
        op.add_column(
            "content_versions",
            sa.Column("source_references", sa.JSON(), nullable=False, server_default="[]"),
        )

    item_columns = {column["name"] for column in sa.inspect(bind).get_columns("content_items")}
    if "status" not in item_columns:
        op.add_column(
            "content_items",
            sa.Column("status", sa.String(length=24), nullable=False, server_default="draft"),
        )

    review_columns = {column["name"] for column in sa.inspect(bind).get_columns("quality_reviews")}
    if "review_type" not in review_columns:
        op.add_column(
            "quality_reviews",
            sa.Column("review_type", sa.String(length=24), nullable=False, server_default="deterministic"),
        )
    if "agent_run_id" not in review_columns:
        op.add_column("quality_reviews", sa.Column("agent_run_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    review_columns = {column["name"] for column in sa.inspect(bind).get_columns("quality_reviews")}
    if "agent_run_id" in review_columns:
        op.drop_column("quality_reviews", "agent_run_id")
    if "review_type" in review_columns:
        op.drop_column("quality_reviews", "review_type")
    version_columns = {column["name"] for column in sa.inspect(bind).get_columns("content_versions")}
    if "source_references" in version_columns:
        op.drop_column("content_versions", "source_references")
    item_columns = {column["name"] for column in sa.inspect(bind).get_columns("content_items")}
    if "status" in item_columns:
        op.drop_column("content_items", "status")
    for table_name in ("usage_events", "agent_runs", "research_sources", "research_records"):
        Base.metadata.tables[table_name].drop(bind=bind, checkfirst=True)
