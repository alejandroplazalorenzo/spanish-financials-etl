# Validation report

Generated 2026-09-23 17:53 by `sfetl run`. Filings selected: 230; transformed: 230; failed: 0.

**Result: ALL GREEN** (exit code 0). Flags on the filers' data (first section) never change a value and do not make the run red; failed filings and red checks of the pipeline's own work (second section) do.

## Rules on the filers' data (before loading)

Filings checked: 230. Validation only flags: no value is changed or dropped because of a flag.

| Rule | Severity | Pass | Flagged | N/A | Checks |
|---|---|---:|---:|---:|---|
| `balance_identity` | error | 209 | 13 | 8 | total assets = total equity + total liabilities (0.1 % or rounding tolerance) |
| `missing_core_metric` | warning | 200 | 30 | 0 | revenue*, net profit, assets, equity, liabilities present (*not for banks/insurers) |
| `sign_check` | error | 228 | 0 | 2 | assets, liabilities, revenue, cash, current items, capex are not negative |
| `component_bounds` | error | 227 | 1 | 2 | current assets and cash <= total assets; current liabilities <= total liabilities |
| `subtotal_check` | warning | 174 | 54 | 2 | catalogue identities: assets, liabilities and equity splits, profit attribution, continuing + discontinued, tax bridge (0.1 % or rounding tolerance) |
| `period_consistency` | warning | 230 | 0 | 0 | reporting period detected from the facts equals the period_end of the index |
| `non_eur_unit` | warning | 223 | 7 | 0 | mapped concepts reported in another currency (recorded, not loaded) |
| `inconsistent_duplicate` | warning | 230 | 0 | 0 | duplicate facts that disagree beyond rounding |
| `nil_fact` | info | 230 | 0 | 0 | metrics tagged nil (not available): loaded as NULL + is_nil, never as 0 |

### `balance_identity` (13 flags)

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

### `missing_core_metric` (40 flags)

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

### `component_bounds` (1 flags)

- INNOVATIVE SOLUTIONS ECOSYSTEM SA FY2023: current_liabilities 14,487,462 exceeds total_liabilities 3,026,715

### `subtotal_check` (63 flags)

