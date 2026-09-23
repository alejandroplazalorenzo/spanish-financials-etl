-- 001: core tables. Long format: one row per (company, fiscal year, metric).

CREATE TABLE company (
    lei           char(20)    PRIMARY KEY,           -- Legal Entity Identifier (ISO 17442)
    name          text        NOT NULL,
    country       char(2)     NOT NULL DEFAULT 'ES',
    is_financial  boolean     NOT NULL DEFAULT false, -- bank or insurer (see README)
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE filing (
    filing_id            integer     PRIMARY KEY,      -- id on filings.xbrl.org
    fxo_id               text        NOT NULL UNIQUE,
    lei                  char(20)    NOT NULL REFERENCES company (lei),
    fiscal_year          smallint    NOT NULL,
    period_end           date        NOT NULL,         -- as declared by the filings index
    document_period_end  date,                         -- as detected from the report facts
    date_added           timestamptz NOT NULL,
    json_url             text        NOT NULL,
    viewer_url           text,
    error_count          integer     NOT NULL DEFAULT 0, -- XBRL validation counts from the index
    warning_count        integer     NOT NULL DEFAULT 0,
    inconsistency_count  integer     NOT NULL DEFAULT 0,
    numeric_facts        integer     NOT NULL,         -- numeric facts found in the report
    loaded_at            timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX filing_lei_year_idx ON filing (lei, fiscal_year);

CREATE TABLE metric (
    metric       text PRIMARY KEY,
    label        text NOT NULL,
    statement    text NOT NULL CHECK (statement IN ('balance_sheet', 'income_statement')),
    period_type  text NOT NULL CHECK (period_type IN ('instant', 'duration')),
    description  text NOT NULL
);

INSERT INTO metric (metric, label, statement, period_type, description) VALUES
    ('revenue',             'Revenue',                         'income_statement', 'duration',
     'ifrs-full:Revenue. Not reported by banks and insurers (left empty for them).'),
    ('operating_profit',    'Operating profit',                'income_statement', 'duration',
     'ifrs-full:ProfitLossFromOperatingActivities.'),
    ('net_profit',          'Profit for the year',             'income_statement', 'duration',
     'ifrs-full:ProfitLoss, including non-controlling interests.'),
    ('net_profit_parent',   'Profit attributable to owners',   'income_statement', 'duration',
     'ifrs-full:ProfitLossAttributableToOwnersOfParent.'),
    ('total_assets',        'Total assets',                    'balance_sheet',    'instant',
     'ifrs-full:Assets.'),
    ('total_equity',        'Total equity',                    'balance_sheet',    'instant',
     'ifrs-full:Equity, including non-controlling interests.'),
    ('total_liabilities',   'Total liabilities',               'balance_sheet',    'instant',
     'ifrs-full:Liabilities, or NoncurrentLiabilities + CurrentLiabilities when the total is not tagged.'),
    ('cash',                'Cash and cash equivalents',       'balance_sheet',    'instant',
     'ifrs-full:CashAndCashEquivalents.'),
    ('current_assets',      'Current assets',                  'balance_sheet',    'instant',
     'ifrs-full:CurrentAssets. Not presented by banks and insurers.'),
    ('current_liabilities', 'Current liabilities',             'balance_sheet',    'instant',
     'ifrs-full:CurrentLiabilities. Not presented by banks and insurers.');

CREATE TABLE financial_fact (
    lei             char(20)      NOT NULL REFERENCES company (lei),
    fiscal_year     smallint      NOT NULL,
    metric          text          NOT NULL REFERENCES metric (metric),
    value_eur       numeric(20,2) NOT NULL,
    decimals        smallint,                  -- reported precision; NULL = exact (INF)
    period_start    date,                      -- NULL for balance-sheet (instant) metrics
    period_end      date          NOT NULL,
    source_concept  text          NOT NULL,    -- e.g. ifrs-full:Revenue, or derived:...
    filing_id       integer       NOT NULL REFERENCES filing (filing_id),
    loaded_at       timestamptz   NOT NULL DEFAULT now(),
    PRIMARY KEY (lei, fiscal_year, metric)
);

CREATE INDEX financial_fact_metric_year_idx ON financial_fact (metric, fiscal_year);

CREATE TABLE validation_issue (
    issue_id     bigserial   PRIMARY KEY,
    filing_id    integer     NOT NULL REFERENCES filing (filing_id),
    lei          char(20)    NOT NULL,
    fiscal_year  smallint    NOT NULL,
    rule         text        NOT NULL,
    severity     text        NOT NULL CHECK (severity IN ('error', 'warning', 'info')),
    metric       text,
    detail       text        NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX validation_issue_filing_idx ON validation_issue (filing_id);
