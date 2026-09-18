"""Throwaway check of planning, calculation, and tool recovery against Hermes."""

import tempfile
from pathlib import Path

from trinity.agent import Agent
from trinity.brain import Brain
from trinity.config import load_config
from trinity.memory import Memory

cfg = load_config()["llm"]
memory = Memory(Path(tempfile.mkdtemp()) / "m.db")
agent = Agent(Brain(cfg["base_url"], cfg["model"], cfg["temperature"]), memory)

for prompt in [
    "What is 18 percent of 2450?",
    "Remind me to stretch in 25 minutes",
    "Research the James Webb telescope and then summarize what it found, briefly",
]:
    agent.reset_session()
    print(f"\n>>> {prompt}")
    for kind, payload in agent.handle(prompt):
        if kind in {"trace", "final", "error"}:
            print(f"  [{kind}] {payload[:300]}")
