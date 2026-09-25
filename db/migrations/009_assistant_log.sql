-- 009: the assistant's own schema: query log, pending A/B choices, pagination.
--
-- Why. The assistant is stateless between messages (a CLI call, or a webhook), so what must
-- survive between two messages lives in PostgreSQL, in its own schema, never mixed with the
-- data:
--   * assistant.query_log: one row per answered message. How it was resolved (mode), the
--     intent and parameters, the SQL the model wrote in free-SQL mode (reviewed by hand to
--     promote frequent questions to fixed intents), latencies, and the user's rating. It feeds
--     the "uncovered questions" report and /stats.
--   * assistant.pending: when the model hesitates between two intents, the two options wait
--     here until the user picks A or B.
--   * assistant.page: remaining pages of a long answer ("more" button of the Telegram adapter).
--
-- Grants are the minimum each code path needs, column by column where it matters: the
-- assistant may set a rating on a logged question, but can never rewrite the question, the
-- mode or the generated SQL. In production a missing column grant of this kind broke a button;
-- the smoke test now performs each of these writes with the real role.

CREATE SCHEMA IF NOT EXISTS assistant;
COMMENT ON SCHEMA assistant IS 'State of the assistant itself (log, choices, pages). No business data.';

CREATE TABLE assistant.query_log (
    query_id         bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id       text        NOT NULL,   -- CLI session or Telegram chat id
    user_id          text,
    question         text        NOT NULL,
    mode             text        NOT NULL
                     CHECK (mode IN ('intent', 'free_sql', 'ambiguous', 'unanswered', 'error')),
    intent_id        text,
    params           jsonb,
    generated_sql    text,       -- free_sql only: candidates to become fixed intents
    row_count        integer,
    llm_ms           integer,
    query_ms         integer,
    error            text,
    rating           smallint    CHECK (rating IN (-1, 1)),   -- NULL = not rated
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX query_log_session_idx ON assistant.query_log (session_id, created_at DESC);
CREATE INDEX query_log_created_idx ON assistant.query_log (created_at DESC);

COMMENT ON COLUMN assistant.query_log.mode IS
    'intent = fixed query of the catalogue. free_sql = SELECT written by the model when no '
    'intent fits (answer flagged as not verified). ambiguous = two intents, the user was asked '
    'A/B. unanswered = nothing fitted, a parameter was missing or the free SQL was refused. '
    'error = exception.';

CREATE TABLE assistant.pending (
    pending_id   bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id   text        NOT NULL,
    question     text        NOT NULL,
    options      jsonb       NOT NULL,   -- [{"intent_id": ..., "params": {...}}, {...}]
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX pending_session_idx ON assistant.pending (session_id, created_at DESC);

CREATE TABLE assistant.page (
    page_id      bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id   text        NOT NULL,
    pages        text[]      NOT NULL,   -- pages NOT yet sent, in order
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX page_created_idx ON assistant.page (created_at);

GRANT USAGE ON SCHEMA assistant TO sfetl_assistant;
GRANT SELECT, INSERT ON assistant.query_log TO sfetl_assistant;
GRANT UPDATE (rating) ON assistant.query_log TO sfetl_assistant;
GRANT SELECT, INSERT, DELETE ON assistant.pending TO sfetl_assistant;
GRANT SELECT, INSERT, DELETE ON assistant.page TO sfetl_assistant;
GRANT UPDATE (pages) ON assistant.page TO sfetl_assistant;

-- Analysts can read the log (for the uncovered-questions report), not change it.
GRANT USAGE ON SCHEMA assistant TO sfetl_reader;
GRANT SELECT ON assistant.query_log TO sfetl_reader;
