from __future__ import annotations

import threading
from typing import Callable

from trinity.agent import Agent
from trinity.messaging import MessagingError, NtfyChannel

Event = Callable[[str], None]


class RemoteBridge:
    """Answers phone messages in their own session and texts the reply back."""

    def __init__(self, channel: NtfyChannel, agent: Agent, on_event: Event) -> None:
        self.channel = channel
        self.agent = agent
        self.on_event = on_event
        self._thread: threading.Thread | None = None
        self._turn_lock = threading.Lock()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        if not self.channel.can_receive:
            self.on_event("The phone link has no inbound topic configured")
            return
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.channel.stop()

    def _listen(self) -> None:
        self.channel.listen(self._handle, self.on_event)

    def _handle(self, text: str) -> None:
        # One phone message at a time, so replies cannot interleave.
        with self._turn_lock:
            self.on_event(f"Phone message: {text}")
            reply = ""
            try:
                for kind, payload in self.agent.handle(text):
                    if kind == "final":
                        reply = payload
                    elif kind == "error":
                        reply = f"I hit a problem: {payload}"
                    elif kind == "trace":
                        self.on_event(f"(phone) {payload}")
            except Exception as exc:
                reply = f"I hit a problem answering that: {exc}"
            try:
                self.channel.publish(reply or "I had no answer for that.")
                self.on_event("Replied to your phone")
            except MessagingError as exc:
                self.on_event(str(exc))
