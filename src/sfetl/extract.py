"""Extract: list Spanish ESEF filings on filings.xbrl.org, select a set and cache xBRL-JSON files.

The public JSON:API is documented at https://filings.xbrl.org/docs/api. Every download is
cached under ``data/`` so re-runs never hit the server again, and requests are spaced by a
small delay to stay a polite client.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
import truststore

from sfetl.config import DATA_DIR, FILINGS_BASE_URL, user_agent

log = logging.getLogger(__name__)

API_PAGE_SIZE = 200
DEFAULT_DELAY_S = 1.0


@dataclass(frozen=True)
class FilingMeta:
    """One row of the filings.xbrl.org index, flattened with its entity."""

    filing_id: int
    fxo_id: str
    lei: str
    entity_name: str
    period_end: date
    date_added: datetime
    json_url: str | None
    package_url: str | None
    viewer_url: str | None
    error_count: int
    warning_count: int
    inconsistency_count: int
    report_url: str | None = None  # the human-readable XHTML report (golden figures come from it)

    @property
    def fiscal_year(self) -> int:
        return fiscal_year_for(self.period_end)

    @property
    def sequence(self) -> int:
        """Trailing number of the fxo_id (0 for the first filing of a period, 1 for the next)."""
        tail = self.fxo_id.rsplit("-", 1)[-1]
        return int(tail) if tail.isdigit() else 0

    def to_json(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["period_end"] = self.period_end.isoformat()
        raw["date_added"] = self.date_added.isoformat()
        return raw


def fiscal_year_for(period_end: date) -> int:
    """Label a fiscal year by the calendar year holding most of its months.

    A year ending 31 Jan 2025 (Inditex) is FY2024; one ending 31 Mar 2024 is FY2023;
    one ending 30 Jun or later is labelled with the year in which it ends.
    """
    return period_end.year if period_end.month >= 6 else period_end.year - 1


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace(" ", "T"))


def parse_index_page(payload: dict[str, Any]) -> list[FilingMeta]:
    """Turn one JSON:API page (with ``include=entity``) into FilingMeta rows."""
    entities: dict[str, dict[str, Any]] = {
        item["id"]: item["attributes"]
        for item in payload.get("included", [])
        if item.get("type") == "entity"
    }
    rows: list[FilingMeta] = []
    for item in payload.get("data", []):
        attrs = item["attributes"]
        entity_ref = item["relationships"]["entity"]["data"]
        entity = entities.get(entity_ref["id"], {})
        rows.append(
            FilingMeta(
                filing_id=int(item["id"]),
                fxo_id=attrs["fxo_id"],
                lei=entity.get("identifier") or attrs["fxo_id"].split("-", 1)[0],
                entity_name=(entity.get("name") or "").strip(),
                period_end=date.fromisoformat(attrs["period_end"]),
                date_added=_parse_datetime(attrs["date_added"]),
                json_url=attrs.get("json_url"),
                package_url=attrs.get("package_url"),
                viewer_url=attrs.get("viewer_url"),
                error_count=int(attrs.get("error_count") or 0),
                warning_count=int(attrs.get("warning_count") or 0),
                inconsistency_count=int(attrs.get("inconsistency_count") or 0),
                report_url=attrs.get("report_url"),
            )
        )
    return rows


def http_session() -> requests.Session:
    # Verify TLS against the operating-system trust store (works behind corporate proxies
    # or antivirus TLS inspection, where the bundled certifi roots do not).
    truststore.inject_into_ssl()
    session = requests.Session()
    session.headers["User-Agent"] = user_agent()
    return session


def index_url(country: str, page: int) -> str:
    flt = quote(
        json.dumps([{"name": "country", "op": "eq", "val": country}], separators=(",", ":"))
    )
    return (
        f"{FILINGS_BASE_URL}/api/filings?filter={flt}"
        f"&page%5Bsize%5D={API_PAGE_SIZE}&page%5Bnumber%5D={page}"
        f"&sort=-date_added&include=entity"
    )


def fetch_index(
    country: str = "ES",
    cache_dir: Path = DATA_DIR / "index",
    refresh: bool = False,
    delay_s: float = DEFAULT_DELAY_S,
) -> list[FilingMeta]:
    """Download (or read from cache) every filing of ``country`` from the index."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"filings_{country}.json"
    if cache_file.is_file() and not refresh:
        pages = json.loads(cache_file.read_text(encoding="utf-8"))
    else:
        session = http_session()
        pages = []
        page = 1
        while True:
            resp = session.get(index_url(country, page), timeout=60)
            resp.raise_for_status()
            payload = resp.json()
            pages.append(payload)
            total = int(payload.get("meta", {}).get("count", 0))
            log.info("index page %d: %d rows (total %d)", page, len(payload["data"]), total)
            if not payload.get("links", {}).get("next") or not payload["data"]:
                break
            page += 1
            time.sleep(delay_s)
        cache_file.write_text(json.dumps(pages), encoding="utf-8")
    rows: list[FilingMeta] = []
    for payload in pages:
        rows.extend(parse_index_page(payload))
    return rows


