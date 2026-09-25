# spanish-financials-etl

[![CI](https://github.com/alejandroplazalorenzo/spanish-financials-etl/actions/workflows/ci.yml/badge.svg)](https://github.com/alejandroplazalorenzo/spanish-financials-etl/actions/workflows/ci.yml)

Turns the official annual reports of Spanish listed companies (ESEF, tagged with XBRL) into a
PostgreSQL dataset: 40 IFRS metrics per company and fiscal year in a governed long table, the
parent and ultimate parent of each company, validation that flags what does not add up, and an
assistant that answers questions in English or Spanish by routing them to fixed, tested queries.

It is a rebuild, on public data, of the financial-statements ETL and the natural-language
assistant I built at work. The design decisions come from that system; every figure below was
measured again here.

## What it does

```
 filings.xbrl.org /api/filings (country = ES)          api.gleif.org (LEI Level 2, CC0)
   │ index + one xBRL-JSON per filing (cached)            │ parent / ultimate parent per LEI
   ▼                                                      │
 1. EXTRACT ─ per filing: a failed download is recorded, the run goes on
 2. TRANSFORM ─ per filing: 40 metrics, nil facts, units, duplicates, extensions, parent names
 3. VALIDATE (before loading) ─ 9 rules on the filers' data; they flag, never edit
 4. LOAD ─ ONE TRANSACTION PER FILING; idempotent; collisions counted, never merged
 5. OWNERSHIP, second pass ─ resolve parents by LEI (GLEIF) or normalised name; keep the rest
 6. CHECK (after loading, in the database) ─ coverage, orphans, nil, golden figures,
    acceptance queries ─▶ reports/validation_report.md; exit code 1 if anything is red
   │
   ▼  curated views only (v_financial, v_company, v_ownership ...), role sfetl_assistant
 ASSISTANT ─ the LLM routes the question to one of 22 intents (JSON-schema output), or says
            none fits; parameters bound, never interpolated; missing ones asked for; A/B when
            ambiguous; free SQL only as a last resort, guarded and ALWAYS marked "not verified";
            every question logged in assistant.query_log. CLI, or an optional Telegram webhook.
```

Code: `src/sfetl/` — `extract.py`, `oim.py` (xBRL-JSON reader), `concepts.py` (catalogue),
`transform.py`, `ownership.py`, `validate.py`, `load.py`, `validate_db.py`, `pipeline.py`,
`migrate.py`, `backup.py`, `telegram.py`, `cli.py`, and `ask/` (`intents.py`, `classify.py`,
`llm.py`, `guardrails.py`, `service.py`, `render.py`, `smoke.py`, `bench.py`, `evaluate.py`).

## Data model

| Object | Grain | Notes |
|---|---|---|
| `company` | one per LEI | surrogate `company_id`, `lei` UNIQUE; name of its latest filing |
| `fiscal_period` | company × fiscal year | UNIQUE (company, year); start, end, months |
| `filing` | one per report used | surrogate id; filings.xbrl.org id and `fxo_id` UNIQUE; XHTML and viewer links |
| `metric` | 40 rows | code UNIQUE, label, statement, category, unit, period type, parent, order |
| `financial_fact` | fiscal period × metric | `value` NULL only when `is_nil` (CHECK); `source_concept`, `decimals`, `filing_id` |
| `validation_issue` | one per flag | rule, severity, metric, detail |
| `ownership` | company × source × relation (× filing) | declared text, cleaned name, parent LEI, parent company if loaded, resolution |
| `assistant.query_log` / `pending` / `page` | assistant state | modes, generated SQL, latencies, rating; A/B choices; pages |
| `v_financial` (view) | long | company, year, statement, category, metric, **unit**, value, `is_nil`, **`is_reported`** |
| `v_company`, `v_metric`, `v_company_ratios`, `v_filing`, `v_validation_issue`, `v_ownership` (views) | | the only relations the assistant can read |

Adding a metric is one `INSERT` into `metric` (in a new migration) plus its concept list in
`concepts.py`; `v_financial` and every intent pick it up without DDL.

## How the transform reads ESEF

| Problem | Rule |
|---|---|
| Concepts | Only `ifrs-full` concepts, in priority order (revenue = `Revenue`, else `RevenueFromContractsWithCustomers`). `RevenueAndOperatingIncome` is not revenue. |
| Extensions | Company extension concepts are **not mapped** (their meaning lives in anchoring relationships the xBRL-JSON does not carry) but **not dropped** either: every current-year, undimensioned EUR extension fact goes to `reports/unmapped_concepts.csv`, with a narrow `looks_like_revenue` hint. |
| Units | Each metric has one unit (EUR, or EUR per share for EPS). A fact in any other unit is flagged `non_eur_unit` and not loaded; nothing is converted. |
| Nil facts | A fact tagged nil is "not available": neither a value nor zero. A concept with a value wins; if only nil facts exist the metric is loaded as NULL + `is_nil`. |
| Derived total | Total liabilities, when not tagged, is non-current + current liabilities (`derived:` lineage, `is_reported = false`). Never Assets − Equity. |
| Periods | OIM writes the instant 31-12-2024 as `2025-01-01T00:00:00`; converted to inclusive dates. Current year only: instants on the period end, durations of 350–380 days ending on it. |
| Fiscal year label | Calendar year holding most of the months: year ending 31-01-2025 (Inditex) = FY2024. |
| Dimensions | Only facts without taxonomy dimensions are consolidated totals. |
| Duplicates | Values beat nil; then the most precise (`decimals`, INF first); then document order. Disagreeing duplicates are kept and flagged. |
| Banks and insurers | Detected from bank/insurance concepts; revenue and the current/non-current split are not expected from them. |
| Parent names | `ifrs-full:NameOfParentEntity` / `NameOfUltimateParentOfGroup` are free text: HTML spans inside words, sentences ("La Sociedad dominante está controlada por X, domiciliada en ..."), "No hay". Cleaned into a name, "none declared" or "unparsed"; the raw text is kept. |

## Decisions from my production system (no internal figures)

These are the decisions of the system I built at work (financial statements of company groups
loaded from a commercial database, and a Telegram assistant over them). The data here is
different; the decisions are the same unless the table says otherwise.

| Decision in production | How it appears here |
|---|---|
| **The file is the unit of atomicity.** One transaction per file; a bad file is rolled back, logged, and the run continues; the process exits with code 1. | One transaction per **filing**; download/transform/load errors are caught per filing, listed under "Failed filings" in the report, and `sfetl run` exits 1. |
| **Nothing is discarded silently.** Unmapped labels go to a CSV, dropped values become warnings, the validation report lists failed files. | `reports/unmapped_concepts.csv`, load warnings, failed filings and red checks in `reports/validation_report.md`, non-zero exit code. |
| **Idempotence** by natural key + delete-and-insert of everything a file owns. | Upserts on LEI / filings.xbrl.org id / company-year; facts, flags and ownership statements replaced by `filing_id`. Loading twice leaves the database identical (tested). |
| **Surrogate keys, natural keys UNIQUE.** A natural key can be wrong at the source; integers are cheaper in every foreign key; joins look the same. | Migration 005. The first version of this repository used natural primary keys; 005 corrects it. |
| **Long fact table + governed catalogue**, not a free EAV: a metric is an `INSERT`, every fact references the catalogue. | `metric` (40 rows, hierarchy, unit, category) + `financial_fact` + the long view `v_financial`. |
| **Three states: value, zero, not available.** | Nil facts → NULL + `is_nil`; a CHECK makes "nil with a value" impossible. |
| **Collisions:** `ON CONFLICT DO NOTHING`, but count what was not inserted and warn. | Same: a second filing for an already loaded company-year inserts nothing and the warning names the filing that holds the values. (The first version blocked the whole load instead; that is gone.) |
| **Validation after loading, in the database:** coverage, orphans, "no file without facts", golden figures checked by hand, acceptance queries printed in the report. | `validate_db.py`, plus the 9 rules on the filers' data before loading. Golden figures are versioned here because they come from public reports. |
| **Trust the data, not the label; NULL before a disguised figure.** In production a group figure was NULL unless consolidated accounts existed, rather than an individual company's figure passed off as the group's. | `is_reported = false` on every derived value; ratios only from reported values, NULL otherwise; no cross-company aggregate views (the "ratio of sums" view of the first version was not a production decision and was removed, migration 004). |
| **Ownership as a graph, two passes:** store each edge as declared, resolve the counterparty by identifier, else by normalised name, keep what does not resolve, mark contradictions instead of choosing a direction. | ESEF parent names (pass 1) + GLEIF Level 2 (pass 2, by LEI) → `ownership`; `v_ownership` marks self-references and cycles as `contradictory`. |
| **Assistant: intents first.** The LLM only classifies the question against a catalogue of fixed, tested, parameterised queries over curated views; parameters are bound and filtered to the declared ones; a missing one is asked for; genuine doubt → A/B buttons; free SQL only when nothing fits; the LLM never sees data; everything logged, uncovered questions reviewed and promoted to intents. | Same design, 22 intents (`ask/intents.py`). One improvement: in production the "not verified" warning of a free-SQL answer sat behind a button when there were rows; here it is **always in the answer text**. |
| **Least privilege.** The assistant's role reads curated views (and simple lookups), never raw facts; grants are explicit, no default privileges; it writes only to its own schema, with column-level UPDATE. | `sfetl_assistant`: SELECT on the 7 `v_*` views, `assistant.*` with `UPDATE (rating)` / `UPDATE (pages)` only. Analysts use `sfetl_reader`. |
| **The RLS trap.** A table with RLS and no policy returns 0 rows without error; views bypass RLS because they run as their owner. | Migration 010 + `tests/integration/test_roles.py` proves both: 0 rows on a granted base table, data through the view. |
| **Test every path with the real role.** A missing column GRANT once broke a button in production and was found by a user. | `tests/integration/test_smoke_intents.py` (CI): every intent and every assistant write, connected as `sfetl_assistant`. |
| **Forward-only migrations with a header explaining why.** In production the runner recorded migrations by file name, which let two files share a number. | Version + checksum + advisory lock; duplicate versions refused; editing an applied migration refused (tested). Every migration from 004 explains its reason. |
| **SQL guardrails.** In production: regular expressions and a keyword deny-list, an allow-list of relations shared with the prompt, a forced LIMIT, a read-only transaction and a statement timeout. | Same layers, but the first one parses the SQL into an AST (sqlglot) instead of matching text: a text filter misses `WITH d AS (DELETE …) SELECT` or `SELECT … INTO` and refuses a harmless `'DELETE'` literal; only the SQL regenerated from the checked tree runs. |
| **Model.** Production used a cloud model (Gemini flash-lite) with JSON-schema output and temperature 0. | A swappable provider. Default: local Ollama `qwen3:4b` (`think: false`, `format` = the JSON schema, temperature 0). `SFETL_LLM_PROVIDER=gemini` uses the production-style setup (implemented and unit-tested with recorded responses; not run against the live API here). |

## Re-measured here on public data

All numbers below come from runs on 23 September 2026 on a laptop (PostgreSQL 16 in Docker,
Python 3.13).

### Pipeline

`sfetl migrate` (it upgraded the database of the first version: migrations 004–010 applied on
top of 001–003), then `sfetl run` (GLEIF downloaded at 17:27; the final run, 17:53, read it from
the cache: `--gleif-offline`). Output: `reports/run_summary.json`, `reports/validation_report.md`,
`reports/unmapped_concepts.csv`. Exit code 0.

| | |
|---|---|
| Spanish filings in the index / fiscal years selected | 542 / FY2023 and FY2024 (the two latest with ≥ 20 filings) |
| Filings selected (latest per company-year) / failed | 230 / 0 |
| Companies (banks or insurers) | 125 (11) |
| Numeric facts read / of which nil | 111,370 / 110 |
| Duplicate groups resolved (all consistent) | 856 |
| Values loaded into `financial_fact` / derived (`is_reported = false`) / nil | 7,398 / 145 / 0 |
| Values not inserted because another filing held them | 0 |
| Extension facts listed in `unmapped_concepts.csv` | 6,472 |
| Checks after loading (in the database) | 27 of 27 passed |
| Time: transform / load / ownership pass | 118 s / 8.6 s / 2.1 s (transform took 21–118 s across the five runs of the day; the machine was shared) |
| GLEIF download + resolution (125 LEIs, 1 s between requests) | 536 s, once; cached under `data/gleif/` |

Coverage per metric, company-years out of 230: total assets, equity and cash 228; profit for
the year 220; revenue 190 (4 from `RevenueFromContractsWithCustomers`); current assets and
current liabilities 200; … ; profit from discontinued operations 80 (only filers that have
them). Full list in `run_summary.json`.

### Validation of the filers' data (before loading)

| Rule | Severity | Pass | Flagged | N/A |
|---|---|---:|---:|---:|
| `balance_identity` (0.1 % or rounding) | error | 209 | 13 | 8 |
| `missing_core_metric` | warning | 200 | 30 | 0 |
| `sign_check` | error | 228 | 0 | 2 |
| `component_bounds` | error | 227 | 1 | 2 |
| `subtotal_check` (7 catalogue identities) | warning | 174 | 54 | 2 |
| `period_consistency` | warning | 230 | 0 | 0 |
| `non_eur_unit` | warning | 223 | 7 | 0 |
| `inconsistent_duplicate` | warning | 230 | 0 | 0 |
| `nil_fact` | info | 230 | 0 | 0 |

What the flags are, checked against the raw facts with a script on the same day:

- **Balance identity, 13:** 4 filings tag `ifrs-full:Liabilities` with the value of total equity
  and liabilities (Realia FY2024, Libertas 7 FY2023/FY2024, Innovative Solutions Ecosystem
  FY2023); in 4 (Amper and Global Dominion, both years) the gap equals exactly the tagged
  liabilities held for sale, presented outside the tagged current-liability subtotal; 5 are
  gaps of 0.13–0.30 % the tagged facts do not explain (CIE Automotive, CEVASA, Tubos Reunidos).
- **Subtotal checks, 63 flags in 54 filings:** profit attribution 32, equity and liabilities 9,
  equity split 7, tax bridge 6, liabilities split 4, assets split 4, continuing + discontinued 1.
  In 27 of the 32 profit-attribution flags, profit = parent share **minus** the
  non-controlling-interest share: the NCI line is tagged with the opposite sign.
- **Non-EUR units, 7 filings:** Berkeley Energia reports in AUD (2 filings, nothing of it
  loaded); 5 filings of Spanish companies that report in euros tag basic EPS with the unit
  "AED per share" (UAE dirham), so their EPS is not loaded.
- **Revenue gap:** 16 filings of non-financial companies have no IFRS revenue; 4 of them tag an
  extension concept that looks like a revenue line (Meliá, `RevenuesNotIncludingFinancialIncome`,
  both years; Ercros FY2023, `Ingresos`; Urbas FY2023, `ImporteNetoDeLaCifraDeNegocios`). They
  are listed in the CSV, not mapped.

### Golden figures (read from the published XHTML, not from the xBRL-JSON)

`golden/golden_figures.yaml`: 9 figures transcribed from the printed statements of the XHTML
annual reports on filings.xbrl.org (line label, printed number, scale of the table heading).
All 9 match `v_financial` after the load: Endesa FY2024 revenue (20.935 M€, the line
"Ingresos por Ventas y Prestaciones de Servicios", not the 21.307 "INGRESOS" total above it) and
total assets (37.345 M€); Inditex FY2024 net sales (38,632 M€) and net profit (5,877 M€); Iberdrola
FY2024 total equity (61.051 M€); Bankinter FY2024 total assets (121.971.823 thousand €);
Telefónica FY2023 cash (7,151 M€); Prosegur Cash FY2024 revenue (2.089.879 thousand €) and
profit for the year (91.046 thousand €). The author should re-check them against the rendered
reports.

### Ownership

458 ESEF statements (2 per filing; 1 filing has none) and 250 GLEIF relationships (125 LEIs
× direct/ultimate). In `v_ownership` (latest ESEF statement per company + GLEIF):

| Source, relation | Parent is a loaded company | Parent outside the data (kept, by name or LEI) | Self-reference (contradictory) | None declared / unparsed |
|---|---:|---:|---:|---:|
| ESEF, direct (124 companies) | 3 | 21 | 98 | 0 / 2 |
| ESEF, ultimate (124) | 2 | 33 | 88 | 1 / 0 |
| GLEIF, direct (125) | 5 | 22 | – | 98 (reporting exceptions) |
| GLEIF, ultimate (125) | 3 | 24 | – | 98 |

- Cycles: 0. Self-references: 186 (the filer names itself; most head their own group, but an
  entity cannot be its own parent, so they are marked, not interpreted).
- GLEIF reporting exceptions: `NON_CONSOLIDATING` 88/89, `NO_KNOWN_PERSON` 7/6,
  `NATURAL_PERSONS` 2/2, `NO_LEI` 1/1 (direct/ultimate).
- Where both sources name a parent (34 company-relations), the ESEF name matches GLEIF's legal
  name in 21; the rest differ for real reasons that the view keeps side by side (a Spanish
  branch vs the company, an intermediate vs the ultimate holding, the register today vs the
  report at year end).
- Parents found among the loaded companies: Prosegur Cash → Prosegur, Acciona Energía →
  Acciona, Santander Consumer Finance → Banco Santander (ESEF and GLEIF); Aedas Homes → Neinor
  Homes and Inmocemento → FCC (GLEIF only).

### Assistant

- **Smoke test with the real role** (`sfetl smoke`, 18:00, on the loaded database): 33/33 OK —
  connected as `sfetl_assistant`; the 22 intents run (3 returned 0 rows for the sample company,
  which is allowed); the 10 writes and refusals on its own schema behave (log, rate, A/B choice,
  pages; rewriting a logged question and reading `financial_fact` are refused).
- **Routing benchmark** (`sfetl bench`, `eval/routing_cases.yaml`: 42 synthetic cases in English
  and Spanish — 35 intent, 4 free SQL, 3 decline — with acceptable alternatives; local
  `qwen3:4b`, 24 September 2026): **38/42**. Intent 33/35 (all 27 expected parameters extracted
  correctly), free SQL 2/4, decline 3/3; median classification 0.9 s. Output:
  `reports/routing_bench.json`.
  - The first run gave 33/42, with **0 of the 7** out-of-catalogue questions routed to "no
    intent": `intent_id` was constrained to the catalogue ids (or null), and under
    grammar-constrained decoding the model never produced null — "What's the weather in Madrid
    tomorrow?" went to `list_metrics`. Production declared `intent_id` as a nullable string and
    read an unknown id as no intent. With that schema the same model scored 38/42, intents
    unchanged (33/35); the repository now uses it.
- **Execution accuracy of the free-SQL fallback** (`sfetl eval-fallback`,
  `eval/fallback_questions.yaml`, 12 questions no intent covers; local `qwen3:4b`): **3/12**
  (2 with exactly the reference columns). Failures: 4 execution errors (invented columns such as
  `net_profit_parent`, broken table aliases, a date compared with `'31-12'`), 1 query the
  guardrail could not parse, 2 wrong values, 2 wrong row counts. Output:
  `reports/fallback_eval.json`. Every reference query was run against the loaded database
  first; two questions were reworded where a literal reading allowed two answers.

A 4B local model is reliable at choosing a prepared query and not at writing SQL. That is the
case for the design: fixed intents answer, free SQL is the exception and always says it was
generated on the fly and not verified, and the query log shows which free-SQL questions keep
coming back so they can become intents. Production used a cloud model for both steps; it was
not measured here.

What an intent answer looks like (the `company_parent` intent run as `sfetl_assistant` against
the loaded data, without the model; the title repeats the question and the caveat always goes
with it):

```
Parent and ultimate parent of Prosegur Cash

1. PROSEGUR CASH, S.A.
   Relation: direct
   Source: esef
   FY: 2024
   Parent: PROSEGUR COMPAÑIA DE SEGURIDAD, S.A.
   Parent LEI: 549300N94L4D5NDBFG97
   Parent loaded here: yes
   Resolved by: lei
...
4. PROSEGUR CASH, S.A.
   Relation: ultimate
   Source: gleif
   Parent: GUBEL SL
   Parent LEI: 959800XR464F2Z0ZZL26
   Parent loaded here: no
   Resolved by: unresolved

Note: ESEF parent names are free text as tagged. 'self_reference' means the filing names the
company itself as its parent (it heads its own group); 'cycle' means two companies name each
other. GLEIF rows are as reported to the LEI register. Nothing here is corrected.
```

A free-SQL answer always starts with: *NOT VERIFIED: generated on the fly by the language model
from the schema; it is not one of the prepared, tested queries. Check it before relying on it.*

### Backup and restore

`sfetl backup --docker` (dump of 172,932 bytes) then `sfetl restore <dump> --docker --dbname
sfetl_restore_check`: the row counts of all 11 tables (including `schema_migrations` and the
assistant schema) are identical in the restored database. In CI the same round trip runs as an
integration test with `pg_dump`/`pg_restore` 16.

## How to run

Requirements: Python ≥ 3.11, Docker, and for `ask`/`bench`/`eval-fallback` Ollama with
`qwen3:4b` (or `SFETL_LLM_PROVIDER=gemini` and a key).

```bash
cp .env.example .env                 # then change the three passwords
docker compose up -d                 # PostgreSQL 16 on 127.0.0.1:55432
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Linux/macOS: .venv/bin/python
.venv/Scripts/sfetl migrate          # apply db/migrations, enable the two role logins
.venv/Scripts/sfetl run              # ~230 files (~171 MB) + GLEIF, 1 s between requests
.venv/Scripts/sfetl smoke            # every intent and assistant write, as sfetl_assistant
.venv/Scripts/sfetl ask "Who owns Prosegur Cash?" --session me
.venv/Scripts/sfetl ask "B" --session me          # answer an A/B question
.venv/Scripts/sfetl rate 12 up                    # rate a logged answer
.venv/Scripts/sfetl report uncovered              # questions no intent answered
.venv/Scripts/sfetl bench                         # routing benchmark (needs the LLM)
.venv/Scripts/sfetl backup --docker && .venv/Scripts/sfetl restore backups/<file>.dump --docker
docker compose down                  # keeps the data volume
```

`sfetl run` options: `--years 2023 2024`, `--max-companies 20`, `--no-load`, `--refresh-index`,
`--no-gleif`, `--gleif-offline`. `sfetl telegram --port 8080` serves the optional webhook
(`TELEGRAM_*` variables in `.env.example`); it is tested with a fake Telegram API and has not
been run against Telegram from this repository. HTTPS uses the operating-system trust store
(`truststore`).

## Tests

```bash
pytest -m "not integration"   # 256 unit tests, offline; the LLM is scripted
pytest -m integration         # 35 tests against PostgreSQL; skipped if it is not reachable
```

Unit tests use trimmed real filings in `tests/fixtures/` (Endesa, Inditex, Bankinter, Amper,
Realia, Berkeley Energia, Prosegur, Prosegur Cash; rebuilt with
`tests/fixtures/build_fixtures.py`). They cover the xBRL-JSON reader (nil facts included), the
catalogue (and that the migration seeds the same 40 codes), period and unit selection,
duplicates, every validation rule, parent-name cleaning and resolution, per-filing isolation of
failures, every intent's SQL (declared parameters only, curated views only), parameter
coercion, the classifier's JSON schema, both LLM providers against recorded responses, the
guardrails, rendering and paging, the routing benchmark harness and the Telegram adapter.

Integration tests create a throw-away database per test: migrations (apply once, refuse an
edited file, upgrade from the first published schema), the catalogue in the database equals
the Python one, loading twice is identical, collisions are counted, a failing filing leaves
nothing behind, derived values are flagged, ownership resolution and cycle marking, the
post-load checks (green, and red on a failed filing or a wrong golden figure), the roles and
the RLS trap, the assistant end to end with a scripted model, the smoke test, and backup/restore.

Local results on 23-09-2026 (Python 3.13): 256 unit passed; 34 integration passed and 1 skipped
(the backup round trip needs `pg_dump` on the host; it was run with `--docker` instead, above).
`ruff check` and `ruff format --check` clean. CI (`.github/workflows/ci.yml`): ruff + unit tests
on Python 3.11–3.13, then the integration tests and the smoke test against a `postgres:16`
service.

## Limitations and next steps

- **Revenue** depends on `ifrs-full:Revenue` (or `RevenueFromContractsWithCustomers`); 16
  non-financial filings have neither. Next: follow the anchoring relationships in each report
  package to map extension concepts to their IFRS parent.
- **Consolidated, current year, EUR only.** Comparatives and restatements are ignored; an AUD
  filer and AED-per-share EPS tags are flagged, not converted.
- **Which report wins** when a company-year has two is a heuristic (last added to the index); a
  newer filing for an already loaded year is counted as a collision, not applied: replacing it
  is a manual decision.
- **Ownership by name** matches exact normalised names only; brand names used as parent names
  ("Nextil", "DESA", "CAF") stay unresolved, and a branch is not its company.
- **The assistant's routing accuracy is not measured yet** for this version (see above); the
  routing and fallback sets are small and synthetic.
- The bank/insurer detection is a marker list checked against these filings, not a sector code.

## Data sources and terms

- [filings.xbrl.org](https://filings.xbrl.org), run by XBRL International, public JSON:API
  (`/api/filings`); for ESEF it collects reports from each country's Officially Appointed
  Mechanism (for Spain, the CNMV) and publishes xBRL-JSON produced with Arelle. Its
  [about page](https://filings.xbrl.org/docs/about): "At present, there are no restrictions on
  the ways that the data can be used" (read on 23-09-2026).
- [GLEIF](https://www.gleif.org) LEI records and Level 2 relationship data, API
  `api.gleif.org`: "The data available through the Access Service are provided under the CC0
  licence" ([terms](https://www.gleif.org/en/meta/lei-data-terms-of-use/), read on 23-09-2026).

Every download is cached under `data/` (gitignored), with a descriptive User-Agent and 1 s
between requests. The figures are the companies' own filings as tagged; they are not audited
by this project, and the validation flags show where they are inconsistent.

## About

Rebuild on public data of the financial-statements ETL and the natural-language assistant I
built at work. No proprietary code or data. Built with AI-assisted development; the design
decisions come from my production system and every figure here was re-measured on public data.

License: MIT.
