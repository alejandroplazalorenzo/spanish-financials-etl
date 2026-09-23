"""Minimal Ollama chat client (local model, deterministic settings)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import requests

from sfetl.config import ollama_model, ollama_url

SQL_BLOCK_RE = re.compile(r"```(?:sql|postgresql|postgres)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class LlmReply:
    text: str
    model: str
    seconds: float


def chat(system: str, user: str, timeout_s: float = 600.0) -> LlmReply:
    """One non-streaming chat call with temperature 0 and a fixed seed."""
    model = ollama_model()
    response = requests.post(
        f"{ollama_url()}/api/chat",
        json={
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {"temperature": 0, "seed": 42, "num_ctx": 8192},
            # keep the model in memory between the questions of an evaluation run; Ollama
            # unloads it this long after the last call
            "keep_alive": "5m",
        },
        timeout=timeout_s,
    )
    response.raise_for_status()
    payload = response.json()
    seconds = float(payload.get("total_duration", 0)) / 1e9
    return LlmReply(text=payload["message"]["content"], model=model, seconds=seconds)


def extract_sql(text: str) -> str:
    """Take the first fenced SQL block, or the whole reply when there is no fence."""
    match = SQL_BLOCK_RE.search(text)
    return (match.group(1) if match else text).strip()
