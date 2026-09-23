"""PostgreSQL connection helpers."""

from __future__ import annotations

import psycopg

from sfetl.config import DbSettings, owner_db, reader_db


def connect(settings: DbSettings, **kwargs: object) -> psycopg.Connection:
    return psycopg.connect(**settings.connect_kwargs(), **kwargs)  # type: ignore[arg-type]


def connect_owner(**kwargs: object) -> psycopg.Connection:
    return connect(owner_db(), **kwargs)


def connect_reader(**kwargs: object) -> psycopg.Connection:
    return connect(reader_db(), **kwargs)
