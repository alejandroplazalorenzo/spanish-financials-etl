-- 005: surrogate keys, a fiscal_period dimension, nil facts and a 40-metric catalogue.
--
-- Why. 001 keyed everything on natural keys: company.lei, metric.metric (text) and facts on
-- (lei, fiscal_year, metric). The production schema this project rebuilds did the opposite
-- from the start, and the reasons hold here too:
--   * a natural key can be wrong at the source and need correcting; with a surrogate key the
--     correction touches one column, not every child row;
--   * every foreign key repeats the key it points to: an integer is cheaper than char(20) or
--     text in the fact table and its indexes;
--   * every join looks the same (x_id).
-- The natural keys stay, as UNIQUE constraints, so the business rules still hold: one company
-- per LEI, one filing per fxo_id, one fiscal period per company and year, one value per
-- (fiscal period, metric).
--
-- fiscal_period becomes a dimension (one row per company and fiscal year, with its dates)
-- instead of repeating the period on every fact.
--
-- Three states, never two: a value, a zero, or "not available". A fact tagged nil is stored as
-- value NULL + is_nil = true; the CHECK below makes "nil with a value" impossible.
--
-- The catalogue grows from 10 to 40 IFRS metrics, each with statement, category, unit, parent
-- (display hierarchy) and order. Adding a metric is one INSERT into metric (plus its concept
-- list in src/sfetl/concepts.py); no table or view has to change.
--
-- These tables hold only data derived from the cached filings, so they are dropped and
-- recreated rather than altered in place. Run `sfetl run` after this migration.

DROP VIEW IF EXISTS company_year_ratios;
DROP VIEW IF EXISTS company_year;
DROP TABLE IF EXISTS validation_issue, financial_fact, metric, filing, company;

