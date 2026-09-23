"""Windows keyboard and mouse automation via SendInput.

Off by default. Enable with ``automation.enabled`` in config.json or the
AUTOMATION switch in the Trinity side rail. Tools refuse to run until then.
"""

from __future__ import annotations

import platform
import time
from typing import Any

# Virtual-key codes for named keys (Windows).
VK: dict[str, int] = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "pause": 0x13,
    "caps_lock": 0x14,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "page_up": 0x21,
    "page_down": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "print_screen": 0x2C,
    "insert": 0x2D,
    "delete": 0x2E,
    "win": 0x5B,
    "windows": 0x5B,
    "cmd": 0x5B,
    "super": 0x5B,
    "apps": 0x5D,
    "numpad0": 0x60,
    "numpad1": 0x61,
    "numpad2": 0x62,
    "numpad3": 0x63,
    "numpad4": 0x64,
    "numpad5": 0x65,
    "numpad6": 0x66,
    "numpad7": 0x67,
    "numpad8": 0x68,
    "numpad9": 0x69,
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
    "num_lock": 0x90,
    "scroll_lock": 0x91,
    ";": 0xBA,
    "=": 0xBB,
    ",": 0xBC,
    "-": 0xBD,
    ".": 0xBE,
    "/": 0xBF,
    "`": 0xC0,
    "[": 0xDB,
    "\\": 0xDC,
    "]": 0xDD,
    "'": 0xDE,
}

MODIFIERS = {"ctrl", "control", "alt", "shift", "win", "windows", "cmd", "super"}

CONSENT_OFF = (
    "Keyboard/mouse automation is off. Turn on the AUTOMATION switch in the "
    "Trinity side rail (or set automation.enabled=true in config.json), then try again."
)
NOT_WINDOWS = "Keyboard/mouse automation only runs on Windows."


def is_enabled() -> bool:
    from trinity.config import load_config

    block = load_config().get("automation")
    if not isinstance(block, dict):
        return False
    return bool(block.get("enabled"))


def set_enabled(enabled: bool) -> None:
    from trinity.config import load_config, save_config

    cfg = load_config()
    automation = cfg.setdefault("automation", {})
    if not isinstance(automation, dict):
        automation = {}
        cfg["automation"] = automation
    automation["enabled"] = bool(enabled)
    save_config(cfg)


def require_ready() -> str | None:
    """Return an error message if automation must not run, else None."""
    if not is_enabled():
        return CONSENT_OFF
    if platform.system() != "Windows":
        return NOT_WINDOWS
    return None


def mouse_move(x: int, y: int) -> str:
    err = require_ready()
    if err:
        return err
    _set_cursor_pos(int(x), int(y))
    return f"Moved mouse to ({int(x)}, {int(y)})."


def mouse_click(button: str = "left", clicks: int = 1, x: int | None = None, y: int | None = None) -> str:
    err = require_ready()
    if err:
        return err
    if x is not None and y is not None:
        _set_cursor_pos(int(x), int(y))
        time.sleep(0.03)
    name = (button or "left").strip().lower()
    down, up = _button_flags(name)
    if down is None:
        return f"Unknown mouse button '{button}'. Use left, right, or middle."
    count = max(1, min(int(clicks or 1), 5))
    for _ in range(count):
        _mouse_event(down)
        time.sleep(0.02)
        _mouse_event(up)
        time.sleep(0.04)
    where = f" at ({int(x)}, {int(y)})" if x is not None and y is not None else ""
    plural = "s" if count > 1 else ""
    return f"Clicked {name} {count} time{plural}{where}."


def mouse_scroll(delta: int, x: int | None = None, y: int | None = None) -> str:
    err = require_ready()
    if err:
        return err
    if x is not None and y is not None:
        _set_cursor_pos(int(x), int(y))
        time.sleep(0.03)
    steps = int(delta)
    if steps == 0:
        return "Scroll delta was 0; nothing sent."
    # Windows expects multiples of WHEEL_DELTA (120). Positive = up.
    _mouse_wheel(steps * 120)
    direction = "up" if steps > 0 else "down"
    return f"Scrolled {direction} by {abs(steps)} notch(es)."


def mouse_position() -> str:
    err = require_ready()
    if err:
        return err
    x, y = _get_cursor_pos()
    return f"Mouse is at ({x}, {y})."


def screen_size() -> str:
    err = require_ready()
    if err:
        return err
    width, height = _get_screen_size()
    return f"Screen is {width}x{height} pixels."


def type_text(text: str, interval_ms: int = 0) -> str:
    err = require_ready()
    if err:
        return err
    body = text if isinstance(text, str) else str(text or "")
    if not body:
        return "Nothing to type."
    if len(body) > 4000:
        return "Text is too long to type in one go (max 4000 characters)."
    delay = max(0, min(int(interval_ms or 0), 200)) / 1000.0
    for ch in body:
        if ch == "\n":
            _tap_vk(VK["enter"])
        elif ch == "\t":
            _tap_vk(VK["tab"])
        else:
            _type_unicode(ch)
        if delay:
            time.sleep(delay)
    return f"Typed {len(body)} character(s)."


