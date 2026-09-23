"""Tiny migration runner.

Applies ``db/migrations/NNN_name.sql`` in order, each in its own transaction, and records it in
``schema_migrations`` with a checksum. A migration that was edited after being applied is an
error: fix forward with a new numbered file instead.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql

from sfetl.config import MIGRATIONS_DIR

MIGRATION_RE = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")
LOCK_KEY = 7_345_001  # arbitrary advisory-lock key: one runner at a time
READER_ROLE = "sfetl_reader"


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    path: Path

    @property
    def sql_text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def checksum(self) -> str:
        # Hash with normalised line endings: a Windows checkout (CRLF) and a Linux one (LF)
        # of the same migration must not look like an edited file.
        return hashlib.sha256(self.path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


class MigrationError(RuntimeError):
    pass


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    found: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_RE.match(path.name)
        if not match:
            raise MigrationError(f"badly named migration file: {path.name}")
        found.append(Migration(version=match.group(1), name=match.group(2), path=path))
    versions = [m.version for m in found]
    if len(versions) != len(set(versions)):
        raise MigrationError(f"duplicate migration versions in {directory}")
    return found


def _ensure_table(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    text        PRIMARY KEY,
            name       text        NOT NULL,
            checksum   text        NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def apply_migrations(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations. Returns the versions applied in this call."""
    migrations = discover(directory)
    applied_now: list[str] = []
    conn.autocommit = False
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
        _ensure_table(conn)
        rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    done = {version: checksum for version, checksum in rows}
    for migration in migrations:
        if migration.version in done:
            if done[migration.version] != migration.checksum:
                raise MigrationError(
                    f"migration {migration.path.name} changed after it was applied; "
                    "create a new migration instead of editing it"
                )
            continue
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
            already = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = %s", (migration.version,)
            ).fetchone()
            if already:
                continue
            conn.execute(migration.sql_text)  # type: ignore[arg-type]
            conn.execute(
                "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                (migration.version, migration.name, migration.checksum),
            )
        applied_now.append(migration.version)
    return applied_now


def set_reader_password(conn: psycopg.Connection, password: str) -> None:
    """Enable LOGIN on the read-only role with the given password (never stored in git)."""
    if not password:
        raise MigrationError("SFETL_READER_PASSWORD is empty")
    with conn.transaction():
        conn.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(READER_ROLE), sql.Literal(password)
            )
        )
