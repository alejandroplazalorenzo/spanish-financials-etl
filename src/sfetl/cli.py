"""Command line: ``sfetl migrate | run | ask | eval``."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from sfetl.config import PROJECT_ROOT, REPORTS_DIR, load_dotenv


def _cmd_migrate(_: argparse.Namespace) -> int:
    from sfetl.db import connect_owner
    from sfetl.migrate import apply_migrations, set_reader_password

    with connect_owner() as conn:
        applied = apply_migrations(conn)
        print(f"applied: {', '.join(applied) if applied else 'nothing (up to date)'}")
        password = os.environ.get("SFETL_READER_PASSWORD", "")
        if password:
            set_reader_password(conn, password)
            print("read-only role sfetl_reader: LOGIN enabled")
        else:
            print("SFETL_READER_PASSWORD not set: sfetl_reader stays NOLOGIN")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from sfetl.pipeline import run

    summary = run(
        fiscal_years=args.years,
        max_companies=args.max_companies,
        refresh_index=args.refresh_index,
        load_db=not args.no_load,
        delay_s=args.delay,
    )
    print(json.dumps(summary.to_json(), indent=2))
    return 0


def _print_table(columns: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    cells = [[("" if v is None else str(v)) for v in row] for row in rows]
    widths = [max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(columns)]
    print(" | ".join(c.ljust(w) for c, w in zip(columns, widths, strict=True)))
    print("-+-".join("-" * w for w in widths))
    for r in cells:
        print(" | ".join(v.ljust(w) for v, w in zip(r, widths, strict=True)))


def _cmd_ask(args: argparse.Namespace) -> int:
    from sfetl.ask.runner import ask

    res = ask(args.question)
    print(f"-- SQL ({res.llm_seconds:.1f}s)\n{res.sql_executed or res.sql_generated}\n")
    if res.error:
        print(f"refused/failed ({res.error_kind}): {res.error}", file=sys.stderr)
        return 1
    assert res.result is not None
    _print_table(res.result.columns, res.result.rows)
    print(f"\n({len(res.result.rows)} rows)")
    return 0


def _cmd_eval(args: argparse.Namespace) -> int:
    from sfetl.ask.evaluate import load_questions, run_eval, write_eval

    summary = run_eval(load_questions(Path(args.questions)), prompt_version=args.prompt)
    write_eval(summary, Path(args.out))
    print(
        f"execution accuracy: {summary['match']}/{summary['questions']} "
        f"({summary['accuracy']:.0%}); strict {summary['strict']}/{summary['questions']}"
    )
    print(f"failures: {summary['failure_categories']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sfetl", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("migrate", help="apply db/migrations and enable the read-only login")
    p.set_defaults(func=_cmd_migrate)

    p = sub.add_parser("run", help="extract, transform, validate and load")
    p.add_argument("--years", type=int, nargs="+", help="fiscal years (default: latest two)")
    p.add_argument("--max-companies", type=int, default=None)
    p.add_argument("--refresh-index", action="store_true", help="re-download the filings index")
    p.add_argument("--no-load", action="store_true", help="stop after validation")
    p.add_argument("--delay", type=float, default=1.0, help="seconds between downloads")
    p.set_defaults(func=_cmd_run)

    p = sub.add_parser("ask", help="question in English or Spanish -> SQL -> result")
    p.add_argument("question")
    p.set_defaults(func=_cmd_ask)

    p = sub.add_parser("eval", help="execution accuracy of `ask` on eval/questions.yaml")
    p.add_argument("--questions", default=str(PROJECT_ROOT / "eval" / "questions.yaml"))
    p.add_argument("--out", default=str(REPORTS_DIR / "eval_results.json"))
    p.add_argument("--prompt", default="v2", choices=["v1", "v2"], help="prompt version")
    p.set_defaults(func=_cmd_eval)
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