def latest_fiscal_years(filings: Iterable[FilingMeta], n_years: int, min_filings: int) -> list[int]:
    """The ``n_years`` most recent fiscal years that have at least ``min_filings`` filings."""
    counts = Counter(f.fiscal_year for f in filings)
    eligible = sorted((fy for fy, n in counts.items() if n >= min_filings), reverse=True)
    return sorted(eligible[:n_years])


def select_filings(
    filings: Iterable[FilingMeta],
    fiscal_years: Iterable[int],
    max_companies: int | None = None,
) -> list[FilingMeta]:
    """Keep the latest filing per (company, fiscal year) for the requested fiscal years.

    "Latest" is deterministic: most recent ``date_added``, then the higher fxo_id sequence,
    then the higher filing id. It is a heuristic: the index does not say which of two reports
    for the same period supersedes the other (they may be an amendment or just another
    language), and its sequence numbers follow the order in which reports were *added*.
    Filings without an xBRL-JSON rendering cannot be used and are dropped. ``max_companies``
    (optional) keeps the companies with the most selected years, ties broken by LEI, so a
    smaller run is still reproducible.
    """
    wanted = set(fiscal_years)
    best: dict[tuple[str, int], FilingMeta] = {}
    for f in filings:
        if f.fiscal_year not in wanted or not f.json_url:
            continue
        key = (f.lei, f.fiscal_year)
        current = best.get(key)
        if current is None or _recency(f) > _recency(current):
            best[key] = f
    chosen = sorted(best.values(), key=lambda f: (f.lei, f.fiscal_year))
    if max_companies is not None:
        per_company: dict[str, int] = defaultdict(int)
        for f in chosen:
            per_company[f.lei] += 1
        keep = sorted(per_company, key=lambda lei: (-per_company[lei], lei))[:max_companies]
        chosen = [f for f in chosen if f.lei in set(keep)]
    return chosen


def _recency(f: FilingMeta) -> tuple[datetime, int, int]:
    return (f.date_added, f.sequence, f.filing_id)


def raw_path(meta: FilingMeta, cache_dir: Path = DATA_DIR / "raw") -> Path:
    return cache_dir / meta.lei / f"{meta.fxo_id}.json.gz"


def download_filing(
    meta: FilingMeta,
    cache_dir: Path = DATA_DIR / "raw",
    session: requests.Session | None = None,
    delay_s: float = DEFAULT_DELAY_S,
) -> tuple[Path, bool]:
    """Download the xBRL-JSON of one filing into a gzip cache. Returns (path, downloaded)."""
    if not meta.json_url:
        raise ValueError(f"{meta.fxo_id} has no xBRL-JSON rendering")
    target = raw_path(meta, cache_dir)
    if target.is_file():
        return target, False
    target.parent.mkdir(parents=True, exist_ok=True)
    session = session or http_session()
    url = FILINGS_BASE_URL + meta.json_url
    with session.get(url, timeout=180, stream=True, headers={"Accept-Encoding": "gzip"}) as resp:
        resp.raise_for_status()
        body = resp.raw.read(decode_content=False)
        encoding = resp.headers.get("Content-Encoding", "")
    if "gzip" not in encoding:
        body = gzip.compress(body)
    json.loads(gzip.decompress(body))  # fail early on a truncated or non-JSON body
    tmp = target.with_suffix(".part")
    tmp.write_bytes(body)
    tmp.replace(target)
    time.sleep(delay_s)
    return target, True


def read_filing(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def save_selection(selected: list[FilingMeta], path: Path = DATA_DIR / "selection.json") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([f.to_json() for f in selected], indent=1), encoding="utf-8")


def load_selection(path: Path = DATA_DIR / "selection.json") -> list[FilingMeta]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [
        FilingMeta(
            **{
                **row,
                "period_end": date.fromisoformat(row["period_end"]),
                "date_added": datetime.fromisoformat(row["date_added"]),
            }
        )
        for row in rows
    ]
