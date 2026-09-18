from __future__ import annotations

import json
import os
from typing import Any

import requests

from trinity.brain import BrainError

# Any OpenAI-compatible endpoint works: OpenAI, OpenRouter, Groq, a local proxy.
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


class CloudBrain:
    """Drop-in replacement for Brain that speaks the OpenAI chat-completions API.

    Only the hosted twin uses this. The desktop app never sends anything here.
    """

    def __init__(
        self,
        base_url: str = "",
        model: str = "",
        api_key: str = "",
        temperature: float = 0.3,
        timeout: int = 120,
    ) -> None:
        self.base_url = (base_url or os.environ.get("TRINITY_CLOUD_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("TRINITY_CLOUD_MODEL") or DEFAULT_MODEL
        self.api_key = api_key or os.environ.get("TRINITY_CLOUD_API_KEY") or ""
        self.temperature = temperature
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def ping(self) -> tuple[bool, str]:
        if not self.configured:
            return False, "No cloud API key is set (TRINITY_CLOUD_API_KEY)."
        try:
            response = requests.get(
                f"{self.base_url}/models", headers=self._headers(), timeout=10
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            return False, f"Cannot reach {self.base_url}: {exc}"
        return True, f"Connected to {self.base_url} using {self.model}."

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not self.configured:
            raise BrainError("No cloud API key is set (TRINITY_CLOUD_API_KEY).")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": to_openai_messages(messages),
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = tools
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise BrainError(f"Cloud request failed: {exc}") from exc
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise BrainError(f"Cloud returned unreadable JSON: {exc}") from exc
        if response.status_code >= 400:
            detail = (data.get("error") or {}).get("message") if isinstance(data, dict) else None
            raise BrainError(str(detail or response.text[:400]))
        choices = data.get("choices") or []
        if not choices:
            raise BrainError("Cloud returned no choices.")
        return from_openai_message(choices[0].get("message") or {})

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }


def to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Trinity's Ollama-shaped history into OpenAI's tool-call format."""
    converted: list[dict[str, Any]] = []
    pending_ids: list[str] = []
    for index, message in enumerate(messages):
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            calls = []
            pending_ids = []
            for position, call in enumerate(message["tool_calls"]):
                function = call.get("function") or {}
                call_id = str(call.get("id") or f"call_{index}_{position}")
                pending_ids.append(call_id)
                arguments = function.get("arguments")
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments or {})
                calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": function.get("name", ""), "arguments": arguments},
                    }
                )
            converted.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or None,
                    "tool_calls": calls,
                }
            )
        elif role == "tool":
            call_id = pending_ids.pop(0) if pending_ids else f"call_{index}_0"
            converted.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": str(message.get("content") or ""),
                }
            )
        else:
            converted.append({"role": role or "user", "content": str(message.get("content") or "")})
    return converted


def from_openai_message(message: dict[str, Any]) -> dict[str, Any]:
    """Convert an OpenAI reply back into the shape Agent expects."""
    result: dict[str, Any] = {"content": message.get("content") or ""}
    calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
        calls.append(
            {
                "id": call.get("id"),
                "function": {"name": function.get("name", ""), "arguments": arguments or {}},
            }
        )
    if calls:
        result["tool_calls"] = calls
    return result
