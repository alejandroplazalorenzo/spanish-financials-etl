"""Shared fixtures for the integration tests (real PostgreSQL; skipped when unreachable).

Each test gets a throw-away database on the configured server, dropped afterwards. Roles are
cluster-wide, so their passwords come from the environment (as in CI) and are (re)set here.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from dataclasses import replace

import psycopg
import pytest
from dbhelpers import FIXTURE_NAMES, connect, load_results
from psycopg import sql

from sfetl.config import DbSettings, owner_db
from sfetl.migrate import apply_migrations, set_assistant_password, set_reader_password


@pytest.fixture(scope="session")
def admin() -> DbSettings:
    settings = owner_db()
    try:
        with psycopg.connect(**{**settings.connect_kwargs(), "connect_timeout": 3}) as conn:
            conn.execute("SELECT 1")
    except psycopg.OperationalError as err:
        pytest.skip(f"PostgreSQL not available: {str(err).splitlines()[0]}")
    return settings


@pytest.fixture
def fresh_db(admin: DbSettings) -> Iterator[DbSettings]:
    name = f"sfetl_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(**admin.connect_kwargs(), autocommit=True) as conn:  # type: ignore[arg-type]
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield replace(admin, dbname=name)
    finally:
        with psycopg.connect(**admin.connect_kwargs(), autocommit=True) as conn:  # type: ignore[arg-type]
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def _password(env: str) -> str:
    value = os.environ.get(env, "")
    if not value:
        pytest.skip(f"{env} not set")
    return value


@pytest.fixture
def migrated(fresh_db: DbSettings) -> DbSettings:
    with connect(fresh_db, autocommit=False) as conn:
        apply_migrations(conn)
    return fresh_db


@pytest.fixture
def roles(migrated: DbSettings) -> dict[str, DbSettings]:
    reader_pw = _password("SFETL_READER_PASSWORD")
    assistant_pw = _password("SFETL_ASSISTANT_PASSWORD")
    with connect(migrated, autocommit=False) as conn:
        set_reader_password(conn, reader_pw)
        set_assistant_password(conn, assistant_pw)
    return {
        "owner": migrated,
        "reader": replace(migrated, user="sfetl_reader", password=reader_pw),
        "assistant": replace(migrated, user="sfetl_assistant", password=assistant_pw),
    }


@pytest.fixture
def loaded(roles: dict[str, DbSettings], transformed) -> dict[str, DbSettings]:
    load_results(roles["owner"], [transformed(n) for n in FIXTURE_NAMES])
    return roles


@pytest.fixture
def as_role(loaded: dict[str, DbSettings]) -> Callable[[str], Callable[[], psycopg.Connection]]:
    def factory(role: str) -> Callable[[], psycopg.Connection]:
        return lambda: connect(loaded[role], autocommit=False)

    return factory
