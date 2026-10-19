"""Start-up pre-flight used by scripts/dev.sh.

    uv run python -m app.devtools.preflight db      # exit 0 if the database answers
    uv run python -m app.devtools.preflight redis   # exit 0 if Redis answers, or REDIS_URL is unset
    uv run python -m app.devtools.preflight show    # print the effective (password-hidden) settings
    uv run python -m app.devtools.preflight migrate # alembic upgrade head, adopting an unrecorded schema

Reads the same settings as the app (.env plus environment), so what it checks is what the API uses.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from urllib.parse import urlsplit

from app.core.config import get_settings


def _db() -> int:
    s = get_settings()
    url = s.require_database_url()
    if url.startswith("sqlite"):
        print(f"database: SQLite ({url.split('///', 1)[-1]})")
        return 0
    import psycopg
    from sqlalchemy.engine import make_url

    u = make_url(url)
    try:
        socket_dir = u.query.get("host")
        host = str(socket_dir if isinstance(socket_dir, str) else (socket_dir[0] if socket_dir else u.host or "localhost"))
        conn = psycopg.connect(host=host, port=u.port or 5432, dbname=u.database,
                               user=u.username, password=u.password, connect_timeout=3)
    except psycopg.OperationalError as e:
        first = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        print(f"database: cannot connect to {u.render_as_string(hide_password=True)} ({first})")
        if "password authentication failed" in str(e):
            print("          The database exists but the password differs. PostgreSQL applies POSTGRES_PASSWORD only when its\n"
                  "          volume is first created: use the original password in DATABASE_URL and POSTGRES_PASSWORD (default\n"
                  "          change-me-locally), or run ALTER USER inside the container. See analysis/HOW_TO_RUN.md, section 14.")
        return 1
    with conn:
        row = conn.execute("show server_version").fetchone()
        version = row[0] if row else "unknown"
    print(f"database: PostgreSQL {version} at {u.render_as_string(hide_password=True)}")
    return 0


def _redis() -> int:
    s = get_settings()
    if not s.redis_url:
        print("redis: not configured (in-process cache)")
        return 0
    import redis

    try:
        redis.from_url(s.redis_url, socket_connect_timeout=2).ping()
    except Exception as e:
        print(f"redis: cannot connect to {urlsplit(s.redis_url).hostname}:{urlsplit(s.redis_url).port} ({type(e).__name__})")
        return 1
    print(f"redis: ok at {urlsplit(s.redis_url).hostname}:{urlsplit(s.redis_url).port}")
    return 0


def _show() -> int:
    from sqlalchemy.engine import make_url

    s = get_settings()
    db = make_url(s.require_database_url())
    local = (db.host or "localhost") in ("localhost", "127.0.0.1") and not db.query.get("host")
    r = urlsplit(s.redis_url) if s.redis_url else None
    values = {"DATA_MODE": s.data_mode, "DATABASE": db.render_as_string(hide_password=True),
              "DATABASE_IS_LOCAL_POSTGRES": "yes" if s.require_database_url().startswith("postgresql") and local else "no",
              "REDIS_IS_LOCAL": "yes" if r and r.hostname in ("localhost", "127.0.0.1") else "no"}
    for k, v in values.items():  # shell-quoted: scripts/dev.sh evaluates this output
        print(f"{k}={shlex.quote(v)}")
    return 0


def _migrate_here() -> int:
    """Runs in its own process (Alembic drives its own event loop)."""
    import asyncio

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import BACKEND_DIR

    url = get_settings().require_database_url()

    async def state() -> tuple[set[str], int]:
        from sqlalchemy import text

        eng = create_async_engine(url)
        async with eng.connect() as conn:
            names = await conn.run_sync(lambda sc: set(inspect(sc).get_table_names()))
            rows = (await conn.execute(text("SELECT count(*) FROM alembic_version"))).scalar() if "alembic_version" in names else 0
        await eng.dispose()
        return names, int(rows or 0)

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    existing, recorded = asyncio.run(state())
    # A failed earlier upgrade can leave an empty alembic_version table behind, so "no record" means
    # either no version table or an empty one.
    adopted = recorded == 0 and "instruments" in existing
    if adopted:
        # Tables created by an earlier start-up with AUTO_CREATE_SCHEMA=true and no migration record.
        command.stamp(cfg, "head")
    command.upgrade(cfg, "head")
    from alembic.script import ScriptDirectory

    print(json.dumps({"adopted_existing_schema": adopted, "revision": ScriptDirectory.from_config(cfg).get_current_head()}))
    return 0


def migrate(url: str | None = None) -> dict[str, object]:
    """`alembic upgrade head` for DATABASE_URL (or `url`), first recording a schema that exists but
    was never migrated, so start-up never fails with "table already exists"."""
    env = {**os.environ, **({"DATABASE_URL": url} if url else {})}
    r = subprocess.run([sys.executable, "-m", "app.devtools.preflight", "_migrate"], capture_output=True, text=True, env=env,
                       cwd=str(__import__("app.core.config", fromlist=["BACKEND_DIR"]).BACKEND_DIR))
    if r.returncode != 0:
        raise RuntimeError(f"migration failed: {r.stderr.strip()[-1500:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def _migrate_cli() -> int:
    try:
        out = migrate()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    if out["adopted_existing_schema"]:
        print(f"migrations: existing tables had no migration record; recorded them as {out['revision']}")
    print(f"migrations: database is at {out['revision']} (latest)")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    raise SystemExit({"db": _db, "redis": _redis, "show": _show, "migrate": _migrate_cli, "_migrate": _migrate_here}.get(cmd, _show)())
