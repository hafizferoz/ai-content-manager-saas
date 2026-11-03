"""Add analytics snapshots, experiments, and community drafts."""
from alembic import op

from app.database import Base
from app import models

revision = "0007_analytics_community"
down_revision = "0006_linkedin_oauth_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in (
        "analytics_snapshots",
        "experiments",
        "community_messages",
        "community_replies",
    ):
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in (
        "community_replies",
        "community_messages",
        "experiments",
        "analytics_snapshots",
    ):
        Base.metadata.tables[table_name].drop(bind=bind, checkfirst=True)
