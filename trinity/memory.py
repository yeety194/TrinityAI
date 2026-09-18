from __future__ import annotations

import re
import secrets
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
                CREATE TABLE IF NOT EXISTS deletions (
                    key TEXT PRIMARY KEY,
                    deleted_at TEXT NOT NULL
                );
                """
            )
            self._migrate_notes(conn)

    def _migrate_notes(self, conn: sqlite3.Connection) -> None:
        """Notes need a stable id and an edit time to survive two-way sync."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(notes)")}
        if "uid" not in columns:
            conn.execute("ALTER TABLE notes ADD COLUMN uid TEXT")
        if "updated_at" not in columns:
            conn.execute("ALTER TABLE notes ADD COLUMN updated_at TEXT")
        conn.execute(
            "UPDATE notes SET updated_at = created_at WHERE updated_at IS NULL OR updated_at = ''"
        )
        for row in conn.execute("SELECT id FROM notes WHERE uid IS NULL OR uid = ''").fetchall():
            conn.execute(
                "UPDATE notes SET uid = ? WHERE id = ?", (secrets.token_hex(8), row["id"])
            )
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS notes_uid ON notes(uid)")

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
                self._tombstone(conn, key)
                return f"Forgot {key}."
            # fuzzy
            rows = conn.execute("SELECT key FROM facts").fetchall()
        match = _best_key(key, [r["key"] for r in rows])
        if match:
            with self._db() as conn:
                conn.execute("DELETE FROM facts WHERE key = ?", (match,))
                self._tombstone(conn, match)
            return f"Forgot {match}."
        return f"Nothing stored under {key}."

    def _tombstone(self, conn: sqlite3.Connection, key: str) -> None:
        """Record the deletion so a sync does not resurrect the fact."""
        conn.execute(
            "INSERT INTO deletions(key, deleted_at) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET deleted_at=excluded.deleted_at",
            (key, _now()),
        )

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
        stamp = _now()
        with self._db() as conn:
            conn.execute(
                "INSERT INTO notes(body, created_at, uid, updated_at) VALUES(?,?,?,?)",
                (body, stamp, secrets.token_hex(8), stamp),
            )
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

    def export_state(self) -> dict[str, list[dict[str, str]]]:
        """Everything needed to merge this memory with another copy of Trinity."""
        with self._db() as conn:
            facts = [
                {"key": r["key"], "value": r["value"], "updated_at": r["updated_at"]}
                for r in conn.execute("SELECT key, value, updated_at FROM facts")
            ]
            notes = [
                {
                    "uid": r["uid"],
                    "body": r["body"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
                for r in conn.execute("SELECT uid, body, created_at, updated_at FROM notes")
            ]
            deletions = [
                {"key": r["key"], "deleted_at": r["deleted_at"]}
                for r in conn.execute("SELECT key, deleted_at FROM deletions")
            ]
        return {"facts": facts, "notes": notes, "deletions": deletions}

    def merge_state(self, payload: dict[str, list[dict[str, str]]]) -> dict[str, int]:
        """Merge another copy's state in. Newest edit wins; deletions are respected."""
        facts = payload.get("facts") or []
        notes = payload.get("notes") or []
        deletions = payload.get("deletions") or []
        counts = {"facts": 0, "notes": 0, "deletions": 0}
        with self._db() as conn:
            for item in deletions:
                key = _clean_key(str(item.get("key") or ""))
                stamp = str(item.get("deleted_at") or "")
                if not key or not stamp:
                    continue
                existing = conn.execute(
                    "SELECT deleted_at FROM deletions WHERE key = ?", (key,)
                ).fetchone()
                if existing and str(existing["deleted_at"]) >= stamp:
                    continue
                conn.execute(
                    "INSERT INTO deletions(key, deleted_at) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET deleted_at=excluded.deleted_at",
                    (key, stamp),
                )
                counts["deletions"] += 1
                local = conn.execute(
                    "SELECT updated_at FROM facts WHERE key = ?", (key,)
                ).fetchone()
                if local and str(local["updated_at"]) <= stamp:
                    conn.execute("DELETE FROM facts WHERE key = ?", (key,))

            for item in facts:
                key = _clean_key(str(item.get("key") or ""))
                value = str(item.get("value") or "").strip()
                stamp = str(item.get("updated_at") or "")
                if not key or not value:
                    continue
                gone = conn.execute(
                    "SELECT deleted_at FROM deletions WHERE key = ?", (key,)
                ).fetchone()
                if gone and str(gone["deleted_at"]) > stamp:
                    continue
                local = conn.execute(
                    "SELECT updated_at FROM facts WHERE key = ?", (key,)
                ).fetchone()
                if local and str(local["updated_at"]) >= stamp:
                    continue
                conn.execute(
                    "INSERT INTO facts(key, value, updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (key, value, stamp or _now()),
                )
                counts["facts"] += 1

            for item in notes:
                uid = str(item.get("uid") or "").strip()
                body = str(item.get("body") or "").strip()
                if not uid or not body:
                    continue
                stamp = str(item.get("updated_at") or item.get("created_at") or _now())
                local = conn.execute(
                    "SELECT updated_at FROM notes WHERE uid = ?", (uid,)
                ).fetchone()
                if local:
                    if str(local["updated_at"]) >= stamp:
                        continue
                    conn.execute(
                        "UPDATE notes SET body = ?, updated_at = ? WHERE uid = ?",
                        (body, stamp, uid),
                    )
                else:
                    conn.execute(
                        "INSERT INTO notes(body, created_at, uid, updated_at) VALUES(?,?,?,?)",
                        (body, str(item.get("created_at") or stamp), uid, stamp),
                    )
                counts["notes"] += 1
        return counts

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
