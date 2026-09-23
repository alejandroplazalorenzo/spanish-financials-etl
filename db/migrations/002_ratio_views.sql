-- 002: analyst views on top of the long table.

-- One row per company and fiscal year, one column per metric (pivot of financial_fact).
CREATE VIEW company_year AS
SELECT
    f.lei,
    c.name                                                        AS company_name,
    c.is_financial,
    f.fiscal_year,
    max(f.value_eur) FILTER (WHERE f.metric = 'revenue')             AS revenue,
    max(f.value_eur) FILTER (WHERE f.metric = 'operating_profit')    AS operating_profit,
    max(f.value_eur) FILTER (WHERE f.metric = 'net_profit')          AS net_profit,
    max(f.value_eur) FILTER (WHERE f.metric = 'net_profit_parent')   AS net_profit_parent,
    max(f.value_eur) FILTER (WHERE f.metric = 'total_assets')        AS total_assets,
    max(f.value_eur) FILTER (WHERE f.metric = 'total_equity')        AS total_equity,
    max(f.value_eur) FILTER (WHERE f.metric = 'total_liabilities')   AS total_liabilities,
    max(f.value_eur) FILTER (WHERE f.metric = 'cash')                AS cash,
    max(f.value_eur) FILTER (WHERE f.metric = 'current_assets')      AS current_assets,
    max(f.value_eur) FILTER (WHERE f.metric = 'current_liabilities') AS current_liabilities
FROM financial_fact f
JOIN company c ON c.lei = f.lei
GROUP BY f.lei, c.name, c.is_financial, f.fiscal_year;

-- Per company-year ratios. NULL when an input is missing or the denominator is not positive.
CREATE VIEW company_year_ratios AS
SELECT
    lei,
    company_name,
    is_financial,
    fiscal_year,
    round(net_profit       / NULLIF(revenue, 0), 4)             AS net_margin,
    round(operating_profit / NULLIF(revenue, 0), 4)             AS operating_margin,
    round(total_equity     / NULLIF(total_assets, 0), 4)        AS equity_ratio,
    round(current_assets   / NULLIF(current_liabilities, 0), 4) AS current_ratio
FROM company_year;

-- Cross-company aggregates per fiscal year: always a ratio of sums over the companies that
-- report BOTH the numerator and the denominator, never an average of company ratios.
CREATE VIEW year_aggregate_ratios AS
SELECT
    fiscal_year,
    is_financial,
    count(*)                                                              AS companies,
    count(*) FILTER (WHERE revenue > 0 AND net_profit IS NOT NULL)        AS companies_net_margin,
    round(sum(net_profit) FILTER (WHERE revenue > 0 AND net_profit IS NOT NULL)
          / NULLIF(sum(revenue) FILTER (WHERE revenue > 0 AND net_profit IS NOT NULL), 0), 4)
                                                                          AS net_margin,
    count(*) FILTER (WHERE revenue > 0 AND operating_profit IS NOT NULL)  AS companies_operating_margin,
    round(sum(operating_profit) FILTER (WHERE revenue > 0 AND operating_profit IS NOT NULL)
          / NULLIF(sum(revenue) FILTER (WHERE revenue > 0 AND operating_profit IS NOT NULL), 0), 4)
                                                                          AS operating_margin,
    count(*) FILTER (WHERE total_assets > 0 AND total_equity IS NOT NULL) AS companies_equity_ratio,
    round(sum(total_equity) FILTER (WHERE total_assets > 0 AND total_equity IS NOT NULL)
          / NULLIF(sum(total_assets) FILTER (WHERE total_assets > 0 AND total_equity IS NOT NULL), 0), 4)
                                                                          AS equity_ratio
FROM company_year
GROUP BY fiscal_year, is_financial;
