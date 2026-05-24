"""
L3 LLM Response Cache — SQLite-backed cache of Qwen agent responses.

In a backtest sweep, the same (agent, prompt) often repeats across vote
runs and across symbols-on-the-same-day. Re-running the LLM is the single
biggest cost in a multi-day backtest. This cache memoizes structured
Pydantic outputs by hashing (model, temperature, prompt, schema_name).

Only active in backtest mode (run_context.is_backtest_mode() is True).
Live runs never hit the cache — they must call the model fresh.

Storage: backtest/cache/llm_responses.sqlite
Schema:
    key TEXT PRIMARY KEY  -- sha256 of (model, temp, prompt, schema)
    model TEXT
    schema TEXT           -- Pydantic class name
    response_json TEXT    -- pydantic .model_dump_json()
    created_at TEXT
"""

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

CACHE_DIR = Path(__file__).resolve().parent / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = CACHE_DIR / "llm_responses.sqlite"


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_cache (
            key TEXT PRIMARY KEY,
            model TEXT,
            schema TEXT,
            response_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


_init_db()


def _make_key(model: str, temperature: float, prompt: str, schema_name: str) -> str:
    h = hashlib.sha256()
    h.update(f"{model}|{temperature}|{schema_name}|".encode())
    h.update(prompt.encode())
    return h.hexdigest()


def get_cached(model: str, temperature: float, prompt: str, schema_name: str) -> Optional[dict]:
    """Return the cached response dict, or None on miss."""
    key = _make_key(model, temperature, prompt, schema_name)
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT response_json FROM llm_cache WHERE key = ?", (key,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def put_cached(model: str, temperature: float, prompt: str, schema_name: str,
               response_dict: dict):
    """Persist a structured response (already serialized to dict)."""
    key = _make_key(model, temperature, prompt, schema_name)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO llm_cache (key, model, schema, response_json) "
            "VALUES (?, ?, ?, ?)",
            (key, model, schema_name, json.dumps(response_dict)),
        )
        conn.commit()
    finally:
        conn.close()


class CachingStructuredLLM:
    """Wraps a structured-output LLM (the Runnable returned by
    .with_structured_output(schema)) with a backtest-only cache layer.

    On .invoke(prompt):
      - In backtest mode, hash the (model, temperature, prompt, schema) and
        check SQLite. On hit, reconstruct the Pydantic instance from the cached
        dict. On miss, call the underlying LLM and persist.
      - In live mode, pass through to the underlying LLM.
    """

    def __init__(self, llm, schema, model: str, temperature: float):
        self._llm = llm
        self._schema = schema
        self._model = model
        self._temperature = temperature
        self._schema_name = schema.__name__ if schema else "raw"

    def _is_backtest(self) -> bool:
        try:
            from backtest.run_context import is_backtest_mode
            return is_backtest_mode()
        except Exception:
            return False

    def invoke(self, prompt, *args, **kwargs) -> Any:
        # Normalize prompt to a string for hashing — LangChain accepts str or
        # message list. For caching we stringify.
        prompt_str = prompt if isinstance(prompt, str) else json.dumps(
            prompt, default=str, sort_keys=True
        )

        if not self._is_backtest():
            return self._llm.invoke(prompt, *args, **kwargs)

        cached = get_cached(self._model, self._temperature, prompt_str, self._schema_name)
        if cached is not None and self._schema is not None:
            try:
                return self._schema(**cached)
            except Exception:
                pass  # cache is stale/incompatible — fall through and re-call

        result = self._llm.invoke(prompt, *args, **kwargs)

        # Serialize for cache
        try:
            if hasattr(result, "model_dump"):
                put_cached(self._model, self._temperature, prompt_str,
                           self._schema_name, result.model_dump())
        except Exception:
            pass  # cache failures must never break the pipeline

        return result

    # Pass through any other attributes (the wrapped object may have other
    # methods like .with_config, .stream, etc).
    def __getattr__(self, name):
        return getattr(self._llm, name)
