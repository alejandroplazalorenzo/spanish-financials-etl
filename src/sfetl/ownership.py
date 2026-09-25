"""Ownership structure: who is the parent and the ultimate parent of each company.

Two public sources, resolved in two passes (the design of the system this project rebuilds:
store every edge as declared, resolve the counterparty afterwards, keep what does not resolve):

1. **ESEF** (pass 1, per filing). IAS 1.138(c) asks for the name of the parent and of the
   ultimate parent; ESEF tags them as ``ifrs-full:NameOfParentEntity`` and
   ``ifrs-full:NameOfUltimateParentOfGroup``. The values are free text and noisy: HTML spans
   inside words, whole sentences ("La Sociedad dominante está controlada por X, domiciliada
   en..."), "No hay", and very often the filer's own name. ``clean_parent_name`` turns each
   value into a name, "none declared" or "unparsed" (the raw text is always kept).
2. **GLEIF Level 2** (pass 2, after every filing is loaded). The LEI register publishes
   "who owns whom" relationships between LEIs (direct and ultimate accounting-consolidation
   parent) or a reporting exception explaining why there is none. Data is CC0.

Resolution of an ESEF statement, in order: the filer itself (self-reference) -> the LEI that
GLEIF gives for the same relation when its legal name matches -> a loaded company with the same
normalised name -> unresolved (kept by name). Cycles and self-references are marked
``contradictory`` in the curated view ``v_ownership`` rather than silently dropped or "fixed".
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import requests

from sfetl.concepts import PARENT_CONCEPTS
from sfetl.config import DATA_DIR
from sfetl.oim import iter_text_facts

log = logging.getLogger(__name__)

Relation = Literal["direct", "ultimate"]
StatementStatus = Literal["named", "none_declared", "unparsed"]
Resolution = Literal["lei", "name", "self", "none_declared", "unparsed", "unresolved"]

GLEIF_API = "https://api.gleif.org/api/v1/lei-records"
MAX_NAME_LENGTH = 120
MAX_NAME_WORDS = 14

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_NONE_RE = re.compile(
    r"^(?:no hay|n/?a|ninguna?|none|not applicable|no aplica|-+)\.?$", re.IGNORECASE
)
_LABEL_PREFIX_RE = re.compile(r"^nombre de la (?:sociedad )?dominante[^:]{0,40}:\s*", re.IGNORECASE)
# "... controlled by X", "... headed by its majority shareholder X"
_CONTROLLED_BY_RE = re.compile(
    r"(?:est[aá] controlada por|encabezado por(?: su socio mayoritario)?|controlled by|"
    r"headed by)\s+(.+)$",
    re.IGNORECASE,
)
# "La Sociedad dominante, X, es matriz de..."
_PARENT_IS_RE = re.compile(r"^la sociedad dominante,\s*(.+?),\s*es\b", re.IGNORECASE)
_CUT_RES = (
    re.compile(r"\s*\((?:en adelante|hereinafter|antes|formerly)\b.*$", re.IGNORECASE),
    re.compile(r",?\s+es la sociedad dominante.*$", re.IGNORECASE),
    re.compile(r",?\s+sociedad dominante\b.*$", re.IGNORECASE),
    re.compile(r",?\s+domiciliada en\b.*$", re.IGNORECASE),
    re.compile(r",?\s+is the parent\b.*$", re.IGNORECASE),
    re.compile(r"\s*\([^()]{1,40}\)\s*$"),  # a short trailing alias such as "(Inditex)"
)
# Legal-form tokens ignored when two names are compared (after dots are removed).
_LEGAL_TOKENS = frozenset(
    {
        "SA",
        "SAU",
        "SL",
        "SLU",
        "SLL",
        "SE",
        "SPA",
        "SAPI",
        "CV",
        "SARL",
        "RL",
        "LTD",
        "LIMITED",
        "PLC",
        "NV",
        "BV",
        "AG",
        "GMBH",
        "INC",
        "UNIPERSONAL",
    }
)
_LEGAL_PHRASES = ("SOCIEDAD ANONIMA", "SOCIEDAD LIMITADA", "SOCIEDAD UNIPERSONAL")


@dataclass(frozen=True)
class ParentStatement:
    """What one filing declares about the parent (``direct``) or ultimate parent."""

    relation: Relation
    raw: str
    name: str | None  # cleaned; None when nothing usable was declared
    status: StatementStatus


def normalise_text(raw: str) -> str:
    """HTML tags out, entities decoded, non-breaking spaces and runs of spaces collapsed."""
    text = html.unescape(_TAG_RE.sub("", raw)).replace("\xa0", " ")
    return _SPACE_RE.sub(" ", text).strip()


def clean_parent_name(raw: str) -> tuple[str | None, StatementStatus]:
    """Turn a noisy ESEF parent-name value into ``(name, status)``."""
    text = _LABEL_PREFIX_RE.sub("", normalise_text(raw))
    if not text or _NONE_RE.match(text):
        return None, "none_declared"
    for pattern in (_CONTROLLED_BY_RE, _PARENT_IS_RE):
        match = pattern.search(text)
        if match:
            text = match.group(1)
            break
    for cut in _CUT_RES:
        text = cut.sub("", text)
    text = text.strip().rstrip(",;:").strip()
    if not text or _NONE_RE.match(text):
        return None, "none_declared"
    if len(text) > MAX_NAME_LENGTH or len(text.split()) > MAX_NAME_WORDS:
        return None, "unparsed"
    return text, "named"


def name_key(name: str) -> str:
    """Comparison key: no accents, no punctuation, upper case, legal forms removed."""
    text = unicodedata.normalize("NFKD", normalise_text(name))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).upper()
    text = text.replace(".", "").replace("&", " Y ")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    for phrase in _LEGAL_PHRASES:
        text = text.replace(phrase, " ")
    tokens = [t for t in text.split() if t not in _LEGAL_TOKENS]
    while tokens and tokens[-1] in {"DE", "Y"}:
        tokens.pop()
    return " ".join(tokens)


def same_name(a: str, b: str) -> bool:
    """Same key, also when one side spells initials apart ("FCyC" vs "F C Y C")."""
    ka, kb = name_key(a), name_key(b)
    return bool(ka) and (ka == kb or ka.replace(" ", "") == kb.replace(" ", ""))


def extract_parent_statements(report: dict[str, Any]) -> list[ParentStatement]:
    """One statement per relation (the first undimensioned fact of each concept)."""
    found: dict[Relation, ParentStatement] = {}
    for fact in iter_text_facts(report, frozenset(PARENT_CONCEPTS)):
        relation: Relation = "direct" if PARENT_CONCEPTS[fact.concept] == "direct" else "ultimate"
        if fact.dimensions or relation in found:
            continue
        name, status = clean_parent_name(fact.value)
        found[relation] = ParentStatement(
            relation=relation, raw=fact.value[:2000], name=name, status=status
        )
    return [found[r] for r in ("direct", "ultimate") if r in found]  # type: ignore[index]


# --------------------------------------------------------------------------------------------
# GLEIF Level 2
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GleifParent:
    relation: Relation
    parent_lei: str | None
    parent_name: str | None
    exception: str | None  # reporting-exception reason when no parent is reported


@dataclass
class GleifRecord:
    lei: str
    parents: list[GleifParent] = field(default_factory=list)

    def for_relation(self, relation: Relation) -> GleifParent | None:
        return next((p for p in self.parents if p.relation == relation), None)


def _gleif_get(session: requests.Session, url: str) -> dict[str, Any] | None:
    resp = session.get(url, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def fetch_gleif_raw(
    lei: str, session: requests.Session, delay_s: float = 1.0
) -> dict[str, Any]:  # pragma: no cover - network
    """Download the direct/ultimate parent (or the reporting exception) of one LEI."""
    raw: dict[str, Any] = {"lei": lei}
    for relation in ("direct", "ultimate"):
        parent = _gleif_get(session, f"{GLEIF_API}/{lei}/{relation}-parent")
        time.sleep(delay_s)
        exception = None
        if parent is None:
            exception = _gleif_get(
                session, f"{GLEIF_API}/{lei}/{relation}-parent-reporting-exception"
            )
            time.sleep(delay_s)
        raw[relation] = parent
        raw[f"{relation}_exception"] = exception
    return raw


def parse_gleif_raw(raw: dict[str, Any]) -> GleifRecord:
    record = GleifRecord(lei=raw["lei"])
    for relation in ("direct", "ultimate"):
        parent = raw.get(relation)
        exception = raw.get(f"{relation}_exception")
        rel: Relation = "direct" if relation == "direct" else "ultimate"
        if parent and parent.get("data"):
            attrs = parent["data"]["attributes"]
            record.parents.append(
                GleifParent(
                    relation=rel,
                    parent_lei=attrs["lei"],
                    parent_name=attrs["entity"]["legalName"]["name"],
                    exception=None,
                )
            )
        elif exception and exception.get("data"):
            reason = exception["data"]["attributes"].get("reason") or "UNKNOWN"
            record.parents.append(GleifParent(rel, None, None, str(reason)))
    return record


def load_gleif(
    leis: Iterable[str],
    cache_dir: Path = DATA_DIR / "gleif",
    offline: bool = False,
    delay_s: float = 1.0,
) -> tuple[dict[str, GleifRecord], list[tuple[str, str]]]:
    """GLEIF records for ``leis`` from the cache, downloading the missing ones unless offline.

    Returns (records, failures). A failed download is reported, never fatal.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    records: dict[str, GleifRecord] = {}
    failures: list[tuple[str, str]] = []
    session: requests.Session | None = None
    for lei in sorted(set(leis)):
        path = cache_dir / f"{lei}.json"
        if path.is_file():
            records[lei] = parse_gleif_raw(json.loads(path.read_text(encoding="utf-8")))
            continue
        if offline:
            continue
        try:  # pragma: no cover - network
            if session is None:
                from sfetl.extract import http_session

                session = http_session()
            raw = fetch_gleif_raw(lei, session, delay_s=delay_s)
            path.write_text(json.dumps(raw), encoding="utf-8")
            records[lei] = parse_gleif_raw(raw)
        except requests.RequestException as err:  # pragma: no cover - network
            failures.append((lei, str(err).splitlines()[0][:200]))
            log.warning("GLEIF %s: %s", lei, err)
    return records, failures


