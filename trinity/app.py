from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime

import customtkinter as ctk

from trinity.agent import Agent
from trinity.brain import Brain
from trinity.config import DEFAULTS, ensure_config, save_config
from trinity.memory import Memory
from trinity.messaging import MessagingError, NtfyChannel
from trinity.remote import RemoteBridge
from trinity.skills import REMOTE_SAFE_TOOLS, set_message_channel
from trinity.voice import Voice

ACCENT = "#54E1C4"
ACCENT_DARK = "#1B746A"
BG = "#080B10"
PANEL = "#101722"
PANEL_RAISED = "#151F2C"
TEXT = "#ECF7F5"
MUTED = "#93A4AF"
USER_BUBBLE = "#163B43"
AI_BUBBLE = "#131D29"
TRACE = "#B0C8C4"
ERROR = "#FF8D8D"


class TrinityApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = ensure_config()
        self.memory = Memory()
        self._startup_warning = ""
        self.agent = Agent(self._build_brain(), self.memory)
        self._build_phone_link()
        self.voice = Voice()
        self.voice.set_handlers(self._on_heard, self._set_status)
        self._stream_lock = threading.Lock()
        self._busy = False
        self._trace_lines: list[str] = []

        ctk.set_appearance_mode("dark")
        self.title("TRINITY · Local Assistant")
        self.geometry("1180x760")
        self.minsize(920, 620)
        self.configure(fg_color=BG)
        if self.cfg["ui"].get("always_on_top"):
            self.attributes("-topmost", True)

        self._build()
        self._refresh_memory()
        self._refresh_brain_state()
        self.after(200, self._boot_check)
        self.after(400, self._warm_apps)
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

    def _build_phone_link(self) -> None:
        messaging = self.cfg["messaging"]
        self.channel = NtfyChannel(
            messaging["server"],
            messaging["outbound_topic"],
            messaging["inbound_topic"],
            messaging.get("token", ""),
        )
        set_message_channel(self.channel)
        allowed = None if messaging.get("remote_can_control_pc") else REMOTE_SAFE_TOOLS
        self.remote_agent = Agent(
            self._build_brain(), self.memory, allowed_tools=allowed, remote=True
        )
        self.bridge = RemoteBridge(self.channel, self.remote_agent, self._trace)

    def _build(self) -> None:
        self._build_header()
        self.tabs = ctk.CTkTabview(
            self,
            fg_color=BG,
            segmented_button_fg_color=PANEL,
            segmented_button_selected_color=ACCENT_DARK,
            segmented_button_selected_hover_color="#248E82",
            segmented_button_unselected_color=PANEL,
            segmented_button_unselected_hover_color=PANEL_RAISED,
            text_color=TEXT,
        )
        self.tabs.pack(fill="both", expand=True, padx=20, pady=(0, 18))
        self.chat_tab = self.tabs.add("Chat")
        self.brain_tab = self.tabs.add("Brain")
        self.phone_tab = self.tabs.add("Phone")
        self._build_chat_tab()
        self._build_brain_tab()
        self._build_phone_tab()
        self.bind("<Control-space>", lambda _event: self._push_to_talk())

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0, height=82)
        header.pack(fill="x", pady=(0, 12))
        header.pack_propagate(False)

        mark = ctk.CTkLabel(
            header,
            text="T",
            width=40,
            height=40,
            corner_radius=20,
            fg_color=ACCENT,
            text_color=BG,
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        mark.pack(side="left", padx=(24, 12), pady=21)

        identity = ctk.CTkFrame(header, fg_color="transparent")
        identity.pack(side="left", fill="y", pady=14)
        ctk.CTkLabel(
            identity,
            text="TRINITY",
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
            text_color=TEXT,
        ).pack(anchor="w")
        self.header_detail = ctk.CTkLabel(
            identity,
            text=f"LOCAL · {self.cfg['llm']['model'].upper()}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=ACCENT,
        )
        self.header_detail.pack(anchor="w")

        self.status = ctk.CTkLabel(
            header,
            text="STARTING",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=MUTED,
        )
        self.status.pack(side="right", padx=(12, 10))

        self.new_session_button = ctk.CTkButton(
            header,
            text="New session",
            width=112,
            height=34,
            fg_color=PANEL_RAISED,
            hover_color="#213143",
            command=self._new_session,
        )
        self.new_session_button.pack(side="right", padx=(0, 20), pady=24)

    def _build_chat_tab(self) -> None:
        hero = ctk.CTkFrame(self.chat_tab, fg_color=PANEL, corner_radius=16)
        hero.pack(fill="x", padx=2, pady=(12, 10))
        ctk.CTkLabel(
            hero,
            text="Your local thinking partner",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=TEXT,
        ).pack(anchor="w", padx=18, pady=(14, 0))
        ctk.CTkLabel(
            hero,
            text="Ask naturally. Trinity keeps this conversation in context, remembers clear facts, and uses tools only when needed.",
            font=ctk.CTkFont(size=12),
            text_color=MUTED,
        ).pack(anchor="w", padx=18, pady=(2, 14))

        self.chat_scroll = ctk.CTkScrollableFrame(self.chat_tab, fg_color=BG, corner_radius=0)
        self.chat_scroll.pack(fill="both", expand=True, padx=2, pady=(0, 10))
        self._add_bubble(
            "Trinity",
            "Systems online. I can talk things through, remember what matters, open apps, and research when you ask.",
        )

        composer = ctk.CTkFrame(self.chat_tab, fg_color=PANEL, corner_radius=16)
        composer.pack(fill="x", padx=2, pady=(0, 2))
        self._build_voice_controls(composer)
        self._build_composer(composer, "chat")

    def _build_brain_tab(self) -> None:
        overview = ctk.CTkFrame(self.brain_tab, fg_color="transparent")
        overview.pack(fill="x", padx=2, pady=(12, 10))

        engine_card = ctk.CTkFrame(overview, fg_color=PANEL, corner_radius=16)
        engine_card.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkLabel(
            engine_card,
            text="LOCAL BRAIN",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=ACCENT,
        ).pack(anchor="w", padx=16, pady=(12, 2))
        ctk.CTkLabel(
            engine_card,
            text=self.cfg["llm"]["model"],
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=TEXT,
        ).pack(anchor="w", padx=16, pady=(0, 12))

        continuity_card = ctk.CTkFrame(overview, fg_color=PANEL, corner_radius=16)
        continuity_card.pack(side="left", fill="x", expand=True, padx=(6, 0))
        ctk.CTkLabel(
            continuity_card,
            text="CONVERSATION",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=ACCENT,
        ).pack(anchor="w", padx=16, pady=(12, 2))
        self.context_status = ctk.CTkLabel(
            continuity_card,
            text="0 turns · context ready",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=TEXT,
        )
        self.context_status.pack(anchor="w", padx=16, pady=(0, 12))

        work_area = ctk.CTkFrame(self.brain_tab, fg_color="transparent")
        work_area.pack(fill="both", expand=True, padx=2, pady=(0, 10))

        trace_panel = ctk.CTkFrame(work_area, fg_color=PANEL, corner_radius=16)
        trace_panel.pack(side="left", fill="both", expand=True, padx=(0, 6))
        ctk.CTkLabel(
            trace_panel,
            text="LIVE ACTIVITY",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=ACCENT,
        ).pack(anchor="w", padx=16, pady=(14, 2))
        ctk.CTkLabel(
            trace_panel,
            text="Visible workflow only — requests, tool use, and results. Private reasoning stays private.",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
        ).pack(anchor="w", padx=16, pady=(0, 10))
        self.trace_box = ctk.CTkTextbox(
            trace_panel,
            fg_color=BG,
            text_color=TRACE,
            wrap="word",
            font=ctk.CTkFont(family="Cascadia Mono", size=12),
        )
        self.trace_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.trace_box.configure(state="disabled")

        memory_panel = ctk.CTkFrame(work_area, fg_color=PANEL, width=330, corner_radius=16)
        memory_panel.pack(side="right", fill="y", padx=(6, 0))
        memory_panel.pack_propagate(False)
        ctk.CTkLabel(
            memory_panel,
            text="LONG-TERM MEMORY",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=ACCENT,
        ).pack(anchor="w", padx=16, pady=(14, 2))
        ctk.CTkLabel(
            memory_panel,
            text="Stored locally on this PC",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
        ).pack(anchor="w", padx=16, pady=(0, 10))
        self.memory_box = ctk.CTkTextbox(
            memory_panel,
            fg_color=BG,
            text_color=TEXT,
            wrap="word",
            font=ctk.CTkFont(size=12),
        )
        self.memory_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.memory_box.configure(state="disabled")

        summary_panel = ctk.CTkFrame(self.brain_tab, fg_color=PANEL, corner_radius=14)
        summary_panel.pack(fill="x", padx=2, pady=(0, 8))
        ctk.CTkLabel(
            summary_panel,
            text="CONTEXT SNAPSHOT",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=ACCENT,
        ).pack(anchor="w", padx=16, pady=(10, 0))
        self.summary_label = ctk.CTkLabel(
            summary_panel,
            text="Building context from this conversation.",
            justify="left",
            anchor="w",
            wraplength=980,
            font=ctk.CTkFont(size=12),
            text_color=MUTED,
        )
        self.summary_label.pack(anchor="w", fill="x", padx=16, pady=(2, 10))

        brain_composer = ctk.CTkFrame(self.brain_tab, fg_color=PANEL, corner_radius=16)
        brain_composer.pack(fill="x", padx=2, pady=(0, 2))
        self._build_composer(brain_composer, "brain")

    def _build_phone_tab(self) -> None:
        messaging = self.cfg["messaging"]

        intro = ctk.CTkFrame(self.phone_tab, fg_color=PANEL, corner_radius=16)
        intro.pack(fill="x", padx=2, pady=(12, 10))
        ctk.CTkLabel(
            intro,
            text="Phone link",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=TEXT,
        ).pack(anchor="w", padx=18, pady=(14, 0))
        ctk.CTkLabel(
            intro,
            text=(
                "Install the ntfy app on your phone, subscribe to the two topics below, and "
                "Trinity can message you. Send to the inbound topic and she answers. "
                "This works while this PC is awake and Trinity is running."
            ),
            font=ctk.CTkFont(size=12),
            text_color=MUTED,
            justify="left",
            wraplength=940,
        ).pack(anchor="w", padx=18, pady=(2, 14))

        self.phone_switch = ctk.CTkSwitch(
            intro,
            text="Enable phone link",
            command=self._toggle_phone_link,
            progress_color=ACCENT,
            button_color=ACCENT,
            text_color=TEXT,
        )
        self.phone_switch.pack(anchor="w", padx=18, pady=(0, 16))

        topics = ctk.CTkFrame(self.phone_tab, fg_color=PANEL, corner_radius=16)
        topics.pack(fill="x", padx=2, pady=(0, 10))
        ctk.CTkLabel(
            topics,
            text="YOUR PRIVATE TOPICS",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=ACCENT,
        ).pack(anchor="w", padx=16, pady=(12, 2))
        ctk.CTkLabel(
            topics,
            text=f"Server  {messaging['server']}",
            font=ctk.CTkFont(size=12),
            text_color=MUTED,
        ).pack(anchor="w", padx=16, pady=(0, 6))
        self._topic_row(topics, "She messages you on", messaging["outbound_topic"])
        self._topic_row(topics, "You message her on", messaging["inbound_topic"])

        actions = ctk.CTkFrame(topics, fg_color="transparent")
        actions.pack(anchor="w", fill="x", padx=16, pady=(4, 14))
        ctk.CTkButton(
            actions,
            text="Send test message",
            width=150,
            height=32,
            fg_color=ACCENT,
            text_color=BG,
            hover_color="#80F0DE",
            command=self._send_test_message,
        ).pack(side="left")
        ctk.CTkButton(
            actions,
            text="Copy both topics",
            width=140,
            height=32,
            fg_color=PANEL_RAISED,
            hover_color="#213143",
            command=self._copy_topics,
        ).pack(side="left", padx=8)

        caution = ctk.CTkFrame(self.phone_tab, fg_color=PANEL, corner_radius=16)
        caution.pack(fill="x", padx=2, pady=(0, 10))
        ctk.CTkLabel(
            caution,
            text="WHO CAN REACH HER",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=ACCENT,
        ).pack(anchor="w", padx=16, pady=(12, 2))
        ctk.CTkLabel(
            caution,
            text=(
                "On the public ntfy.sh server the topic name is the only secret, so treat it like "
                "a password. Phone messages are limited to research, memory, notes, and texting; "
                "they cannot open apps, read your clipboard, or search your files."
            ),
            font=ctk.CTkFont(size=12),
            text_color=MUTED,
            justify="left",
            wraplength=940,
        ).pack(anchor="w", padx=16, pady=(0, 10))
        self.remote_power_switch = ctk.CTkSwitch(
            caution,
            text="Also let phone messages control this PC (not recommended)",
            command=self._toggle_remote_power,
            progress_color="#C9705F",
            button_color="#C9705F",
            text_color=TEXT,
        )
        self.remote_power_switch.pack(anchor="w", padx=16, pady=(0, 14))
        if messaging.get("remote_can_control_pc"):
            self.remote_power_switch.select()
        if messaging.get("enabled"):
            self.phone_switch.select()
            self.after(900, self._start_phone_link)

    def _topic_row(self, parent: ctk.CTkFrame, label: str, topic: str) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=3)
        ctk.CTkLabel(
            row, text=label, width=170, anchor="w", text_color=MUTED, font=ctk.CTkFont(size=12)
        ).pack(side="left")
        value = ctk.CTkEntry(
            row, height=32, fg_color=BG, border_color="#263748", text_color=TEXT
        )
        value.pack(side="left", fill="x", expand=True)
        value.insert(0, topic)
        value.configure(state="readonly")

    def _toggle_phone_link(self) -> None:
        enabled = bool(self.phone_switch.get())
        self.cfg["messaging"]["enabled"] = enabled
        save_config(self.cfg)
        if enabled:
            self._start_phone_link()
        else:
            self.bridge.stop()
            self._trace("Phone link turned off")

    def _start_phone_link(self) -> None:
        self.bridge.start()
        self._trace("Phone link starting")

    def _toggle_remote_power(self) -> None:
        full = bool(self.remote_power_switch.get())
        self.cfg["messaging"]["remote_can_control_pc"] = full
        save_config(self.cfg)
        self.remote_agent.allowed_tools = None if full else REMOTE_SAFE_TOOLS
        self._trace(
            "Phone messages may now control this PC"
            if full
            else "Phone messages restricted to research and memory"
        )

    def _send_test_message(self) -> None:
        def work() -> None:
            try:
                self.channel.publish("Trinity here. The phone link works.")
                self._trace("Test message sent to your phone")
            except MessagingError as exc:
                self._trace(str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _copy_topics(self) -> None:
        messaging = self.cfg["messaging"]
        summary = (
            f"server: {messaging['server']}\n"
            f"from Trinity: {messaging['outbound_topic']}\n"
            f"to Trinity: {messaging['inbound_topic']}"
        )
        self.clipboard_clear()
        self.clipboard_append(summary)
        self._trace("Copied the phone-link topics to the clipboard")

    def _build_voice_controls(self, parent: ctk.CTkFrame) -> None:
        controls = ctk.CTkFrame(parent, fg_color="transparent")
        controls.pack(fill="x", padx=14, pady=(10, 0))
        self.arm_switch = ctk.CTkSwitch(
            controls,
            text='Wake word · "Trinity"',
            command=self._toggle_arm,
            progress_color=ACCENT,
            button_color=ACCENT,
            text_color=TEXT,
        )
        self.arm_switch.pack(side="left")
        self.speak_switch = ctk.CTkSwitch(
            controls,
            text="Speak replies",
            command=self._toggle_speak,
            progress_color=ACCENT,
            button_color=ACCENT,
            text_color=TEXT,
        )
        self.speak_switch.pack(side="left", padx=18)
        if self.cfg["voice"]["speak_replies"]:
            self.speak_switch.select()
        ctk.CTkLabel(controls, text="Voice", text_color=MUTED).pack(side="left", padx=(12, 0))
        self.voice_choice = ctk.CTkOptionMenu(
            controls,
            values=self.voice.voice_names(),
            width=190,
            command=self._change_voice,
        )
        self.voice_choice.pack(side="left", padx=8)
        self.voice_choice.set(self.voice.current_voice_name())
        ctk.CTkButton(
            controls,
            text="Preview",
            width=72,
            height=28,
            fg_color=PANEL_RAISED,
            hover_color="#213143",
            command=lambda: self.voice.speak("Voice check. I am Trinity."),
        ).pack(side="left")

    def _build_composer(self, parent: ctk.CTkFrame, source: str) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=12)
        entry = ctk.CTkEntry(
            row,
            placeholder_text=(
                "Ask Trinity anything…" if source == "chat" else "Ask from the Brain workspace…"
            ),
            height=44,
            fg_color=BG,
            border_color="#263748",
            text_color=TEXT,
        )
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _event, area=source: self._send_typed(area))
        if source == "chat":
            self.chat_entry = entry
        else:
            self.brain_entry = entry

        mic = ctk.CTkButton(
            row,
            text="Mic",
            width=66,
            height=44,
            fg_color=PANEL_RAISED,
            hover_color="#213143",
            command=self._push_to_talk,
        )
        mic.pack(side="left", padx=(8, 0))
        send = ctk.CTkButton(
            row,
            text="Send",
            width=82,
            height=44,
            fg_color=ACCENT,
            text_color=BG,
            hover_color="#80F0DE",
            command=lambda area=source: self._send_typed(area),
        )
        send.pack(side="left", padx=(8, 0))
        if source == "chat":
            self.chat_mic, self.chat_send = mic, send
        else:
            self.brain_mic, self.brain_send = mic, send

    def _warm_apps(self) -> None:
        def work() -> None:
            from trinity.apps import list_installed_apps

            try:
                list_installed_apps()
                self._trace("App index ready")
            except Exception:
                self._trace("App index was unavailable; chat is still ready")

        threading.Thread(target=work, daemon=True).start()

    def _boot_check(self) -> None:
        if self._startup_warning:
            self._trace(self._startup_warning)
            self._add_bubble("Trinity", self._startup_warning)
        ok, message = self.agent.ping()
        self._set_status("Ready" if ok else "No brain", error=not ok)
        self._trace(message)
        if not ok:
            self._add_bubble(
                "Trinity",
                f"{message}\n\nInstall Ollama, then run: ollama pull {self.cfg['llm']['model']}",
            )

    def _new_session(self) -> None:
        if self._busy:
            return
        self.agent.reset_session()
        for child in self.chat_scroll.winfo_children():
            child.destroy()
        self._trace_lines.clear()
        self._render_trace()
        self._add_bubble("Trinity", "New conversation started. Long-term memory is still available.")
        self._trace("New session started; long-term memory retained")
        self._refresh_memory()
        self._refresh_brain_state()
        self._set_status("Ready")

    def _refresh_memory(self) -> None:
        facts = self.memory.list_facts(limit=80)
        text = "\n".join(f"{key}\n  {value}\n" for key, value in facts) if facts else "Nothing stored yet."
        self.memory_box.configure(state="normal")
        self.memory_box.delete("1.0", "end")
        self.memory_box.insert("1.0", text)
        self.memory_box.configure(state="disabled")

    def _refresh_brain_state(self) -> None:
        state = self.agent.conversation_state()
        self.context_status.configure(text=f"{state['turns']} turns · {state['messages']} active messages")
        self.summary_label.configure(text=str(state["summary"]))

    def _toggle_arm(self) -> None:
        armed = bool(self.arm_switch.get())
        threading.Thread(target=self.voice.arm, args=(armed,), daemon=True).start()
        self._trace("Wake-word listening enabled" if armed else "Wake-word listening disabled")

    def _toggle_speak(self) -> None:
        self.voice.speak_replies = bool(self.speak_switch.get())

    def _change_voice(self, display_name: str) -> None:
        self.voice.set_voice(display_name)
        self.cfg["voice"]["piper_voice"] = self.voice.current_voice_key()
        save_config(self.cfg)
        self._trace(f"Selected local voice: {display_name}")
        self._set_status(f"Voice: {display_name}")

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
            self._trace("A request arrived while Trinity was still working; please try again in a moment")
            return
        self._busy = True
        self._set_inputs_enabled(False)
        self._add_bubble("You", text)
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
                    self.voice.speak(spoken)
            except Exception as exc:
                self.after(0, lambda: self._finish_error(f"Something went wrong: {exc}"))

    def _finish_reply(self, text: str) -> None:
        self._add_bubble("Trinity", text)
        self._refresh_memory()
        self._refresh_brain_state()
        self._set_status("Ready")
        self._busy = False
        self._set_inputs_enabled(True)

    def _finish_error(self, text: str) -> None:
        self._add_bubble("Trinity", text)
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

    def _add_bubble(self, who: str, text: str) -> None:
        wrap = ctk.CTkFrame(self.chat_scroll, fg_color="transparent")
        wrap.pack(fill="x", pady=6)
        if who == "You":
            color, name_color, anchor, pad = USER_BUBBLE, "#B7D5D1", "e", (120, 8)
        else:
            color, name_color, anchor, pad = AI_BUBBLE, ACCENT, "w", (8, 120)
        bubble = ctk.CTkFrame(wrap, fg_color=color, corner_radius=14)
        bubble.pack(anchor=anchor, fill="x", padx=pad)
        ctk.CTkLabel(
            bubble,
            text=who.upper(),
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=name_color,
        ).pack(anchor="w", padx=14, pady=(9, 0))
        ctk.CTkLabel(
            bubble,
            text=text,
            font=ctk.CTkFont(size=14),
            text_color=TEXT,
            justify="left",
            wraplength=690,
            anchor="w",
        ).pack(anchor="w", padx=14, pady=(2, 11), fill="x")
        self.after(30, lambda: self.chat_scroll._parent_canvas.yview_moveto(1.0))

    def _trace(self, text: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._trace_lines.append(f"[{timestamp}] {text}")
        self._trace_lines = self._trace_lines[-160:]
        try:
            self.after(0, self._render_trace)
        except tk.TclError:
            pass

    def _render_trace(self) -> None:
        self.trace_box.configure(state="normal")
        self.trace_box.delete("1.0", "end")
        self.trace_box.insert("1.0", "\n".join(self._trace_lines) or "Waiting for your first request…")
        self.trace_box.see("end")
        self.trace_box.configure(state="disabled")

    def _set_status(self, message: str, error: bool = False) -> None:
        def apply() -> None:
            self.status.configure(text=message.upper(), text_color=ERROR if error else ACCENT)

        try:
            self.after(0, apply)
        except tk.TclError:
            pass

    def _on_close(self) -> None:
        self.bridge.stop()
        self.voice.stop_listening()
        self.voice.interrupt_speech()
        self.destroy()


def run() -> None:
    app = TrinityApp()
    app.mainloop()
