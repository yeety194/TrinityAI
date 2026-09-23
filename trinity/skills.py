from __future__ import annotations

import json
import os
import platform
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pyperclip

from trinity import apps, automation, reasoning, research
from trinity.memory import Memory

MAX_TOOL_RESULT = 8000

READABLE_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".json",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".log",
    ".py",
    ".js",
    ".ts",
    ".html",
    ".css",
    ".xml",
    ".bat",
    ".ps1",
}

# Windows virtual-key codes for the media and volume keys.
MEDIA_KEYS = {
    "play": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "mute": 0xAD,
    "volume_down": 0xAE,
    "volume_up": 0xAF,
}

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
        _fn(
            "calculate",
            "Evaluate arithmetic exactly. Always use this for sums, percentages, and conversions "
            "instead of working them out yourself.",
            {"expression": _str("Maths expression, e.g. '18% of 2450' or 'sqrt(196)+12'")},
            ["expression"],
        ),
        _fn(
            "deep_research",
            "Search the web, read the top sources, and return notes with links. Use for anything "
            "that deserves more than a single search.",
            {
                "topic": _str("What to investigate"),
                "depth": _str("How many sources to read, 1 to 4 (default 3)"),
            },
            ["topic"],
        ),
        _fn(
            "set_reminder",
            "Schedule a reminder. Understands 'in 20 minutes', 'in an hour', 'at 7pm', 'tomorrow'.",
            {
                "text": _str("What to remind about"),
                "when": _str("When, in the user's own words"),
            },
            ["text", "when"],
        ),
        _fn(
            "list_reminders",
            "Show reminders that have not fired yet.",
            {},
            [],
        ),
        _fn(
            "cancel_reminder",
            "Cancel a pending reminder by describing it, or 'all'.",
            {"text": _str("Words from the reminder, or 'all'")},
            ["text"],
        ),
        _fn(
            "system_status",
            "Live machine health: CPU, memory, disk, and battery.",
            {},
            [],
        ),
        _fn(
            "list_directory",
            "List what is inside a folder under the user profile.",
            {"path": _str("Folder path or special name like desktop or downloads")},
            ["path"],
        ),
        _fn(
            "read_document",
            "Read a text-based file under the user profile so you can summarize or answer about it.",
            {"path": _str("Path to a text, code, markdown, csv, or json file")},
            ["path"],
        ),
        _fn(
            "media_control",
            "Control playback and volume on this PC: play, pause, next, previous, "
            "volume_up, volume_down, mute.",
            {"action": _str("One of play, pause, next, previous, volume_up, volume_down, mute")},
            ["action"],
        ),
        _fn(
            "mouse_move",
            "Move the mouse cursor to absolute screen coordinates. Requires AUTOMATION on.",
            {
                "x": _str("Horizontal pixel position"),
                "y": _str("Vertical pixel position"),
            },
            ["x", "y"],
        ),
        _fn(
            "mouse_click",
            "Click the mouse (optionally after moving). Requires AUTOMATION on.",
            {
                "button": _str("left (default), right, or middle"),
                "clicks": _str("How many clicks, 1 to 5 (default 1)"),
                "x": _str("Optional X before clicking"),
                "y": _str("Optional Y before clicking"),
            },
            [],
        ),
        _fn(
            "mouse_scroll",
            "Scroll the mouse wheel. Positive delta scrolls up. Requires AUTOMATION on.",
            {
                "delta": _str("Wheel notches; positive=up, negative=down"),
                "x": _str("Optional X before scrolling"),
                "y": _str("Optional Y before scrolling"),
            },
            ["delta"],
        ),
        _fn(
            "mouse_position",
            "Report the current mouse cursor coordinates. Requires AUTOMATION on.",
            {},
            [],
        ),
        _fn(
            "screen_size",
            "Report the primary screen width and height in pixels. Requires AUTOMATION on.",
            {},
            [],
        ),
        _fn(
            "type_text",
            "Type text with the keyboard as if the user typed it. Requires AUTOMATION on.",
            {
                "text": _str("Characters to type"),
                "interval_ms": _str("Optional delay between keys in milliseconds"),
            },
            ["text"],
        ),
        _fn(
            "key_press",
            "Press one key (enter, tab, esc, f5, a, …). Requires AUTOMATION on.",
            {"key": _str("Key name, e.g. enter, tab, esc, delete, f5, a")},
            ["key"],
        ),
        _fn(
            "hotkey",
            "Press a key chord such as ctrl+c or ctrl+shift+s. Requires AUTOMATION on.",
            {"keys": _str("Chord like 'ctrl+c' or space-separated 'ctrl shift s'")},
            ["keys"],
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


def _arg(args: dict[str, Any], *names: str, default: str = "") -> str:
    """Local models often rename parameters, so accept the common aliases."""
    for name in names:
        if name not in args:
            continue
        value = args[name]
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is not None and not isinstance(value, (str, dict, list)):
            return str(value)
    return default


def _open_app(args: dict[str, Any], _m: Memory) -> str:
    return apps.open_app(_arg(args, "name", "app", "application", "program", "value"))


def _list_apps(args: dict[str, Any], _m: Memory) -> str:
    return apps.list_apps(_arg(args, "query", "filter", "name", "search"))


def _open_url(args: dict[str, Any], _m: Memory) -> str:
    return apps.open_url(_arg(args, "url", "link", "address", "site", "value"))


def _open_folder(args: dict[str, Any], _m: Memory) -> str:
    return apps.open_folder(_arg(args, "path", "folder", "directory", "value", default="home"))


def _web_search(args: dict[str, Any], _m: Memory) -> str:
    query = _arg(args, "query", "q", "search", "text", "topic", "value")
    return research.web_search(query, _arg(args, "mode", "type", default="web"))


def _read_url(args: dict[str, Any], _m: Memory) -> str:
    return research.read_url(_arg(args, "url", "link", "address", "value"))


def _wikipedia(args: dict[str, Any], _m: Memory) -> str:
    return research.wikipedia(_arg(args, "topic", "query", "title", "q", "subject", "value"))


def _weather(args: dict[str, Any], memory: Memory) -> str:
    location = _arg(args, "location", "city", "place", "where", "query", "value")
    home = (memory.get_fact("home location") or "").strip()
    if not location or SELF_LOCATION_RE.match(location):
        location = home
    if not location:
        return "No location given. Ask the user which city to check."
    return research.weather(location)


def _remember(args: dict[str, Any], memory: Memory) -> str:
    return memory.remember(
        _arg(args, "key", "label", "name", "fact"),
        _arg(args, "value", "content", "detail", "text"),
    )


def _recall(args: dict[str, Any], memory: Memory) -> str:
    return memory.recall(_arg(args, "query", "key", "q", "search", "topic", "value"))


def _forget(args: dict[str, Any], memory: Memory) -> str:
    return memory.forget(_arg(args, "key", "label", "name", "value"))


def _list_memory(_args: dict[str, Any], memory: Memory) -> str:
    facts = memory.list_facts()
    if not facts:
        return "Memory is empty."
    return "\n".join(f"- {k}: {v}" for k, v in facts)


def _add_note(args: dict[str, Any], memory: Memory) -> str:
    return memory.add_note(_arg(args, "text", "note", "body", "content", "value"))


def _list_notes(_args: dict[str, Any], memory: Memory) -> str:
    return memory.recent_notes()


def _now(_args: dict[str, Any], _m: Memory) -> str:
    return datetime.now().strftime("%A, %B %d, %Y %I:%M %p")


def _clipboard_get(_args: dict[str, Any], _m: Memory) -> str:
    text = pyperclip.paste()
    return text if text else "(clipboard is empty or not text)"


def _clipboard_set(args: dict[str, Any], _m: Memory) -> str:
    pyperclip.copy(_arg(args, "text", "content", "value"))
    return "Copied to clipboard."


def _search_files(args: dict[str, Any], _m: Memory) -> str:
    query = _arg(args, "query", "name", "filename", "q", "value").lower()
    if not query:
        return "Need a filename query."
    root = _allowed_root(_arg(args, "where", "path", "folder", "location", default="home"))
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


def _calculate(args: dict[str, Any], _m: Memory) -> str:
    expression = _arg(args, "expression", "expr", "query", "input", "value", "math")
    try:
        return f"{expression} = {reasoning.calculate(expression)}"
    except reasoning.CalculationError as exc:
        return str(exc)


def _deep_research(args: dict[str, Any], _m: Memory) -> str:
    topic = _arg(args, "topic", "query", "subject", "q", "value")
    depth = _arg(args, "depth", "sources", "count", default="3")
    try:
        count = int(float(depth))
    except ValueError:
        count = 3
    return research.deep_research(topic, count)


def _set_reminder(args: dict[str, Any], memory: Memory) -> str:
    body = _arg(args, "text", "body", "what", "message", "reminder", "value")
    when = _arg(args, "when", "time", "delay", "at", "due")
    if not body:
        return "What should the reminder say?"
    due = reasoning.parse_when(when or body)
    if due is None:
        return (
            "I could not read that time. Try 'in 20 minutes', 'at 7pm', or 'tomorrow'."
        )
    return memory.add_reminder(body, due)


def _list_reminders(_args: dict[str, Any], memory: Memory) -> str:
    pending = memory.pending_reminders()
    if not pending:
        return "Nothing is scheduled."
    lines = []
    for _ident, body, due in pending:
        lines.append(f"- {body} (in {reasoning.describe_delay(due)})")
    return "\n".join(lines)


def _cancel_reminder(args: dict[str, Any], memory: Memory) -> str:
    return memory.cancel_reminder(_arg(args, "text", "which", "body", "value"))


def _system_status(_args: dict[str, Any], _m: Memory) -> str:
    try:
        import psutil
    except ImportError:
        return "System status needs psutil: pip install psutil"
    memory_info = psutil.virtual_memory()
    disk = psutil.disk_usage(str(Path.home().anchor or "C:\\"))
    lines = [
        f"CPU: {psutil.cpu_percent(interval=0.4)}% across {psutil.cpu_count()} threads",
        f"Memory: {memory_info.percent}% used "
        f"({memory_info.used / 1e9:.1f} of {memory_info.total / 1e9:.1f} GB)",
        f"Disk: {disk.percent}% used ({disk.free / 1e9:.0f} GB free)",
    ]
    battery = getattr(psutil, "sensors_battery", lambda: None)()
    if battery is not None:
        state = "charging" if battery.power_plugged else "on battery"
        lines.append(f"Battery: {battery.percent:.0f}% {state}")
    return "\n".join(lines)


def _list_directory(args: dict[str, Any], _m: Memory) -> str:
    root = _allowed_root(_arg(args, "path", "folder", "directory", "where", default="home"))
    if root is None:
        return "That location is outside the user profile."
    if not root.exists():
        return f"Path not found: {root}"
    entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    if not entries:
        return f"{root} is empty."
    lines = [f"Contents of {root}:"]
    for entry in entries[:40]:
        if entry.is_dir():
            lines.append(f"- {entry.name}/")
        else:
            lines.append(f"- {entry.name} ({entry.stat().st_size / 1024:.0f} KB)")
    if len(entries) > 40:
        lines.append(f"…and {len(entries) - 40} more")
    return "\n".join(lines)


def _read_document(args: dict[str, Any], _m: Memory) -> str:
    raw = _arg(args, "path", "file", "document", "name", "value")
    if not raw:
        return "Which file?"
    target = _allowed_root(raw)
    if target is None:
        return "That file is outside the user profile."
    if target.is_dir():
        return f"{target} is a folder. Use list_directory for it."
    if not target.exists():
        return f"File not found: {target}"
    if target.suffix.lower() not in READABLE_SUFFIXES:
        return (
            f"I can only read text-based files ({', '.join(sorted(READABLE_SUFFIXES))}), "
            f"and that is {target.suffix or 'unknown'}."
        )
    if target.stat().st_size > 2_000_000:
        return f"{target.name} is too large to read in one go."
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"Could not read {target.name}: {exc}"
    clipped = text[:7000]
    suffix = "\n…(truncated)" if len(text) > len(clipped) else ""
    return f"{target.name} ({len(text)} characters):\n{clipped}{suffix}"


def _media_control(args: dict[str, Any], _m: Memory) -> str:
    action = _arg(args, "action", "command", "control", "value").lower().replace(" ", "_")
    aliases = {
        "play_pause": "play",
        "pause": "play",
        "resume": "play",
        "stop": "play",
        "skip": "next",
        "next_track": "next",
        "previous_track": "previous",
        "back": "previous",
        "louder": "volume_up",
        "quieter": "volume_down",
        "volume": "volume_up",
        "unmute": "mute",
    }
    action = aliases.get(action, action)
    key = MEDIA_KEYS.get(action)
    if key is None:
        return f"I do not know the media action '{action}'."
    try:
        _tap_key(key)
    except OSError as exc:
        return f"Could not send that key: {exc}"
    return f"Sent {action.replace('_', ' ')}."


def _tap_key(code: int) -> None:
    import ctypes

    user32 = ctypes.windll.user32
    user32.keybd_event(code, 0, 0, 0)
    user32.keybd_event(code, 0, 2, 0)


def _int_arg(args: dict[str, Any], *names: str, default: int | None = None) -> int | None:
    raw = _arg(args, *names)
    if not raw:
        return default
    try:
        return int(float(str(raw).strip()))
    except ValueError:
        return default


def _mouse_move(args: dict[str, Any], _m: Memory) -> str:
    x = _int_arg(args, "x", "left", "horizontal")
    y = _int_arg(args, "y", "top", "vertical")
    if x is None or y is None:
        return "Need both x and y pixel coordinates."
    try:
        return automation.mouse_move(x, y)
    except OSError as exc:
        return f"Could not move the mouse: {exc}"


def _mouse_click(args: dict[str, Any], _m: Memory) -> str:
    button = _arg(args, "button", "btn", "which", default="left") or "left"
    clicks = _int_arg(args, "clicks", "count", "times", default=1) or 1
    x = _int_arg(args, "x", "left", "horizontal")
    y = _int_arg(args, "y", "top", "vertical")
    try:
        return automation.mouse_click(button, clicks, x, y)
    except OSError as exc:
        return f"Could not click: {exc}"


def _mouse_scroll(args: dict[str, Any], _m: Memory) -> str:
    delta = _int_arg(args, "delta", "amount", "notches", "scroll", "value")
    if delta is None:
        return "Need a scroll delta (positive = up)."
    x = _int_arg(args, "x", "left", "horizontal")
    y = _int_arg(args, "y", "top", "vertical")
    try:
        return automation.mouse_scroll(delta, x, y)
    except OSError as exc:
        return f"Could not scroll: {exc}"


def _mouse_position(_args: dict[str, Any], _m: Memory) -> str:
    try:
        return automation.mouse_position()
    except OSError as exc:
        return f"Could not read the mouse position: {exc}"


def _screen_size(_args: dict[str, Any], _m: Memory) -> str:
    try:
        return automation.screen_size()
    except OSError as exc:
        return f"Could not read the screen size: {exc}"


def _type_text(args: dict[str, Any], _m: Memory) -> str:
    text = _arg(args, "text", "content", "string", "value", "message")
    interval = _int_arg(args, "interval_ms", "interval", "delay", "delay_ms", default=0) or 0
    try:
        return automation.type_text(text, interval)
    except OSError as exc:
        return f"Could not type: {exc}"


def _key_press(args: dict[str, Any], _m: Memory) -> str:
    key = _arg(args, "key", "name", "button", "value")
    if not key:
        return "Which key?"
    try:
        return automation.key_press(key)
    except OSError as exc:
        return f"Could not press that key: {exc}"


def _hotkey(args: dict[str, Any], _m: Memory) -> str:
    raw = _arg(args, "keys", "key", "combo", "chord", "hotkey", "value")
    if not raw:
        return "Which hotkey? Example: ctrl+c"
    parts = automation.parse_hotkey(raw)
    if not parts:
        return "Which hotkey? Example: ctrl+c"
    try:
        return automation.hotkey(*parts)
    except OSError as exc:
        return f"Could not send that hotkey: {exc}"


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
    "calculate": _calculate,
    "deep_research": _deep_research,
    "set_reminder": _set_reminder,
    "list_reminders": _list_reminders,
    "cancel_reminder": _cancel_reminder,
    "system_status": _system_status,
    "list_directory": _list_directory,
    "read_document": _read_document,
    "media_control": _media_control,
    "mouse_move": _mouse_move,
    "mouse_click": _mouse_click,
    "mouse_scroll": _mouse_scroll,
    "mouse_position": _mouse_position,
    "screen_size": _screen_size,
    "type_text": _type_text,
    "key_press": _key_press,
    "hotkey": _hotkey,
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
