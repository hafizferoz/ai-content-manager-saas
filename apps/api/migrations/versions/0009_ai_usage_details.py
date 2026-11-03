"""Track AI workflow task, status, duration, and usage availability."""
import sqlalchemy as sa
from alembic import op

revision = "0009_ai_usage_details"
down_revision = "0008_ai_provider_configuration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in sa.inspect(bind).get_columns("usage_events")}
    columns = (
        ("task_type", sa.String(length=80), ""),
        ("usage_available", sa.Boolean(), sa.true()),
        ("status", sa.String(length=24), "succeeded"),
        ("error_category", sa.String(length=80), ""),
        ("duration_ms", sa.Integer(), 0),
    )
    for name, column_type, default in columns:
        if name not in existing:
            op.add_column(
                "usage_events",
                sa.Column(name, column_type, nullable=False, server_default=sa.text(str(default).lower()) if isinstance(default, bool) else str(default)),
            )


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("usage_events")}
    for name in ("duration_ms", "error_category", "status", "usage_available", "task_type"):
        if name in existing:
            op.drop_column("usage_events", name)
