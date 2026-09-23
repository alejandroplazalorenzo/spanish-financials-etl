# spanish-financials-etl

Turns the official annual financial reports of Spanish listed companies (ESEF, tagged with
XBRL) into a clean PostgreSQL dataset: ten canonical metrics per company and fiscal year, a
separate validation step that flags what does not add up, ratio views, and an "ask your data"
command that answers questions in English or Spanish with one guarded, read-only SQL query
written by a local LLM, with a measured evaluation.

## Why

Annual accounts are the core of company analysis. Issuers listed on EU regulated markets
publish them in ESEF (European Single Electronic Format): an XHTML report whose financial
statements carry XBRL tags, so the numbers are machine-readable. In practice the data is
messy. The same figure is tagged several times, current and prior year sit side by side,
totals are mixed with breakdowns by segment, and companies choose different IFRS concepts (or
invent their own) for the same line. Banks and insurers use a different layout. Tagging errors
reach the published filing. Reading ESEF correctly means deciding all of that explicitly.

## Architecture

```
 filings.xbrl.org /api/filings, filter country = ES (public JSON:API)
   │  index: 3 pages of 200 · reports: one xBRL-JSON per filing, gzip, 1 s apart
   ▼
 ┌──────────────────────────────┐
 │ 1. EXTRACT                   │ ──▶ data/ (gitignored): index +
 │ latest report per company    │     xBRL-JSON .json.gz cache
 │ and fiscal year              │
 └──────────────┬───────────────┘
                ▼
 ┌──────────────────────────────┐
 │ 2. TRANSFORM                 │  period · units · dimensions · duplicates
 │ facts -> 10 canonical metrics│  ifrs-full mapping · bank/insurer detection
 └──────────────┬───────────────┘
                ▼
 ┌──────────────────────────────┐
 │ 3. VALIDATE (8 rules)        │ ──▶ reports/validation_report.md
 │ flags only, never edits      │ ──▶ validation_issue rows
 └──────────────┬───────────────┘
                ▼
 ┌──────────────────────────────┐
 │ 4. LOAD  PostgreSQL 16       │ ◀── sfetl migrate: db/migrations/NNN_*.sql
 │ idempotent upserts           │     (schema_migrations + checksums)
 │ company · filing · metric    │
 │ financial_fact               │
 │ validation_issue · 3 views   │
 └──────────────┬───────────────┘
                │ role sfetl_reader: SELECT only, read-only session, 5 s timeout
                ▼
 ┌──────────────────────────────┐
 │ 5. ASK                       │  question (EN/ES) + schema + company names
 │ Ollama qwen2.5:7b-instruct   │  -> SQL -> guardrail -> rows
 │                              │  guardrail: parse, one SELECT, allow-listed
 │                              │  relations, forced LIMIT
 └──────────────────────────────┘
```

Code: `src/sfetl/` — `extract.py`, `oim.py` (xBRL-JSON reader), `concepts.py` (mapping),
`transform.py`, `validate.py`, `migrate.py`, `load.py`, `pipeline.py`, `cli.py`, and
`ask/` (`prompt.py`, `llm.py`, `guardrails.py`, `runner.py`, `evaluate.py`).

## Data model

| Object | Grain | Notes |
|---|---|---|
| `company` | one row per LEI | name from the latest filing; `is_financial` for banks and insurers |
| `filing` | one row per report used | index metadata, detected period end, XBRL error/warning counts, link to the viewer |
| `metric` | one row per metric | label, statement, instant/duration, source concepts |
| `financial_fact` | (LEI, fiscal year, metric) | `value_eur`, reported `decimals`, period, `source_concept` (lineage), `filing_id` |
| `validation_issue` | one row per flag | rule, severity, metric, human-readable detail |
| `company_year` (view) | company-year | pivot of `financial_fact`, one column per metric |
| `company_year_ratios` (view) | company-year | net margin, operating margin, equity ratio, current ratio |
| `year_aggregate_ratios` (view) | fiscal year × financial/non-financial | ratios of sums, with the number of companies behind each |

Metrics: `revenue`, `operating_profit`, `net_profit`, `net_profit_parent`, `total_assets`,
`total_equity`, `total_liabilities`, `cash`, `current_assets`, `current_liabilities`.

## How the transform reads ESEF

