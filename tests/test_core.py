from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from trinity.agent import (
    Agent,
    _force_from_hint,
    _looks_failed,
    _parse_text_tools,
    needs_plan,
)
from trinity.apps import looks_like_url, pick_app
from trinity.brain import Brain
from trinity.memory import Memory
from trinity.reasoning import CalculationError, calculate, parse_when
from trinity.research import describe_weather_code
from trinity.router import routing_hint
from trinity.skills import _arg, dispatch, schemas


class PickAppTests(unittest.TestCase):
    def test_exact_and_partial(self) -> None:
        apps = [
            {"name": "Google Chrome", "target": "chrome", "kind": "appid"},
            {"name": "Spotify", "target": "spotify", "kind": "appid"},
            {"name": "Notepad", "target": "notepad", "kind": "appid"},
        ]
        self.assertEqual(pick_app("spotify", apps)["name"], "Spotify")
        self.assertEqual(pick_app("Google Chrome", apps)["name"], "Google Chrome")
        self.assertEqual(pick_app("note", apps)["name"], "Notepad")

    def test_programs_are_not_treated_as_web_addresses(self) -> None:
        self.assertTrue(looks_like_url("github.com"))
        self.assertTrue(looks_like_url("https://example.org/page"))
        self.assertFalse(looks_like_url("notepad.exe"))
        self.assertFalse(looks_like_url("run.bat"))
        self.assertFalse(looks_like_url("spotify"))


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


class ReasoningTests(unittest.TestCase):
    def test_empty_reply_does_not_claim_success(self) -> None:
        class SilentBrain:
            def complete(self, messages, tools=None):
                return {"content": ""}

        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(SilentBrain(), Memory(Path(tmp) / "t.db"))
            finals = [p for kind, p in agent.handle("Open Spotify") if kind == "final"]
        self.assertTrue(finals)
        self.assertNotEqual(finals[0], "Done.")
        self.assertIn("don't have an answer", finals[0])

    def test_empty_reply_is_nudged_once_before_giving_up(self) -> None:
        class StallingBrain:
            def __init__(self) -> None:
                self.calls = 0

            def complete(self, messages, tools=None):
                self.calls += 1
                if self.calls == 1:
                    return {"content": ""}
                return {"content": "Here is the real answer."}

        brain = StallingBrain()
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(brain, Memory(Path(tmp) / "t.db"))
            finals = [p for kind, p in agent.handle("tell me something") if kind == "final"]
        self.assertEqual(finals, ["Here is the real answer."])
        self.assertEqual(brain.calls, 2)

    def test_plans_only_for_multi_step_requests(self) -> None:
        self.assertTrue(needs_plan("research the latest mars mission and then summarize it"))
        self.assertTrue(needs_plan("compare the two laptops I looked at yesterday please"))
        self.assertFalse(needs_plan("open spotify"))
        self.assertFalse(needs_plan("what time is it"))

    def test_failed_tool_results_are_recognized(self) -> None:
        self.assertTrue(_looks_failed("No topic given."))
        self.assertTrue(_looks_failed("Could not read that file"))
        self.assertTrue(_looks_failed(""))
        self.assertFalse(_looks_failed("Lisbon is the capital of Portugal."))

    def test_recovery_suggests_a_different_tool(self) -> None:
        class Quiet:
            def complete(self, messages, tools=None):
                return {"content": "ok"}

        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(Quiet(), Memory(Path(tmp) / "t.db"))
            advice = agent._recover("wikipedia", "No topic given.", {})
        self.assertIn("web_search", advice)

    def test_recovery_gives_up_after_repeated_failures(self) -> None:
        class Quiet:
            def complete(self, messages, tools=None):
                return {"content": "ok"}

        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(Quiet(), Memory(Path(tmp) / "t.db"))
            failures: dict[str, int] = {}
            for _ in range(2):
                self.assertTrue(agent._recover("weather", "Could not geocode", failures))
            self.assertEqual(agent._recover("weather", "Could not geocode", failures), "")


class CalculatorTests(unittest.TestCase):
    def test_arithmetic_and_words(self) -> None:
        self.assertEqual(calculate("2+2"), "4")
        self.assertEqual(calculate("18 percent of 2450"), "441")
        self.assertEqual(calculate("sqrt(196) + 12"), "26")
        self.assertEqual(calculate("1200 divided by 16"), "75")

    def test_refuses_anything_that_is_not_maths(self) -> None:
        for bad in ("__import__('os').system('dir')", "open('x')", "[1,2,3]"):
            with self.assertRaises(CalculationError):
                calculate(bad)


class ReminderTests(unittest.TestCase):
    def test_relative_and_clock_times(self) -> None:
        now = datetime(2026, 9, 18, 10, 0).astimezone()
        self.assertEqual(parse_when("in 20 minutes", now), now + timedelta(minutes=20))
        self.assertEqual(parse_when("in an hour", now), now + timedelta(hours=1))
        evening = parse_when("at 7pm", now)
        self.assertIsNotNone(evening)
        self.assertEqual(evening.hour, 19)
        self.assertIsNone(parse_when("sometime soonish", now))

    def test_reminders_are_stored_listed_and_fired_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = Memory(Path(tmp) / "t.db")
            past = datetime.now(timezone.utc) - timedelta(minutes=1)
            memory.add_reminder("stretch", past)
            memory.add_reminder("later thing", datetime.now(timezone.utc) + timedelta(hours=5))
            self.assertEqual(len(memory.pending_reminders()), 2)
            due = memory.due_reminders()
            self.assertEqual([body for _i, body in due], ["stretch"])
            memory.mark_reminder_fired(due[0][0])
            self.assertEqual(memory.due_reminders(), [])
            self.assertIn("later thing", memory.cancel_reminder("later"))
            self.assertEqual(memory.pending_reminders(), [])


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
        self.assertEqual(_arg({"name": "  Spotify "}, "name"), "Spotify")


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
        self.assertEqual(_force_from_hint("Call open_app with name=spotify"), ("open_app", {"name": "spotify"}))
        self.assertIsNone(_force_from_hint("Call make_coffee with strength=strong"))


