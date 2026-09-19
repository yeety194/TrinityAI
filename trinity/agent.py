from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from trinity.brain import Brain, BrainError
from trinity.config import build_system_prompt
from trinity.memory import Memory
from trinity.router import routing_hint
from trinity.skills import dispatch, known_tools, pretty_args, schemas

MAX_ROUNDS = 12
MAX_HISTORY_MESSAGES = 48
KEEP_HISTORY_MESSAGES = 28

# When one source comes up empty, reach for the next one instead of giving up.
TOOL_FALLBACKS = {
    "wikipedia": "web_search",
    "web_search": "wikipedia",
    "read_url": "web_search",
    "open_app": "list_apps",
    "search_files": "list_directory",
    "read_document": "list_directory",
    "weather": "web_search",
    "write_own_code": "read_own_code",
    "patch_own_code": "read_own_code",
    "read_own_code": "list_own_code",
}

FAILURE_MARKERS = (
    "no topic given",
    "no results",
    "no matching",
    "could not",
    "couldn't",
    "failed",
    "unknown tool",
    "not found",
    "no web results",
    "nothing stored",
    "no location given",
    "path not found",
    "need a",
    "is not installed",
    "unreadable",
)

PLAN_TRIGGERS = (
    " and then ",
    " then ",
    " after that ",
    " also ",
    "compare",
    "research",
    "summarize",
    "plan ",
    "figure out",
    "look into",
    "deep dive",
    "step by step",
)


def needs_plan(text: str) -> bool:
    """Plan only for genuinely multi-part work; simple asks stay fast."""
    lowered = f" {text.strip().lower()} "
    if len(lowered.split()) < 8:
        return False
    if any(trigger in lowered for trigger in PLAN_TRIGGERS):
        return True
    return lowered.count("?") > 1


def _looks_failed(result: str) -> bool:
    lowered = result.strip().lower()
    if not lowered:
        return True
    return any(marker in lowered[:160] for marker in FAILURE_MARKERS)


