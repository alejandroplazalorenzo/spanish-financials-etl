"""Optional Telegram adapter for the assistant (webhook).

The production assistant this project rebuilds was used from Telegram; this adapter keeps the
parts of that front end that are about safety and usability, around the same ``Assistant``:

* the webhook refuses any request without Telegram's secret header (``X-Telegram-Bot-Api-
  Secret-Token``), so nobody else can invoke it;
* an allow-list of Telegram user ids, checked on messages AND on button presses;
* admin-only ``/stats`` (questions by mode, ratings, latencies, most repeated uncovered
  questions);
* answers longer than one message are paged with a "More" button (remaining pages live in
  ``assistant.page``); results with many rows are sent as a CSV file;
* A/B buttons for ambiguous questions, and "Useful / Not useful" buttons that set the rating.

It always answers HTTP 200 to Telegram (otherwise Telegram retries the same update forever),
except 403 for a wrong secret. Tested offline with a fake Telegram API; it has not been run
against Telegram from this repository.

Environment: TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET, TELEGRAM_ALLOWED_IDS,
TELEGRAM_ADMIN_IDS (comma-separated ids; empty admin list = nobody, not everybody).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol

import psycopg
import requests

from sfetl.ask.render import Answer
from sfetl.ask.service import FREE_SQL_NOTE, Assistant
from sfetl.config import env_list, load_dotenv
from sfetl.db import connect_assistant

log = logging.getLogger(__name__)

HELP = """\
Ask about the annual accounts of Spanish listed companies (ESEF reports, FY2023-FY2024), in
English or Spanish. For example:
- What was Iberdrola's revenue in 2024?
- Top 10 companies by total assets
- Who is the parent of Prosegur Cash?
- Which companies made a loss in 2024?
- Data-quality flags of Amper
Follow-ups work ("and Repsol?"). If I hesitate between two readings I ask A or B. If no prepared
query fits I may draft one on the fly: that answer is always marked as not verified."""

Buttons = list[list[tuple[str, str]]]


class TelegramApi:
    def __init__(self, token: str, session: requests.Session | None = None) -> None:
        self.base = f"https://api.telegram.org/bot{token}"
        self.session = session or requests.Session()

    def _post(self, method: str, **kwargs: Any) -> None:  # pragma: no cover - network
        response = self.session.post(f"{self.base}/{method}", timeout=30, **kwargs)
        if not response.ok:
            log.error("Telegram %s failed: %s", method, response.text[:300])

    def send_message(self, chat_id: int, text: str, buttons: Buttons | None = None) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": label, "callback_data": data} for label, data in row]
                    for row in buttons
                ]
            }
        self._post("sendMessage", json=payload)

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        self._post("answerCallbackQuery", json={"callback_query_id": callback_id, "text": text})

    def send_document(self, chat_id: int, filename: str, content: str, caption: str) -> None:
        self._post(
            "sendDocument",
            data={"chat_id": chat_id, "caption": caption[:1000]},
            files={"document": (filename, content.encode("utf-8"), "text/csv")},
        )


class PageStore(Protocol):
    def save(self, session_id: str, pages: list[str]) -> int: ...

    def pop(self, page_id: int, session_id: str) -> tuple[str, bool] | None: ...


class DbPageStore:
    """Remaining pages in ``assistant.page`` (assistant role: INSERT, UPDATE(pages), DELETE)."""

    def __init__(self, connect: Callable[[], psycopg.Connection] = connect_assistant) -> None:
        self.connect = connect

    def save(self, session_id: str, pages: list[str]) -> int:
        with self.connect() as conn:
            conn.execute("DELETE FROM assistant.page WHERE created_at < now() - interval '1 day'")
            row = conn.execute(
                "INSERT INTO assistant.page (session_id, pages) VALUES (%s, %s) RETURNING page_id",
                (session_id, pages),
            ).fetchone()
            return int(row[0])  # type: ignore[index]

    def pop(self, page_id: int, session_id: str) -> tuple[str, bool] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT pages FROM assistant.page WHERE page_id = %s AND session_id = %s",
                (page_id, session_id),
            ).fetchone()
            if row is None or not row[0]:
                return None
            first, *rest = row[0]
            if rest:
                conn.execute(
                    "UPDATE assistant.page SET pages = %s WHERE page_id = %s", (rest, page_id)
                )
            else:
                conn.execute("DELETE FROM assistant.page WHERE page_id = %s", (page_id,))
            return first, bool(rest)


class TelegramBot:
    def __init__(
        self,
        assistant: Assistant,
        api: TelegramApi,
        pages: PageStore,
        secret: str,
        allowed_ids: frozenset[str],
        admin_ids: frozenset[str],
    ) -> None:
        if not secret:
            raise ValueError("TELEGRAM_WEBHOOK_SECRET must be set")
        self.assistant = assistant
        self.api = api
        self.pages = pages
        self.secret = secret
        self.allowed = allowed_ids
        self.admins = admin_ids

    # ---- webhook -------------------------------------------------------------------------

    def handle(self, headers: Mapping[str, str], body: bytes) -> int:
        received = {k.lower(): v for k, v in headers.items()}
        if received.get("x-telegram-bot-api-secret-token") != self.secret:
            return 403
        try:
            update = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return 200
        try:
            if "callback_query" in update:
                self._callback(update["callback_query"])
            elif "message" in update:
                self._message(update["message"])
        except Exception:
            log.exception("update failed")
        return 200

    def _message(self, msg: dict[str, Any]) -> None:
        chat_id = msg.get("chat", {}).get("id")
        user_id = str(msg.get("from", {}).get("id", ""))
        text = (msg.get("text") or "").strip()
        if chat_id is None or not text:
            return
        if user_id not in self.allowed:
            self.api.send_message(chat_id, "You do not have access to this assistant.")
            return
        command = text.split()[0].split("@")[0].lower()
        if command in ("/start", "/help"):
            self.api.send_message(chat_id, HELP)
            return
        if command == "/stats":
            if user_id not in self.admins:
                self.api.send_message(chat_id, "That command is not available to you.")
                return
            self.api.send_message(chat_id, format_stats(self.assistant.stats()))
            return
        self.send_answer(chat_id, self.assistant.ask(text, str(chat_id), user_id))

    def _callback(self, cb: dict[str, Any]) -> None:
        user_id = str(cb.get("from", {}).get("id", ""))
        chat_id = cb.get("message", {}).get("chat", {}).get("id")
        if user_id not in self.allowed:
            self.api.answer_callback(cb["id"], "You do not have access.")
            return
        kind, _, rest = str(cb.get("data", "")).partition(":")
        if kind == "r":
            query_id, _, value = rest.partition(":")
            self.assistant.rate(int(query_id), int(value))
            self.api.answer_callback(cb["id"], "Thanks for the feedback.")
            return
        if chat_id is None:
            self.api.answer_callback(cb["id"], "That message is too old; ask again.")
            return
        if kind == "m":
            popped = self.pages.pop(int(rest), str(chat_id))
            if popped is None:
                self.api.answer_callback(cb["id"], "That answer has expired; ask again.")
                return
            page, more = popped
            self.api.answer_callback(cb["id"])
            self.api.send_message(chat_id, page, [[("More", f"m:{rest}")]] if more else None)
            return
        if kind == "c":
            pending_id, _, choice = rest.partition(":")
            answer = self.assistant.choose(int(pending_id), int(choice), str(chat_id), user_id)
            self.api.answer_callback(cb["id"])
            if answer is None:
                self.api.send_message(chat_id, "That choice has expired; ask again.")
            else:
                self.send_answer(chat_id, answer)
            return
        self.api.answer_callback(cb["id"])

    # ---- answers -------------------------------------------------------------------------

    def send_answer(self, chat_id: int, answer: Answer) -> None:
        buttons: Buttons = []
        if answer.pending_id is not None:
            for n, title in enumerate(answer.choices[:2]):
                label = f"{'AB'[n]}) {title}"[:60]
                buttons.append([(label, f"c:{answer.pending_id}:{n}")])
        rating: list[tuple[str, str]] = []
        if answer.query_id is not None and answer.mode in ("intent", "free_sql") and answer.rows:
            rating = [
                ("Useful", f"r:{answer.query_id}:1"),
                ("Not useful", f"r:{answer.query_id}:-1"),
            ]
        csv = answer.csv
        if csv is not None:
            header = answer.title or "Answer"
            if answer.mode == "free_sql":
                header += f"\n\n{FREE_SQL_NOTE}"
            header += f"\n\n{len(answer.rows)} rows: sent as a CSV file."
            self.api.send_message(chat_id, header, [rating] if rating else None)
            self.api.send_document(chat_id, f"{answer.intent_id or 'answer'}.csv", csv, header)
            return
        first, *rest = answer.pages()
        if rest:
            page_id = self.pages.save(str(chat_id), rest)
            buttons.append([("More", f"m:{page_id}")])
        if rating:
            buttons.append(rating)
        self.api.send_message(chat_id, first, buttons or None)


def format_stats(stats: Mapping[str, Any]) -> str:
    lines = [f"Questions: {stats['questions']}"]
    lines += [f"  {mode}: {n}" for mode, n in stats["by_mode"].items()]
    lines.append(f"Rated useful: {stats['rated_up']} / not useful: {stats['rated_down']}")
    if stats.get("avg_llm_ms") is not None:
        lines.append(
            f"Average latency: model {stats['avg_llm_ms']} ms, query {stats['avg_query_ms']} ms"
        )
    if stats["top_uncovered"]:
        lines.append("Most repeated uncovered questions:")
        lines += [f'  "{q[:60]}" ({n})' for q, n in stats["top_uncovered"]]
    return "\n".join(lines)


def bot_from_env(assistant: Assistant | None = None) -> TelegramBot:  # pragma: no cover
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN must be set")
    return TelegramBot(
        assistant=assistant or Assistant(),
        api=TelegramApi(token),
        pages=DbPageStore(),
        secret=os.environ.get("TELEGRAM_WEBHOOK_SECRET", ""),
        allowed_ids=env_list("TELEGRAM_ALLOWED_IDS"),
        admin_ids=env_list("TELEGRAM_ADMIN_IDS"),
    )


def serve(bot: TelegramBot, host: str = "127.0.0.1", port: int = 8080) -> None:  # pragma: no cover
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or 0)
            status = bot.handle(dict(self.headers.items()), self.rfile.read(length))
            self.send_response(status)
            self.end_headers()
            self.wfile.write(b"ok" if status == 200 else b"forbidden")

        def log_message(self, fmt: str, *args: Any) -> None:
            log.info("webhook %s", fmt % args)

    server = ThreadingHTTPServer((host, port), Handler)
    log.info("Telegram webhook listening on http://%s:%d", host, port)
    server.serve_forever()