# --------------------------------------------------------------------------------------------
# Resolution (pure: the database pass in load.py feeds it rows and writes the answers back)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CompanyRef:
    company_id: int
    lei: str
    name: str


@dataclass(frozen=True)
class ResolvedParent:
    parent_company_id: int | None
    parent_lei: str | None
    resolution: Resolution


class CompanyIndex:
    """Loaded companies by LEI and by normalised name (ambiguous names never match)."""

    def __init__(self, companies: Sequence[CompanyRef]) -> None:
        self.by_lei = {c.lei: c for c in companies}
        by_key: dict[str, list[CompanyRef]] = {}
        for c in companies:
            by_key.setdefault(name_key(c.name), []).append(c)
        self.by_key = {k: v[0] for k, v in by_key.items() if len(v) == 1 and k}

    def by_name(self, name: str) -> CompanyRef | None:
        return self.by_key.get(name_key(name))


def resolve_esef_statement(
    company: CompanyRef,
    name: str | None,
    status: StatementStatus,
    gleif: GleifParent | None,
    index: CompanyIndex,
) -> ResolvedParent:
    if status == "none_declared":
        return ResolvedParent(None, None, "none_declared")
    if status == "unparsed" or name is None:
        return ResolvedParent(None, None, "unparsed")
    key = name_key(name)
    if key and key == name_key(company.name):
        return ResolvedParent(company.company_id, company.lei, "self")
    if gleif and gleif.parent_lei and gleif.parent_name and same_name(gleif.parent_name, name):
        if gleif.parent_lei == company.lei:
            return ResolvedParent(company.company_id, company.lei, "self")
        target = index.by_lei.get(gleif.parent_lei)
        if target is not None:
            return ResolvedParent(target.company_id, target.lei, "lei")
        return ResolvedParent(None, gleif.parent_lei, "unresolved")
    target = index.by_name(name)
    if target is not None:
        if target.company_id == company.company_id:
            return ResolvedParent(company.company_id, company.lei, "self")
        return ResolvedParent(target.company_id, target.lei, "name")
    return ResolvedParent(None, None, "unresolved")


def resolve_gleif_parent(
    company: CompanyRef, parent: GleifParent, index: CompanyIndex
) -> ResolvedParent:
    if parent.parent_lei is None:
        return ResolvedParent(None, None, "none_declared")
    if parent.parent_lei == company.lei:
        return ResolvedParent(company.company_id, company.lei, "self")
    target = index.by_lei.get(parent.parent_lei)
    if target is not None:
        return ResolvedParent(target.company_id, target.lei, "lei")
    return ResolvedParent(None, parent.parent_lei, "unresolved")
