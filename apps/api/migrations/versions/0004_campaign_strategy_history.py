"""Persist generated campaign strategy history."""
from alembic import op

from app.database import Base
from app import models

revision = "0004_campaign_strategy_history"
down_revision = "0003_ai_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.tables["campaign_strategies"].create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.tables["campaign_strategies"].drop(bind=op.get_bind(), checkfirst=True)
