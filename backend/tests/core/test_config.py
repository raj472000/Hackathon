"""Settings: PostgreSQL through psycopg, and .env values that survive Docker Compose."""

import os

import pytest

from app.core.config import Settings


def test_no_database_endpoint_is_built_in():
    s = Settings(_env_file=None)
    assert s.database_url is None, "DATABASE_URL comes from .env"
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        s.require_database_url()
    assert s.auto_create_schema is False, "the schema comes from Alembic migrations"


@pytest.mark.parametrize("given", [
    "postgresql://u:p@db:5432/markets",
    "postgres://u:p@db:5432/markets",
    "postgresql+asyncpg://u:p@db:5432/markets",
    "postgresql+psycopg2://u:p@db:5432/markets",
    "postgresql+psycopg://u:p@db:5432/markets",
])
def test_any_postgres_url_form_is_normalised_to_psycopg(given):
    assert Settings(_env_file=None, database_url=given).database_url == "postgresql+psycopg://u:p@db:5432/markets"


def test_asyncpg_ssl_parameter_is_translated_for_psycopg():
    s = Settings(_env_file=None, database_url="postgresql+asyncpg://u:p@db:5432/m?ssl=require")
    assert s.database_url == "postgresql+psycopg://u:p@db:5432/m?sslmode=require"


def test_sqlite_is_still_accepted_for_tests():
    assert Settings(_env_file=None, database_url="sqlite+aiosqlite:///x.db").database_url == "sqlite+aiosqlite:///x.db"


def test_inline_comments_and_stray_spaces_from_env_files_are_removed(tmp_path):
    """Docker Compose passes `KEY=value   # note` through with the note attached, and a value can
    carry a leading space. Both used to stop the app from starting."""
    env = tmp_path / ".env"
    env.write_text(
        "DATA_MODE=live                   # demo = local mock MCP servers\n"
        "REDIS_URL= redis://localhost:6379/0                        # e.g. redis://redis:6379/0\n"
        "API_AUTH_TOKEN=                    # set a long random value\n"
        "LLM_ENABLED=false   # off\n"
        "THEME_REFRESH_CRON=30 6 1 * *      # monthly\n"
        "CORS_ORIGINS=http://localhost:5173#frag\n"
    )
    s = Settings(_env_file=env)
    assert s.data_mode == "live"
    assert s.redis_url == "redis://localhost:6379/0"
    assert s.api_auth_token is None
    assert s.llm_enabled is False
    assert s.theme_refresh_cron == "30 6 1 * *"
    assert s.cors_origins == "http://localhost:5173#frag", "a # without a space before it is part of the value"


def test_compose_style_environment_values_are_cleaned(monkeypatch):
    monkeypatch.setenv("DATA_MODE", "demo                     # demo = local mock MCP servers")
    monkeypatch.setenv("SCHEDULER_ENABLED", "true             # set false on the API")
    s = Settings(_env_file=None)
    assert s.data_mode == "demo" and s.scheduler_enabled is True


async def test_startup_refuses_an_unmigrated_database_with_instructions(tmp_path):
    from app.core.container import build_container
    from app.main import SchemaNotReady, init_database

    s = Settings(_env_file=None, app_env="test", database_url=f"sqlite+aiosqlite:///{tmp_path}/empty.db", scheduler_enabled=False)
    c = build_container(s)
    with pytest.raises(SchemaNotReady) as e:
        await init_database(c)
    assert "alembic upgrade head" in str(e.value) and "no migration has been applied" in str(e.value)
    await c.close()


async def test_startup_refuses_a_database_behind_the_code(tmp_path):
    from sqlalchemy import text

    from app.core.container import build_container
    from app.main import SchemaNotReady, init_database

    s = Settings(_env_file=None, app_env="test", database_url=f"sqlite+aiosqlite:///{tmp_path}/old.db", scheduler_enabled=False)
    c = build_container(s)
    async with c.engine.begin() as conn:
        await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        await conn.execute(text("INSERT INTO alembic_version VALUES ('000000000000')"))
    with pytest.raises(SchemaNotReady) as e:
        await init_database(c)
    assert "000000000000" in str(e.value) and "alembic upgrade head" in str(e.value)
    await c.close()


