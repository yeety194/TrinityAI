from __future__ import annotations

import re

from trinity.apps import looks_like_url

OPEN_RE = re.compile(
    r"^(?:hey |ok |okay )?(?:trinity[, ]+)?(?:please )?(?:open|launch|start|run)\s+(.+)$",
    re.I,
)

FOLDER_NAMES = {"desktop", "documents", "downloads", "pictures", "music", "videos", "home"}
FOLDER_FILLERS = {"my", "the", "folder", "directory"}

# "open"/"run" also start ordinary sentences, so these words mean the request is a
# phrase rather than an app name and must not be forced into open_app.
PHRASE_MARKERS = {
    "search",
    "google",
    "research",
    "timer",
    "reminder",
    "alarm",
    "about",
    "query",
    "news",
    "weather",
    "summary",
    "summarize",
    "through",
}
LEADING_FILLERS = {"a", "an", "the", "up", "over", "again", "down", "out", "me", "some"}
MAX_APP_WORDS = 4

# "what's the weather" with no city means the user's own location.
SELF_LOCATION_RE = re.compile(
    r"\b(where i (?:live|am)|here|outside|my (?:area|city|town|place|location)|at home|locally)\b",
    re.I,
)


def routing_hint(text: str, home_location: str = "") -> str | None:
    lowered = text.strip().lower()

    open_match = OPEN_RE.match(lowered)
    if open_match:
        hint = _open_hint(open_match.group(1))
        if hint:
            return hint

    if re.search(r"\b(remember|don't forget|dont forget|note that|my name is)\b", lowered):
        return "If this is a durable fact, call remember. If it is a freeform reminder, call add_note."

    if re.search(r"\b(search|look up|lookup|research|google|find out|what is|who is|news)\b", lowered):
        return "Use web_search and wikipedia/read_url as needed. Do not guess current facts."

    if re.search(r"\bweather\b", lowered):
        return _weather_hint(lowered, home_location)

    if re.search(r"\b(what time|what'?s the time|date today)\b", lowered):
        return "Call now."

    if re.search(
        r"\b(click|double[- ]?click|right[- ]?click|type\b|"
        r"press (?:enter|tab|escape|esc|ctrl)|hotkey|move (?:the )?mouse|"
        r"scroll (?:up|down)|keyboard|mouse)\b",
        lowered,
    ):
        return (
            "Use mouse_move/mouse_click/mouse_scroll/type_text/key_press/hotkey as needed. "
            "If automation is off, tell the user to enable the AUTOMATION switch."
        )

    return None


def _weather_hint(lowered: str, home_location: str) -> str:
    if not home_location:
        return "Call weather with the place they named, or ask for a city if none."
    if SELF_LOCATION_RE.search(lowered) or not re.search(r"\bin\s+\w", lowered):
        return f"Call weather with location={home_location}"
    return (
        "Call weather with the place they named. Their own home location is "
        f"{home_location}; never invent a different city."
    )


def _open_hint(target: str) -> str | None:
    cleaned = target.strip(" .!?\"'")
    if not cleaned:
        return None

    if looks_like_url(cleaned):
        return f"Call open_url with url={cleaned}"

    words = cleaned.split()
    folder_words = [word for word in words if word not in FOLDER_FILLERS]
    if len(folder_words) == 1 and folder_words[0] in FOLDER_NAMES:
        return f"Call open_folder with path={folder_words[0]}"

    if words[0] in LEADING_FILLERS:
        return None
    if any(word in PHRASE_MARKERS for word in words):
        return None
    if len(words) > MAX_APP_WORDS:
        return None
    return f"Call open_app with name={cleaned}"