- AEDAS HOMES SA FY2023: equity_split: total_equity 931,088,264 vs equity_parent + non_controlling_interests 109,448,355 (gap 821,639,909)
- AENA S.M.E. SA FY2023: profit_attribution: net_profit 1,645,069,000 vs net_profit_parent + net_profit_nci 1,616,559,000 (gap 28,510,000)
- AENA S.M.E. SA FY2024: profit_attribution: net_profit 1,972,028,000 vs net_profit_parent + net_profit_nci 1,896,420,000 (gap 75,608,000)
- AMPER, S.A. FY2023: equity_and_liabilities: equity_and_liabilities 374,713,000 vs total_equity + total_liabilities 372,303,000 (gap 2,410,000)
- AMPER, S.A. FY2023: assets_split: total_assets 374,713,000 vs non_current_assets + current_assets 370,168,000 (gap 4,545,000)
- AMPER, S.A. FY2024: equity_and_liabilities: equity_and_liabilities 408,406,000 vs total_equity + total_liabilities 344,281,000 (gap 64,125,000)
- AMPER, S.A. FY2024: assets_split: total_assets 408,406,000 vs non_current_assets + current_assets 352,867,000 (gap 55,539,000)
- APPLUS SERVICES, S.A. FY2023: profit_attribution: net_profit 33,538,000 vs net_profit_parent + net_profit_nci 6,844,000 (gap 26,694,000)
- APPLUS SERVICES, S.A. FY2023: tax_bridge: profit_continuing 39,452,000 vs profit_before_tax - income_tax 35,574,000 (gap 3,878,000)
- AUDAX RENOVABLES S.A. FY2024: tax_bridge: profit_continuing 88,950,000 vs profit_before_tax - income_tax 63,253,000 (gap 25,697,000)
- CAIXABANK SA FY2023: equity_split: total_equity 36,339,000,000 vs equity_parent + non_controlling_interests 38,238,000,000 (gap -1,899,000,000)
- CAIXABANK SA FY2024: equity_split: total_equity 36,865,000,000 vs equity_parent + non_controlling_interests 37,459,000,000 (gap -594,000,000)
- CIE AUTOMOTIVE SA FY2023: equity_and_liabilities: equity_and_liabilities 5,668,999,000 vs total_equity + total_liabilities 5,652,600,000 (gap 16,399,000)
- CIE AUTOMOTIVE SA FY2023: profit_attribution: net_profit 361,189,000 vs net_profit_parent + net_profit_nci 279,161,000 (gap 82,028,000)
- CIE AUTOMOTIVE SA FY2024: equity_and_liabilities: equity_and_liabilities 5,960,905,000 vs total_equity + total_liabilities 5,943,225,000 (gap 17,680,000)
- CIE AUTOMOTIVE SA FY2024: profit_attribution: net_profit 362,957,000 vs net_profit_parent + net_profit_nci 288,363,000 (gap 74,594,000)
- COMPAÑÍA ESPAÑOLA DE VIVIENDAS EN ALQUILER S.A. FY2023: equity_and_liabilities: equity_and_liabilities 557,601,000 vs total_equity + total_liabilities 556,776,000 (gap 825,000)
- COMPAÑÍA ESPAÑOLA DE VIVIENDAS EN ALQUILER S.A. FY2024: equity_and_liabilities: equity_and_liabilities 605,098,000 vs total_equity + total_liabilities 604,284,000 (gap 814,000)
- CONSTRUCCIONES Y AUXILIAR DE FERROCARRILES, S.A. FY2023: profit_attribution: net_profit 92,317,000 vs net_profit_parent + net_profit_nci -85,999,000 (gap 178,316,000)
- CORPORACION FINANCIERA ALBA, S.A. FY2023: profit_attribution: net_profit 232,100,000 vs net_profit_parent + net_profit_nci 235,900,000 (gap -3,800,000)
- CORPORACION FINANCIERA ALBA, S.A. FY2024: profit_attribution: net_profit 91,600,000 vs net_profit_parent + net_profit_nci 96,600,000 (gap -5,000,000)
- ECOLUMBER S.A. FY2023: tax_bridge: profit_continuing -6,278,158 vs profit_before_tax - income_tax -13,823,221 (gap 7,545,063)
- ENCE ENERGIA Y CELULOSA S.A. FY2023: profit_attribution: net_profit -21,070,000 vs net_profit_parent + net_profit_nci -28,370,000 (gap 7,300,000)
- ENCE ENERGIA Y CELULOSA S.A. FY2024: profit_attribution: net_profit 20,120,000 vs net_profit_parent + net_profit_nci 42,982,000 (gap -22,862,000)
- GESTAMP AUTOMOCION SA FY2023: profit_attribution: net_profit 320,037,000 vs net_profit_parent + net_profit_nci 241,299,000 (gap 78,738,000)
- GESTAMP AUTOMOCION SA FY2024: profit_attribution: net_profit 284,648,000 vs net_profit_parent + net_profit_nci 92,332,000 (gap 192,316,000)
- GLOBAL DOMINION ACCESS SA FY2023: equity_and_liabilities: equity_and_liabilities 1,843,703,000 vs total_equity + total_liabilities 1,719,469,000 (gap 124,234,000)
- GLOBAL DOMINION ACCESS SA FY2023: assets_split: total_assets 1,843,703,000 vs non_current_assets + current_assets 1,690,728,000 (gap 152,975,000)
- GLOBAL DOMINION ACCESS SA FY2024: equity_and_liabilities: equity_and_liabilities 1,750,473,000 vs total_equity + total_liabilities 1,665,241,000 (gap 85,232,000)
- GLOBAL DOMINION ACCESS SA FY2024: assets_split: total_assets 1,750,473,000 vs non_current_assets + current_assets 1,648,948,000 (gap 101,525,000)
- GRENERGY RENOVABLES SA FY2023: tax_bridge: profit_continuing 86,563,000 vs profit_before_tax - income_tax 51,055,000 (gap 35,508,000)
- GRENERGY RENOVABLES SA FY2024: tax_bridge: profit_continuing 118,610,000 vs profit_before_tax - income_tax 59,600,000 (gap 59,010,000)
- GRUPO EMPRESARIAL SAN JOSE, SA FY2023: profit_attribution: net_profit 21,412,000 vs net_profit_parent + net_profit_nci 18,476,000 (gap 2,936,000)
- GRUPO EMPRESARIAL SAN JOSE, SA FY2024: profit_attribution: net_profit 32,397,000 vs net_profit_parent + net_profit_nci 33,649,000 (gap -1,252,000)
- IBERDROLA SA FY2023: profit_attribution: net_profit 5,394,000,000 vs net_profit_parent + net_profit_nci 4,212,000,000 (gap 1,182,000,000)
- IBERDROLA SA FY2024: profit_attribution: net_profit 5,948,000,000 vs net_profit_parent + net_profit_nci 5,276,000,000 (gap 672,000,000)
- INNOVATIVE SOLUTIONS ECOSYSTEM SA FY2023: continuing_plus_discontinued: net_profit -7,451,548 vs profit_continuing + profit_discontinued -7,551,688 (gap 100,140)
- INNOVATIVE SOLUTIONS ECOSYSTEM SA FY2023: tax_bridge: profit_continuing 123,666 vs profit_before_tax - income_tax -7,551,688 (gap 7,675,354)
- INNOVATIVE SOLUTIONS ECOSYSTEM SA FY2023: liabilities_split: total_liabilities 3,026,715 vs non_current_liabilities + current_liabilities 18,757,225 (gap -15,730,510)
- LIBERTAS 7 SOCIEDAD ANONIMA FY2023: liabilities_split: total_liabilities 148,222,778 vs non_current_liabilities + current_liabilities 52,676,306 (gap 95,546,472)
- LIBERTAS 7 SOCIEDAD ANONIMA FY2024: liabilities_split: total_liabilities 161,568,096 vs non_current_liabilities + current_liabilities 64,655,956 (gap 96,912,140)
- MINERALES Y PRODUCTOS DERIVADOS SA FY2023: profit_attribution: net_profit 86,059,000 vs net_profit_parent + net_profit_nci 83,291,000 (gap 2,768,000)
- MINERALES Y PRODUCTOS DERIVADOS SA FY2024: profit_attribution: net_profit 80,263,000 vs net_profit_parent + net_profit_nci 78,823,000 (gap 1,440,000)
- NATURHOUSE HEALTH, S.A. FY2023: profit_attribution: net_profit 11,247,000 vs net_profit_parent + net_profit_nci 11,339,000 (gap -92,000)
- NEINOR HOMES SA FY2023: profit_attribution: net_profit 99,509,000 vs net_profit_parent + net_profit_nci 91,364,000 (gap 8,145,000)
- NEINOR HOMES SA FY2024: profit_attribution: net_profit 76,747,000 vs net_profit_parent + net_profit_nci 62,393,000 (gap 14,354,000)
- NUEVA EXPRESION TEXTIL SA FY2023: equity_split: total_equity -37,962,916 vs equity_parent + non_controlling_interests -37,760,894 (gap -202,022)
- NUEVA EXPRESION TEXTIL SA FY2024: equity_split: total_equity -7,166,000 vs equity_parent + non_controlling_interests -6,665,000 (gap -501,000)
- NYESA VALORES CORPORACION SA FY2024: profit_attribution: net_profit -5,956,985 vs net_profit_parent + net_profit_nci -5,767,027 (gap -189,958)
- PROMOTORA DE INFORMACIONES, S.A. FY2023: profit_attribution: net_profit -31,598,000 vs net_profit_parent + net_profit_nci -33,412,000 (gap 1,814,000)
- PROMOTORA DE INFORMACIONES, S.A. FY2024: profit_attribution: net_profit -10,571,000 vs net_profit_parent + net_profit_nci -12,575,000 (gap 2,004,000)
- PUIG BRANDS S.A. FY2024: profit_attribution: net_profit 542,533,000 vs net_profit_parent + net_profit_nci 518,765,000 (gap 23,768,000)
- REALIA BUSINESS, S.A. FY2024: liabilities_split: total_liabilities 2,039,461,000 vs non_current_liabilities + current_liabilities 815,548,000 (gap 1,223,913,000)
- RENTA 4 BANCO SA FY2023: equity_split: total_equity 141,688,000 vs equity_parent + non_controlling_interests 27,731,000 (gap 113,957,000)
- RENTA 4 BANCO SA FY2024: equity_split: total_equity 159,688,000 vs equity_parent + non_controlling_interests 33,877,000 (gap 125,811,000)
- REPSOL SA FY2023: profit_attribution: net_profit 3,284,000,000 vs net_profit_parent + net_profit_nci 3,052,000,000 (gap 232,000,000)
- REPSOL SA FY2024: profit_attribution: net_profit 1,610,000,000 vs net_profit_parent + net_profit_nci 1,902,000,000 (gap -292,000,000)
- SACYR SA FY2023: profit_attribution: net_profit 350,234,000 vs net_profit_parent + net_profit_nci -43,790,000 (gap 394,024,000)
- SACYR SA FY2024: profit_attribution: net_profit 257,733,000 vs net_profit_parent + net_profit_nci -30,987,000 (gap 288,720,000)
- TUBACEX, S.A. FY2023: profit_attribution: net_profit 33,214,000 vs net_profit_parent + net_profit_nci 42,816,000 (gap -9,602,000)
- TUBACEX, S.A. FY2024: profit_attribution: net_profit 30,235,000 vs net_profit_parent + net_profit_nci 25,249,000 (gap 4,986,000)
- TUBOS REUNIDOS S.A. FY2024: equity_and_liabilities: equity_and_liabilities 461,563,000 vs total_equity + total_liabilities 460,656,000 (gap 907,000)
- UNICAJA BANCO SA FY2023: profit_attribution: net_profit 266,532,000 vs net_profit_parent + net_profit_nci 266,874,000 (gap -342,000)

