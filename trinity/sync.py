from __future__ import annotations

import time
from typing import Any

import requests

from trinity.memory import Memory

HEARTBEAT_GRACE_SECONDS = 150


class SyncError(RuntimeError):
    pass


class SyncClient:
    """Desktop side of the hosted twin: exchange memory and say "I'm awake"."""

    def __init__(self, base_url: str, token: str = "", timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def sync_memory(self, memory: Memory) -> str:
        payload = memory.export_state()
        shared = len(payload["facts"]) + len(payload["notes"])
        data = self._post("/sync", payload)
        counts = memory.merge_state(data)
        if not any(counts.values()):
            return f"Synced with the cloud twin: shared {shared} items, nothing new came back."
        return (
            f"Synced with the cloud twin: shared {shared} items, and took in "
            f"{counts['facts']} facts, {counts['notes']} notes, "
            f"{counts['deletions']} deletions."
        )

    def heartbeat(self) -> None:
        self._post("/heartbeat", {"at": int(time.time())})

    def handled_ids(self) -> set[str]:
        data = self._post("/handled", {})
        ids = data.get("ids") or []
        return {str(value) for value in ids}

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise SyncError("No cloud twin is configured.")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = requests.post(
                f"{self.base_url}{path}", json=payload, headers=headers, timeout=self.timeout
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise SyncError(f"Cloud twin unreachable: {exc}") from exc
        except ValueError as exc:
            raise SyncError(f"Cloud twin sent unreadable JSON: {exc}") from exc
        return data if isinstance(data, dict) else {}


class Presence:
    """Tracks desktop heartbeats so only one Trinity answers a phone message."""

    def __init__(self, grace_seconds: int = HEARTBEAT_GRACE_SECONDS) -> None:
        self.grace_seconds = grace_seconds
        self._last_seen = 0.0

    def record(self, at: float | None = None) -> None:
        self._last_seen = at if at is not None else time.time()

    @property
    def desktop_awake(self) -> bool:
        return (time.time() - self._last_seen) < self.grace_seconds

    def status(self) -> dict[str, Any]:
        return {
            "desktop_awake": self.desktop_awake,
            "seconds_since_heartbeat": (
                None if not self._last_seen else int(time.time() - self._last_seen)
            ),
        }
