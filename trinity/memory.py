from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from trinity.config import DATA_DIR

DB_PATH = DATA_DIR / "trinity.db"

NAME_RE = re.compile(r"\bmy name(?:'s| is)\s+([A-Za-z][\w'-]*(?:\s+[A-Za-z][\w'-]*){0,2})", re.I)
HOME_RE = re.compile(r"\bi live in\s+([A-Za-z][\w'-]*(?:[\s,]+[A-Za-z][\w'-]*){0,3})", re.I)
FAVORITE_RE = re.compile(r"\bmy favorite\s+([A-Za-z][A-Za-z ]{1,38}?)\s+is\s+(.{1,120})", re.I)
PREFERENCE_RE = re.compile(r"\bi prefer\s+(.{2,120})", re.I)

# Words that end a captured phrase, so "my name is Alex and I live in Boston"
# stores the name "Alex" rather than the rest of the sentence.
_PHRASE_BREAKS = {"and", "but", "so", "then", "i", "my", "we", "which", "who"}
_VAGUE_STARTS = {"that", "it", "this", "you", "your", "them", "these", "those"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class Memory:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        with self._db() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    body TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    fired INTEGER NOT NULL DEFAULT 0
                );
                """
            )

    def remember(self, key: str, value: str) -> str:
        key = _clean_key(key)
        value = value.strip()
        if not key or not value:
            return "Need both a key and a value to remember."
        ts = _now()
        with self._db() as conn:
            conn.execute(
                "INSERT INTO facts(key, value, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, ts),
            )
        return f"Remembered {key}: {value}"

    def forget(self, key: str) -> str:
        key = _clean_key(key)
        with self._db() as conn:
            cur = conn.execute("DELETE FROM facts WHERE key = ?", (key,))
            if cur.rowcount:
                return f"Forgot {key}."
            # fuzzy
            rows = conn.execute("SELECT key FROM facts").fetchall()
        match = _best_key(key, [r["key"] for r in rows])
        if match:
            with self._db() as conn:
                conn.execute("DELETE FROM facts WHERE key = ?", (match,))
            return f"Forgot {match}."
        return f"Nothing stored under {key}."

    def get_fact(self, key: str) -> str | None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT value FROM facts WHERE key = ?", (_clean_key(key),)
            ).fetchone()
        return row["value"] if row else None

    def append_to_fact(self, key: str, value: str) -> str:
        """Add to a list-style fact so repeat mentions accumulate instead of overwriting."""
        key = _clean_key(key)
        value = value.strip()
        if not key or not value:
            return "Need both a key and a value to remember."
        existing = self.get_fact(key)
        if not existing:
            return self.remember(key, value)
        parts = [part.strip() for part in existing.split(";") if part.strip()]
        if any(value.lower() == part.lower() for part in parts):
            return f"Already knew {key}: {value}"
        parts.append(value)
        return self.remember(key, "; ".join(parts))

    def recall(self, query: str, limit: int = 8) -> str:
        query = query.strip()
        with self._db() as conn:
            if not query:
                rows = conn.execute(
                    "SELECT key, value, updated_at FROM facts ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                like = f"%{query}%"
                rows = conn.execute(
                    "SELECT key, value, updated_at FROM facts "
                    "WHERE key LIKE ? OR value LIKE ? ORDER BY updated_at DESC LIMIT ?",
                    (like, like, limit),
                ).fetchall()
        if not rows:
            return "No matching memories."
        return "\n".join(f"- {r['key']}: {r['value']}" for r in rows)

    def list_facts(self, limit: int = 40) -> list[tuple[str, str]]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT key, value FROM facts ORDER BY key COLLATE NOCASE LIMIT ?",
                (limit,),
            ).fetchall()
        return [(r["key"], r["value"]) for r in rows]

    def add_note(self, body: str) -> str:
        body = body.strip()
        if not body:
            return "Note was empty."
        with self._db() as conn:
            conn.execute("INSERT INTO notes(body, created_at) VALUES(?,?)", (body, _now()))
        return "Note saved."

    def recent_notes(self, limit: int = 8) -> str:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT body, created_at FROM notes ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        if not rows:
            return "No notes yet."
        return "\n".join(f"- ({r['created_at']}) {r['body']}" for r in rows)

    def format_for_prompt(self, limit: int = 30) -> str:
        facts = self.list_facts(limit=limit)
        if not facts:
            return "(empty — use remember when the user shares durable facts)"
        return "\n".join(f"- {k}: {v}" for k, v in facts)

    def add_reminder(self, body: str, due_at: datetime) -> str:
        body = body.strip()
        if not body:
            return "The reminder had no text."
        with self._db() as conn:
            conn.execute(
                "INSERT INTO reminders(body, due_at, created_at) VALUES(?,?,?)",
                (body, due_at.astimezone(timezone.utc).isoformat(), _now()),
            )
        local = due_at.astimezone().strftime("%A %I:%M %p")
        return f"Reminder set for {local}: {body}"

    def pending_reminders(self) -> list[tuple[int, str, datetime]]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT id, body, due_at FROM reminders WHERE fired = 0 ORDER BY due_at"
            ).fetchall()
        result = []
        for row in rows:
            try:
                due = datetime.fromisoformat(row["due_at"])
            except ValueError:
                continue
            result.append((int(row["id"]), row["body"], due))
        return result

    def due_reminders(self, now: datetime | None = None) -> list[tuple[int, str]]:
        moment = now or datetime.now(timezone.utc)
        due = [
            (ident, body)
            for ident, body, when in self.pending_reminders()
            if when <= moment
        ]
        return due

    def mark_reminder_fired(self, reminder_id: int) -> None:
        with self._db() as conn:
            conn.execute("UPDATE reminders SET fired = 1 WHERE id = ?", (reminder_id,))

    def cancel_reminder(self, text: str) -> str:
        pending = self.pending_reminders()
        if not pending:
            return "Nothing is scheduled."
        needle = text.strip().lower()
        if not needle or needle in {"all", "everything"}:
            with self._db() as conn:
                conn.execute("UPDATE reminders SET fired = 1 WHERE fired = 0")
            return f"Cancelled {len(pending)} reminders."
        for ident, body, _when in pending:
            if needle in body.lower():
                self.mark_reminder_fired(ident)
                return f"Cancelled: {body}"
        return f"No pending reminder matches '{text}'."

    def capture_user_fact(self, text: str) -> str | None:
        """Remember clear, user-owned facts even if the model forgets to call a tool.

        One sentence can carry several facts, so every pattern gets a chance to match.
        """
        clean = " ".join(text.strip().split())
        saved: list[str] = []

        name = NAME_RE.search(clean)
        if name:
            value = _trim_phrase(name.group(1))
            if value:
                saved.append(self.remember("user name", value))

        home = HOME_RE.search(clean)
        if home:
            value = _trim_phrase(home.group(1))
            if value:
                saved.append(self.remember("home location", value))

        favorite = FAVORITE_RE.search(clean)
        if favorite:
            category = "favorite " + _clean_key(favorite.group(1))
            value = _trim_value(favorite.group(2))
            if value:
                saved.append(self.remember(category, value))

        preference = PREFERENCE_RE.search(clean)
        if preference:
            value = _trim_value(preference.group(1))
            first = value.split(" ")[0].lower() if value else ""
            if value and first not in _VAGUE_STARTS and len(value.split()) <= 14:
                saved.append(self.append_to_fact("preferences", value))

        return "; ".join(saved) if saved else None


def _clean_key(key: str) -> str:
    return " ".join(key.strip().lower().split())


def _trim_phrase(raw: str) -> str:
    """Keep the leading words of a short phrase, stopping at a clause break."""
    kept: list[str] = []
    for word in _trim_value(raw).split():
        if kept and word.lower().strip(",.") in _PHRASE_BREAKS:
            break
        kept.append(word)
    return " ".join(kept).strip(" ,.!?;:")


def _trim_value(raw: str) -> str:
    text = re.split(r"\s+and\s+(?:my|i)\b", raw, maxsplit=1, flags=re.I)[0]
    return text.strip().strip(" ,.!?;:\"'")


def _best_key(query: str, keys: list[str]) -> str | None:
    query = query.lower()
    for key in keys:
        if query == key or query in key or key in query:
            return key
    return None
