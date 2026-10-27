"""Bind approvals to brand guidance state."""
import sqlalchemy as sa
from alembic import op

revision = "0002_brand_bound_approvals"
down_revision = "0001_phase1_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("approvals")}
    if "brand_fingerprint" not in columns:
        op.add_column(
            "approvals",
            sa.Column("brand_fingerprint", sa.String(length=64), nullable=False, server_default=""),
        )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("approvals")}
    if "brand_fingerprint" in columns:
        op.drop_column("approvals", "brand_fingerprint")
