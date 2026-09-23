from pathlib import Path

import pytest

from sfetl.migrate import MigrationError, discover


def test_repository_migrations_are_well_formed() -> None:
    migrations = discover()
    assert [m.version for m in migrations] == sorted(m.version for m in migrations)
    assert migrations[0].version == "001"
    assert all(len(m.checksum) == 64 for m in migrations)


def test_badly_named_file_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "1_init.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError):
        discover(tmp_path)


def test_duplicate_versions_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "001_a.sql").write_text("SELECT 1;")
    (tmp_path / "001_b.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError):
        discover(tmp_path)