| Problem | Rule |
|---|---|
| Concepts | Only `ifrs-full` concepts, in priority order (e.g. revenue = `Revenue`, else `RevenueFromContractsWithCustomers`). `RevenueAndOperatingIncome` is not used: it adds other operating income. Company extension concepts are ignored: their meaning lives in anchoring relationships that xBRL-JSON does not carry. |
| Banks and insurers | Detected when the report tags bank/insurance concepts (`DepositsFromCustomers`, `LoansAndAdvancesToCustomers`, `InsuranceRevenue`...). They have no revenue or current/non-current split, so those metrics are not expected from them and their margins stay NULL. |
| Total liabilities | `ifrs-full:Liabilities`, or `NoncurrentLiabilities + CurrentLiabilities` when the total is not tagged (lineage recorded as `derived:...`). Never `Assets − Equity`: that would make the balance check pass by construction. |
| Periods | xBRL-JSON writes the instant 31-12-2024 as `2025-01-01T00:00:00`; converted to inclusive dates. Instants must end on the fiscal year end; durations must end on it and last 350–380 days. Prior-year comparatives in each report are ignored: each year comes from its own report. |
| Fiscal year label | Calendar year holding most of the months: year ending 31-01-2025 (Inditex) = FY2024; ending 31-03-2024 = FY2023. |
| Units and decimals | Only `iso4217:EUR` is loaded; other currencies are recorded as a flag, not converted. Values in xBRL-JSON are already in euros; `decimals` is precision (`-6` = to the million) and is stored, never applied. |
| Dimensions | Only facts without taxonomy dimensions are consolidated totals; anything with an axis (segments, equity components) is skipped. |
| Duplicates | Same concept, period and unit tagged more than once: keep the most precise (highest `decimals`, INF first), then the first in document order. Duplicates that disagree beyond rounding are kept but flagged. |
| Several reports per company-year | Keep the one added last to the index (then higher sequence, then higher id). |

## Design decisions

| Decision | Rejected alternative | Why |
|---|---|---|
| **Long fact table** `(lei, fiscal_year, metric)` plus a pivot view | One wide table with a column per metric | Adding a metric needs a row in `metric` and a column in the pivot view, never an `ALTER TABLE` on the facts; each value carries its own lineage (`source_concept`, `decimals`, `filing_id`), and the primary key enforces one value per company-year-metric. Analysts still get the wide shape through `company_year`. |
| **Numbered SQL migrations** with a checksum in `schema_migrations`; an edited, already-applied file is an error | `CREATE TABLE IF NOT EXISTS` at start-up, or an ORM auto-migrate | The schema history is reviewable and reproducible in CI; checksums stop silent drift between environments. Fix forward with a new file. |
| **Validation as its own step** that only flags | Fixing values inside the transform (e.g. forcing Assets = Equity + Liabilities) | A "fix" hides the filer's error and my own mapping errors. Flags are stored next to the data, so an analyst can filter them, and a rule change never alters a number. The one blocking rule is `unique_value`: if two filings would write the same key the load stops rather than letting `ON CONFLICT` pick a winner. |
| **Ratio of sums** for cross-company margins, over companies that report both terms | Average of company ratios | A mean of ratios weights a 5 M€ company like a 40 bn€ one and is dominated by outliers. Measured on this data: non-financial net margin FY2023 is 6.53 % as ratio of sums versus 16.83 % as a simple average (median 4.10 %). The gap comes from a few extreme ratios: five FY2023 company-years have net margins above +100 % or below −100 % (up to 1,116 %). Restricting both sums to the same companies avoids dividing one population's profit by another's revenue. |
| **Read-only role** `sfetl_reader` with per-object `GRANT SELECT`, `default_transaction_read_only`, `statement_timeout` | The owner account for everything | Analysts and the LLM path cannot write even if a check fails. Grants are explicit, so a new table is invisible until a migration exposes it. The password is set by `sfetl migrate` from `.env`, never versioned. |
| **Guardrails for LLM SQL**: parse with sqlglot, exactly one `SELECT`, walk the AST for write clauses (`INTO`, `FOR UPDATE`, DML inside CTEs), deny server functions (`pg_*`, `set_config`...), allow-list relations, force a `LIMIT`, then run the SQL regenerated from the checked AST in a read-only transaction | Regex/keyword blacklists, or trusting the prompt | Keyword filters miss `WITH d AS (DELETE ...) SELECT` or `SELECT ... INTO`; the prompt is not a security boundary. Executing the regenerated SQL means what runs is exactly what was checked. |

## Results (real run, 23 September 2026)

### Pipeline

Commands, on a database recreated from scratch (`docker compose down -v`, `up -d`):
`sfetl migrate` then `sfetl run` (started 14:29:28). Full output: `reports/run_summary.json`.

