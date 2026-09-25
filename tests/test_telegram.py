"""The optional Telegram adapter, offline: fake Telegram API, fake assistant, in-memory pages."""

from __future__ import annotations

import json
from typing import Any

import pytest

from sfetl.ask.render import CSV_MIN_ROWS, Answer
from sfetl.ask.service import FREE_SQL_NOTE
from sfetl.telegram import TelegramBot

SECRET = "s3cret"
HEADERS = {"X-Telegram-Bot-Api-Secret-Token": SECRET}


class FakeApi:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, Any]] = []
        self.callbacks: list[tuple[str, str | None]] = []
        self.documents: list[tuple[int, str, str]] = []

    def send_message(self, chat_id: int, text: str, buttons: Any = None) -> None:
        self.sent.append((chat_id, text, buttons))

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        self.callbacks.append((callback_id, text))

    def send_document(self, chat_id: int, filename: str, content: str, caption: str) -> None:
        self.documents.append((chat_id, filename, content))


class FakeAssistant:
    def __init__(self, answer: Answer) -> None:
        self.answer = answer
        self.asked: list[tuple[str, str, str | None]] = []
        self.rated: list[tuple[int, int]] = []
        self.chosen: list[tuple[int, int]] = []

    def ask(self, question: str, session_id: str, user_id: str | None = None) -> Answer:
        self.asked.append((question, session_id, user_id))
        return self.answer

    def rate(self, query_id: int, rating: int) -> bool:
        self.rated.append((query_id, rating))
        return True

    def choose(self, pending_id: int, choice: int, session_id: str, user_id: str | None = None):
        self.chosen.append((pending_id, choice))
        return self.answer

    def stats(self) -> dict[str, Any]:
        return {
            "questions": 3,
            "by_mode": {"intent": 2, "free_sql": 1},
            "rated_up": 1,
            "rated_down": 0,
            "avg_llm_ms": 900,
            "avg_query_ms": 12,
            "top_uncovered": [("how many employees?", 2)],
        }


class MemoryPages:
    def __init__(self) -> None:
        self.store: dict[int, tuple[str, list[str]]] = {}

    def save(self, session_id: str, pages: list[str]) -> int:
        page_id = len(self.store) + 1
        self.store[page_id] = (session_id, list(pages))
        return page_id

    def pop(self, page_id: int, session_id: str) -> tuple[str, bool] | None:
        owner, pages = self.store.get(page_id, ("", []))
        if owner != session_id or not pages:
            return None
        first = pages.pop(0)
        return first, bool(pages)


def make(answer: Answer) -> tuple[TelegramBot, FakeApi, FakeAssistant]:
    api, assistant = FakeApi(), FakeAssistant(answer)
    bot = TelegramBot(assistant, api, MemoryPages(), SECRET, frozenset({"7"}), frozenset({"7"}))  # type: ignore[arg-type]
    return bot, api, assistant


def message(text: str, user: int = 7, chat: int = 70) -> bytes:
    return json.dumps(
        {"message": {"chat": {"id": chat}, "from": {"id": user}, "text": text}}
    ).encode()


def callback(data: str, user: int = 7, chat: int = 70) -> bytes:
    body = {
        "callback_query": {
            "id": "cb",
            "data": data,
            "from": {"id": user},
            "message": {"chat": {"id": chat}},
        }
    }
    return json.dumps(body).encode()


SHORT = Answer(
    text="Revenue of Endesa\n\nValue: 1.0 M EUR",
    mode="intent",
    query_id=11,
    columns=["Value"],
    rows=[(1,)],
)


def test_requests_without_the_secret_are_refused() -> None:
    bot, api, assistant = make(SHORT)
    assert bot.handle({"X-Telegram-Bot-Api-Secret-Token": "wrong"}, message("hi")) == 403
    assert bot.handle({}, message("hi")) == 403
    assert not api.sent and not assistant.asked


def test_users_outside_the_allow_list_get_nothing() -> None:
    bot, api, assistant = make(SHORT)
    assert bot.handle(HEADERS, message("revenue of Endesa", user=8)) == 200
    assert not assistant.asked and "do not have access" in api.sent[0][1]
    bot.handle(HEADERS, callback("r:11:1", user=8))
    assert not assistant.rated


def test_an_answer_comes_with_rating_buttons_and_session_is_the_chat() -> None:
    bot, api, assistant = make(SHORT)
    bot.handle(HEADERS, message("revenue of Endesa"))
    assert assistant.asked == [("revenue of Endesa", "70", "7")]
    _, text, buttons = api.sent[0]
    assert text.startswith("Revenue of Endesa")
    assert buttons == [[("Useful", "r:11:1"), ("Not useful", "r:11:-1")]]
    bot.handle(HEADERS, callback("r:11:-1"))
    assert assistant.rated == [(11, -1)]


def test_long_answers_are_paged_with_a_more_button() -> None:
    long_text = "\n".join(f"line {i}" for i in range(2000))
    bot, api, _ = make(Answer(text=long_text, mode="intent", query_id=5, rows=[(1,)]))
    bot.handle(HEADERS, message("list everything"))
    first_buttons = api.sent[0][2]
    assert first_buttons[0] == [("More", "m:1")]
    pages = 1
    while True:
        bot.handle(HEADERS, callback("m:1"))
        _, _, buttons = api.sent[-1]
        pages += 1
        if not buttons:
            break
    assert pages > 2
    assert "\n".join(t for _, t, _ in api.sent) == long_text


def test_many_rows_are_sent_as_csv_and_free_sql_keeps_its_warning() -> None:
    rows = [(i,) for i in range(CSV_MIN_ROWS + 5)]
    answer = Answer(text="t", mode="free_sql", query_id=9, title="Counts", columns=["n"], rows=rows)
    bot, api, _ = make(answer)
    bot.handle(HEADERS, message("something unusual"))
    assert api.documents and api.documents[0][2].splitlines()[0] == "n"
    assert FREE_SQL_NOTE in api.sent[0][1]


def test_ambiguous_answers_offer_two_buttons_that_resolve() -> None:
    answer = Answer(
        text="I am not sure...",
        mode="ambiguous",
        query_id=3,
        pending_id=42,
        choices=["Revenue of X", "Key figures of X"],
    )
    bot, api, assistant = make(answer)
    bot.handle(HEADERS, message("X?"))
    buttons = api.sent[0][2]
    assert buttons[0][0][1] == "c:42:0" and buttons[1][0][1] == "c:42:1"
    bot.handle(HEADERS, callback("c:42:1"))
    assert assistant.chosen == [(42, 1)]


@pytest.mark.parametrize(("user", "allowed"), [(7, True), (9, False)])
def test_stats_is_admin_only(user: int, allowed: bool) -> None:
    api, assistant = FakeApi(), FakeAssistant(SHORT)
    bot = TelegramBot(
        assistant,
        api,
        MemoryPages(),
        SECRET,
        frozenset({"7", "9"}),  # type: ignore[arg-type]
        frozenset({"7"}),
    )
    bot.handle(HEADERS, message("/stats", user=user))
    text = api.sent[0][1]
    assert ("Questions: 3" in text) is allowed
    assert ("how many employees?" in text) is allowed


def test_a_secret_is_mandatory() -> None:
    with pytest.raises(ValueError):
        TelegramBot(FakeAssistant(SHORT), FakeApi(), MemoryPages(), "", frozenset(), frozenset())  # type: ignore[arg-type]
