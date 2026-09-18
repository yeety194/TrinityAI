from __future__ import annotations

import json
import os
import platform
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pyperclip

from trinity import apps, research
from trinity.memory import Memory

MAX_TOOL_RESULT = 8000

# Models sometimes pass the user's phrasing straight through as the location.
SELF_LOCATION_RE = re.compile(
    r"^(?:here|home|my (?:area|city|town|place|location)|where i (?:live|am))\b", re.I
)

ToolFn = Callable[[dict[str, Any], Memory], str]


def schemas() -> list[dict[str, Any]]:
    return [
        _fn(
            "open_app",
            "Launch an installed Windows application by name (Chrome, Discord, Spotify, Notepad, Cursor, etc.).",
            {"name": _str("App name as the user said it")},
            ["name"],
        ),
        _fn(
            "list_apps",
            "List installed apps, optionally filtered by a search string.",
            {"query": _str("Optional filter, e.g. 'discord'")},
            [],
        ),
        _fn(
            "open_url",
            "Open a website in the default browser.",
            {"url": _str("Full URL or domain")},
            ["url"],
        ),
        _fn(
            "open_folder",
            "Open a folder in File Explorer. Special names: desktop, documents, downloads, pictures, home.",
            {"path": _str("Folder path or special name")},
            ["path"],
        ),
        _fn(
            "web_search",
            "Search the live web. Use for news, facts you are unsure about, people, products, or current events.",
            {
                "query": _str("Search query"),
                "mode": _str("'web' (default) or 'news'"),
            },
            ["query"],
        ),
        _fn(
            "read_url",
            "Fetch and extract readable text from a web page after search, when you need more detail.",
            {"url": _str("http(s) URL")},
            ["url"],
        ),
        _fn(
            "wikipedia",
            "Get a concise Wikipedia summary for a topic.",
            {"topic": _str("Person, place, or subject")},
            ["topic"],
        ),
        _fn(
            "weather",
            "Current weather for a city or place via Open-Meteo.",
            {"location": _str("City and optional region/country")},
            ["location"],
        ),
        _fn(
            "remember",
            "Store a durable fact about the user or their world. Use whenever they share a lasting preference, name, project, person, or decision.",
            {
                "key": _str("Short label, e.g. 'user name' or 'favorite editor'"),
                "value": _str("What to remember"),
            },
            ["key", "value"],
        ),
        _fn(
            "recall",
            "Search long-term memory for facts matching a query.",
            {"query": _str("What to look up")},
            ["query"],
        ),
        _fn(
            "forget",
            "Delete a remembered fact by key.",
            {"key": _str("Memory key")},
            ["key"],
        ),
        _fn(
            "list_memory",
            "Show stored long-term facts.",
            {},
            [],
        ),
        _fn(
            "add_note",
            "Save a freeform note or reminder in Trinity's notebook.",
            {"text": _str("Note body")},
            ["text"],
        ),
        _fn(
            "list_notes",
            "Show recent notes.",
            {},
            [],
        ),
        _fn(
            "now",
            "Current local date and time.",
            {},
            [],
        ),
        _fn(
            "clipboard_get",
            "Read the current Windows clipboard text.",
            {},
            [],
        ),
        _fn(
            "clipboard_set",
            "Copy text to the Windows clipboard.",
            {"text": _str("Text to copy")},
            ["text"],
        ),
        _fn(
            "search_files",
            "Search the user's home folders by filename (not file contents). Restricted to the user profile.",
            {
                "query": _str("Filename fragment"),
                "where": _str("home, desktop, documents, downloads, or a subfolder under the profile"),
            },
            ["query"],
        ),
        _fn(
            "system_info",
            "Basic local system info: OS, user, machine, home path.",
            {},
            [],
        ),
    ]


def dispatch(name: str, arguments: dict[str, Any], memory: Memory) -> str:
    fn = HANDLERS.get(name)
    if not fn:
        return f"Unknown tool: {name}"
    try:
        result = fn(arguments or {}, memory)
    except Exception as exc:
        return f"Tool {name} failed: {exc}"
    if len(result) > MAX_TOOL_RESULT:
        return result[:MAX_TOOL_RESULT] + "\n…(truncated)"
    return result


def _open_app(args: dict[str, Any], _m: Memory) -> str:
    return apps.open_app(str(args.get("name") or ""))


def _list_apps(args: dict[str, Any], _m: Memory) -> str:
    return apps.list_apps(str(args.get("query") or ""))


def _open_url(args: dict[str, Any], _m: Memory) -> str:
    return apps.open_url(str(args.get("url") or ""))


def _open_folder(args: dict[str, Any], _m: Memory) -> str:
    return apps.open_folder(str(args.get("path") or "home"))


def _web_search(args: dict[str, Any], _m: Memory) -> str:
    return research.web_search(str(args.get("query") or ""), str(args.get("mode") or "web"))


