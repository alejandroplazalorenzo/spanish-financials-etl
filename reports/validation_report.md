# Validation report

Generated 2026-09-23 14:29 by `sfetl run`. Filings checked: 230.

Validation only flags. No value is changed or dropped because of a flag.

| Rule | Severity | Pass | Flagged | N/A | Checks |
|---|---|---:|---:|---:|---|
| `balance_identity` | error | 209 | 13 | 8 | total assets = total equity + total liabilities (0.1 % or rounding tolerance) |
| `missing_core_metric` | warning | 200 | 30 | 0 | revenue*, net profit, assets, equity, liabilities present (*not for banks/insurers) |
| `sign_check` | error | 228 | 0 | 2 | assets, liabilities, revenue, cash and current items are not negative |
| `component_bounds` | error | 227 | 1 | 2 | current assets and cash <= total assets; current liabilities <= total liabilities |
| `period_consistency` | warning | 230 | 0 | 0 | reporting period detected from the facts equals the period_end of the index |
| `non_eur_unit` | warning | 228 | 2 | 0 | mapped concepts reported in a currency other than EUR (recorded, not loaded) |
| `inconsistent_duplicate` | warning | 230 | 0 | 0 | duplicate facts that disagree beyond rounding |
| `unique_value` | error | 230 | 0 | 0 | exactly one value per (company, fiscal year, metric) |

## `balance_identity` (13 flags)

- AMPER, S.A. FY2023: assets 374,713,000 vs equity + liabilities 372,303,000 (gap 2,410,000, 0.64%; liabilities from derived:NoncurrentLiabilities+CurrentLiabilities)
- AMPER, S.A. FY2024: assets 408,406,000 vs equity + liabilities 344,281,000 (gap 64,125,000, 15.70%; liabilities from derived:NoncurrentLiabilities+CurrentLiabilities)
- CIE AUTOMOTIVE SA FY2023: assets 5,668,999,000 vs equity + liabilities 5,652,600,000 (gap 16,399,000, 0.29%; liabilities from ifrs-full:Liabilities)
- CIE AUTOMOTIVE SA FY2024: assets 5,960,905,000 vs equity + liabilities 5,943,225,000 (gap 17,680,000, 0.30%; liabilities from ifrs-full:Liabilities)
- COMPAÑÍA ESPAÑOLA DE VIVIENDAS EN ALQUILER S.A. FY2023: assets 557,601,000 vs equity + liabilities 556,776,000 (gap 825,000, 0.15%; liabilities from ifrs-full:Liabilities)
- COMPAÑÍA ESPAÑOLA DE VIVIENDAS EN ALQUILER S.A. FY2024: assets 605,098,000 vs equity + liabilities 604,284,000 (gap 814,000, 0.13%; liabilities from ifrs-full:Liabilities)
- GLOBAL DOMINION ACCESS SA FY2023: assets 1,843,703,000 vs equity + liabilities 1,719,469,000 (gap 124,234,000, 6.74%; liabilities from derived:NoncurrentLiabilities+CurrentLiabilities)
- GLOBAL DOMINION ACCESS SA FY2024: assets 1,750,473,000 vs equity + liabilities 1,665,241,000 (gap 85,232,000, 4.87%; liabilities from derived:NoncurrentLiabilities+CurrentLiabilities)
- INNOVATIVE SOLUTIONS ECOSYSTEM SA FY2023: assets 3,026,715 vs equity + liabilities -12,703,795 (gap 15,730,510, 519.72%; liabilities from ifrs-full:Liabilities)
- LIBERTAS 7 SOCIEDAD ANONIMA FY2023: assets 148,222,778 vs equity + liabilities 243,769,250 (gap -95,546,472, -64.46%; liabilities from ifrs-full:Liabilities)
- LIBERTAS 7 SOCIEDAD ANONIMA FY2024: assets 161,568,096 vs equity + liabilities 258,480,236 (gap -96,912,140, -59.98%; liabilities from ifrs-full:Liabilities)
- REALIA BUSINESS, S.A. FY2024: assets 2,039,461,000 vs equity + liabilities 3,263,374,000 (gap -1,223,913,000, -60.01%; liabilities from ifrs-full:Liabilities)
- TUBOS REUNIDOS S.A. FY2024: assets 461,563,000 vs equity + liabilities 460,656,000 (gap 907,000, 0.20%; liabilities from derived:NoncurrentLiabilities+CurrentLiabilities)

