"""Backup + restore round trip (needs pg_dump/pg_restore on PATH, same major as the server)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sfetl.backup import backup, restore, verify
from sfetl.config import DbSettings

pytestmark = pytest.mark.integration


def _client_major() -> int | None:
    if shutil.which("pg_dump") is None or shutil.which("pg_restore") is None:
        return None
    out = subprocess.run(["pg_dump", "--version"], capture_output=True, text=True).stdout
    match = re.search(r"(\d+)\.", out)
    return int(match.group(1)) if match else None


def test_backup_then_restore_gives_the_same_counts(loaded, tmp_path: Path) -> None:
    owner: DbSettings = loaded["owner"]
    client = _client_major()
    if client is None:
        pytest.skip("pg_dump/pg_restore not on PATH (use `sfetl backup --docker` locally)")
    import psycopg

    with psycopg.connect(**owner.connect_kwargs()) as conn:  # type: ignore[arg-type]
        server = int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000  # type: ignore[index]
    if client < server:
        pytest.skip(f"pg_dump {client} is older than the server ({server})")
    dump = backup(tmp_path / "sfetl.dump", settings=owner)
    target = f"{owner.dbname}_restored"
    restored = restore(dump, target, settings=owner)
    try:
        ok, detail = verify(owner, restored)
        assert ok, detail
        assert detail["financial_fact"][0] > 0
    finally:
        with psycopg.connect(**owner.connect_kwargs(), autocommit=True) as conn:  # type: ignore[arg-type]
            conn.execute(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)')  # type: ignore[arg-type]