CREATE TABLE company (
    company_id       bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lei              char(20)    NOT NULL UNIQUE,         -- natural key (ISO 17442)
    name             text        NOT NULL,
    name_period_end  date        NOT NULL,                -- period end of the filing it came from
    country          char(2)     NOT NULL DEFAULT 'ES',
    is_financial     boolean     NOT NULL DEFAULT false,  -- bank or insurer (see README)
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE fiscal_period (
    fiscal_period_id bigint   GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id       bigint   NOT NULL REFERENCES company (company_id),
    fiscal_year      smallint NOT NULL,     -- calendar year holding most of the months
    period_start     date,
    period_end       date     NOT NULL,
    months           smallint,
    UNIQUE (company_id, fiscal_year)
);

CREATE TABLE filing (
    filing_id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_filing_id     integer     NOT NULL UNIQUE,     -- id on filings.xbrl.org
    fxo_id               text        NOT NULL UNIQUE,
    company_id           bigint      NOT NULL REFERENCES company (company_id),
    fiscal_period_id     bigint      NOT NULL REFERENCES fiscal_period (fiscal_period_id),
    period_end           date        NOT NULL,            -- as declared by the filings index
    document_period_end  date,                            -- as detected from the report facts
    date_added           timestamptz NOT NULL,
    json_url             text        NOT NULL,
    report_url           text,                            -- human-readable XHTML report
    viewer_url           text,
    error_count          integer     NOT NULL DEFAULT 0,  -- XBRL validation counts (index)
    warning_count        integer     NOT NULL DEFAULT 0,
    inconsistency_count  integer     NOT NULL DEFAULT 0,
    numeric_facts        integer     NOT NULL,
    nil_facts            integer     NOT NULL DEFAULT 0,
    is_financial         boolean     NOT NULL DEFAULT false,  -- bank/insurer markers found
    loaded_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX filing_company_idx ON filing (company_id);
CREATE INDEX filing_fiscal_period_idx ON filing (fiscal_period_id);

CREATE TABLE metric (
    metric_id    integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code         text    NOT NULL UNIQUE,                  -- stable slug used by the code
    label        text    NOT NULL,
    statement    text    NOT NULL
                 CHECK (statement IN ('balance_sheet', 'income_statement', 'cash_flow')),
    category     text    NOT NULL,
    unit         text    NOT NULL CHECK (unit IN ('EUR', 'EUR/share')),
    period_type  text    NOT NULL CHECK (period_type IN ('instant', 'duration')),
    parent_id    integer REFERENCES metric (metric_id),    -- display hierarchy / subtotals
    sort_order   integer NOT NULL,
    description  text    NOT NULL                          -- IFRS concepts, in priority order
);

INSERT INTO metric (code, label, statement, category, unit, period_type, sort_order, description)
VALUES
    ('total_assets', 'Total assets', 'balance_sheet', 'assets', 'EUR', 'instant', 10,
     'ifrs-full:Assets'),
    ('non_current_assets', 'Non-current assets', 'balance_sheet', 'assets', 'EUR', 'instant', 20,
     'ifrs-full:NoncurrentAssets'),
    ('property_plant_equipment', 'Property, plant and equipment', 'balance_sheet', 'assets',
     'EUR', 'instant', 30, 'ifrs-full:PropertyPlantAndEquipment'),
    ('right_of_use_assets', 'Right-of-use assets', 'balance_sheet', 'assets', 'EUR', 'instant',
     40, 'ifrs-full:RightofuseAssets'),
    ('investment_property', 'Investment property', 'balance_sheet', 'assets', 'EUR', 'instant',
     50, 'ifrs-full:InvestmentProperty'),
    ('goodwill', 'Goodwill', 'balance_sheet', 'assets', 'EUR', 'instant', 60,
     'ifrs-full:Goodwill'),
    ('intangible_assets', 'Intangible assets other than goodwill', 'balance_sheet', 'assets',
     'EUR', 'instant', 70, 'ifrs-full:IntangibleAssetsOtherThanGoodwill'),
    ('current_assets', 'Current assets', 'balance_sheet', 'assets', 'EUR', 'instant', 80,
     'ifrs-full:CurrentAssets'),
    ('inventories', 'Inventories', 'balance_sheet', 'assets', 'EUR', 'instant', 90,
     'ifrs-full:Inventories'),
    ('trade_receivables', 'Trade and other current receivables', 'balance_sheet', 'assets',
     'EUR', 'instant', 100, 'ifrs-full:TradeAndOtherCurrentReceivables'),
    ('cash', 'Cash and cash equivalents', 'balance_sheet', 'assets', 'EUR', 'instant', 110,
     'ifrs-full:CashAndCashEquivalents'),
    ('equity_and_liabilities', 'Total equity and liabilities', 'balance_sheet',
     'equity_and_liabilities', 'EUR', 'instant', 120, 'ifrs-full:EquityAndLiabilities'),
    ('total_equity', 'Total equity', 'balance_sheet', 'equity', 'EUR', 'instant', 130,
     'ifrs-full:Equity'),
    ('equity_parent', 'Equity attributable to owners of the parent', 'balance_sheet', 'equity',
     'EUR', 'instant', 140, 'ifrs-full:EquityAttributableToOwnersOfParent'),
    ('issued_capital', 'Issued capital', 'balance_sheet', 'equity', 'EUR', 'instant', 150,
     'ifrs-full:IssuedCapital'),
    ('non_controlling_interests', 'Non-controlling interests', 'balance_sheet', 'equity', 'EUR',
     'instant', 160, 'ifrs-full:NoncontrollingInterests'),
    ('total_liabilities', 'Total liabilities', 'balance_sheet', 'liabilities', 'EUR', 'instant',
     170, 'ifrs-full:Liabilities; derived from NoncurrentLiabilities + CurrentLiabilities when '
          'not tagged (is_reported = false)'),
    ('non_current_liabilities', 'Non-current liabilities', 'balance_sheet', 'liabilities', 'EUR',
     'instant', 180, 'ifrs-full:NoncurrentLiabilities'),
    ('current_liabilities', 'Current liabilities', 'balance_sheet', 'liabilities', 'EUR',
     'instant', 190, 'ifrs-full:CurrentLiabilities'),
    ('trade_payables', 'Trade and other current payables', 'balance_sheet', 'liabilities', 'EUR',
     'instant', 200, 'ifrs-full:TradeAndOtherCurrentPayables'),
    ('revenue', 'Revenue', 'income_statement', 'revenue', 'EUR', 'duration', 210,
     'ifrs-full:Revenue, ifrs-full:RevenueFromContractsWithCustomers'),
    ('employee_benefits_expense', 'Employee benefits expense', 'income_statement', 'operating',
     'EUR', 'duration', 220, 'ifrs-full:EmployeeBenefitsExpense'),
    ('depreciation_amortisation', 'Depreciation and amortisation expense', 'income_statement',
     'operating', 'EUR', 'duration', 230, 'ifrs-full:DepreciationAndAmortisationExpense'),
    ('operating_profit', 'Operating profit', 'income_statement', 'operating', 'EUR', 'duration',
     240, 'ifrs-full:ProfitLossFromOperatingActivities'),
    ('finance_income', 'Finance income', 'income_statement', 'financial', 'EUR', 'duration', 250,
     'ifrs-full:FinanceIncome'),
    ('finance_costs', 'Finance costs', 'income_statement', 'financial', 'EUR', 'duration', 260,
     'ifrs-full:FinanceCosts'),
    ('profit_before_tax', 'Profit before tax', 'income_statement', 'profit', 'EUR', 'duration',
     270, 'ifrs-full:ProfitLossBeforeTax'),
    ('income_tax', 'Income tax expense', 'income_statement', 'tax', 'EUR', 'duration', 280,
     'ifrs-full:IncomeTaxExpenseContinuingOperations'),
    ('profit_continuing', 'Profit from continuing operations', 'income_statement', 'profit',
     'EUR', 'duration', 290, 'ifrs-full:ProfitLossFromContinuingOperations'),
    ('profit_discontinued', 'Profit from discontinued operations', 'income_statement', 'profit',
     'EUR', 'duration', 300, 'ifrs-full:ProfitLossFromDiscontinuedOperations'),
    ('net_profit', 'Profit for the year', 'income_statement', 'profit', 'EUR', 'duration', 310,
     'ifrs-full:ProfitLoss'),
    ('net_profit_parent', 'Profit attributable to owners of the parent', 'income_statement',
     'profit', 'EUR', 'duration', 320, 'ifrs-full:ProfitLossAttributableToOwnersOfParent'),
    ('net_profit_nci', 'Profit attributable to non-controlling interests', 'income_statement',
     'profit', 'EUR', 'duration', 330,
     'ifrs-full:ProfitLossAttributableToNoncontrollingInterests'),
    ('basic_eps', 'Basic earnings per share', 'income_statement', 'per_share', 'EUR/share',
     'duration', 340, 'ifrs-full:BasicEarningsLossPerShare'),
    ('operating_cash_flow', 'Cash flows from operating activities', 'cash_flow', 'cash_flow',
     'EUR', 'duration', 350, 'ifrs-full:CashFlowsFromUsedInOperatingActivities'),
    ('investing_cash_flow', 'Cash flows from investing activities', 'cash_flow', 'cash_flow',
     'EUR', 'duration', 360, 'ifrs-full:CashFlowsFromUsedInInvestingActivities'),
    ('capex_ppe', 'Purchase of property, plant and equipment', 'cash_flow', 'cash_flow', 'EUR',
     'duration', 370, 'ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities'),
    ('financing_cash_flow', 'Cash flows from financing activities', 'cash_flow', 'cash_flow',
     'EUR', 'duration', 380, 'ifrs-full:CashFlowsFromUsedInFinancingActivities'),
    ('dividends_paid', 'Dividends paid', 'cash_flow', 'cash_flow', 'EUR', 'duration', 390,
     'ifrs-full:DividendsPaidClassifiedAsFinancingActivities'),
    ('net_change_in_cash', 'Increase (decrease) in cash and cash equivalents', 'cash_flow',
     'cash_flow', 'EUR', 'duration', 400, 'ifrs-full:IncreaseDecreaseInCashAndCashEquivalents');

UPDATE metric m
SET parent_id = p.metric_id
FROM (VALUES
    ('non_current_assets', 'total_assets'),
    ('property_plant_equipment', 'non_current_assets'),
    ('right_of_use_assets', 'non_current_assets'),
    ('investment_property', 'non_current_assets'),
    ('goodwill', 'non_current_assets'),
    ('intangible_assets', 'non_current_assets'),
    ('current_assets', 'total_assets'),
    ('inventories', 'current_assets'),
    ('trade_receivables', 'current_assets'),
    ('cash', 'current_assets'),
    ('total_equity', 'equity_and_liabilities'),
    ('equity_parent', 'total_equity'),
    ('issued_capital', 'equity_parent'),
    ('non_controlling_interests', 'total_equity'),
    ('total_liabilities', 'equity_and_liabilities'),
    ('non_current_liabilities', 'total_liabilities'),
    ('current_liabilities', 'total_liabilities'),
    ('trade_payables', 'current_liabilities'),
    ('income_tax', 'profit_before_tax'),
    ('profit_continuing', 'net_profit'),
    ('profit_discontinued', 'net_profit'),
    ('net_profit_parent', 'net_profit'),
    ('net_profit_nci', 'net_profit'),
    ('capex_ppe', 'investing_cash_flow'),
    ('dividends_paid', 'financing_cash_flow')
) AS h (child, parent)
JOIN metric p ON p.code = h.parent
WHERE m.code = h.child;

CREATE TABLE financial_fact (
    fiscal_period_id bigint        NOT NULL REFERENCES fiscal_period (fiscal_period_id),
    metric_id        integer       NOT NULL REFERENCES metric (metric_id),
    value            numeric(24,6),                  -- in `metric.unit`; NULL only when nil
    is_nil           boolean       NOT NULL DEFAULT false,
    decimals         smallint,                       -- reported precision; NULL = exact (INF)
    period_start     date,                           -- NULL for balance-sheet (instant) metrics
    period_end       date          NOT NULL,
    source_concept   text          NOT NULL,         -- ifrs-full:Revenue, or derived:...
    filing_id        bigint        NOT NULL REFERENCES filing (filing_id),
    loaded_at        timestamptz   NOT NULL DEFAULT now(),
    PRIMARY KEY (fiscal_period_id, metric_id),
    CONSTRAINT financial_fact_nil_has_no_value CHECK ((value IS NULL) = is_nil)
);
CREATE INDEX financial_fact_metric_idx ON financial_fact (metric_id);
CREATE INDEX financial_fact_filing_idx ON financial_fact (filing_id);

CREATE TABLE validation_issue (
    issue_id     bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filing_id    bigint      NOT NULL REFERENCES filing (filing_id),
    rule         text        NOT NULL,
    severity     text        NOT NULL CHECK (severity IN ('error', 'warning', 'info')),
    metric_id    integer     REFERENCES metric (metric_id),
    detail       text        NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX validation_issue_filing_idx ON validation_issue (filing_id);