def _read_url(args: dict[str, Any], _m: Memory) -> str:
    return research.read_url(str(args.get("url") or ""))


def _wikipedia(args: dict[str, Any], _m: Memory) -> str:
    return research.wikipedia(str(args.get("topic") or ""))


def _weather(args: dict[str, Any], memory: Memory) -> str:
    location = str(args.get("location") or "").strip()
    home = (memory.get_fact("home location") or "").strip()
    if not location or SELF_LOCATION_RE.match(location):
        location = home
    if not location:
        return "No location given. Ask the user which city to check."
    return research.weather(location)


def _remember(args: dict[str, Any], memory: Memory) -> str:
    return memory.remember(str(args.get("key") or ""), str(args.get("value") or ""))


def _recall(args: dict[str, Any], memory: Memory) -> str:
    return memory.recall(str(args.get("query") or ""))


def _forget(args: dict[str, Any], memory: Memory) -> str:
    return memory.forget(str(args.get("key") or ""))


def _list_memory(_args: dict[str, Any], memory: Memory) -> str:
    facts = memory.list_facts()
    if not facts:
        return "Memory is empty."
    return "\n".join(f"- {k}: {v}" for k, v in facts)


def _add_note(args: dict[str, Any], memory: Memory) -> str:
    return memory.add_note(str(args.get("text") or ""))


def _list_notes(_args: dict[str, Any], memory: Memory) -> str:
    return memory.recent_notes()


def _now(_args: dict[str, Any], _m: Memory) -> str:
    return datetime.now().strftime("%A, %B %d, %Y %I:%M %p")


def _clipboard_get(_args: dict[str, Any], _m: Memory) -> str:
    text = pyperclip.paste()
    return text if text else "(clipboard is empty or not text)"


def _clipboard_set(args: dict[str, Any], _m: Memory) -> str:
    text = str(args.get("text") or "")
    pyperclip.copy(text)
    return "Copied to clipboard."


def _search_files(args: dict[str, Any], _m: Memory) -> str:
    query = str(args.get("query") or "").strip().lower()
    if not query:
        return "Need a filename query."
    root = _allowed_root(str(args.get("where") or "home"))
    if root is None:
        return "That location is outside the user profile."
    hits: list[str] = []
    skip = {".git", ".venv", "node_modules", "AppData", "__pycache__"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        rel = Path(dirpath)
        if "AppData" in rel.parts:
            dirnames[:] = []
            continue
        for name in filenames:
            if query in name.lower():
                hits.append(str(Path(dirpath) / name))
                if len(hits) >= 20:
                    return "\n".join(hits)
    return "\n".join(hits) if hits else "No matching files."


def _system_info(_args: dict[str, Any], _m: Memory) -> str:
    return (
        f"OS: {platform.system()} {platform.release()} ({platform.version()})\n"
        f"Machine: {platform.node()}\n"
        f"User: {os.environ.get('USERNAME') or os.environ.get('USER')}\n"
        f"Home: {Path.home()}\n"
        f"Python: {platform.python_version()}"
    )


def _allowed_root(where: str) -> Path | None:
    home = Path.home().resolve()
    mapping = {
        "home": home,
        "desktop": home / "Desktop",
        "documents": home / "Documents",
        "downloads": home / "Downloads",
        "pictures": home / "Pictures",
        "music": home / "Music",
        "videos": home / "Videos",
    }
    key = where.strip().lower()
    if key in mapping:
        return mapping[key]
    candidate = Path(os.path.expandvars(os.path.expanduser(where))).resolve()
    try:
        candidate.relative_to(home)
    except ValueError:
        return None
    return candidate


def _fn(name: str, description: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        },
    }


def _str(description: str) -> dict[str, str]:
    return {"type": "string", "description": description}


HANDLERS: dict[str, ToolFn] = {
    "open_app": _open_app,
    "list_apps": _list_apps,
    "open_url": _open_url,
    "open_folder": _open_folder,
    "web_search": _web_search,
    "read_url": _read_url,
    "wikipedia": _wikipedia,
    "weather": _weather,
    "remember": _remember,
    "recall": _recall,
    "forget": _forget,
    "list_memory": _list_memory,
    "add_note": _add_note,
    "list_notes": _list_notes,
    "now": _now,
    "clipboard_get": _clipboard_get,
    "clipboard_set": _clipboard_set,
    "search_files": _search_files,
    "system_info": _system_info,
}


def known_tools() -> frozenset[str]:
    return frozenset(HANDLERS)


def pretty_args(arguments: dict[str, Any]) -> str:
    if not arguments:
        return ""
    try:
        compact = json.dumps(arguments, ensure_ascii=False)
    except TypeError:
        compact = str(arguments)
    if len(compact) > 120:
        compact = compact[:117] + "…"
    return compact
