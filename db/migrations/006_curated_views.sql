-- 006: curated views, the only objects the assistant can read (migration 008).
--
-- Why. In production the assistant never read the raw fact tables: the complexity that leads
-- to a wrong answer (which filing wins, which figure is derived, which companies have no
-- revenue) is resolved once, in a view, and every query goes through it. The raw tables stay
-- for the pipeline and for analysts.
--
-- v_financial is the long view: one row per company, fiscal year and metric, with the
-- catalogue metadata (statement, category, unit) next to the value. A new metric appears here
-- as soon as it is INSERTed into metric; nothing else changes.
--
-- is_reported = false marks a value computed here rather than tagged by the filer
-- (source_concept 'derived:...'). A derived figure is never presented as a reported one.

CREATE VIEW v_company AS
SELECT
    c.lei,
    c.name,
    c.country,
    c.is_financial,
    min(fp.fiscal_year) AS first_fiscal_year,
    max(fp.fiscal_year) AS last_fiscal_year,
    count(DISTINCT fi.filing_id) AS filings
FROM company c
LEFT JOIN fiscal_period fp ON fp.company_id = c.company_id
LEFT JOIN filing fi ON fi.company_id = c.company_id
GROUP BY c.company_id, c.lei, c.name, c.country, c.is_financial;

CREATE VIEW v_metric AS
SELECT
    m.code,
    m.label,
    m.statement,
    m.category,
    m.unit,
    m.period_type,
    p.code AS parent_code,
    m.sort_order,
    m.description
FROM metric m
LEFT JOIN metric p ON p.metric_id = m.parent_id;

CREATE VIEW v_financial AS
SELECT
    c.lei,
    c.name                                         AS company_name,
    c.is_financial,
    fp.fiscal_year,
    fp.period_start                                AS fiscal_year_start,
    fp.period_end                                  AS fiscal_year_end,
    m.statement,
    m.category,
    m.code                                         AS metric_code,
    m.label                                        AS metric_label,
    m.unit,
    m.sort_order,
    f.value,
    f.is_nil,
    (f.source_concept NOT LIKE 'derived:%')        AS is_reported,
    f.source_concept,
    f.decimals,
    fi.fxo_id,
    fi.viewer_url
FROM financial_fact f
JOIN fiscal_period fp ON fp.fiscal_period_id = f.fiscal_period_id
JOIN company c        ON c.company_id = fp.company_id
JOIN metric m         ON m.metric_id = f.metric_id
JOIN filing fi        ON fi.filing_id = f.filing_id;

COMMENT ON VIEW v_financial IS
    'One row per company, fiscal year and metric. value is NULL when the filer tagged the fact '
    'nil (is_nil). is_reported = false when the value was computed here (derived), not tagged.';

-- Per company-year ratios, computed here from reported values only (never from a derived one).
-- NULL when an input is missing or nil, or when the denominator is not positive: no estimate.
CREATE VIEW v_company_ratios AS
WITH pivot AS (
    SELECT
        lei,
        company_name,
        is_financial,
        fiscal_year,
        max(value) FILTER (WHERE metric_code = 'revenue')             AS revenue,
        max(value) FILTER (WHERE metric_code = 'operating_profit')    AS operating_profit,
        max(value) FILTER (WHERE metric_code = 'net_profit')          AS net_profit,
        max(value) FILTER (WHERE metric_code = 'total_assets')        AS total_assets,
        max(value) FILTER (WHERE metric_code = 'total_equity')        AS total_equity,
        max(value) FILTER (WHERE metric_code = 'current_assets')      AS current_assets,
        max(value) FILTER (WHERE metric_code = 'current_liabilities') AS current_liabilities
    FROM v_financial
    WHERE is_reported AND NOT is_nil
    GROUP BY lei, company_name, is_financial, fiscal_year
)
SELECT
    lei,
    company_name,
    is_financial,
    fiscal_year,
    round(net_profit       / NULLIF(GREATEST(revenue, 0), 0), 4)             AS net_margin,
    round(operating_profit / NULLIF(GREATEST(revenue, 0), 0), 4)             AS operating_margin,
    round(total_equity     / NULLIF(GREATEST(total_assets, 0), 0), 4)        AS equity_ratio,
    round(current_assets   / NULLIF(GREATEST(current_liabilities, 0), 0), 4) AS current_ratio
FROM pivot;

COMMENT ON VIEW v_company_ratios IS
    'Ratios of ONE company-year, computed from reported values (not reported by the company). '
    'Fractions: 0.12 = 12 %. NULL when an input is missing, nil or the denominator is not positive.';

CREATE VIEW v_filing AS
SELECT
    c.lei,
    c.name AS company_name,
    fp.fiscal_year,
    fi.fxo_id,
    fi.period_end,
    fi.date_added,
    fi.viewer_url,
    fi.report_url,
    fi.error_count   AS xbrl_errors,
    fi.warning_count AS xbrl_warnings,
    fi.numeric_facts,
    fi.nil_facts,
    (SELECT count(*) FROM financial_fact f WHERE f.filing_id = fi.filing_id) AS metrics_loaded,
    (SELECT count(*) FROM validation_issue v
      WHERE v.filing_id = fi.filing_id AND v.severity = 'error')             AS flags_error,
    (SELECT count(*) FROM validation_issue v
      WHERE v.filing_id = fi.filing_id AND v.severity = 'warning')           AS flags_warning
FROM filing fi
JOIN company c        ON c.company_id = fi.company_id
JOIN fiscal_period fp ON fp.fiscal_period_id = fi.fiscal_period_id;

CREATE VIEW v_validation_issue AS
SELECT
    c.lei,
    c.name AS company_name,
    fp.fiscal_year,
    fi.fxo_id,
    v.rule,
    v.severity,
    m.code AS metric_code,
    v.detail
FROM validation_issue v
JOIN filing fi        ON fi.filing_id = v.filing_id
JOIN company c        ON c.company_id = fi.company_id
JOIN fiscal_period fp ON fp.fiscal_period_id = fi.fiscal_period_id
LEFT JOIN metric m    ON m.metric_id = v.metric_id;