| | |
|---|---|
| Spanish filings in the index (`country = ES`) | 542 |
| Fiscal years selected (two latest with ≥ 20 filings) | 2023, 2024 |
| Filings in those years / selected (latest per company-year) | 233 / 230 (3 company-years had two reports) |
| Companies | 125 (11 banks or insurers) |
| xBRL-JSON files | 230, 171 MB gzipped, 0 failures |
| Numeric facts read | 111,260 |
| Duplicate groups resolved (all consistent) | 655 |
| Values loaded into `financial_fact` | 2,130 |
| Time: transform / load | 23.5 s / 1.4 s |

The 230 files were downloaded earlier the same day by the same extract code
(`extract.download_filing`, 1 s between requests: 328 s); the recorded run read them from the
local cache (`downloaded_now: 0`).

Coverage per metric (company-years out of 230): total assets 228, total equity 228, cash 228,
total liabilities 222 (145 of them derived from non-current + current), net profit 220, net
profit attributable 215, current assets 200, current liabilities 200, operating profit 199,
revenue 190 (4 from `RevenueFromContractsWithCustomers`).

### Validation (`reports/validation_report.md`)

| Rule | Severity | Pass | Flagged | N/A |
|---|---|---:|---:|---:|
| `balance_identity` (0.1 % or rounding tolerance) | error | 209 | 13 | 8 |
| `missing_core_metric` | warning | 200 | 30 | 0 |
| `sign_check` | error | 228 | 0 | 2 |
| `component_bounds` | error | 227 | 1 | 2 |
| `period_consistency` | warning | 230 | 0 | 0 |
| `non_eur_unit` | warning | 228 | 2 | 0 |
| `inconsistent_duplicate` | warning | 230 | 0 | 0 |
| `unique_value` | error | 230 | 0 | 0 |

68 issues stored in `validation_issue` (14 errors, 54 warnings). What the flags turned out to be,
checked against the raw facts of each filing:

- **Balance identity, 13 flags.** 4 company-years tag `ifrs-full:Liabilities` with the value
  of *total equity and liabilities* (it equals total assets): Realia FY2024, Libertas 7 FY2023
  and FY2024, Innovative Solutions Ecosystem FY2023. In 4 more (Amper FY2023/FY2024, Global
  Dominion FY2023/FY2024) the gap equals exactly the tagged
  `LiabilitiesIncludedInDisposalGroupsClassifiedAsHeldForSale`, presented outside the subtotal
  the filer tagged as `CurrentLiabilities`, so the derived total misses it. The other 5 are
  small gaps of 0.13–0.30 % that the tagged facts alone do not explain (CIE Automotive,
  CEVASA, Tubos Reunidos).
- **Component bounds, 1 flag**: the same Innovative Solutions filing (current liabilities larger
  than the mis-tagged total).
- **Missing core metrics, 40 in 30 filings**: revenue 18 (16 company-years of non-financial
  companies that tag revenue with an extension concept, a broader IFRS concept such as
  `RevenueAndOperatingIncome`, or only by component such as `RentalIncome`; Repsol, Cellnex,
  Colonial and Meliá are among them; plus the 2 AUD filings), net profit 10, total
  liabilities 8, equity 2, assets 2.
- **Non-EUR, 2 filings**: Berkeley Energia reports in AUD; nothing of it is loaded.
- **Period consistency**: the period detected from the facts matched the index in all 230.

### Example: top 5 companies by revenue, fiscal year 2024

| Company | Revenue (M€) | Net profit (M€) | Net margin | Source filing (`fxo_id`) |
|---|---:|---:|---:|---|
| IBERDROLA SA | 44,739 | 5,948 | 13.29 % | 5QK37QC7NWOJ8D7WVQ45-2024-12-31-ESEF-ES-0 |
| ACS ACTIVIDADES DE CONSTRUCCION Y SERVICIOS, S.A. | 41,633 | 1,080 | 2.59 % | 95980020140005558665-2024-12-31-ESEF-ES-0 |
| TELEFONICA SA | 41,315 | 209 | 0.51 % | 549300EEJH4FEPDBBR25-2024-12-31-ESEF-ES-0 |
| INDUSTRIA DE DISEÑO TEXTIL, S.A. | 38,632 | 5,877 | 15.21 % | 549300TTCXZOGZM2EY83-2025-01-31-ESEF-ES-0 |
| INTERNATIONAL CONSOLIDATED AIRLINES GROUP, S.A. | 32,100 | 2,732 | 8.51 % | 959800TZHQRUSH1ESL13-2024-12-31-ESEF-ES-0 |