def key_press(key: str) -> str:
    err = require_ready()
    if err:
        return err
    code = _resolve_vk(key)
    if code is None:
        return f"Unknown key '{key}'."
    _tap_vk(code)
    return f"Pressed {key.strip().lower()}."


def hotkey(*keys: str) -> str:
    err = require_ready()
    if err:
        return err
    parts = _normalize_hotkey_parts(keys)
    if not parts:
        return "Need at least one key for a hotkey."
    codes: list[int] = []
    for part in parts:
        code = _resolve_vk(part)
        if code is None:
            return f"Unknown key '{part}' in hotkey."
        codes.append(code)
    for code in codes:
        _key_down(code)
        time.sleep(0.01)
    for code in reversed(codes):
        _key_up(code)
        time.sleep(0.01)
    return f"Sent hotkey {'+'.join(parts)}."


def parse_hotkey(raw: str) -> list[str]:
    """Split 'ctrl+shift+s' or 'ctrl s' into key names."""
    text = (raw or "").strip().lower()
    if not text:
        return []
    if "+" in text:
        return [p.strip() for p in text.split("+") if p.strip()]
    return [p for p in text.replace(",", " ").split() if p]


# ------------------------------------------------------------------ Win32 I/O


def _button_flags(name: str) -> tuple[int | None, int | None]:
    mapping = {
        "left": (0x0002, 0x0004),  # MOUSEEVENTF_LEFTDOWN / LEFTUP
        "right": (0x0008, 0x0010),
        "middle": (0x0020, 0x0040),
        "l": (0x0002, 0x0004),
        "r": (0x0008, 0x0010),
        "m": (0x0020, 0x0040),
    }
    return mapping.get(name, (None, None))


def _normalize_hotkey_parts(keys: tuple[str, ...] | list[str]) -> list[str]:
    parts: list[str] = []
    for item in keys:
        if not item:
            continue
        if isinstance(item, str) and ("+" in item or " " in item or "," in item):
            parts.extend(parse_hotkey(item))
        else:
            parts.append(str(item).strip().lower())
    # Stable order: modifiers first, then the rest in given order.
    mods = [p for p in parts if p in MODIFIERS]
    rest = [p for p in parts if p not in MODIFIERS]
    seen: set[str] = set()
    ordered: list[str] = []
    for part in mods + rest:
        if part in seen:
            continue
        seen.add(part)
        ordered.append(part)
    return ordered


def _resolve_vk(name: str) -> int | None:
    key = (name or "").strip().lower()
    if not key:
        return None
    if key in VK:
        return VK[key]
    if len(key) == 1:
        ch = key.upper()
        if "A" <= ch <= "Z" or "0" <= ch <= "9":
            return ord(ch)
    return None


def _user32() -> Any:
    import ctypes

    return ctypes.windll.user32


def _set_cursor_pos(x: int, y: int) -> None:
    if not _user32().SetCursorPos(int(x), int(y)):
        raise OSError("SetCursorPos failed")


def _get_cursor_pos() -> tuple[int, int]:
    import ctypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT()
    if not _user32().GetCursorPos(ctypes.byref(pt)):
        raise OSError("GetCursorPos failed")
    return int(pt.x), int(pt.y)


def _get_screen_size() -> tuple[int, int]:
    user32 = _user32()
    return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))


def _mouse_event(flags: int, data: int = 0) -> None:
    _user32().mouse_event(flags, 0, 0, data, 0)


def _mouse_wheel(amount: int) -> None:
    # MOUSEEVENTF_WHEEL = 0x0800
    _mouse_event(0x0800, int(amount))


def tap_virtual_key(code: int) -> None:
    """Press one virtual-key code (no consent gate). Used by media_control too."""
    if platform.system() != "Windows":
        raise OSError(NOT_WINDOWS)
    _key_down(code)
    time.sleep(0.01)
    _key_up(code)


def _tap_vk(code: int) -> None:
    tap_virtual_key(code)


def _key_down(code: int) -> None:
    _user32().keybd_event(int(code) & 0xFF, 0, 0, 0)


def _key_up(code: int) -> None:
    _user32().keybd_event(int(code) & 0xFF, 0, 2, 0)  # KEYEVENTF_KEYUP


def _type_unicode(ch: str) -> None:
    """Type one Unicode character with SendInput KEYEVENTF_UNICODE."""
    import ctypes
    from ctypes import wintypes

    INPUT_KEYBOARD = 1
    KEYEVENTF_UNICODE = 0x0004
    KEYEVENTF_KEYUP = 0x0002

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT(ctypes.Structure):
        class _I(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]

        _anonymous_ = ("i",)
        _fields_ = [("type", wintypes.DWORD), ("i", _I)]

    extra = ctypes.pointer(ctypes.c_ulong(0))
    down = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE, 0, extra))
    up = INPUT(
        type=INPUT_KEYBOARD,
        ki=KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, extra),
    )
    arr = (INPUT * 2)(down, up)
    sent = _user32().SendInput(2, ctypes.byref(arr), ctypes.sizeof(INPUT))
    if sent != 2:
        raise OSError(f"SendInput typed {sent}/2 events for {ch!r}")
