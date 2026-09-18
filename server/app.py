"""Trinity's hosted twin.

Answers phone messages while the desktop is asleep, using a cloud model and a
synced copy of long-term memory. Desktop-only tools are not available here.

Environment:
  TRINITY_SYNC_TOKEN        shared secret the desktop sends (required)
  TRINITY_CLOUD_API_KEY     key for the OpenAI-compatible endpoint
  TRINITY_CLOUD_BASE_URL    defaults to https://api.openai.com/v1
  TRINITY_CLOUD_MODEL       defaults to gpt-4o-mini
  TRINITY_NTFY_SERVER       defaults to https://ntfy.sh
  TRINITY_NTFY_OUT          topic the twin publishes replies to
  TRINITY_NTFY_IN           topic the twin listens on
  TRINITY_DB                memory database path, defaults to /data/trinity.db
"""

from __future__ import annotations

import hmac
import os
import threading
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request

from trinity.agent import Agent
from trinity.cloud import CloudBrain
from trinity.memory import Memory
from trinity.messaging import NtfyChannel
from trinity.remote import RemoteBridge
from trinity.skills import REMOTE_SAFE_TOOLS, set_message_channel
from trinity.sync import Presence

MAX_HANDLED = 500

app = Flask(__name__)
presence = Presence()
memory = Memory(Path(os.environ.get("TRINITY_DB", "/data/trinity.db")))
brain = CloudBrain()
handled: list[str] = []
handled_lock = threading.Lock()
events: list[str] = []


def log(message: str) -> None:
    events.append(message)
    del events[:-200]
    print(f"[trinity] {message}", flush=True)


def authorized() -> bool:
    expected = os.environ.get("TRINITY_SYNC_TOKEN", "")
    if not expected:
        return False
    header = request.headers.get("Authorization", "")
    provided = header[7:] if header.startswith("Bearer ") else ""
    return hmac.compare_digest(provided, expected)


@app.post("/sync")
def sync() -> Any:
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    incoming = request.get_json(silent=True) or {}
    counts = memory.merge_state(incoming)
    log(f"Memory sync applied {counts}")
    return jsonify(memory.export_state())


@app.post("/heartbeat")
def heartbeat() -> Any:
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    presence.record()
    return jsonify({"ok": True})


@app.post("/handled")
def handled_messages() -> Any:
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    with handled_lock:
        return jsonify({"ids": list(handled)})


@app.get("/health")
def health() -> Any:
    ok, detail = brain.ping()
    return jsonify(
        {
            "brain_ready": ok,
            "brain": detail,
            "listening": bool(os.environ.get("TRINITY_NTFY_IN")),
            "facts": len(memory.list_facts(limit=10_000)),
            **presence.status(),
        }
    )


def remember_handled(text: str) -> None:
    with handled_lock:
        handled.append(text)
        del handled[:-MAX_HANDLED]


def start_phone_listener() -> None:
    inbound = os.environ.get("TRINITY_NTFY_IN", "")
    outbound = os.environ.get("TRINITY_NTFY_OUT", "")
    if not inbound or not outbound:
        log("No ntfy topics set; the twin will only serve sync and health")
        return
    channel = NtfyChannel(
        os.environ.get("TRINITY_NTFY_SERVER", "https://ntfy.sh"),
        outbound,
        inbound,
        os.environ.get("TRINITY_NTFY_TOKEN", ""),
        state_path=Path(os.environ.get("TRINITY_DB", "/data/trinity.db")).with_name(
            "remote_state.json"
        ),
    )
    set_message_channel(channel)
    agent = Agent(brain, memory, allowed_tools=REMOTE_SAFE_TOOLS, remote=True)
    bridge = RemoteBridge(
        channel,
        agent,
        log,
        should_answer=lambda: not presence.desktop_awake,
        on_answered=remember_handled,
    )
    bridge.start()
    log("Phone listener started; staying quiet while the desktop is awake")


start_phone_listener()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