Read with the flags in mind: Repsol is absent because it does not tag `ifrs-full:Revenue`.
Aggregate net margin of non-financial companies (ratio of sums): 6.53 % in FY2023 (96
companies), 7.87 % in FY2024 (84 companies).

### Ask your data: NL→SQL execution accuracy

Setup: Ollama at `127.0.0.1:11434`, `qwen2.5:7b-instruct` (Q4_K_M), temperature 0, seed 42,
laptop RTX 3070. A question passes when the generated query returns the same rows as the
reference query (numbers rounded to 4 decimals; order compared only for ranking questions).
Extra columns are accepted if some choice of columns reproduces the reference exactly;
"strict" also requires the same columns. Commands: `sfetl eval --questions <file> --prompt
<v1|v2> --out reports/<name>.json`, run at 14:30–14:31.

| Question set | Prompt | Execution accuracy | Strict | English | Spanish |
|---|---|---:|---:|---:|---:|
| `eval/questions.yaml` (26) | v1 | **16/26 (61.5 %)** | 7/26 | 12/16 | 4/10 |
| `eval/questions.yaml` (26) | v2 | 18/26 (69.2 %) | 10/26 | 12/16 | 6/10 |
| `eval/questions_holdout.yaml` (12) | v1 | 8/12 (66.7 %) | 5/12 | 5/6 | 3/6 |
| `eval/questions_holdout.yaml` (12) | v2 | **10/12 (83.3 %)** | 9/12 | 5/6 | 5/6 |

How to read it honestly:
- **v1** is the prompt as first written; its run on the 26 questions is the untuned baseline.
  I then read the failures and wrote **v2** (five extra rules, one per failure class, in
  `src/sfetl/ask/prompt.py`). Because v2 was written looking at those 26 questions, its 69.2 %
  there is optimistic. The 12 **held-out** questions were written after the first run and
  before v2, and never used for tuning: 8/12 → 10/12 is the fairer estimate of the change.
- Samples are small: one held-out question is 8.3 points. v2 fixed 5 of the 26 and **broke 3**
  that v1 answered (q13, q19, q21); on the held-out set it fixed 3 and broke 1 (h06).
- The v1 evaluation was run twice (finished at 14:24 and 14:30, the second on the rebuilt
  database) and produced identical SQL for all 26 questions.
- The guardrail rejected none of the 76 generated queries: every one was a single SELECT.
  Its rejections are covered by unit tests instead. Median model time per question 0.9 s once
  the model is loaded.

Failure categories observed, from reading each of the 24 failing queries (4 runs):

| Category | Count | Example |
|---|---:|---|
| Column or relation that does not exist | 12 | `company_name` in `financial_fact`; `net_margin` read from `company_year` |
| Cross-company ratio over mismatched populations | 5 | `sum(net_profit)/sum(revenue)` including companies with no revenue, instead of `year_aggregate_ratios` |
| Company not found | 3 | `ILIKE 'iberdrola'` without wildcards; `'%inditex%'` (brand, not the legal name); `'%respol%'` typo |
| Wrong source or meaning | 2 | "companies that filed" counted from figures (107) instead of filings (108); "largest loss" sorted descending |
| Missing filter | 1 | summed every metric of `financial_fact` instead of `metric = 'cash'` |
| Type error | 1 | compared a date with the string `'31-12'` |

Example (`sfetl ask`, prompt v2, 23-09-2026 14:33; the SQL line is wrapped here):

```
$ sfetl ask "¿Qué empresas no financieras tuvieron el mayor margen operativo en 2024? Dame las 5 primeras."
-- SQL (2.0s)
WITH company_year_ratios_2024 AS (SELECT lei, company_name, operating_margin FROM company_year_ratios
WHERE fiscal_year = 2024 AND is_financial = FALSE ORDER BY operating_margin DESC NULLS LAST LIMIT 5)
SELECT lei, company_name, operating_margin FROM company_year_ratios_2024 LIMIT 200

lei                  | company_name                        | operating_margin
---------------------+-------------------------------------+-----------------
959800CJH35NNZQQW653 | CORPORACION FINANCIERA ALBA, S.A.   | 6.0397
959800PM2YJU406K2789 | SOLARIA ENERGIA Y MEDIO AMBIENTE SA | 0.8909
959800L8KD863DP30X04 | MERLIN PROPERTIES SOCIMI, S.A.      | 0.7593
95980020140005821826 | LIBERTAS 7 SOCIEDAD ANONIMA         | 0.7015
959800RGBUGJA3UVZZ88 | REALIA BUSINESS, S.A.               | 0.5930
```

