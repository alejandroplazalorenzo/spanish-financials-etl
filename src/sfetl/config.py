"""Runtime configuration read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
MIGRATIONS_DIR = PROJECT_ROOT / "db" / "migrations"
REPORTS_DIR = PROJECT_ROOT / "reports"

FILINGS_BASE_URL = "https://filings.xbrl.org"


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env reader: KEY=VALUE lines, no interpolation. Existing env vars win."""
    env_path = path or PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class DbSettings:
    host: str
    port: int
    dbname: str
    user: str
    password: str

    def connect_kwargs(self) -> dict[str, str | int]:
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.user,
            "password": self.password,
            "connect_timeout": 5,
        }


def owner_db() -> DbSettings:
    load_dotenv()
    return DbSettings(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "55432")),
        dbname=os.environ.get("POSTGRES_DB", "sfetl"),
        user=os.environ.get("POSTGRES_USER", "sfetl_owner"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def reader_db() -> DbSettings:
    load_dotenv()
    owner = owner_db()
    return DbSettings(
        host=owner.host,
        port=owner.port,
        dbname=owner.dbname,
        user="sfetl_reader",  # created by db/migrations/003_readonly_role.sql
        password=os.environ.get("SFETL_READER_PASSWORD", ""),
    )


def ollama_url() -> str:
    load_dotenv()
    return os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")


def ollama_model() -> str:
    load_dotenv()
    return os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct")


def user_agent() -> str:
    load_dotenv()
    return os.environ.get("SFETL_USER_AGENT", "spanish-financials-etl/0.1 (portfolio project)")