### `non_eur_unit` (36 flags)

- AMPER, S.A. FY2023: ifrs-full:BasicEarningsLossPerShare reported in iso4217:AED/xbrli:shares
- AMPER, S.A. FY2024: ifrs-full:BasicEarningsLossPerShare reported in iso4217:AED/xbrli:shares
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:BasicEarningsLossPerShare reported in iso4217:AUD/xbrli:shares
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:CashAndCashEquivalents reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:CurrentAssets reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:CurrentLiabilities reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:CashFlowsFromUsedInFinancingActivities reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:IncomeTaxExpenseContinuingOperations reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:IssuedCapital reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:ProfitLoss reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:NoncurrentAssets reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:CashFlowsFromUsedInOperatingActivities reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:ProfitLossBeforeTax reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:PropertyPlantAndEquipment reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:Assets reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:Equity reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:Liabilities reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2023: ifrs-full:TradeAndOtherCurrentPayables reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:BasicEarningsLossPerShare reported in iso4217:AUD/xbrli:shares
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:CashAndCashEquivalents reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:CurrentAssets reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:CurrentLiabilities reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:IncomeTaxExpenseContinuingOperations reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:IssuedCapital reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:ProfitLoss reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:NoncurrentAssets reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:CashFlowsFromUsedInOperatingActivities reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:ProfitLossBeforeTax reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:PropertyPlantAndEquipment reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:Assets reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:Equity reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:Liabilities reported in iso4217:AUD
- BERKELEY ENERGIA LIMITED FY2024: ifrs-full:TradeAndOtherCurrentPayables reported in iso4217:AUD
- CONSTRUCCIONES Y AUXILIAR DE FERROCARRILES, S.A. FY2023: ifrs-full:BasicEarningsLossPerShare reported in iso4217:AED/xbrli:shares
- MONTEBALITO, S.A. FY2024: ifrs-full:BasicEarningsLossPerShare reported in iso4217:AED/xbrli:shares
- NATURHOUSE HEALTH, S.A. FY2023: ifrs-full:BasicEarningsLossPerShare reported in iso4217:AED/xbrli:shares

