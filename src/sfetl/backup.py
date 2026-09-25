"""Backup and a *tested* restore.

``sfetl backup`` writes a custom-format ``pg_dump``; ``sfetl restore`` loads it into a new
database of the same server and then compares the row count of every table of the source with
the restored copy, so a backup is only called good after it has been restored. With
``--docker`` the PostgreSQL client tools of the compose service are used (``docker compose exec``),
so nothing needs installing on the host.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import psycopg
from psycopg import sql

from sfetl.config import PROJECT_ROOT, DbSettings, owner_db

TABLES = (
    "company",
    "fiscal_period",
    "filing",
    "metric",
    "financial_fact",
    "validation_issue",
    "ownership",
    "schema_migrations",
    "assistant.query_log",
    "assistant.pending",
    "assistant.page",
)


class BackupError(RuntimeError):
    pass


def _client(tool: str, use_docker: bool, settings: DbSettings) -> list[str]:
    if use_docker:
        return ["docker", "compose", "exec", "-T", "db", tool, "-U", settings.user]
    if shutil.which(tool) is None:
        raise BackupError(f"{tool} not found on PATH (use --docker)")
    return [tool, "-h", settings.host, "-p", str(settings.port), "-U", settings.user]


def _env(settings: DbSettings) -> dict[str, str]:
    return {**os.environ, "PGPASSWORD": settings.password}


def backup(out: Path, use_docker: bool = False, settings: DbSettings | None = None) -> Path:
    settings = settings or owner_db()
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [*_client("pg_dump", use_docker, settings), "-d", settings.dbname, "-Fc"]
    with out.open("wb") as fh:
        done = subprocess.run(
            cmd, stdout=fh, stderr=subprocess.PIPE, env=_env(settings), cwd=PROJECT_ROOT
        )
    if done.returncode != 0:
        raise BackupError(done.stderr.decode(errors="replace").strip()[:500])
    return out


def restore(
    dump: Path, target_db: str, use_docker: bool = False, settings: DbSettings | None = None
) -> DbSettings:
    """Restore ``dump`` into a fresh database ``target_db`` (dropped first if it exists)."""
    settings = settings or owner_db()
    if target_db == settings.dbname:
        raise BackupError("refusing to restore over the source database")
    with psycopg.connect(**settings.connect_kwargs(), autocommit=True) as conn:  # type: ignore[arg-type]
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(target_db))
        )
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_db)))
    cmd = [*_client("pg_restore", use_docker, settings), "-d", target_db, "--exit-on-error"]
    with dump.open("rb") as fh:
        done = subprocess.run(
            cmd, stdin=fh, capture_output=True, env=_env(settings), cwd=PROJECT_ROOT
        )
    if done.returncode != 0:
        raise BackupError(done.stderr.decode(errors="replace").strip()[:500])
    return replace(settings, dbname=target_db)


def table_counts(settings: DbSettings) -> dict[str, int]:
    counts: dict[str, int] = {}
    with psycopg.connect(**settings.connect_kwargs()) as conn:  # type: ignore[arg-type]
        for table in TABLES:
            schema, _, name = table.rpartition(".")
            ident = sql.Identifier(schema, name) if schema else sql.Identifier(name)
            row = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(ident)).fetchone()
            counts[table] = int(row[0]) if row else -1
    return counts


def verify(source: DbSettings, restored: DbSettings) -> tuple[bool, dict[str, tuple[int, int]]]:
    a, b = table_counts(source), table_counts(restored)
    detail = {t: (a[t], b[t]) for t in TABLES}
    return all(x == y for x, y in detail.values()), detail
