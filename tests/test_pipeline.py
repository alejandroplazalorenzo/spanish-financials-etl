"""Per-filing isolation, offline: a failing download or transform never stops the run."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from sfetl import extract, pipeline

FIXTURES = Path(__file__).parent / "fixtures"

GOOD = ["endesa_2024.json", "amper_2024.json", "realia_2024.json"]
BROKEN_DOWNLOAD = "inditex_fy2024.json"
BROKEN_JSON = "bankinter_2024.json"


@pytest.fixture
def offline(monkeypatch, fixture_meta, tmp_path: Path):
    names = [*GOOD, BROKEN_DOWNLOAD, BROKEN_JSON]
    metas = [fixture_meta[n] for n in names]
    by_fxo = {fixture_meta[n].fxo_id: n for n in names}

    def download(meta, delay_s=0.0):
        if by_fxo[meta.fxo_id] == BROKEN_DOWNLOAD:
            raise ConnectionError("simulated 503 from filings.xbrl.org")
        return Path(by_fxo[meta.fxo_id]), False

    def read(path: Path):
        if path.name == BROKEN_JSON:
            raise json.JSONDecodeError("simulated truncated file", "", 0)
        return json.loads((FIXTURES / path.name).read_text(encoding="utf-8"))

    monkeypatch.setattr(extract, "fetch_index", lambda **_: metas)
    monkeypatch.setattr(extract, "save_selection", lambda _: None)  # never touch data/
    monkeypatch.setattr(extract, "download_filing", download)
    monkeypatch.setattr(extract, "raw_path", lambda meta: Path(by_fxo[meta.fxo_id]))
    monkeypatch.setattr(extract, "read_filing", read)
    return tmp_path


def test_failed_filings_are_isolated_reported_and_make_the_run_red(offline: Path) -> None:
    summary = pipeline.run(fiscal_years=[2024], load_db=False, reports_dir=offline)
    stages = {f["stage"] for f in summary.failed_filings}
    assert stages == {"download", "transform"}
    assert summary.exit_code == 1
    assert summary.metric_values > 0  # the good filings went through
    report = (offline / "validation_report.md").read_text(encoding="utf-8")
    assert "**Result: RED**" in report
    assert "simulated 503" in report and "simulated truncated file" in report


def test_unmapped_extensions_are_written_not_dropped(offline: Path) -> None:
    summary = pipeline.run(fiscal_years=[2024], load_db=False, reports_dir=offline)
    with (offline / "unmapped_concepts.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == summary.unmapped_extension_facts > 0
    assert all(not r["concept"].startswith("ifrs-full:") for r in rows)
    assert {"looks_like_revenue", "company_has_ifrs_revenue"} <= set(rows[0])
