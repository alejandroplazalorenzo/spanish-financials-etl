"""Command line.

ETL:        sfetl migrate | run
Assistant:  sfetl ask | rate | stats | report uncovered | smoke | bench | eval-fallback
Telegram:   sfetl telegram
Operations: sfetl backup | restore
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from sfetl.config import PROJECT_ROOT, REPORTS_DIR, load_dotenv


def _cmd_migrate(_: argparse.Namespace) -> int:
    from sfetl.db import connect_owner
    from sfetl.migrate import apply_migrations, set_assistant_password, set_reader_password

    with connect_owner() as conn:
        applied = apply_migrations(conn)
        print(f"applied: {', '.join(applied) if applied else 'nothing (up to date)'}")
        for env, setter, role in (
            ("SFETL_READER_PASSWORD", set_reader_password, "sfetl_reader"),
            ("SFETL_ASSISTANT_PASSWORD", set_assistant_password, "sfetl_assistant"),
        ):
            password = os.environ.get(env, "")
            if password:
                setter(conn, password)
                print(f"role {role}: LOGIN enabled")
            else:
                print(f"{env} not set: {role} stays NOLOGIN")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from sfetl.pipeline import run

    summary = run(
        fiscal_years=args.years,
        max_companies=args.max_companies,
        refresh_index=args.refresh_index,
        load_db=not args.no_load,
        delay_s=args.delay,
        use_gleif=not args.no_gleif,
        gleif_offline=args.gleif_offline,
    )
    print(json.dumps(summary.to_json(), indent=2, default=str))
    if summary.exit_code:
        print("RED: see reports/validation_report.md (failed filings / checks)", file=sys.stderr)
    return summary.exit_code


def _cmd_ask(args: argparse.Namespace) -> int:
    from sfetl.ask.service import Assistant

    answer = Assistant().ask(
        args.question,
        session_id=args.session,
        user_id=os.environ.get("USERNAME", os.environ.get("USER")),
    )
    print(answer.text)
    print(f"\n[mode: {answer.mode}; query_id: {answer.query_id}]")
    if args.csv and answer.rows:
        from sfetl.ask.render import to_csv

        Path(args.csv).write_text(to_csv(answer.columns, answer.rows), encoding="utf-8")
        print(f"rows written to {args.csv}")
    return 0 if answer.mode in ("intent", "free_sql", "ambiguous") else 1


def _cmd_rate(args: argparse.Namespace) -> int:
    from sfetl.ask.service import Assistant

    ok = Assistant().rate(args.query_id, 1 if args.rating == "up" else -1)
    print("rated" if ok else "no such query")
    return 0 if ok else 1


def _cmd_stats(_: argparse.Namespace) -> int:
    from sfetl.ask.service import Assistant
    from sfetl.telegram import format_stats

    print(format_stats(Assistant().stats()))
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    from sfetl.db import connect_assistant

    out = Path(args.out)
    with connect_assistant() as conn:
        rows = conn.execute(
            """SELECT question, mode, count(*) AS times, max(created_at) AS last_asked,
                      (array_agg(generated_sql ORDER BY created_at DESC)
                           FILTER (WHERE generated_sql IS NOT NULL))[1] AS last_sql,
                      (array_agg(error ORDER BY created_at DESC)
                           FILTER (WHERE error IS NOT NULL))[1] AS last_error
               FROM assistant.query_log
               WHERE mode IN ('unanswered', 'free_sql')
               GROUP BY question, mode
               ORDER BY times DESC, last_asked DESC"""
        ).fetchall()
    lines = [
        "# Uncovered questions",
        "",
        f"Generated {datetime.now():%Y-%m-%d %H:%M} from `assistant.query_log`. Questions no "
        "intent answered: `free_sql` ones were answered by a generated query (candidates to "
        "become fixed intents once reviewed); `unanswered` ones got no answer.",
        "",
    ]
    for question, mode, times, last, sql, error in rows:
        lines.append(f"- **{question}** ({mode}, {times}x, last {last:%Y-%m-%d})")
        if sql:
            lines.append(f"  - generated SQL: `{' '.join(sql.split())}`")
        if error:
            lines.append(f"  - reason: {error}")
    if not rows:
        lines.append("- none logged yet")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(rows)} uncovered question(s) -> {out}")
    return 0


def _cmd_smoke(args: argparse.Namespace) -> int:
    from sfetl.ask.smoke import run_smoke

    results, sample = run_smoke()
    print(f"sample parameters: {sample}")
    for r in results:
        rows = "" if r.rows is None else f" ({r.rows} rows)"
        print(f"{'OK  ' if r.ok else 'FAIL'} {r.name}{rows}{' - ' + r.detail if r.detail else ''}")
    failed = [r for r in results if not r.ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} OK")
    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {
                    "run_at": datetime.now().isoformat(timespec="seconds"),
                    "sample": sample,
                    "results": [r.__dict__ for r in results],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return 1 if failed else 0


def _cmd_bench(args: argparse.Namespace) -> int:
    from sfetl.ask.bench import load_cases, run_bench
    from sfetl.ask.llm import provider_from_env

    summary = run_bench(provider_from_env(), load_cases(Path(args.cases)))
    Path(args.out).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"routing: {summary['passed']}/{summary['cases']} {summary['by_expect']}")
    return 0


def _cmd_eval_fallback(args: argparse.Namespace) -> int:
    from sfetl.ask.evaluate import load_questions, run_fallback_eval
    from sfetl.ask.llm import provider_from_env

    summary = run_fallback_eval(provider_from_env(), load_questions(Path(args.questions)))
    Path(args.out).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print(
        f"fallback execution accuracy: {summary['match']}/{summary['questions']}; "
        f"strict {summary['strict']}; failures {summary['failure_categories']}"
    )
    return 0


def _cmd_telegram(args: argparse.Namespace) -> int:  # pragma: no cover - long-running server
    from sfetl.telegram import bot_from_env, serve

    serve(bot_from_env(), host=args.host, port=args.port)
    return 0


def _cmd_backup(args: argparse.Namespace) -> int:
    from sfetl.backup import backup

    out = Path(args.out or PROJECT_ROOT / "backups" / f"sfetl_{datetime.now():%Y%m%d_%H%M}.dump")
    path = backup(out, use_docker=args.docker)
    print(f"backup written: {path} ({path.stat().st_size:,} bytes)")
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    from sfetl.backup import restore, verify
    from sfetl.config import owner_db

    restored = restore(Path(args.dump), args.dbname, use_docker=args.docker)
    ok, detail = verify(owner_db(), restored)
    for table, (a, b) in detail.items():
        print(f"{'OK  ' if a == b else 'DIFF'} {table}: source {a}, restored {b}")
    print("restore verified" if ok else "restore DIFFERS from the source")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sfetl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("migrate", help="apply db/migrations and enable the role logins")
    p.set_defaults(func=_cmd_migrate)

    p = sub.add_parser("run", help="extract, transform, validate, load, resolve ownership, check")
    p.add_argument("--years", type=int, nargs="+", help="fiscal years (default: latest two)")
    p.add_argument("--max-companies", type=int, default=None)
    p.add_argument("--refresh-index", action="store_true", help="re-download the filings index")
    p.add_argument("--no-load", action="store_true", help="stop after validation")
    p.add_argument("--delay", type=float, default=1.0, help="seconds between downloads")
    p.add_argument("--no-gleif", action="store_true", help="skip GLEIF Level 2 relationships")
    p.add_argument("--gleif-offline", action="store_true", help="GLEIF from cache only")
    p.set_defaults(func=_cmd_run)

    p = sub.add_parser("ask", help="question (EN/ES) -> intent (or flagged free SQL) -> answer")
    p.add_argument("question")
    p.add_argument("--session", default="cli", help="conversation id (follow-ups, A/B choices)")
    p.add_argument("--csv", help="also write the rows to this CSV file")
    p.set_defaults(func=_cmd_ask)

    p = sub.add_parser("rate", help="rate a logged answer")
    p.add_argument("query_id", type=int)
    p.add_argument("rating", choices=["up", "down"])
    p.set_defaults(func=_cmd_rate)

    p = sub.add_parser("stats", help="usage statistics of the assistant")
    p.set_defaults(func=_cmd_stats)

    p = sub.add_parser("report", help="reports from the assistant log")
    p.add_argument("which", choices=["uncovered"])
    p.add_argument("--out", default=str(REPORTS_DIR / "uncovered_questions.md"))
    p.set_defaults(func=_cmd_report)

    p = sub.add_parser("smoke", help="run every intent and every assistant write as its role")
    p.add_argument("--out", default=None, help="write the results as JSON")
    p.set_defaults(func=_cmd_smoke)

    p = sub.add_parser("bench", help="routing benchmark of the classifier (needs the LLM)")
    p.add_argument("--cases", default=str(PROJECT_ROOT / "eval" / "routing_cases.yaml"))
    p.add_argument("--out", default=str(REPORTS_DIR / "routing_bench.json"))
    p.set_defaults(func=_cmd_bench)

    p = sub.add_parser("eval-fallback", help="execution accuracy of the free-SQL fallback")
    p.add_argument("--questions", default=str(PROJECT_ROOT / "eval" / "fallback_questions.yaml"))
    p.add_argument("--out", default=str(REPORTS_DIR / "fallback_eval.json"))
    p.set_defaults(func=_cmd_eval_fallback)

    p = sub.add_parser("telegram", help="serve the optional Telegram webhook")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(func=_cmd_telegram)

    p = sub.add_parser("backup", help="pg_dump the database (custom format)")
    p.add_argument("--out", default=None)
    p.add_argument("--docker", action="store_true", help="use the compose service's pg_dump")
    p.set_defaults(func=_cmd_backup)

    p = sub.add_parser("restore", help="restore a dump into a new database and verify counts")
    p.add_argument("dump")
    p.add_argument("--dbname", default="sfetl_restore_check")
    p.add_argument("--docker", action="store_true", help="use the compose service's pg_restore")
    p.set_defaults(func=_cmd_restore)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # company names contain accents
    return int(args.func(args))
