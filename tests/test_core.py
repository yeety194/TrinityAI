from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from trinity.agent import Agent, _force_from_hint, _parse_text_tools
from trinity.apps import looks_like_url, pick_app
from trinity.brain import Brain
from trinity.cloud import from_openai_message, to_openai_messages
from trinity.config import build_system_prompt
from trinity.memory import Memory
from trinity.messaging import new_topic
from trinity.research import describe_weather_code
from trinity.router import routing_hint
from trinity.skills import REMOTE_SAFE_TOOLS, _arg, dispatch, schemas
from trinity.sync import Presence


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


class PhoneLinkTests(unittest.TestCase):
    def test_topics_are_unguessable_and_distinct(self) -> None:
        first, second = new_topic("in"), new_topic("in")
        self.assertNotEqual(first, second)
        self.assertGreater(len(first), 24)

    def test_remote_toolset_excludes_pc_control(self) -> None:
        for blocked in ("open_app", "open_folder", "clipboard_get", "search_files"):
            self.assertNotIn(blocked, REMOTE_SAFE_TOOLS)
        for allowed in ("web_search", "remember", "send_text"):
            self.assertIn(allowed, REMOTE_SAFE_TOOLS)

    def test_blocked_tool_is_refused_with_an_explanation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = Memory(Path(tmp) / "t.db")
            result = dispatch("open_app", {"name": "discord"}, memory, REMOTE_SAFE_TOOLS)
        self.assertIn("only works at the PC", result)

    def test_remote_schemas_are_filtered(self) -> None:
        names = {tool["function"]["name"] for tool in schemas(REMOTE_SAFE_TOOLS)}
        self.assertEqual(names, set(REMOTE_SAFE_TOOLS))
        self.assertIn("open_app", {tool["function"]["name"] for tool in schemas()})

    def test_desktop_only_request_gets_a_clear_answer_without_the_model(self) -> None:
        class NeverCalledBrain:
            def complete(self, messages, tools=None):
                raise AssertionError("the model should not be consulted")

        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(
                NeverCalledBrain(),
                Memory(Path(tmp) / "t.db"),
                allowed_tools=REMOTE_SAFE_TOOLS,
                remote=True,
            )
            finals = [p for kind, p in agent.handle("Open Discord for me") if kind == "final"]
        self.assertIn("only works at the PC", finals[0])
        self.assertIn("research", finals[0])

    def test_empty_reply_does_not_claim_success(self) -> None:
        class SilentBrain:
            def complete(self, messages, tools=None):
                return {"content": ""}

        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(SilentBrain(), Memory(Path(tmp) / "t.db"))
            finals = [p for kind, p in agent.handle("Open Discord") if kind == "final"]
        self.assertTrue(finals)
        self.assertNotEqual(finals[0], "Done.")
        self.assertIn("don't have an answer", finals[0])

    def test_remote_prompt_states_the_desktop_limits(self) -> None:
        prompt = build_system_prompt("(empty)", "", remote=True)
        self.assertIn("phone link", prompt)
        self.assertIn("clipboard", prompt)
        self.assertNotIn("phone link, not at the PC", build_system_prompt("(empty)"))


class MemorySyncTests(unittest.TestCase):
    def test_newest_edit_wins_in_both_directions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Memory(Path(tmp) / "a.db")
            cloud = Memory(Path(tmp) / "b.db")
            desktop.remember("user name", "Casey")
            cloud.remember("favorite editor", "Cursor")
            cloud.merge_state(desktop.export_state())
            desktop.merge_state(cloud.export_state())
            self.assertEqual(desktop.get_fact("favorite editor"), "Cursor")
            self.assertEqual(cloud.get_fact("user name"), "Casey")

    def test_deleted_fact_is_not_resurrected_by_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Memory(Path(tmp) / "a.db")
            cloud = Memory(Path(tmp) / "b.db")
            desktop.remember("home location", "Denver")
            cloud.merge_state(desktop.export_state())
            self.assertEqual(cloud.get_fact("home location"), "Denver")
            desktop.forget("home location")
            cloud.merge_state(desktop.export_state())
            self.assertIsNone(cloud.get_fact("home location"))
            desktop.merge_state(cloud.export_state())
            self.assertIsNone(desktop.get_fact("home location"))

    def test_notes_merge_without_duplicating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Memory(Path(tmp) / "a.db")
            cloud = Memory(Path(tmp) / "b.db")
            desktop.add_note("buy coffee")
            cloud.merge_state(desktop.export_state())
            cloud.merge_state(desktop.export_state())
            self.assertEqual(cloud.export_state()["notes"].__len__(), 1)


class PresenceTests(unittest.TestCase):
    def test_twin_defers_while_the_desktop_is_awake(self) -> None:
        presence = Presence(grace_seconds=60)
        self.assertFalse(presence.desktop_awake)
        presence.record()
        self.assertTrue(presence.desktop_awake)
        presence.record(at=time.time() - 120)
        self.assertFalse(presence.desktop_awake)


class CloudFormatTests(unittest.TestCase):
    def test_tool_calls_and_results_are_paired_for_openai(self) -> None:
        history = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "weather in Denver"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "weather", "arguments": {"location": "Denver"}}}
                ],
            },
            {"role": "tool", "tool_name": "weather", "content": "clear, 70F"},
        ]
        converted = to_openai_messages(history)
        call_id = converted[2]["tool_calls"][0]["id"]
        self.assertEqual(converted[2]["tool_calls"][0]["function"]["arguments"], '{"location": "Denver"}')
        self.assertEqual(converted[3]["tool_call_id"], call_id)

    def test_openai_reply_is_converted_back(self) -> None:
        message = from_openai_message(
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "function": {"name": "web_search", "arguments": '{"query": "spacex"}'},
                    }
                ],
            }
        )
        self.assertEqual(message["tool_calls"][0]["function"]["arguments"], {"query": "spacex"})
        self.assertEqual(message["content"], "")


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


class ToolArgumentTests(unittest.TestCase):
    def test_renamed_parameters_still_work(self) -> None:
        self.assertEqual(_arg({"query": "Portugal"}, "topic", "query"), "Portugal")
        self.assertEqual(_arg({"city": "Miami"}, "location", "city"), "Miami")
        self.assertEqual(_arg({}, "path", default="home"), "home")
        self.assertEqual(_arg({"name": "  Discord "}, "name"), "Discord")


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