## `missing_core_metric` (40 flags)

- ALANTRA PARTNERS, S.A. FY2023: revenue not found (non-financial company)
- ALANTRA PARTNERS, S.A. FY2024: revenue not found (non-financial company)
- ARIMA REAL ESTATE SOCIMI, S.A. FY2023: revenue not found (non-financial company)
- ARIMA REAL ESTATE SOCIMI, S.A. FY2024: revenue not found (non-financial company)
- ATRYS HEALTH, S.A. FY2024: revenue not found (non-financial company)
- BERKELEY ENERGIA LIMITED FY2023: net_profit not found (non-financial company)
- BERKELEY ENERGIA LIMITED FY2023: revenue not found (non-financial company)
- BERKELEY ENERGIA LIMITED FY2023: total_assets not found (non-financial company)
- BERKELEY ENERGIA LIMITED FY2023: total_equity not found (non-financial company)
- BERKELEY ENERGIA LIMITED FY2023: total_liabilities not found (non-financial company)
- BERKELEY ENERGIA LIMITED FY2024: net_profit not found (non-financial company)
- BERKELEY ENERGIA LIMITED FY2024: revenue not found (non-financial company)
- BERKELEY ENERGIA LIMITED FY2024: total_assets not found (non-financial company)
- BERKELEY ENERGIA LIMITED FY2024: total_equity not found (non-financial company)
- BERKELEY ENERGIA LIMITED FY2024: total_liabilities not found (non-financial company)
- CELLNEX TELECOM SA FY2023: revenue not found (non-financial company)
- CELLNEX TELECOM SA FY2023: total_liabilities not found (non-financial company)
- CELLNEX TELECOM SA FY2024: revenue not found (non-financial company)
- CELLNEX TELECOM SA FY2024: total_liabilities not found (non-financial company)
- COX ABG GROUP SA FY2024: net_profit not found (non-financial company)
- DESARROLLOS ESPECIALES DE SISTEMAS DE ANCLAJE S.A. FY2023: net_profit not found (non-financial company)
- DESARROLLOS ESPECIALES DE SISTEMAS DE ANCLAJE S.A. FY2024: net_profit not found (non-financial company)
- ENAGAS, S.A. FY2023: net_profit not found (non-financial company)
- ENAGAS, S.A. FY2024: net_profit not found (non-financial company)
- ERCROS S.A. FY2023: revenue not found (non-financial company)
- INMOBILIARIA COLONIAL SOCIMI SOCIEDAD ANONIMA FY2024: revenue not found (non-financial company)
- LAR ESPAÑA REAL ESTATE SOCIMI SA FY2023: net_profit not found (non-financial company)
- LOGISTA INTEGRAL SA FY2023: total_liabilities not found (non-financial company)
- LOGISTA INTEGRAL SA FY2024: total_liabilities not found (non-financial company)
- MAPFRE S.A. FY2023: total_liabilities not found (financial company)
- MAPFRE S.A. FY2024: total_liabilities not found (financial company)
- MELIA HOTELS INTERNATIONAL SA FY2023: revenue not found (non-financial company)
- MELIA HOTELS INTERNATIONAL SA FY2024: revenue not found (non-financial company)
- MONTEBALITO, S.A. FY2023: revenue not found (non-financial company)
- MONTEBALITO, S.A. FY2024: revenue not found (non-financial company)
- RENTA CORPORACION REAL ESTATE S.A. FY2023: net_profit not found (non-financial company)
- RENTA CORPORACION REAL ESTATE S.A. FY2024: net_profit not found (non-financial company)
- REPSOL SA FY2023: revenue not found (non-financial company)
- REPSOL SA FY2024: revenue not found (non-financial company)
- URBAS GRUPO FINANCIERO SA FY2023: revenue not found (non-financial company)

## `component_bounds` (1 flags)

- INNOVATIVE SOLUTIONS ECOSYSTEM SA FY2023: current_liabilities 14,487,462 exceeds total_liabilities 3,026,715

## `non_eur_unit` (14 flags)

- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:CashAndCashEquivalents reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:CurrentAssets reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:CurrentLiabilities reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:ProfitLoss reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:Assets reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:Equity reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:Liabilities reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:CashAndCashEquivalents reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:CurrentAssets reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:CurrentLiabilities reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:ProfitLoss reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:Assets reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:Equity reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:Liabilities reported in iso4217:AUD