class Agent:
    def __init__(self, brain: Brain, memory: Memory | None = None) -> None:
        self.brain = brain
        self.memory = memory or Memory()
        self.history: list[dict[str, Any]] = []
        self._session_summary = ""
        self._turn_count = 0

    def ping(self) -> tuple[bool, str]:
        return self.brain.ping()

    def reset_session(self) -> None:
        self.history.clear()
        self._session_summary = ""
        self._turn_count = 0

    def conversation_state(self) -> dict[str, str | int]:
        return {
            "turns": self._turn_count,
            "messages": len(self.history),
            "summary": self._session_summary or "Building context from this conversation.",
        }

    def handle(self, user_text: str) -> Iterator[tuple[str, str]]:
        self._turn_count += 1
        yield ("trace", f"Turn {self._turn_count}: received your request")
        captured = self.memory.capture_user_fact(user_text)
        if captured:
            yield ("activity", "saved a personal memory")
            yield ("trace", "Saved a clear personal fact to long-term memory")
        hint = routing_hint(user_text, self.memory.get_fact("home location") or "")
        payload = user_text
        if hint:
            payload = f"{user_text}\n\n[Trinity routing hint: {hint}]"
        self.history.append({"role": "user", "content": payload})
        self._trim()
        tools = schemas()
        final = ""
        plan: list[str] = []
        if needs_plan(user_text):
            plan = self._make_plan(user_text)
            if plan:
                yield ("trace", "Plan: " + " | ".join(plan))
                self.history.append(
                    {
                        "role": "assistant",
                        "content": "My plan:\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(plan, 1)),
                    }
                )
        failures: dict[str, int] = {}
        nudged_empty = False
        for _round in range(MAX_ROUNDS):
            messages = [
                {
                    "role": "system",
                    "content": build_system_prompt(
                        self.memory.format_for_prompt(), self._session_summary
                    ),
                },
                *self.history,
            ]
            try:
                message = self.brain.complete(messages, tools=tools)
            except BrainError as exc:
                yield ("error", str(exc))
                if self.history and self.history[-1].get("role") == "user":
                    self.history.pop()
                return
            tool_calls = message.get("tool_calls") or []
            content = (message.get("content") or "").strip()
            if not tool_calls:
                tool_calls = _parse_text_tools(content)
            if not tool_calls and _round == 0:
                forced = _force_from_hint(hint)
                if forced:
                    name, args = forced
                    tool_calls = [{"function": {"name": name, "arguments": args}}]
            if tool_calls:
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                }
                self.history.append(assistant_msg)
                for call in tool_calls:
                    fn = call.get("function") or {}
                    name = str(fn.get("name") or "")
                    raw_args = fn.get("arguments") or {}
                    args = _as_dict(raw_args)
                    yield ("status", name.replace("_", " "))
                    yield ("activity", f"{name} {pretty_args(args)}".strip())
                    yield ("trace", f"Using {name}: {pretty_args(args) or 'no arguments'}")
                    result = dispatch(name, args, self.memory)
                    yield ("trace", f"{name} finished: {_brief(result)}")
                    self.history.append(
                        {"role": "tool", "tool_name": name, "content": result}
                    )
                    advice = self._recover(name, result, failures)
                    if advice:
                        yield ("trace", f"Recovering: {_brief(advice)}")
                        self.history.append({"role": "user", "content": advice})
                continue
            if not content and not nudged_empty:
                # An empty reply usually means it stalled; ask once for a real answer.
                nudged_empty = True
                yield ("trace", "Empty reply; asking for a real answer")
                self.history.append(
                    {
                        "role": "user",
                        "content": (
                            "You returned nothing. Answer in plain words now, using what the "
                            "tools already returned. If you cannot do something, say so and "
                            "explain what you can do instead."
                        ),
                    }
                )
                continue
            final = content
            if final:
                self.history.append({"role": "assistant", "content": final})
            yield ("trace", "Response ready")
            # An empty reply must not sound like the request succeeded.
            yield ("final", final or "I don't have an answer for that one.")
            return
        yield ("final", final or "I ran out of tool steps. Try asking in a smaller piece.")

    def _make_plan(self, user_text: str) -> list[str]:
        """Sketch the steps for a multi-part request before touching any tools."""
        prompt = (
            "Break this request into at most four short imperative steps naming the tool for "
            "each step where one applies. Available tools: "
            f"{', '.join(sorted(known_tools()))}.\n"
            "Reply with one step per line and nothing else.\n\n"
            f"Request: {user_text}"
        )
        try:
            message = self.brain.complete(
                [
                    {"role": "system", "content": "You plan tool use for a desktop assistant."},
                    {"role": "user", "content": prompt},
                ]
            )
        except BrainError:
            return []
        steps = []
        for line in str(message.get("content") or "").splitlines():
            step = line.strip().lstrip("-*0123456789.) ").strip()
            if step:
                steps.append(step)
        return steps[:4]

    def _recover(self, name: str, result: str, failures: dict[str, int]) -> str:
        """Nudge toward a different approach when a tool comes back empty or broken."""
        if not _looks_failed(result):
            return ""
        failures[name] = failures.get(name, 0) + 1
        if failures[name] > 2:
            return ""
        alternative = TOOL_FALLBACKS.get(name)
        if alternative:
            return (
                f"{name} did not return anything useful. Try {alternative} instead, "
                "or correct the arguments and try once more."
            )
        return (
            f"{name} did not return anything useful. Check the arguments, try a different "
            "tool, or tell the user plainly that it did not work."
        )

    def _trim(self) -> None:
        if len(self.history) <= MAX_HISTORY_MESSAGES:
            return
        older = self.history[:-KEEP_HISTORY_MESSAGES]
        summary = self._summarize(older)
        if summary:
            self._session_summary = summary
        self.history = self.history[-KEEP_HISTORY_MESSAGES:]
        while self.history and self.history[0].get("role") == "tool":
            self.history.pop(0)

    def _summarize(self, messages: list[dict[str, Any]]) -> str:
        transcript = "\n".join(
            f"{item.get('role', 'unknown')}: {str(item.get('content', ''))[:800]}"
            for item in messages
            if item.get("content")
        )
        if not transcript:
            return self._session_summary
        prompt = (
            "Summarize this local assistant conversation for future continuity. Keep only "
            "user goals, confirmed facts, decisions, open threads, and important outcomes. "
            "Do not invent facts; use compact bullet points.\n\n"
            f"Existing summary:\n{self._session_summary or '(none)'}\n\n"
            f"Older conversation:\n{transcript}"
        )
        try:
            message = self.brain.complete(
                [
                    {"role": "system", "content": "You create accurate, compact session notes."},
                    {"role": "user", "content": prompt},
                ]
            )
        except BrainError:
            return self._session_summary
        return str(message.get("content") or self._session_summary).strip()


def _hint_tool(hint: str) -> str | None:
    match = re.match(r"Call (\w+)\b", hint)
    return match.group(1) if match else None


def _force_from_hint(hint: str | None) -> tuple[str, dict[str, Any]] | None:
    if not hint:
        return None
    match = re.match(r"Call (\w+) with (\w+)=(.+)$", hint)
    if not match or match.group(1) not in known_tools():
        return None
    return match.group(1), {match.group(2): match.group(3)}


def _parse_text_tools(content: str) -> list[dict[str, Any]]:
    """Recover a tool call that the model wrote as plain JSON text instead of a tool call."""
    for candidate in _json_objects(content):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        name = data.get("name") or data.get("tool")
        if isinstance(data.get("function"), dict):
            inner = data["function"]
            name = inner.get("name") or name
            data = {**data, "arguments": inner.get("arguments", data.get("arguments"))}
        if not isinstance(name, str) or name not in known_tools():
            continue
        args = data.get("arguments") or data.get("parameters") or data.get("args") or {}
        return [{"function": {"name": name, "arguments": args}}]
    return []


def _json_objects(text: str) -> Iterator[str]:
    """Yield balanced top-level JSON objects, so nested arguments survive."""
    if not text:
        return
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                yield text[start : index + 1]


def _as_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {"value": raw}
    return {}


def _brief(text: str, limit: int = 180) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"
