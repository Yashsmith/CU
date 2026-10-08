"""PRD §34 human takeover — AI <-> HUMAN handoff at step boundaries.

The signal is a file (`control.json`) inside the session dir, so a separate
operator process (CLI) can pause/resume/stop a running agent without shared
memory. The loop only ever reads it at a step boundary: an in-flight model
decision is never half-applied — the pending action is simply never executed
(desktop-use interrupt contract).

Flow:
  AI running --take_control--> HUMAN (desktop stays alive, agent idle)
           --release---------> AI (resume_from continues) | stop (end)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

COMMANDS = ("run", "pause", "takeover", "stop")


class ControlState:
    def __init__(self, session_dir: str | Path) -> None:
        self.dir = Path(session_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "control.json"
        if not self.path.exists():
            self._write("run", "init")

    def _write(self, command: str, by: str) -> dict[str, Any]:
        assert command in COMMANDS, f"unknown command {command!r}"
        state = {"command": command, "by": by, "at": time.time()}
        self.path.write_text(json.dumps(state))
        return state

    def read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {"command": "run", "by": "recovered", "at": time.time()}

    @property
    def command(self) -> str:
        cmd = self.read().get("command", "run")
        return cmd if cmd in COMMANDS else "run"

    # ---------------------------------------------------------- operator ----
    def pause(self, by: str = "operator") -> dict[str, Any]:
        return self._write("pause", by)

    def resume(self, by: str = "operator") -> dict[str, Any]:
        return self._write("run", by)

    def take_control(self, by: str = "human") -> dict[str, Any]:
        """Stop model execution, keep desktop alive for the human."""
        return self._write("takeover", by)

    def release(self, by: str = "human", stop: bool = False) -> dict[str, Any]:
        """Hand back to the agent (resume) or end the session (stop)."""
        return self._write("stop" if stop else "run", by)

    def stop(self, by: str = "operator") -> dict[str, Any]:
        return self._write("stop", by)

    # -------------------------------------------------------------- loop ----
    def paused(self) -> bool:
        return self.command in ("pause", "takeover")

    def should_stop(self) -> bool:
        return self.command == "stop"
