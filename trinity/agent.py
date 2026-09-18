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

MAX_ROUNDS = 8
MAX_HISTORY_MESSAGES = 48
KEEP_HISTORY_MESSAGES = 28


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
                        {
                            "role": "tool",
                            "tool_name": name,
                            "content": result,
                        }
                    )
                continue
            final = content
            if final:
                self.history.append({"role": "assistant", "content": final})
            yield ("trace", "Response ready")
            yield ("final", final or "Done.")
            return
        yield ("final", final or "I ran out of tool steps. Try asking in a smaller piece.")

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
