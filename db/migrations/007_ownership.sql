-- 007: ownership structure (parent and ultimate parent of each company).
--
-- Why. Company data without "who owns whom" cannot answer group questions. Production stored
-- the ownership graph as declared by each report and resolved the counterparties in a second
-- pass, keeping what did not resolve (by name) instead of dropping it, and marking
-- contradictory declarations instead of choosing a direction.
--
-- Here the sources are public: the parent names that ESEF filings tag
-- (ifrs-full:NameOfParentEntity / NameOfUltimateParentOfGroup, free text, one row per filing and
-- relation) and GLEIF Level 2 relationships between LEIs (one row per company and relation).
-- parent_company_id is filled only when the parent is a company loaded here; otherwise the row
-- keeps parent_name (and parent_lei when GLEIF gives one).
--
-- resolution: lei | name | self | none_declared | unparsed | unresolved | pending
--   self          = the declared parent is the company itself. An entity cannot be its own
--                   parent, so the view marks it contradictory (many filers put their own name
--                   in this tag because they head the reporting group);
--   none_declared = "No hay" or a GLEIF reporting exception (reason in detail);
--   unparsed      = free text from which no name could be read (raw text kept);
--   pending       = inserted by pass 1, not yet resolved by pass 2.

CREATE TABLE ownership (
    ownership_id      bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id        bigint      NOT NULL REFERENCES company (company_id),
    source            text        NOT NULL CHECK (source IN ('esef', 'gleif')),
    relation          text        NOT NULL CHECK (relation IN ('direct', 'ultimate')),
    filing_id         bigint      REFERENCES filing (filing_id),       -- ESEF rows only
    parent_name_raw   text,                                            -- as declared
    parent_name       text,                                            -- cleaned
    parent_lei        char(20),
    parent_company_id bigint      REFERENCES company (company_id),
    resolution        text        NOT NULL DEFAULT 'pending'
                      CHECK (resolution IN ('lei', 'name', 'self', 'none_declared',
                                            'unparsed', 'unresolved', 'pending')),
    detail            text,
    loaded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ownership_esef_has_filing CHECK ((source = 'esef') = (filing_id IS NOT NULL)),
    CONSTRAINT ownership_one_statement UNIQUE NULLS NOT DISTINCT
        (company_id, source, relation, filing_id)
);
CREATE INDEX ownership_parent_idx ON ownership (parent_company_id);
CREATE INDEX ownership_filing_idx ON ownership (filing_id);

-- Latest ESEF statement per company and relation, plus the GLEIF relationship.
-- contradictory = self-reference, or a cycle (A declares B as parent and B declares A).
CREATE VIEW v_ownership AS
WITH latest_esef AS (
    SELECT DISTINCT ON (o.company_id, o.relation) o.*
    FROM ownership o
    JOIN filing fi ON fi.filing_id = o.filing_id
    WHERE o.source = 'esef'
    ORDER BY o.company_id, o.relation, fi.period_end DESC, fi.filing_id DESC
),
edges AS (
    SELECT * FROM latest_esef
    UNION ALL
    SELECT * FROM ownership WHERE source = 'gleif'
),
cycles AS (
    SELECT DISTINCT
        LEAST(a.company_id, a.parent_company_id)    AS x,
        GREATEST(a.company_id, a.parent_company_id) AS y
    FROM edges a
    JOIN edges b
      ON b.company_id = a.parent_company_id
     AND b.parent_company_id = a.company_id
    WHERE a.parent_company_id IS NOT NULL
      AND a.parent_company_id <> a.company_id
)
SELECT
    c.lei,
    c.name                                               AS company_name,
    e.relation,
    e.source,
    fp.fiscal_year,
    e.parent_name_raw                                    AS declared_text,
    e.parent_name,
    e.parent_lei,
    p.name                                               AS parent_company_name,
    (p.company_id IS NOT NULL AND p.company_id <> c.company_id) AS parent_in_dataset,
    e.resolution,
    e.detail,
    (e.resolution = 'self' OR cy.x IS NOT NULL)          AS contradictory,
    CASE
        WHEN e.resolution = 'self' THEN 'self_reference'
        WHEN cy.x IS NOT NULL THEN 'cycle'
    END                                                  AS contradiction
FROM edges e
JOIN company c ON c.company_id = e.company_id
LEFT JOIN company p ON p.company_id = e.parent_company_id
LEFT JOIN filing fi ON fi.filing_id = e.filing_id
LEFT JOIN fiscal_period fp ON fp.fiscal_period_id = fi.fiscal_period_id
LEFT JOIN cycles cy
       ON cy.x = LEAST(e.company_id, e.parent_company_id)
      AND cy.y = GREATEST(e.company_id, e.parent_company_id);

COMMENT ON VIEW v_ownership IS
    'Parent and ultimate parent per company: latest ESEF statement and GLEIF Level 2. '
    'contradictory = the company names itself as its parent, or two companies declare each '
    'other as parent. Report these as declared; never pick a direction.';

-- Company search text: the legal name plus what the company declares about itself in the
-- parent tags (often the brand: "Industria de Diseño Textil, S.A. (Inditex)"), so that a user
-- can find a company by the name people actually use. Columns of 006 unchanged, one appended.
CREATE OR REPLACE VIEW v_company AS
SELECT
    c.lei,
    c.name,
    c.country,
    c.is_financial,
    min(fp.fiscal_year) AS first_fiscal_year,
    max(fp.fiscal_year) AS last_fiscal_year,
    count(DISTINCT fi.filing_id) AS filings,
    c.name || coalesce(' | ' || (
        SELECT string_agg(DISTINCT regexp_replace(o.parent_name_raw, '<[^>]+>', '', 'g'), ' | ')
        FROM ownership o
        WHERE o.company_id = c.company_id AND o.source = 'esef' AND o.resolution = 'self'
    ), '') AS search_text
FROM company c
LEFT JOIN fiscal_period fp ON fp.company_id = c.company_id
LEFT JOIN filing fi ON fi.company_id = c.company_id
GROUP BY c.company_id, c.lei, c.name, c.country, c.is_financial;
