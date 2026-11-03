"""Store LinkedIn OAuth state and encrypted credentials."""
from alembic import op

from app.database import Base
from app import models

revision = "0006_linkedin_oauth_credentials"
down_revision = "0005_social_capability_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in ("linkedin_credentials", "oauth_states"):
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in ("oauth_states", "linkedin_credentials"):
        Base.metadata.tables[table_name].drop(bind=bind, checkfirst=True)
