from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from trinity.memory import Memory
from trinity.router import routing_hint
from trinity.skills import dispatch, schemas


class AutomationTests(unittest.TestCase):
    def test_tools_are_registered(self) -> None:
        names = {item["function"]["name"] for item in schemas()}
        for tool in (
            "mouse_move",
            "mouse_click",
            "mouse_scroll",
            "mouse_position",
            "screen_size",
            "type_text",
            "key_press",
            "hotkey",
        ):
            self.assertIn(tool, names)

    def test_hotkey_parsing(self) -> None:
        from trinity import automation

        self.assertEqual(automation.parse_hotkey("ctrl+c"), ["ctrl", "c"])
        self.assertEqual(automation.parse_hotkey("ctrl shift s"), ["ctrl", "shift", "s"])
        self.assertEqual(automation.parse_hotkey(""), [])

    def test_disabled_by_default_and_refuses_tools(self) -> None:
        import trinity.config as cfg
        from trinity import automation

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_secrets, old_config, old_data = cfg.SECRETS_PATH, cfg.CONFIG_PATH, cfg.DATA_DIR
            cfg.DATA_DIR = root
            cfg.SECRETS_PATH = root / "secrets.json"
            cfg.CONFIG_PATH = root / "config.json"
            cfg.CONFIG_PATH.write_text("{}", encoding="utf-8")
            try:
                self.assertFalse(automation.is_enabled())
                with tempfile.TemporaryDirectory() as mem_tmp:
                    mem = Memory(Path(mem_tmp) / "t.db")
                    refused = dispatch("type_text", {"text": "hi"}, mem)
                self.assertIn("automation is off", refused.lower())
            finally:
                cfg.SECRETS_PATH = old_secrets
                cfg.CONFIG_PATH = old_config
                cfg.DATA_DIR = old_data

    def test_enabled_gate_still_requires_windows(self) -> None:
        import trinity.config as cfg
        from trinity import automation

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_secrets, old_config, old_data = cfg.SECRETS_PATH, cfg.CONFIG_PATH, cfg.DATA_DIR
            cfg.DATA_DIR = root
            cfg.SECRETS_PATH = root / "secrets.json"
            cfg.CONFIG_PATH = root / "config.json"
            try:
                automation.set_enabled(True)
                self.assertTrue(automation.is_enabled())
                with patch("trinity.automation.platform.system", return_value="Linux"):
                    self.assertIn("Windows", automation.require_ready() or "")
                    self.assertIn("Windows", automation.type_text("hi"))
            finally:
                cfg.SECRETS_PATH = old_secrets
                cfg.CONFIG_PATH = old_config
                cfg.DATA_DIR = old_data

    def test_media_control_does_not_need_automation_consent(self) -> None:
        import trinity.config as cfg

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_secrets, old_config, old_data = cfg.SECRETS_PATH, cfg.CONFIG_PATH, cfg.DATA_DIR
            cfg.DATA_DIR = root
            cfg.SECRETS_PATH = root / "secrets.json"
            cfg.CONFIG_PATH = root / "config.json"
            cfg.CONFIG_PATH.write_text('{"automation":{"enabled":false}}', encoding="utf-8")
            try:
                with tempfile.TemporaryDirectory() as mem_tmp:
                    mem = Memory(Path(mem_tmp) / "t.db")
                    with patch("trinity.automation.tap_virtual_key") as tap:
                        result = dispatch("media_control", {"action": "play"}, mem)
                self.assertIn("Sent play", result)
                tap.assert_called_once()
                self.assertNotIn("automation is off", result.lower())
            finally:
                cfg.SECRETS_PATH = old_secrets
                cfg.CONFIG_PATH = old_config
                cfg.DATA_DIR = old_data

    def test_router_hints_automation_requests(self) -> None:
        hint = routing_hint("click the save button") or ""
        self.assertIn("mouse_click", hint)
        hint2 = routing_hint("type hello into the box") or ""
        self.assertIn("type_text", hint2)


class ScrapGuardTests(unittest.TestCase):
    """WhatsApp / phone LINK / cloud twin must stay out of the tree."""

    def test_removed_modules_are_gone(self) -> None:
        import importlib.util

        for name in (
            "trinity.whatsapp",
            "trinity.messaging",
            "trinity.remote",
            "trinity.cloud",
            "trinity.sync",
        ):
            self.assertIsNone(importlib.util.find_spec(name), msg=f"{name} should not exist")

    def test_no_whatsapp_surface_in_tool_schemas(self) -> None:
        blob = " ".join(
            f"{item['function']['name']} {item['function']['description']}".lower()
            for item in schemas()
        )
        self.assertNotIn("whatsapp", blob)
        self.assertNotIn("ntfy", blob)
        self.assertNotIn("send_text", blob)


if __name__ == "__main__":
    unittest.main()
