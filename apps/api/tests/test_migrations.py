import os
from datetime import datetime, timezone

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.pool import NullPool

from app.database import Base

def test_clean_database_migrates_to_head(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    config = Config(config_path)
    config.set_main_option(
        "script_location",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "migrations")),
    )
    database_path = tmp_path / "migration.db"
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        tables = set(inspect(engine).get_table_names())
        assert {
            "users", "session_tokens", "organizations", "memberships", "brand_profiles", "campaigns",
            "content_items", "content_versions", "quality_reviews", "approvals",
            "social_accounts", "social_account_capabilities", "publish_jobs", "mock_publications",
            "audit_logs", "campaign_strategies", "linkedin_credentials", "oauth_states",
        } <= tables
        approval_columns = {column["name"] for column in inspect(engine).get_columns("approvals")}
        assert "brand_fingerprint" in approval_columns
    finally:
        engine.dispose()


def test_brand_approval_migration_adds_column(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    config = Config(config_path)
    config.set_main_option(
        "script_location",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "migrations")),
    )
    database_path = tmp_path / "legacy.db"
    url = f"sqlite:///{database_path}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE approvals (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        connection.exec_driver_sql("INSERT INTO alembic_version (version_num) VALUES ('0001_phase1_schema')")
    engine.dispose()

    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0002_brand_bound_approvals")

    engine = create_engine(url)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("approvals")}
        assert "brand_fingerprint" in columns
    finally:
        engine.dispose()


def test_ai_migration_upgrades_phase_one_schema(tmp_path):
    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    config = Config(config_path)
    config.set_main_option(
        "script_location",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "migrations")),
    )
    database_path = tmp_path / "phase-one.db"
    url = f"sqlite:///{database_path}"
    engine = create_engine(url, poolclass=NullPool)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        for table_name in (
            "oauth_states", "linkedin_credentials", "social_account_capabilities",
            "campaign_strategies", "usage_events", "agent_runs", "research_sources", "research_records"
        ):
            connection.exec_driver_sql(f"DROP TABLE {table_name}")
        connection.exec_driver_sql("ALTER TABLE quality_reviews DROP COLUMN agent_run_id")
        connection.exec_driver_sql("ALTER TABLE quality_reviews DROP COLUMN review_type")
        connection.exec_driver_sql("ALTER TABLE content_versions DROP COLUMN source_references")
        connection.exec_driver_sql("ALTER TABLE content_items DROP COLUMN status")
        connection.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO alembic_version (version_num) VALUES ('0002_brand_bound_approvals')"
        )
    engine.dispose()

    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        assert {"research_records", "research_sources", "agent_runs", "usage_events"} <= set(inspector.get_table_names())
        assert {"campaign_strategies", "social_account_capabilities", "linkedin_credentials", "oauth_states"} <= set(inspector.get_table_names())
        assert {"source_references"} <= {column["name"] for column in inspector.get_columns("content_versions")}
        assert {"status"} <= {column["name"] for column in inspector.get_columns("content_items")}
        assert {"review_type", "agent_run_id"} <= {column["name"] for column in inspector.get_columns("quality_reviews")}
    finally:
        engine.dispose()


def test_social_capability_migration_backfills_existing_mock_accounts(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    config = Config(config_path)
    config.set_main_option(
        "script_location",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "migrations")),
    )
    database_path = tmp_path / "pre-capability.db"
    url = f"sqlite:///{database_path}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        accounts = Base.metadata.tables["social_accounts"]
        connection.execute(accounts.insert().values(
            id=1,
            organization_id=1,
            platform="linkedin",
            display_name="Existing mock",
            external_account_id="existing-page",
            is_mock=True,
            active=True,
            created_at=datetime.now(timezone.utc),
        ))
        connection.exec_driver_sql("DROP TABLE social_account_capabilities")
        connection.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO alembic_version (version_num) VALUES ('0004_campaign_strategy_history')"
        )
    engine.dispose()

    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    engine = create_engine(url)
    try:
        capabilities = Base.metadata.tables["social_account_capabilities"]
        with engine.connect() as connection:
            rows = connection.execute(select(
                capabilities.c.capability,
                capabilities.c.status,
                capabilities.c.enabled,
            )).all()
        by_name = {row.capability: row for row in rows}
        assert by_name["publish"].status == "available"
        assert by_name["publish"].enabled is True
        assert by_name["analytics"].status == "not_implemented"
        assert by_name["analytics"].enabled is False
    finally:
        engine.dispose()
