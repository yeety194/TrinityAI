from __future__ import annotations

import json
import secrets
import threading
from pathlib import Path
from typing import Callable

import requests

from trinity.config import DATA_DIR

STATE_PATH = DATA_DIR / "remote_state.json"
MAX_MESSAGE_CHARS = 3500

Handler = Callable[[str], None]
Status = Callable[[str], None]


class MessagingError(RuntimeError):
    pass


def new_topic(kind: str) -> str:
    """Topic names are the only secret on a public ntfy server, so make them unguessable."""
    return f"trinity-{kind}-{secrets.token_urlsafe(18)}"


class NtfyChannel:
    """Two-way phone link: publish Trinity's messages, stream the user's replies back."""

    def __init__(
        self,
        server: str,
        outbound_topic: str,
        inbound_topic: str,
        token: str = "",
        state_path: Path | None = None,
    ) -> None:
        self.server = server.rstrip("/")
        self.outbound_topic = outbound_topic
        self.inbound_topic = inbound_topic
        self.token = token
        self.state_path = state_path or STATE_PATH
        self._stop = threading.Event()
        self._handled_ids: set[str] = set()

    @property
    def configured(self) -> bool:
        return bool(self.server and self.outbound_topic)

    @property
    def can_receive(self) -> bool:
        return bool(self.server and self.inbound_topic)

    def publish(self, message: str, title: str = "Trinity") -> str:
        if not self.configured:
            raise MessagingError("The phone link is not configured yet.")
        body = message.strip()
        if not body:
            return "Nothing to send."
        if len(body) > MAX_MESSAGE_CHARS:
            body = body[: MAX_MESSAGE_CHARS - 1] + "…"
        try:
            response = requests.post(
                f"{self.server}/{self.outbound_topic}",
                data=body.encode("utf-8"),
                headers={**self._headers(), "Title": _ascii_header(title)},
                timeout=20,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise MessagingError(f"Could not reach the phone link: {exc}") from exc
        return "Sent to your phone."

    def listen(self, on_message: Handler, on_status: Status | None = None) -> None:
        """Stream inbound messages, resuming from the last one seen so nothing is lost."""
        self._stop.clear()
        announced = False
        while not self._stop.is_set():
            try:
                since = self._since()
                with requests.get(
                    f"{self.server}/{self.inbound_topic}/json",
                    params={"since": since} if since else None,
                    headers=self._headers(),
                    stream=True,
                    timeout=(15, None),
                ) as response:
                    response.raise_for_status()
                    if on_status and not announced:
                        on_status("Phone link connected")
                        announced = True
                    for line in response.iter_lines():
                        if self._stop.is_set():
                            break
                        if not line:
                            continue
                        event = _decode(line)
                        if not event:
                            continue
                        kind = event.get("event")
                        if kind in {"open", "keepalive"}:
                            # Mark where we are even when idle, so a restart can
                            # replay anything sent while this PC was asleep.
                            self._remember_position(event, offset=0)
                            continue
                        if kind != "message":
                            continue
                        self._remember_position(event, offset=1)
                        message_id = str(event.get("id") or "")
                        if message_id and message_id in self._handled_ids:
                            continue
                        if message_id:
                            self._handled_ids.add(message_id)
                        text = str(event.get("message") or "").strip()
                        if text:
                            on_message(text)
            except requests.RequestException as exc:
                if self._stop.is_set():
                    break
                announced = False
                if on_status:
                    on_status(f"Phone link dropped, retrying: {exc}")
                self._stop.wait(5)

    def stop(self) -> None:
        self._stop.set()

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": "TrinityAI/0.4 (local desktop assistant)"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _since(self) -> str:
        """A stored Unix timestamp replays anything missed; empty means new messages only.

        ntfy rejects "now", so the parameter is left off when there is no position.
        """
        seen = self._read_state().get(self.inbound_topic)
        return str(seen) if isinstance(seen, int) else ""

    def _remember_position(self, event: dict[str, object], offset: int) -> None:
        stamp = event.get("time")
        if not isinstance(stamp, int):
            return
        position = stamp + offset
        state = self._read_state()
        previous = state.get(self.inbound_topic)
        if isinstance(previous, int) and previous >= position:
            return
        state[self.inbound_topic] = position
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(state), encoding="utf-8")
        except OSError:
            pass

    def _read_state(self) -> dict[str, object]:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return state if isinstance(state, dict) else {}


def _decode(line: bytes | str) -> dict[str, object] | None:
    try:
        data = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _ascii_header(value: str) -> str:
    return value.encode("ascii", "replace").decode("ascii")
