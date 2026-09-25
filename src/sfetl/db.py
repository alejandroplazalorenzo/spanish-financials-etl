"""PostgreSQL connection helpers, one per role."""

from __future__ import annotations

import psycopg

from sfetl.config import DbSettings, assistant_db, owner_db, reader_db


def connect(settings: DbSettings, **kwargs: object) -> psycopg.Connection:
    return psycopg.connect(**settings.connect_kwargs(), **kwargs)  # type: ignore[arg-type]


def connect_owner(**kwargs: object) -> psycopg.Connection:
    return connect(owner_db(), **kwargs)


def connect_reader(**kwargs: object) -> psycopg.Connection:
    return connect(reader_db(), **kwargs)


def connect_assistant(**kwargs: object) -> psycopg.Connection:
    return connect(assistant_db(), **kwargs)
