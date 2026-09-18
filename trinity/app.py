from __future__ import annotations

import threading
import time
import tkinter as tk
from datetime import datetime
from typing import Any

import customtkinter as ctk

from trinity.agent import Agent
from trinity.brain import Brain
from trinity.config import DEFAULTS, ensure_config, save_config, save_secret
from trinity.hud import (
    ALERT,
    AMBER,
    CYAN,
    CYAN_DIM,
    MUTED,
    TEXT,
    VOID,
    Bracket,
    Reactor,
    Waveform,
)
from trinity.memory import Memory
from trinity.reasoning import describe_delay
from trinity.speech import SpeechError
from trinity.voice import ELEVENLABS_KEY, Voice

PANEL = "#080E16"
PANEL_EDGE = "#14283A"
RAISED = "#0E1B28"
MONO = "Consolas"

BOOT_LINES = (
    "TRINITY CORE ONLINE",
    "LOCAL REASONING ENGINE LINKED",
    "MEMORY ARCHIVE MOUNTED",
    "TOOL BUS NOMINAL",
)


class TrinityApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = ensure_config()
        self.memory = Memory()
        self._startup_warning = ""
        self.agent = Agent(self._build_brain(), self.memory)
        self.voice = Voice()
        self.voice.set_handlers(self._on_heard, self._set_status)
        self._stream_lock = threading.Lock()
        self._busy = False
        self._trace_lines: list[str] = []
        self._cloud_voices: dict[str, str] = {}

        ctk.set_appearance_mode("dark")
        self.title("TRINITY")
        self.geometry("1280x820")
        self.minsize(1080, 700)
        self.configure(fg_color=VOID)
        if self.cfg["ui"].get("always_on_top"):
            self.attributes("-topmost", True)

        self._build()
        self._refresh_memory()
        self._refresh_brain_state()
        self.after(150, self._boot_sequence)
        self.after(400, self._boot_check)
        self.after(700, self._warm_apps)
        self.after(1500, self._telemetry_tick)
        self.after(2000, self._reminder_tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_brain(self) -> Brain:
        llm = self.cfg["llm"]
        settings = (
            llm["model"],
            float(llm["temperature"]),
            int(llm.get("context_window", 16384)),
        )
        try:
            return Brain(llm["base_url"], *settings)
        except ValueError as exc:
            fallback = DEFAULTS["llm"]["base_url"]
            self._startup_warning = f"{exc} Using {fallback} instead."
            return Brain(fallback, *settings)

    # ---------------------------------------------------------------- layout

    def _build(self) -> None:
        self._build_hud_rail()

        body = ctk.CTkFrame(self, fg_color=VOID)
        body.pack(side="right", fill="both", expand=True)

        self.tabs = ctk.CTkTabview(
            body,
            fg_color=VOID,
            border_width=1,
            border_color=PANEL_EDGE,
            segmented_button_fg_color=PANEL,
            segmented_button_selected_color=CYAN_DIM,
            segmented_button_selected_hover_color="#2A87A0",
            segmented_button_unselected_color=PANEL,
            segmented_button_unselected_hover_color=RAISED,
            text_color=TEXT,
        )
        self.tabs.pack(fill="both", expand=True, padx=(0, 18), pady=18)
        self.chat_tab = self.tabs.add("COMMS")
        self.brain_tab = self.tabs.add("CORE")
        self.voice_tab = self.tabs.add("VOICE")
        self._build_chat_tab()
        self._build_brain_tab()
        self._build_voice_tab()
        self.bind("<Control-space>", lambda _event: self._push_to_talk())

    def _build_hud_rail(self) -> None:
        rail = ctk.CTkFrame(self, fg_color=PANEL, width=300, corner_radius=0)
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)

        ctk.CTkLabel(
            rail,
            text="T R I N I T Y",
            font=ctk.CTkFont(family=MONO, size=20, weight="bold"),
            text_color=CYAN,
        ).pack(pady=(26, 2))
        self.tagline = ctk.CTkLabel(
            rail,
            text="INITIALIZING",
            font=ctk.CTkFont(family=MONO, size=10),
            text_color=MUTED,
        )
        self.tagline.pack()

        self.reactor = Reactor(rail, size=192)
        self.reactor.pack(pady=16)

        self.status = ctk.CTkLabel(
            rail,
            text="BOOT",
            font=ctk.CTkFont(family=MONO, size=15, weight="bold"),
            text_color=CYAN,
        )
        self.status.pack()

        self.wave = Waveform(rail, width=252, height=46)
        self.wave.pack(pady=(12, 18))

        Bracket(rail, width=252, height=24, label="telemetry").pack(padx=24, anchor="w")
        self.telemetry = ctk.CTkLabel(
            rail,
            text="reading sensors…",
            font=ctk.CTkFont(family=MONO, size=11),
            text_color=MUTED,
            justify="left",
            anchor="w",
        )
        self.telemetry.pack(fill="x", padx=26, pady=(4, 14))

        Bracket(rail, width=252, height=24, label="scheduled").pack(padx=24, anchor="w")
        self.reminder_label = ctk.CTkLabel(
            rail,
            text="nothing queued",
            font=ctk.CTkFont(family=MONO, size=11),
            text_color=MUTED,
            justify="left",
            anchor="w",
            wraplength=248,
        )
        self.reminder_label.pack(fill="x", padx=26, pady=(4, 16))

        controls = ctk.CTkFrame(rail, fg_color="transparent")
        controls.pack(side="bottom", fill="x", pady=(0, 20), padx=22)
        self.arm_switch = ctk.CTkSwitch(
            controls,
            text='WAKE WORD "TRINITY"',
            command=self._toggle_arm,
            progress_color=CYAN,
            button_color=CYAN,
            text_color=TEXT,
            font=ctk.CTkFont(family=MONO, size=11),
        )
        self.arm_switch.pack(anchor="w", pady=4)
        self.speak_switch = ctk.CTkSwitch(
            controls,
            text="SPOKEN REPLIES",
            command=self._toggle_speak,
            progress_color=CYAN,
            button_color=CYAN,
            text_color=TEXT,
            font=ctk.CTkFont(family=MONO, size=11),
        )
        self.speak_switch.pack(anchor="w", pady=4)
        if self.cfg["voice"]["speak_replies"]:
            self.speak_switch.select()
        ctk.CTkButton(
            controls,
            text="NEW SESSION",
            height=32,
            fg_color=RAISED,
            hover_color="#1B3245",
            text_color=TEXT,
            font=ctk.CTkFont(family=MONO, size=11),
            command=self._new_session,
        ).pack(fill="x", pady=(10, 0))

    def _build_chat_tab(self) -> None:
        self.chat_scroll = ctk.CTkScrollableFrame(
            self.chat_tab, fg_color=VOID, corner_radius=0
        )
        self.chat_scroll.pack(fill="both", expand=True, padx=4, pady=(10, 8))
        composer = ctk.CTkFrame(
            self.chat_tab,
            fg_color=PANEL,
            corner_radius=12,
            border_width=1,
            border_color=CYAN_DIM,
        )
        composer.pack(fill="x", padx=4, pady=(0, 4))
        self._build_composer(composer, "chat")

    def _build_brain_tab(self) -> None:
        top = ctk.CTkFrame(self.brain_tab, fg_color="transparent")
        top.pack(fill="x", padx=4, pady=(10, 8))
        self.model_card = self._stat_card(top, "LOCAL BRAIN", self.cfg["llm"]["model"])
        self.context_status = self._stat_card(top, "SESSION", "0 turns")
        self.voice_card = self._stat_card(top, "VOICE", self.voice.describe_engine())

        work = ctk.CTkFrame(self.brain_tab, fg_color="transparent")
        work.pack(fill="both", expand=True, padx=4, pady=(0, 8))

        trace_panel = self._panel(work, "activity log", side="left")
        self.trace_box = ctk.CTkTextbox(
            trace_panel,
            fg_color=VOID,
            text_color="#9FD7E8",
            wrap="word",
            font=ctk.CTkFont(family=MONO, size=12),
        )
        self.trace_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.trace_box.configure(state="disabled")

        memory_panel = self._panel(work, "memory archive", side="right", width=340)
        self.memory_box = ctk.CTkTextbox(
            memory_panel,
            fg_color=VOID,
            text_color=TEXT,
            wrap="word",
            font=ctk.CTkFont(family=MONO, size=12),
        )
        self.memory_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.memory_box.configure(state="disabled")

        summary = self._panel(self.brain_tab, "context snapshot", fill="x")
        self.summary_label = ctk.CTkLabel(
            summary,
            text="Building context from this conversation.",
            justify="left",
            anchor="w",
            wraplength=900,
            font=ctk.CTkFont(size=12),
            text_color=MUTED,
        )
        self.summary_label.pack(anchor="w", fill="x", padx=16, pady=(0, 12))

        composer = ctk.CTkFrame(
            self.brain_tab,
            fg_color=PANEL,
            corner_radius=12,
            border_width=1,
            border_color=CYAN_DIM,
        )
        composer.pack(fill="x", padx=4, pady=(0, 4))
        self._build_composer(composer, "brain")

    def _build_voice_tab(self) -> None:
        voice_cfg = self.cfg["voice"]

        intro = self._panel(self.voice_tab, "speech engine", fill="x")
        ctk.CTkLabel(
            intro,
            text=(
                "ElevenLabs gives her a natural voice and needs an API key; her replies are sent "
                "to their service to be spoken. Piper runs entirely on this PC with no account. "
                "She falls back to Piper whenever ElevenLabs is unavailable."
            ),
            font=ctk.CTkFont(size=12),
            text_color=MUTED,
            justify="left",
            wraplength=860,
        ).pack(anchor="w", padx=16, pady=(0, 12))
        self.engine_choice = ctk.CTkSegmentedButton(
            intro,
            values=["ElevenLabs", "Piper (local)"],
            command=self._change_engine,
            selected_color=CYAN_DIM,
            selected_hover_color="#2A87A0",
            unselected_color=RAISED,
            text_color=TEXT,
        )
        self.engine_choice.pack(anchor="w", padx=16, pady=(0, 14))
        self.engine_choice.set(
            "ElevenLabs" if voice_cfg.get("engine") == "elevenlabs" else "Piper (local)"
        )

        cloud = self._panel(self.voice_tab, "elevenlabs", fill="x")
        key_row = ctk.CTkFrame(cloud, fg_color="transparent")
        key_row.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(
            key_row, text="API KEY", width=92, anchor="w", text_color=MUTED,
            font=ctk.CTkFont(family=MONO, size=11),
        ).pack(side="left")
        self.key_entry = ctk.CTkEntry(
            key_row,
            height=34,
            show="*",
            placeholder_text="paste your ElevenLabs key",
            fg_color=VOID,
            border_color=PANEL_EDGE,
            text_color=TEXT,
        )
        self.key_entry.pack(side="left", fill="x", expand=True)
        if self.voice.cloud.api_key:
            self.key_entry.insert(0, self.voice.cloud.api_key)
        ctk.CTkButton(
            key_row,
            text="SAVE",
            width=86,
            height=34,
            fg_color=CYAN_DIM,
            hover_color="#2A87A0",
            text_color=TEXT,
            font=ctk.CTkFont(family=MONO, size=11),
            command=self._save_api_key,
        ).pack(side="left", padx=(8, 0))

        voice_row = ctk.CTkFrame(cloud, fg_color="transparent")
        voice_row.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(
            voice_row, text="VOICE", width=92, anchor="w", text_color=MUTED,
            font=ctk.CTkFont(family=MONO, size=11),
        ).pack(side="left")
        self.cloud_voice_choice = ctk.CTkOptionMenu(
            voice_row,
            values=[voice_cfg.get("elevenlabs_voice_name") or "Rachel"],
            width=230,
            fg_color=RAISED,
            button_color=CYAN_DIM,
            command=self._change_cloud_voice,
        )
        self.cloud_voice_choice.pack(side="left")
        ctk.CTkButton(
            voice_row,
            text="LOAD MY VOICES",
            width=150,
            height=32,
            fg_color=RAISED,
            hover_color="#1B3245",
            text_color=TEXT,
            font=ctk.CTkFont(family=MONO, size=11),
            command=self._load_cloud_voices,
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            voice_row,
            text="PREVIEW",
            width=100,
            height=32,
            fg_color=RAISED,
            hover_color="#1B3245",
            text_color=TEXT,
            font=ctk.CTkFont(family=MONO, size=11),
            command=self._preview_voice,
        ).pack(side="left")

        self.voice_status = ctk.CTkLabel(
            cloud,
            text=self.voice.describe_engine(),
            font=ctk.CTkFont(family=MONO, size=12),
            text_color=CYAN,
            justify="left",
            wraplength=860,
        )
        self.voice_status.pack(anchor="w", padx=16, pady=(0, 14))

        local = self._panel(self.voice_tab, "local fallback", fill="x")
        piper_row = ctk.CTkFrame(local, fg_color="transparent")
        piper_row.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkLabel(
            piper_row, text="PIPER", width=92, anchor="w", text_color=MUTED,
            font=ctk.CTkFont(family=MONO, size=11),
        ).pack(side="left")
        self.voice_choice = ctk.CTkOptionMenu(
            piper_row,
            values=self.voice.voice_names(),
            width=230,
            fg_color=RAISED,
            button_color=CYAN_DIM,
            command=self._change_voice,
        )
        self.voice_choice.pack(side="left")
        self.voice_choice.set(self.voice.current_voice_name())
        ctk.CTkLabel(
            piper_row,
            text="add more with setup_local_voice.ps1",
            text_color=MUTED,
            font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=10)

    def _panel(
        self,
        parent: Any,
        label: str,
        side: str | None = None,
        fill: str = "both",
        width: int | None = None,
    ) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(
            parent, fg_color=PANEL, corner_radius=12, border_width=1, border_color=PANEL_EDGE
        )
        if side:
            frame.pack(
                side=side, fill="both", expand=width is None, padx=(0, 6) if side == "left" else (6, 0)
            )
            if width:
                frame.configure(width=width)
                frame.pack_propagate(False)
        else:
            frame.pack(fill=fill, expand=fill == "both", padx=4, pady=(0, 8))
        Bracket(frame, width=260, height=24, label=label).pack(anchor="w", padx=12, pady=(10, 4))
        return frame

    def _stat_card(self, parent: ctk.CTkFrame, label: str, value: str) -> ctk.CTkLabel:
        card = ctk.CTkFrame(
            parent, fg_color=PANEL, corner_radius=12, border_width=1, border_color=PANEL_EDGE
        )
        card.pack(side="left", fill="x", expand=True, padx=3)
        ctk.CTkLabel(
            card,
            text=label,
            font=ctk.CTkFont(family=MONO, size=10, weight="bold"),
            text_color=CYAN,
        ).pack(anchor="w", padx=14, pady=(10, 0))
        value_label = ctk.CTkLabel(
            card,
            text=value,
            font=ctk.CTkFont(family=MONO, size=14, weight="bold"),
            text_color=TEXT,
            anchor="w",
            wraplength=300,
            justify="left",
        )
        value_label.pack(anchor="w", padx=14, pady=(0, 12))
        return value_label

    def _build_composer(self, parent: ctk.CTkFrame, source: str) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=12)
        ctk.CTkLabel(
            row,
            text="▌",
            font=ctk.CTkFont(family=MONO, size=20, weight="bold"),
            text_color=CYAN,
            width=16,
        ).pack(side="left", padx=(0, 6))
        entry = ctk.CTkEntry(
            row,
            placeholder_text="speak or type your request",
            height=44,
            fg_color=VOID,
            border_color=CYAN_DIM,
            border_width=2,
            text_color=TEXT,
            font=ctk.CTkFont(family=MONO, size=13),
        )
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _event, area=source: self._send_typed(area))
        mic = ctk.CTkButton(
            row,
            text="MIC",
            width=74,
            height=44,
            fg_color=RAISED,
            hover_color="#1B3245",
            text_color=TEXT,
            font=ctk.CTkFont(family=MONO, size=12),
            command=self._push_to_talk,
        )
        mic.pack(side="left", padx=(8, 0))
        send = ctk.CTkButton(
            row,
            text="SEND",
            width=92,
            height=44,
            fg_color=CYAN_DIM,
            hover_color="#2A87A0",
            text_color=TEXT,
            font=ctk.CTkFont(family=MONO, size=12),
            command=lambda area=source: self._send_typed(area),
        )
        send.pack(side="left", padx=(8, 0))
        if source == "chat":
            self.chat_entry, self.chat_mic, self.chat_send = entry, mic, send
        else:
            self.brain_entry, self.brain_mic, self.brain_send = entry, mic, send

    # ------------------------------------------------------------- lifecycle

    def _boot_sequence(self) -> None:
        for index, line in enumerate(BOOT_LINES):
            self.after(index * 220, lambda text=line: self._trace(text))
        self.after(len(BOOT_LINES) * 220, lambda: self.tagline.configure(text="LOCAL ASSISTANT"))

    def _boot_check(self) -> None:
        if self._startup_warning:
            self._trace(self._startup_warning)
            self._add_message("Trinity", self._startup_warning)
        ok, message = self.agent.ping()
        self._set_status("Ready" if ok else "No brain", error=not ok)
        self._trace(message)
        self._add_message(
            "Trinity",
            "Systems online. I can reason through multi-step work, run calculations, research "
            "with sources, open things on this PC, keep reminders, and remember what matters.",
        )
        if not ok:
            self._add_message(
                "Trinity",
                f"{message}\n\nStart Ollama, then run: ollama pull {self.cfg['llm']['model']}",
            )

    def _warm_apps(self) -> None:
        def work() -> None:
            from trinity.apps import list_installed_apps

            try:
                list_installed_apps()
                self._trace("App index ready")
            except Exception:
                self._trace("App index unavailable; chat still works")

        threading.Thread(target=work, daemon=True).start()

    def _telemetry_tick(self) -> None:
        def work() -> None:
            try:
                import psutil

                memory_info = psutil.virtual_memory()
                text = (
                    f"cpu   {psutil.cpu_percent(interval=0.3):5.1f}%\n"
                    f"mem   {memory_info.percent:5.1f}%  "
                    f"{memory_info.used / 1e9:.1f}/{memory_info.total / 1e9:.1f} GB"
                )
                battery = getattr(psutil, "sensors_battery", lambda: None)()
                if battery is not None:
                    plugged = "AC" if battery.power_plugged else "BATT"
                    text += f"\npwr   {battery.percent:5.0f}%  {plugged}"
            except Exception:
                text = "sensors unavailable"
            self.after(0, lambda: self.telemetry.configure(text=text))

        threading.Thread(target=work, daemon=True).start()
        self.after(5000, self._telemetry_tick)

    def _reminder_tick(self) -> None:
        pending = self.memory.pending_reminders()
        if pending:
            lines = [
                f"{body[:34]}  ·  {describe_delay(due)}" for _ident, body, due in pending[:3]
            ]
            self.reminder_label.configure(text="\n".join(lines))
        else:
            self.reminder_label.configure(text="nothing queued")
        for ident, body in self.memory.due_reminders():
            self.memory.mark_reminder_fired(ident)
            self._trace(f"Reminder fired: {body}")
            self._add_message("Trinity", f"Reminder: {body}")
            self.voice.speak(f"Reminder. {body}")
        self.after(15000, self._reminder_tick)

    def _new_session(self) -> None:
        if self._busy:
            return
        self.agent.reset_session()
        for child in self.chat_scroll.winfo_children():
            child.destroy()
        self._trace_lines.clear()
        self._render_trace()
        self._add_message("Trinity", "New session. The memory archive is untouched.")
        self._refresh_memory()
        self._refresh_brain_state()
        self._set_status("Ready")

    # ------------------------------------------------------------ interaction

    def _toggle_arm(self) -> None:
        armed = bool(self.arm_switch.get())
        threading.Thread(target=self.voice.arm, args=(armed,), daemon=True).start()
        self.wave.set_active(armed)
        self._trace("Wake-word listening enabled" if armed else "Wake-word listening disabled")

    def _toggle_speak(self) -> None:
        self.voice.speak_replies = bool(self.speak_switch.get())

    def _change_engine(self, label: str) -> None:
        engine = "elevenlabs" if label.startswith("ElevenLabs") else "piper"
        self.voice.set_engine(engine)
        self.cfg["voice"]["engine"] = engine
        save_config(self.cfg)
        self._refresh_voice_status()
        self._trace(f"Speech engine set to {label}")

    def _change_voice(self, display_name: str) -> None:
        self.voice.set_voice(display_name)
        self.cfg["voice"]["piper_voice"] = self.voice.current_voice_key()
        save_config(self.cfg)
        self._refresh_voice_status()
        self._trace(f"Local voice set to {display_name}")

    def _save_api_key(self) -> None:
        key = self.key_entry.get().strip()
        save_secret(ELEVENLABS_KEY, key)
        self.voice.set_api_key(key)
        if not key:
            self._trace("Cleared the ElevenLabs key; using the local voice")
            self._refresh_voice_status()
            return

        def work() -> None:
            try:
                detail = self.voice.cloud.check()
            except SpeechError as exc:
                detail = str(exc)
            self._trace(detail)
            self.after(0, self._refresh_voice_status)

        threading.Thread(target=work, daemon=True).start()

    def _load_cloud_voices(self) -> None:
        def work() -> None:
            try:
                voices = self.voice.cloud.list_voices()
            except SpeechError as exc:
                self._trace(str(exc))
                return
            if not voices:
                self._trace("That account has no voices")
                return
            self._cloud_voices = dict(voices)
            names = list(self._cloud_voices)
            current = next(
                (name for name, vid in voices if vid == self.voice.cloud.voice_id), names[0]
            )

            def apply() -> None:
                self.cloud_voice_choice.configure(values=names)
                self.cloud_voice_choice.set(current)

            self.after(0, apply)
            self._trace(f"Loaded {len(names)} ElevenLabs voices")

        threading.Thread(target=work, daemon=True).start()

    def _change_cloud_voice(self, name: str) -> None:
        voice_id = self._cloud_voices.get(name)
        if not voice_id:
            self._trace("Load your voices first so I know that voice's id")
            return
        self.voice.set_cloud_voice(name, voice_id)
        self.cfg["voice"]["elevenlabs_voice_name"] = name
        self.cfg["voice"]["elevenlabs_voice_id"] = voice_id
        save_config(self.cfg)
        self._refresh_voice_status()
        self._trace(f"ElevenLabs voice set to {name}")

    def _preview_voice(self) -> None:
        self.voice.speak("Trinity online. This is how I sound.")

    def _refresh_voice_status(self) -> None:
        description = self.voice.describe_engine()
        self.voice_status.configure(text=description)
        self.voice_card.configure(text=description)

    def _push_to_talk(self) -> None:
        if not self._busy:
            self._set_status("Listening")
            self._trace("Listening for a spoken request")
            self.voice.listen_once()

    def _send_typed(self, source: str) -> None:
        entry = self.chat_entry if source == "chat" else self.brain_entry
        text = entry.get().strip()
        if not text:
            return
        entry.delete(0, tk.END)
        self._handle_user(text, source)

    def _on_heard(self, text: str) -> None:
        self.after(0, lambda: self._handle_user(text, "voice"))

    def _handle_user(self, text: str, source: str) -> None:
        if self._busy:
            self._trace("Busy with the previous request; try again in a moment")
            return
        self._busy = True
        self._set_inputs_enabled(False)
        self._add_message("You", text)
        self._trace(f"Request from {source}: {text}")
        self._set_status("Thinking")
        threading.Thread(target=self._reply, args=(text,), daemon=True).start()

    def _reply(self, text: str) -> None:
        with self._stream_lock:
            try:
                spoken = ""
                for kind, payload in self.agent.handle(text):
                    if kind == "status":
                        self.after(0, lambda value=payload: self._set_status(value))
                    elif kind in {"activity", "trace"}:
                        self.after(0, lambda value=payload: self._trace(value))
                    elif kind == "final":
                        spoken = payload
                        self.after(0, lambda value=payload: self._finish_reply(value))
                    elif kind == "error":
                        self.after(0, lambda value=payload: self._finish_error(value))
                        return
                if spoken:
                    self.after(0, lambda: self._set_status("Speaking"))
                    self.wave.set_active(True)
                    self.voice.speak(spoken)
                    self.after(900, lambda: self.wave.set_active(self.voice.is_armed()))
            except Exception as exc:
                self.after(0, lambda: self._finish_error(f"Something went wrong: {exc}"))

    def _finish_reply(self, text: str) -> None:
        self._add_message("Trinity", text)
        self._refresh_memory()
        self._refresh_brain_state()
        self._set_status("Ready")
        self._busy = False
        self._set_inputs_enabled(True)

    def _finish_error(self, text: str) -> None:
        self._add_message("Trinity", text)
        self._trace(f"Error: {text}")
        self._set_status("Error", error=True)
        self._busy = False
        self._set_inputs_enabled(True)

    def _set_inputs_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget in (
            self.chat_entry,
            self.brain_entry,
            self.chat_mic,
            self.brain_mic,
            self.chat_send,
            self.brain_send,
        ):
            widget.configure(state=state)

    # ------------------------------------------------------------- rendering

    def _add_message(self, who: str, text: str) -> None:
        wrap = ctk.CTkFrame(self.chat_scroll, fg_color="transparent")
        wrap.pack(fill="x", pady=5)
        mine = who == "You"
        accent = AMBER if mine else CYAN
        block = ctk.CTkFrame(
            wrap,
            fg_color=RAISED if mine else PANEL,
            corner_radius=10,
            border_width=1,
            border_color=PANEL_EDGE,
        )
        block.pack(anchor="e" if mine else "w", fill="x", padx=(140, 6) if mine else (6, 140))
        header = ctk.CTkFrame(block, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(8, 0))
        ctk.CTkLabel(
            header,
            text=("USER" if mine else "TRINITY"),
            font=ctk.CTkFont(family=MONO, size=10, weight="bold"),
            text_color=accent,
        ).pack(side="left")
        ctk.CTkLabel(
            header,
            text=datetime.now().strftime("%H:%M:%S"),
            font=ctk.CTkFont(family=MONO, size=10),
            text_color=MUTED,
        ).pack(side="right")
        ctk.CTkLabel(
            block,
            text=text,
            font=ctk.CTkFont(size=14),
            text_color=TEXT,
            justify="left",
            wraplength=640,
            anchor="w",
        ).pack(anchor="w", padx=12, pady=(3, 11), fill="x")
        self.after(30, lambda: self.chat_scroll._parent_canvas.yview_moveto(1.0))

    def _trace(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self._trace_lines.append(f"[{stamp}] {text}")
        self._trace_lines = self._trace_lines[-200:]
        try:
            self.after(0, self._render_trace)
        except tk.TclError:
            pass

    def _render_trace(self) -> None:
        self.trace_box.configure(state="normal")
        self.trace_box.delete("1.0", "end")
        self.trace_box.insert("1.0", "\n".join(self._trace_lines) or "awaiting input…")
        self.trace_box.see("end")
        self.trace_box.configure(state="disabled")

    def _refresh_memory(self) -> None:
        facts = self.memory.list_facts(limit=80)
        text = "\n".join(f"{key}\n  {value}\n" for key, value in facts) if facts else "empty"
        self.memory_box.configure(state="normal")
        self.memory_box.delete("1.0", "end")
        self.memory_box.insert("1.0", text)
        self.memory_box.configure(state="disabled")

    def _refresh_brain_state(self) -> None:
        state = self.agent.conversation_state()
        self.context_status.configure(
            text=f"{state['turns']} turns · {state['messages']} msgs"
        )
        self.summary_label.configure(text=str(state["summary"]))

    def _set_status(self, message: str, error: bool = False) -> None:
        def apply() -> None:
            self.status.configure(
                text=message.upper(), text_color=ALERT if error else CYAN
            )
            key = message.strip().lower()
            if error:
                self.reactor.set_state("error")
            elif key.startswith("listen"):
                self.reactor.set_state("listening")
                self.wave.set_active(True)
            elif key.startswith("speak"):
                self.reactor.set_state("speaking")
            elif key.startswith(("think", "transcrib", "loading")):
                self.reactor.set_state("thinking")
            elif key.startswith(("ready", "standby", "speech ready", "yes")):
                self.reactor.set_state("ready")
                self.wave.set_active(self.voice.is_armed())
            else:
                self.reactor.set_state("working")

        try:
            self.after(0, apply)
        except tk.TclError:
            pass

    def _on_close(self) -> None:
        self.reactor.stop()
        self.wave.stop()
        self.voice.stop_listening()
        self.voice.interrupt_speech()
        self.destroy()


def run() -> None:
    app = TrinityApp()
    app.mainloop()
