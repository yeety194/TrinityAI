from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import requests

class BrainError(RuntimeError):
    pass


class Brain:
    def __init__(
        self,
        base_url: str,
        model: str,
        temperature: float = 0.25,
        context_window: int = 16384,
    ) -> None:
        if not _is_loopback_url(base_url):
            raise ValueError("Trinity's local brain must use a loopback Ollama URL.")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.context_window = context_window

    def ping(self) -> tuple[bool, str]:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=3)
            response.raise_for_status()
            models = [m.get("name", "") for m in response.json().get("models", [])]
            if not models:
                return True, "Ollama is running, but no models are installed."
            names = ", ".join(models[:8])
            if any(self.model in name or name.startswith(self.model) for name in models):
                return True, f"Connected. Using {self.model}."
            return True, f"Ollama is up. Model '{self.model}' not found. Installed: {names}"
        except requests.RequestException as exc:
            return False, f"Cannot reach Ollama at {self.base_url}. {exc}"

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.context_window,
            },
        }
        if tools:
            payload["tools"] = tools
        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=(15, 300),
            )
        except requests.RequestException as exc:
            raise BrainError(f"Ollama request failed: {exc}") from exc
        if response.status_code == 404:
            raise BrainError(
                f"Model '{self.model}' is not installed. In a terminal run: ollama pull {self.model}"
            )
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise BrainError(f"Ollama returned unreadable JSON: {exc}") from exc
        if response.status_code >= 400:
            raise BrainError(data.get("error") or response.text[:400])
        if data.get("error"):
            raise BrainError(str(data["error"]))
        message = data.get("message") or {}
        if not isinstance(message, dict):
            raise BrainError("Ollama returned an empty message.")
        return message


def _is_loopback_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }
