from __future__ import annotations

import math
import random
import tkinter as tk
from typing import Any

CYAN = "#5FE3FF"
CYAN_DIM = "#1E6E85"
AMBER = "#FFB347"
VOID = "#04070C"
GRID = "#0C1622"
TEXT = "#DCEEF5"
MUTED = "#6F8A99"
ALERT = "#FF7A7A"

STATE_COLORS = {
    "idle": CYAN_DIM,
    "ready": CYAN,
    "listening": "#7DF9C0",
    "thinking": AMBER,
    "working": AMBER,
    "speaking": CYAN,
    "error": ALERT,
}


class Reactor(tk.Canvas):
    """The arc-reactor core: rotating rings that react to what Trinity is doing."""

    def __init__(self, master: Any, size: int = 168, **kwargs: Any) -> None:
        super().__init__(
            master,
            width=size,
            height=size,
            bg=VOID,
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self.size = size
        self.state = "idle"
        self._angle = 0.0
        self._pulse = 0.0
        self._level = 0.0
        self._running = True
        self._tick()

    def set_state(self, state: str) -> None:
        self.state = state if state in STATE_COLORS else "ready"

    def set_level(self, level: float) -> None:
        """0..1 audio or activity level that widens the inner glow."""
        self._level = max(0.0, min(1.0, level))

    def stop(self) -> None:
        self._running = False

    def _tick(self) -> None:
        if not self._running:
            return
        speed = {"thinking": 7.0, "working": 7.0, "listening": 3.0, "speaking": 5.0}.get(
            self.state, 1.2
        )
        self._angle = (self._angle + speed) % 360
        self._pulse += 0.12
        self._draw()
        try:
            self.after(50, self._tick)
        except tk.TclError:
            self._running = False

    def _draw(self) -> None:
        self.delete("all")
        color = STATE_COLORS.get(self.state, CYAN)
        center = self.size / 2
        breath = (math.sin(self._pulse) + 1) / 2

        # Outer tick ring
        outer = center - 6
        for step in range(0, 360, 15):
            long_tick = step % 45 == 0
            length = 9 if long_tick else 5
            self._radial_line(center, outer, outer - length, step, color, 2 if long_tick else 1)

        # Two counter-rotating arc sets
        self._arc_set(center, outer - 16, self._angle, color, span=70, width=3)
        self._arc_set(center, outer - 30, -self._angle * 1.5, CYAN_DIM, span=48, width=2)

        # Inner core
        core = 20 + 10 * breath + 14 * self._level
        self.create_oval(
            center - core,
            center - core,
            center + core,
            center + core,
            outline=color,
            width=2,
        )
        glow = core * 0.55
        self.create_oval(
            center - glow,
            center - glow,
            center + glow,
            center + glow,
            fill=color,
            outline="",
        )

    def _arc_set(
        self, center: float, radius: float, angle: float, color: str, span: int, width: int
    ) -> None:
        for offset in (0, 120, 240):
            self.create_arc(
                center - radius,
                center - radius,
                center + radius,
                center + radius,
                start=angle + offset,
                extent=span,
                style="arc",
                outline=color,
                width=width,
            )

    def _radial_line(
        self,
        center: float,
        outer: float,
        inner: float,
        degrees: float,
        color: str,
        width: int,
    ) -> None:
        rad = math.radians(degrees)
        self.create_line(
            center + outer * math.cos(rad),
            center + outer * math.sin(rad),
            center + inner * math.cos(rad),
            center + inner * math.sin(rad),
            fill=color,
            width=width,
        )


class Waveform(tk.Canvas):
    """Bar meter that idles low and jumps while listening or speaking."""

    def __init__(self, master: Any, width: int = 260, height: int = 44, bars: int = 28) -> None:
        super().__init__(
            master, width=width, height=height, bg=VOID, highlightthickness=0, bd=0
        )
        self.bar_count = bars
        self.width = width
        self.height = height
        self.active = False
        self._values = [0.08] * bars
        self._running = True
        self._tick()

    def set_active(self, active: bool) -> None:
        self.active = active

    def stop(self) -> None:
        self._running = False

    def _tick(self) -> None:
        if not self._running:
            return
        for index in range(self.bar_count):
            target = random.uniform(0.2, 1.0) if self.active else random.uniform(0.04, 0.14)
            self._values[index] += (target - self._values[index]) * 0.35
        self._draw()
        try:
            self.after(70, self._tick)
        except tk.TclError:
            self._running = False

    def _draw(self) -> None:
        self.delete("all")
        slot = self.width / self.bar_count
        bar_width = max(2, slot * 0.5)
        middle = self.height / 2
        color = "#7DF9C0" if self.active else CYAN_DIM
        for index, value in enumerate(self._values):
            extent = value * (self.height / 2 - 3)
            x = index * slot + slot / 2
            self.create_line(
                x, middle - extent, x, middle + extent, fill=color, width=bar_width
            )


class Bracket(tk.Canvas):
    """Corner brackets drawn around a panel title, for the HUD look."""

    def __init__(self, master: Any, width: int = 200, height: int = 26, label: str = "") -> None:
        super().__init__(
            master, width=width, height=height, bg=VOID, highlightthickness=0, bd=0
        )
        self.create_line(0, 2, 12, 2, fill=CYAN, width=2)
        self.create_line(1, 2, 1, height - 4, fill=CYAN, width=2)
        self.create_line(width - 12, height - 3, width - 1, height - 3, fill=CYAN_DIM, width=2)
        self.create_text(
            20,
            height / 2,
            text=label.upper(),
            anchor="w",
            fill=CYAN,
            font=("Consolas", 10, "bold"),
        )


def grid_background(canvas: tk.Canvas, width: int, height: int, step: int = 34) -> None:
    """Faint blueprint grid behind the HUD."""
    for x in range(0, width, step):
        canvas.create_line(x, 0, x, height, fill=GRID)
    for y in range(0, height, step):
        canvas.create_line(0, y, width, y, fill=GRID)
