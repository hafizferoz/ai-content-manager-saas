import os

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


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
            "social_accounts", "publish_jobs", "mock_publications", "audit_logs",
        } <= tables
        approval_columns = {column["name"] for column in inspect(engine).get_columns("approvals")}
        assert "brand_fingerprint" in approval_columns
    finally:
        engine.dispose()


def test_brand_approval_migration_adds_column_to_existing_schema(tmp_path, monkeypatch):
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
    command.upgrade(config, "head")

    engine = create_engine(url)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("approvals")}
        assert "brand_fingerprint" in columns
    finally:
        engine.dispose()