The SQL is right; the answer still needs judgement (an investment holding with little revenue
tops the list).

## How to run

Requirements: Python ≥ 3.11, Docker, and (for `ask`/`eval`) Ollama with `qwen2.5:7b-instruct`.

```bash
cp .env.example .env                 # then change both passwords
docker compose up -d                 # PostgreSQL 16 on 127.0.0.1:55432
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"    # Linux/macOS: .venv/bin/python
.venv/Scripts/sfetl migrate          # apply db/migrations, enable the read-only login
.venv/Scripts/sfetl run              # first run downloads ~230 files (~171 MB), 1 s apart
.venv/Scripts/sfetl ask "Which 5 companies had the highest revenue in 2024?"
.venv/Scripts/sfetl eval --questions eval/questions_holdout.yaml --out reports/eval_holdout_v2.json
docker compose down                  # keeps the data volume
```

`sfetl run --years 2023 2024 --max-companies 20` limits the scope; `--no-load` stops after
validation; `--refresh-index` re-reads the index. HTTPS uses the operating-system trust store
(`truststore`), so it also works behind TLS-inspecting proxies or antivirus.

## Tests

```bash
pytest -m "not integration"   # 92 unit tests, offline
pytest -m integration         # 6 tests against PostgreSQL; skipped if it is not reachable
```

Unit tests use trimmed real filings in `tests/fixtures/` (84 KB: Endesa, Inditex, Bankinter,
Amper, Realia, Berkeley Energia; rebuilt with `tests/fixtures/build_fixtures.py`) and cover the
period parser, concept mapping, decimals, period and fiscal-year selection, dimension filtering,
duplicate resolution, currency handling, every validation rule, the SQL guardrail (INSERT,
UPDATE, DELETE, DROP, multiple statements, `SELECT INTO`, DML in CTEs, server functions,
catalog access) and the evaluation matcher. Integration tests create a throw-away database and
check that migrations apply once and refuse edited files, that loading twice leaves the data
identical, that the ratio view is a ratio of sums, and that the reader role cannot write.
CI (`.github/workflows/ci.yml`): ruff + unit tests on Python 3.11 and 3.12, then integration
tests against a `postgres:16` service. Local results on 23-09-2026: 92 + 6 passed (Python 3.13);
the workflow itself has not run yet because the repository has no remote.

## Limitations and next steps

- **Coverage of revenue** depends on companies tagging `ifrs-full:Revenue` (or
  `RevenueFromContractsWithCustomers`); 16 non-financial company-years, Repsol among them, do
  not. Next: follow the anchoring relationships in each report package to map extension
  concepts to their IFRS parent, and decide case by case on broader or partial IFRS concepts.
- **Consolidated only**, current year only, EUR only. Prior-year comparatives (and restatements)
  are ignored; an AUD filer is flagged, not converted.
- **Which report wins** when a company-year has two is a heuristic (last added): the index does
  not say whether the second is an amendment or a translation.
- **Held-for-sale liabilities** outside the tagged current-liability subtotal leave the derived
  total short; the validation catches it, the transform does not correct it.
- **Evaluation** is small (38 questions) and single-model; the next step is more held-out
  questions, a second model, and a check that the answer's company is the one intended when
  names are ambiguous.
- The bank/insurer detection is a marker list checked against these filings, not a sector code.

## Data source and terms

Filings come from [filings.xbrl.org](https://filings.xbrl.org), run by XBRL International,
through its public JSON:API (`/api/filings`). For ESEF it collects the reports from each
country's Officially Appointed Mechanism (for Spain, the CNMV) and publishes xBRL-JSON produced
with the Arelle processor. Its [about page](https://filings.xbrl.org/docs/about) states: "At
present, there are no restrictions on the ways that the data can be used" (read on 23-09-2026).
This project caches every download under `data/` (gitignored), sends a descriptive
User-Agent and waits 1 s between requests. The figures are the companies' own filings as
tagged; they are not audited by this project, and the validation flags show where they are
inconsistent.

## About

Rebuild on public data of a system I designed and ran in production at work (financial
statements of automotive dealer groups). It contains no proprietary code or data. Built with
AI-assisted development; design decisions, evaluation and review are mine.

License: MIT.
