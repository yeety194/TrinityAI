from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from trinity.agent import Agent, _force_from_hint, _parse_text_tools
from trinity.apps import looks_like_url, pick_app
from trinity.brain import Brain
from trinity.memory import Memory
from trinity.research import describe_weather_code
from trinity.router import routing_hint


class PickAppTests(unittest.TestCase):
    def test_exact_and_partial(self) -> None:
        apps = [
            {"name": "Google Chrome", "target": "chrome", "kind": "appid"},
            {"name": "Discord", "target": "discord", "kind": "appid"},
            {"name": "Notepad", "target": "notepad", "kind": "appid"},
        ]
        self.assertEqual(pick_app("discord", apps)["name"], "Discord")
        self.assertEqual(pick_app("Google Chrome", apps)["name"], "Google Chrome")
        self.assertEqual(pick_app("note", apps)["name"], "Notepad")

    def test_programs_are_not_treated_as_web_addresses(self) -> None:
        self.assertTrue(looks_like_url("github.com"))
        self.assertTrue(looks_like_url("https://example.org/page"))
        self.assertFalse(looks_like_url("notepad.exe"))
        self.assertFalse(looks_like_url("run.bat"))
        self.assertFalse(looks_like_url("discord"))


class MemoryTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(Path(tmp) / "t.db")
            self.assertIn("Remembered", mem.remember("user name", "Alex"))
            self.assertIn("Alex", mem.recall("name"))
            self.assertEqual(mem.list_facts(), [("user name", "Alex")])
            self.assertIn("Forgot", mem.forget("user name"))
            self.assertEqual(mem.recall("name"), "No matching memories.")

    def test_clear_personal_fact_is_captured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(Path(tmp) / "t.db")
            self.assertIn("Remembered", mem.capture_user_fact("My name is Alex."))
            self.assertIn("Alex", mem.recall("name"))

    def test_capture_stops_at_clause_break_and_keeps_both_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(Path(tmp) / "t.db")
            mem.capture_user_fact("My name is Alex and I live in Boston.")
            self.assertEqual(mem.get_fact("user name"), "Alex")
            self.assertEqual(mem.get_fact("home location"), "Boston")

    def test_preferences_accumulate_instead_of_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(Path(tmp) / "t.db")
            mem.capture_user_fact("I prefer dark mode.")
            mem.capture_user_fact("I prefer not to be interrupted while gaming.")
            mem.capture_user_fact("I prefer dark mode.")
            stored = mem.get_fact("preferences")
            self.assertEqual(
                stored, "dark mode; not to be interrupted while gaming"
            )


class LocalBrainTests(unittest.TestCase):
    def test_remote_brain_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Brain("https://remote.example", "hermes3:8b")


class ConversationTests(unittest.TestCase):
    def test_activity_trace_and_session_compaction(self) -> None:
        class StubBrain:
            def complete(self, messages, tools=None):
                return {"content": "Compact continuity note."}

        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(StubBrain(), Memory(Path(tmp) / "t.db"))
            events = list(agent.handle("Hello Trinity"))
            self.assertIn(("final", "Compact continuity note."), events)
            self.assertTrue(any(kind == "trace" for kind, _value in events))
            agent.history = [{"role": "user", "content": "Earlier topic"}] * 50
            agent._trim()
            self.assertEqual(agent.conversation_state()["messages"], 28)
            self.assertEqual(agent.conversation_state()["summary"], "Compact continuity note.")


class RouterTests(unittest.TestCase):
    def test_open_and_research(self) -> None:
        self.assertIn("open_app", routing_hint("launch Spotify") or "")
        self.assertIn("open_url", routing_hint("open github.com") or "")
        self.assertIsNotNone(routing_hint("search for SpaceX launch"))

    def test_sentences_starting_with_open_verbs_are_not_app_launches(self) -> None:
        self.assertIn("web_search", routing_hint("run a web search for SpaceX") or "")
        self.assertNotIn("open_app", routing_hint("start a timer for 10 minutes") or "")
        self.assertNotIn("open_app", routing_hint("open up about the weather") or "")

    def test_folder_requests_route_to_open_folder(self) -> None:
        self.assertIn("open_folder", routing_hint("open my downloads folder") or "")
        self.assertIn("open_folder", routing_hint("open desktop") or "")

    def test_weather_without_a_city_uses_remembered_home(self) -> None:
        hint = routing_hint("What's the weather where I live?", "Denver")
        self.assertEqual(hint, "Call weather with location=Denver")
        self.assertEqual(_force_from_hint(hint), ("weather", {"location": "Denver"}))

    def test_weather_with_a_named_city_keeps_that_city(self) -> None:
        hint = routing_hint("What's the weather in Miami?", "Denver") or ""
        self.assertNotIn("location=Denver", hint)
        self.assertIsNone(_force_from_hint(hint))


class WeatherFormattingTests(unittest.TestCase):
    def test_codes_become_words(self) -> None:
        self.assertEqual(describe_weather_code(2), "partly cloudy")
        self.assertEqual(describe_weather_code(95), "thunderstorms")
        self.assertEqual(describe_weather_code(None), "unknown conditions")


class ToolParsingTests(unittest.TestCase):
    def test_json_tool_call_with_nested_arguments_is_recovered(self) -> None:
        calls = _parse_text_tools('{"name": "web_search", "arguments": {"query": "spacex"}}')
        self.assertEqual(calls[0]["function"]["name"], "web_search")
        self.assertEqual(calls[0]["function"]["arguments"], {"query": "spacex"})

    def test_prose_and_unknown_tools_are_ignored(self) -> None:
        self.assertEqual(_parse_text_tools("Sure, here is the plan."), [])
        self.assertEqual(_parse_text_tools('{"name": "totally_fake_tool"}'), [])

    def test_forced_hint_must_name_a_real_tool(self) -> None:
        self.assertEqual(_force_from_hint("Call open_app with name=discord"), ("open_app", {"name": "discord"}))
        self.assertIsNone(_force_from_hint("Call make_coffee with strength=strong"))


if __name__ == "__main__":
    unittest.main()
