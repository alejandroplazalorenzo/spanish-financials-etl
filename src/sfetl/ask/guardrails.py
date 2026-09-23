"""Guardrails for LLM-written SQL.

The model's text is parsed into an AST, checked, given a LIMIT, and only the SQL regenerated
from that checked AST is executed ("validate what you run"). This is one layer; the others are
the read-only role, a read-only transaction and a statement timeout (see ``runner.py``).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# Objects the ask module may read. Anything else (catalogs, schema_migrations...) is refused.
ALLOWED_RELATIONS = frozenset(
    {
        "company",
        "filing",
        "metric",
        "financial_fact",
        "validation_issue",
        "company_year",
        "company_year_ratios",
        "year_aggregate_ratios",
    }
)

# Functions with side effects or that read the server (the read-only role blocks most of this
# anyway; refusing it here gives a clear error instead of a timeout or a permission failure).
FORBIDDEN_FUNCTION_PREFIXES = (
    "pg_",
    "lo_",
    "dblink",
    "set_config",
    "current_setting",
    "query_to_xml",
    "table_to_xml",
    "txid_",
)

WRITE_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,
    exp.Copy,
    exp.Grant,
    exp.Lock,
    exp.Into,
    exp.Set,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
)


class UnsafeSQLError(ValueError):
    """The SQL is not a single, read-only SELECT over the allowed relations."""


def parse_single_select(text: str) -> exp.Query:
    cleaned = text.strip().rstrip(";").strip()
    if not cleaned:
        raise UnsafeSQLError("empty SQL")
    try:
        statements = [s for s in sqlglot.parse(cleaned, read="postgres") if s is not None]
    except ParseError as err:
        raise UnsafeSQLError(
            f"could not parse SQL: {err.errors[0]['description'] if err.errors else err}"
        ) from err
    if len(statements) != 1:
        raise UnsafeSQLError(f"expected exactly one statement, got {len(statements)}")
    statement = statements[0]
    if not isinstance(statement, exp.Select | exp.SetOperation):
        raise UnsafeSQLError(f"only SELECT is allowed, got {statement.key.upper()}")
    return statement


def check_query(query: exp.Query) -> None:
    for node in query.walk():
        if isinstance(node, WRITE_NODES):
            raise UnsafeSQLError(f"forbidden clause: {node.key.upper()}")
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name.startswith(FORBIDDEN_FUNCTION_PREFIXES):
                raise UnsafeSQLError(f"forbidden function: {name}")
    cte_names = {cte.alias_or_name.lower() for cte in query.find_all(exp.CTE)}
    for table in query.find_all(exp.Table):
        if not table.name:
            continue  # table-valued function or subquery alias; functions are checked above
        schema = (table.db or "").lower()
        name = table.name.lower()
        if schema and schema != "public":
            raise UnsafeSQLError(f"relation outside the public schema: {schema}.{name}")
        if name not in ALLOWED_RELATIONS and name not in cte_names:
            raise UnsafeSQLError(f"relation not allowed: {name}")


def enforce_limit(query: exp.Query, max_rows: int) -> exp.Query:
    limit = query.args.get("limit")
    if isinstance(limit, exp.Limit):
        value = limit.expression
        if isinstance(value, exp.Literal) and value.is_int and int(value.this) <= max_rows:
            return query
    return query.limit(max_rows)


def guard_sql(text: str, max_rows: int = 200) -> str:
    """Return the SQL that is safe to run, or raise UnsafeSQLError."""
    query = parse_single_select(text)
    check_query(query)
    return enforce_limit(query, max_rows).sql(dialect="postgres")