## Checks of the pipeline's own work (after loading, in the database)

### Failed filings

- **PASS** Every selected filing processed without error (0 failed)

### Coverage of this run

- **PASS** Filings of this run in the database = 230 (found: 230)
- **PASS** Every filing links one company (125 companies for 230 filings)
- **PASS** No filing of this run left without facts (0 unexplained)
- explained: `213800JX3V4TPO7TCJ08-2023-06-30-ESEF-ES-0` reports its figures in another currency (flagged `non_eur_unit`, not converted)
- explained: `213800JX3V4TPO7TCJ08-2024-06-30-ESEF-ES-0` reports its figures in another currency (flagged `non_eur_unit`, not converted)

### Referential integrity

- **PASS** financial_fact -> fiscal_period (orphans: 0)
- **PASS** financial_fact -> metric (orphans: 0)
- **PASS** financial_fact -> filing (orphans: 0)
- **PASS** fiscal_period -> company (orphans: 0)
- **PASS** filing -> company (orphans: 0)
- **PASS** validation_issue -> filing (orphans: 0)
- **PASS** ownership -> company (orphans: 0)
- **PASS** fact's fiscal period belongs to its filing's company (orphans: 0)

### Nil facts (not available, never 0)

- **PASS** No nil fact carries a value (rows: 0)
- **PASS** No NULL value without is_nil (rows: 0)
- values loaded as nil (NULL + is_nil): 0
- derived values (is_reported = false in v_financial): 145