class SpeechKeyTests(unittest.TestCase):
    def test_normalize_strips_headers_quotes_and_finds_sk_key(self) -> None:
        from trinity.speech import normalize_api_key

        self.assertEqual(normalize_api_key('  "sk_abc123DEF"  '), "sk_abc123DEF")
        self.assertEqual(normalize_api_key("xi-api-key: sk_abc123DEF"), "sk_abc123DEF")
        self.assertEqual(normalize_api_key("Bearer sk_abc123DEF"), "sk_abc123DEF")
        self.assertEqual(
            normalize_api_key(
                "curl https://api.elevenlabs.io/v1/user -H 'xi-api-key: sk_abc123DEF'"
            ),
            "sk_abc123DEF",
        )

    def test_voice_id_is_rejected_as_a_key(self) -> None:
        from trinity.speech import key_problem

        self.assertIn("Voice ID", key_problem("21m00Tcm4TlvDq8ikWAM") or "")
        self.assertIsNone(key_problem("sk_" + ("a" * 48)))

    def test_explain_keeps_residency_and_401_messages(self) -> None:
        from trinity.speech import explain_response

        class Fake:
            def __init__(self, code: int, payload: dict | None, text: str = "") -> None:
                self.status_code = code
                self._payload = payload
                self.text = text

            def json(self) -> dict:
                if self._payload is None:
                    raise ValueError("no json")
                return self._payload

        residency = explain_response(
            Fake(
                401,
                {
                    "detail": {
                        "status": "invalid_api_key",
                        "message": "The API key used is for a data residency stack, however this server is a global server.",
                    }
                },
            )
        )
        self.assertIn("residency", residency.lower())
        rejected = explain_response(
            Fake(401, {"detail": {"status": "invalid_api_key", "message": "Invalid API key"}})
        )
        self.assertIn("Invalid API key", rejected)
        self.assertIn("Developers", explain_response(Fake(401, None, "")))

    def test_check_uses_the_api_error_instead_of_could_not_reach(self) -> None:
        from trinity.speech import ElevenLabsVoice, SpeechError

        class Fake:
            status_code = 401
            text = ""

            def json(self) -> dict:
                return {"detail": {"status": "invalid_api_key", "message": "Invalid API key"}}

            def raise_for_status(self) -> None:
                raise AssertionError("HTTP errors should not be wrapped as connection failures")

        voice = ElevenLabsVoice("sk_" + ("b" * 48), "21m00Tcm4TlvDq8ikWAM")
        with patch("trinity.speech.requests.get", return_value=Fake()):
            with self.assertRaises(SpeechError) as raised:
                voice.check()
        self.assertIn("Invalid API key", str(raised.exception))
        self.assertNotIn("Could not reach", str(raised.exception))


class SecretStorageTests(unittest.TestCase):
    def test_saved_secret_wins_over_environment(self) -> None:
        import trinity.config as cfg

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secrets = root / "secrets.json"
            secrets.write_text('{"elevenlabs_api_key": "sk_from_file"}', encoding="utf-8")
            old_secrets, old_config, old_data = cfg.SECRETS_PATH, cfg.CONFIG_PATH, cfg.DATA_DIR
            cfg.SECRETS_PATH = secrets
            cfg.CONFIG_PATH = root / "config.json"
            cfg.DATA_DIR = root
            try:
                with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "sk_from_env"}):
                    self.assertEqual(
                        cfg.load_secret("elevenlabs_api_key", "ELEVENLABS_API_KEY"),
                        "sk_from_file",
                    )
            finally:
                cfg.SECRETS_PATH = old_secrets
                cfg.CONFIG_PATH = old_config
                cfg.DATA_DIR = old_data

    def test_save_config_moves_a_key_out_of_config_json(self) -> None:
        import trinity.config as cfg

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_secrets, old_config, old_data = cfg.SECRETS_PATH, cfg.CONFIG_PATH, cfg.DATA_DIR
            cfg.DATA_DIR = root
            cfg.SECRETS_PATH = root / "secrets.json"
            cfg.CONFIG_PATH = root / "config.json"
            try:
                cfg.save_config(
                    {"voice": {"engine": "elevenlabs", "elevenlabs_api_key": "sk_should_move"}}
                )
                saved = cfg.CONFIG_PATH.read_text(encoding="utf-8")
                self.assertNotIn("sk_should_move", saved)
                self.assertEqual(cfg.load_secret("elevenlabs_api_key"), "sk_should_move")
            finally:
                cfg.SECRETS_PATH = old_secrets
                cfg.CONFIG_PATH = old_config
                cfg.DATA_DIR = old_data


if __name__ == "__main__":
    unittest.main()
