from __future__ import annotations

import json
import os
import re
import subprocess
import webbrowser
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from trinity.config import DATA_DIR

CACHE = DATA_DIR / "apps_cache.json"

PROGRAM_SUFFIXES = {
    "exe",
    "msi",
    "lnk",
    "bat",
    "cmd",
    "ps1",
    "py",
    "dll",
    "appref",
}

ALIASES = {
    "browser": "microsoft edge",
    "chrome": "google chrome",
    "edge": "microsoft edge",
    "firefox": "firefox",
    "notepad": "notepad",
    "calculator": "calculator",
    "calc": "calculator",
    "explorer": "file explorer",
    "files": "file explorer",
    "file explorer": "file explorer",
    "settings": "settings",
    "terminal": "windows terminal",
    "cmd": "command prompt",
    "powershell": "windows powershell",
    "vscode": "visual studio code",
    "code": "visual studio code",
    "cursor": "cursor",
    "spotify": "spotify",
    "discord": "discord",
    "steam": "steam",
    "obs": "obs studio",
    "word": "word",
    "excel": "excel",
    "outlook": "outlook",
    "photos": "photos",
    "paint": "paint",
    "task manager": "task manager",
}


def open_url(url: str) -> str:
    url = url.strip()
    if not url:
        return "No URL given."
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "That does not look like a safe web URL."
    webbrowser.open(url)
    return f"Opened {url}"


def open_folder(path: str) -> str:
    target = _expand_user_path(path)
    if not target.exists():
        return f"Path not found: {target}"
    os.startfile(target)  # noqa: S606
    return f"Opened {target}"


def open_app(name: str) -> str:
    raw = name.strip()
    if not raw:
        return "Which app?"
    if looks_like_url(raw):
        return open_url(raw)
    query = ALIASES.get(raw.lower(), raw)

    apps = list_installed_apps()
    match = pick_app(query, apps)
    if match:
        _launch(match)
        return f"Opened {match['name']}"

    # Direct executable or start-menu shortcut search
    shortcut = _find_shortcut(query)
    if shortcut:
        os.startfile(shortcut)  # noqa: S606
        return f"Opened {shortcut.stem}"

    return (
        f"I couldn't find an installed app matching '{raw}'. "
        "Try the exact Start Menu name, or ask me to list apps."
    )


def list_apps(query: str = "", limit: int = 25) -> str:
    apps = list_installed_apps()
    if query.strip():
        ranked = sorted(apps, key=lambda a: _score(query, a["name"]), reverse=True)
        ranked = [a for a in ranked if _score(query, a["name"]) > 0.25][:limit]
    else:
        ranked = sorted(apps, key=lambda a: a["name"].lower())[:limit]
    if not ranked:
        return "No matching apps."
    return "\n".join(f"- {a['name']}" for a in ranked)


def list_installed_apps() -> list[dict[str, str]]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cached = _read_cache()
    if cached:
        return cached
    apps = _from_powershell() + _from_start_menu()
    dedup: dict[str, dict[str, str]] = {}
    for app in apps:
        key = app["name"].strip().lower()
        if key and key not in dedup:
            dedup[key] = app
    result = list(dedup.values())
    CACHE.write_text(json.dumps(result), encoding="utf-8")
    return result


def pick_app(query: str, apps: list[dict[str, str]]) -> dict[str, str] | None:
    if not apps:
        return None
    q = query.strip().lower()
    exact = [a for a in apps if a["name"].lower() == q]
    if exact:
        return exact[0]
    starts = [a for a in apps if a["name"].lower().startswith(q)]
    if len(starts) == 1:
        return starts[0]
    contains = [a for a in apps if q in a["name"].lower()]
    if len(contains) == 1:
        return contains[0]
    ranked = sorted(apps, key=lambda a: _score(query, a["name"]), reverse=True)
    best = ranked[0]
    if _score(query, best["name"]) >= 0.58:
        return best
    if starts:
        return sorted(starts, key=lambda a: len(a["name"]))[0]
    return None


def _launch(app: dict[str, str]) -> None:
    kind = app.get("kind", "appid")
    target = app["target"]
    if kind == "path":
        os.startfile(target)  # noqa: S606
        return
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{target}"], close_fds=True)


def _from_powershell() -> list[dict[str, str]]:
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            timeout=40,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if not completed.stdout.strip():
        return []
    try:
        data: Any = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    apps = []
    for item in data:
        name = str(item.get("Name") or "").strip()
        app_id = str(item.get("AppID") or "").strip()
        if name and app_id:
            apps.append({"name": name, "target": app_id, "kind": "appid"})
    return apps


def _from_start_menu() -> list[dict[str, str]]:
    roots = [
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    ]
    found: list[dict[str, str]] = []
    for root in roots:
        if not root.exists():
            continue
        for lnk in root.rglob("*.lnk"):
            found.append({"name": lnk.stem, "target": str(lnk), "kind": "path"})
    return found


def _find_shortcut(query: str) -> Path | None:
    apps = [a for a in _from_start_menu() if a["kind"] == "path"]
    match = pick_app(query, apps)
    if match:
        return Path(match["target"])
    return None


def _read_cache() -> list[dict[str, str]]:
    if not CACHE.exists():
        return []
    age = os.path.getmtime(CACHE)
    import time

    if time.time() - age > 6 * 3600:
        return []
    try:
        data = json.loads(CACHE.read_text(encoding="utf-8"))
        if isinstance(data, list) and data:
            return data
    except json.JSONDecodeError:
        return []
    return []


def _score(query: str, name: str) -> float:
    q = query.lower().strip()
    n = name.lower()
    if q == n:
        return 1.0
    if n.startswith(q) or q.startswith(n):
        return 0.9
    if q in n:
        return 0.75 + min(len(q) / max(len(n), 1), 0.2)
    return SequenceMatcher(None, q, n).ratio()


def looks_like_url(text: str) -> bool:
    candidate = text.strip()
    if re.match(r"^https?://", candidate, re.I):
        return True
    match = re.match(r"^[\w-]+(?:\.[\w-]+)+(?:/.*)?$", candidate, re.I)
    if not match:
        return False
    host = candidate.split("/", 1)[0]
    suffix = host.rsplit(".", 1)[-1].lower()
    # "notepad.exe" and "run.bat" are programs, not web addresses.
    return suffix.isalpha() and len(suffix) >= 2 and suffix not in PROGRAM_SUFFIXES


def _expand_user_path(path: str) -> Path:
    cleaned = path.strip().strip('"')
    lowered = cleaned.lower()
    specials = {
        "downloads": Path.home() / "Downloads",
        "documents": Path.home() / "Documents",
        "desktop": Path.home() / "Desktop",
        "pictures": Path.home() / "Pictures",
        "music": Path.home() / "Music",
        "videos": Path.home() / "Videos",
        "home": Path.home(),
    }
    if lowered in specials:
        return specials[lowered]
    return Path(os.path.expandvars(os.path.expanduser(cleaned)))