### Ownership

- **PASS** No ownership statement left unresolved by the second pass (pending: 0)

| source | resolution | statements (all filings) |
|---|---|---:|
| esef | lei | 10 |
| esef | none_declared | 2 |
| esef | self | 345 |
| esef | unparsed | 3 |
| esef | unresolved | 98 |
| gleif | lei | 8 |
| gleif | none_declared | 196 |
| gleif | unresolved | 46 |

Curated view `v_ownership` (latest ESEF statement per company + GLEIF):

| source | relation | rows | parent is a loaded company | unresolved | self-reference | cycle |
|---|---|---:|---:|---:|---:|---:|
| esef | direct | 124 | 3 | 21 | 98 | 0 |
| esef | ultimate | 124 | 2 | 33 | 88 | 0 |
| gleif | direct | 125 | 5 | 22 | 0 | 0 |
| gleif | ultimate | 125 | 3 | 24 | 0 | 0 |

### Golden figures (read by hand from the published XHTML reports)

- **PASS** ENDESA SA FY2024 revenue = 20.935 x 1,000,000 (loaded: 20,935,000,000)
- **PASS** ENDESA SA FY2024 total_assets = 37.345 x 1,000,000 (loaded: 37,345,000,000)
- **PASS** INDUSTRIA DE DISEÑO TEXTIL, S.A. FY2024 revenue = 38,632 x 1,000,000 (loaded: 38,632,000,000)
- **PASS** INDUSTRIA DE DISEÑO TEXTIL, S.A. FY2024 net_profit = 5,877 x 1,000,000 (loaded: 5,877,000,000)
- **PASS** IBERDROLA SA FY2024 total_equity = 61.051 x 1,000,000 (loaded: 61,051,000,000)
- **PASS** BANKINTER SOCIEDAD ANONIMA FY2024 total_assets = 121.971.823 x 1,000 (loaded: 121,971,823,000)
- **PASS** TELEFONICA SA FY2023 cash = 7,151 x 1,000,000 (loaded: 7,151,000,000)
- **PASS** PROSEGUR CASH, S.A. FY2024 revenue = 2.089.879 x 1,000 (loaded: 2,089,879,000)
- **PASS** PROSEGUR CASH, S.A. FY2024 net_profit = 91.046 x 1,000 (loaded: 91,046,000)