async def test_startup_accepts_a_database_at_the_current_migration(tmp_path):
    import subprocess
    import sys

    from app.core.container import build_container
    from app.main import init_database

    url = f"sqlite+aiosqlite:///{tmp_path}/migrated.db"
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], capture_output=True, text=True,  # noqa: ASYNC221
                       env={**os.environ, "DATABASE_URL": url})
    assert r.returncode == 0, r.stderr[-800:]
    c = build_container(Settings(_env_file=None, app_env="test", database_url=url, scheduler_enabled=False))
    await init_database(c)
    await c.close()


def _alembic(url, *args):
    import os
    import subprocess
    import sys

    return subprocess.run([sys.executable, "-m", "alembic", *args], capture_output=True, text=True, env={**os.environ, "DATABASE_URL": url})


async def test_auto_created_schema_is_recorded_so_migrations_still_work(tmp_path):
    """AUTO_CREATE_SCHEMA=true used to leave tables without an alembic_version row, after which
    `alembic upgrade head` failed with "table already exists"."""
    from app.core.container import build_container
    from app.main import init_database

    url = f"sqlite+aiosqlite:///{tmp_path}/auto.db"
    c = build_container(Settings(_env_file=None, app_env="test", database_url=url, auto_create_schema=True, scheduler_enabled=False))
    await init_database(c)
    await c.close()
    up = _alembic(url, "upgrade", "head")
    assert up.returncode == 0, up.stderr[-600:]
    assert _alembic(url, "check").returncode == 0


async def test_migrate_adopts_a_schema_that_was_created_without_migrations(tmp_path):
    from app.core.container import build_container
    from app.db.session import create_all
    from app.devtools.preflight import migrate

    url = f"sqlite+aiosqlite:///{tmp_path}/legacy.db"
    c = build_container(Settings(_env_file=None, app_env="test", database_url=url, scheduler_enabled=False))
    await create_all(c.engine)  # an older start-up created tables but recorded no migration
    await c.close()
    assert "already exists" in _alembic(url, "upgrade", "head").stderr, "the failure this guards against"
    out = migrate(url)
    assert out["adopted_existing_schema"] is True and out["revision"]
    assert _alembic(url, "check").returncode == 0
    assert migrate(url)["adopted_existing_schema"] is False, "a second run is a plain no-op upgrade"


async def test_jobs_left_running_by_a_stopped_process_are_marked_interrupted(tmp_path):
    """A process killed mid-job used to leave the run 'running' forever on the status pages."""
    from datetime import UTC, datetime, timedelta

    from app.core.container import build_container
    from app.db.models import JobRun
    from app.main import init_database
    from app.services import jobs

    c = build_container(Settings(_env_file=None, app_env="test", database_url=f"sqlite+aiosqlite:///{tmp_path}/j.db",
                                 auto_create_schema=True, scheduler_enabled=False))
    await init_database(c)
    async with c.sessions() as s:
        s.add(JobRun(kind="full_refresh", job_key="full_refresh", status="running", started_at=datetime.now(UTC) - timedelta(minutes=5)))
        s.add(JobRun(kind="rrg", job_key="rrg", status="running", started_at=datetime.now(UTC)))
        await s.commit()
    async with c.cache.lock("job:rrg"):  # this one is genuinely still running in this process
        n = await jobs.mark_interrupted(c)
    assert n == 1
    async with c.sessions() as s:
        runs = {r.job_key: r for r in await jobs.last_runs(s)}
    assert runs["full_refresh"].status == "failed" and "interrupted" in runs["full_refresh"].error and runs["full_refresh"].finished_at
    assert runs["rrg"].status == "running"
    await c.close()