### Acceptance queries

- **PASS** 'Top 5 companies by revenue, latest fiscal year' returns rows
- **PASS** 'Coverage per fiscal year (companies with total assets / with revenue)' returns rows
- **PASS** 'Parents that are themselves loaded companies (latest ESEF statement or GLEIF)' returns rows

### Top 5 companies by revenue, latest fiscal year (5 rows)

| company_name | fiscal_year | revenue_meur | source_concept |
|---|---|---|---|
| IBERDROLA SA | 2024 | 44739 | ifrs-full:Revenue |
| ACS ACTIVIDADES DE CONSTRUCCION Y SERVICIOS, S.A. | 2024 | 41633 | ifrs-full:Revenue |
| TELEFONICA SA | 2024 | 41315 | ifrs-full:Revenue |
| INDUSTRIA DE DISEÑO TEXTIL, S.A. | 2024 | 38632 | ifrs-full:Revenue |
| INTERNATIONAL CONSOLIDATED AIRLINES GROUP, S.A. | 2024 | 32100 | ifrs-full:Revenue |

### Coverage per fiscal year (companies with total assets / with revenue) (2 rows)

| fiscal_year | with_total_assets | with_revenue | derived_values |
|---|---|---|---|
| 2023 | 121 | 102 | 79 |
| 2024 | 107 | 88 | 66 |

### Parents that are themselves loaded companies (latest ESEF statement or GLEIF) (13 rows)

| company_name | relation | source | parent_company_name |
|---|---|---|---|
| AEDAS HOMES SA | direct | gleif | NEINOR HOMES SA |
| AEDAS HOMES SA | ultimate | gleif | NEINOR HOMES SA |
| Corporacion Acciona Energias Renovables SA | direct | esef | Acciona SA |
| Corporacion Acciona Energias Renovables SA | direct | gleif | Acciona SA |
| Corporacion Acciona Energias Renovables SA | ultimate | esef | Acciona SA |
| Corporacion Acciona Energias Renovables SA | ultimate | gleif | Acciona SA |
| INMOCEMENTO S.A. | direct | gleif | FOMENTO DE CONSTRUCCIONES Y CONTRATAS S.A. |
| PROSEGUR CASH, S.A. | direct | esef | PROSEGUR COMPAÑIA DE SEGURIDAD, S.A. |
| PROSEGUR CASH, S.A. | direct | gleif | PROSEGUR COMPAÑIA DE SEGURIDAD, S.A. |
| SANTANDER CONSUMER FINANCE SA | direct | esef | BANCO SANTANDER S.A. |
| SANTANDER CONSUMER FINANCE SA | direct | gleif | BANCO SANTANDER S.A. |
| SANTANDER CONSUMER FINANCE SA | ultimate | esef | BANCO SANTANDER S.A. |
| ... 1 more rows |

### Load warnings (not blocking, read them)

- none: no value was dropped while loading

### Unmapped extension concepts

- 6472 current-year, undimensioned EUR facts of company extension concepts are not mapped (their meaning lives in each company's taxonomy package); all listed in `reports/unmapped_concepts.csv`, none dropped silently.
- Revenue gap: 16 filings of non-financial companies have no IFRS revenue; 4 of them tag an extension concept that looks like a revenue line by name (`looks_like_revenue`, 20 rows in the CSV). It is not mapped: its definition lives in the company's own taxonomy.

### Row counts (whole database)

- company: 125
- fiscal_period: 230
- filing: 230
- metric: 40
- financial_fact: 7398
- validation_issue: 153
- ownership: 708

| statement | values |
|---|---:|
| balance_sheet | 3726 |
| cash_flow | 1130 |
| income_statement | 2542 |
