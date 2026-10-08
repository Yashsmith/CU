"""PRD §32 recovery: retry -> alternate -> replan, then stop. No infinite loops."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .actions import Action

MAX_RETRIES = 2
NUDGE_PX = 15

Decision = Literal["retry", "alternate", "replan", "give_up"]


@dataclass
class RecoveryPolicy:
    max_retries: int = MAX_RETRIES
    failures: int = 0
    last_error: str = ""

    def note_success(self) -> None:
        self.failures = 0
        self.last_error = ""

    def next(self, error: str) -> tuple[Decision, str]:
        """Advance on failure. Returns (decision, detail)."""
        self.failures += 1
        self.last_error = error
        if self.failures == 1:
            return "retry", f"retrying same action ({error})"
        if self.failures == 2:
            return "alternate", f"trying alternate action ({error})"
        return "give_up", f"recovery exhausted after {self.max_retries} retries ({error})"

    @property
    def exhausted(self) -> bool:
        return self.failures > self.max_retries


def alternate_action(action: Action) -> Action:
    """Build the alternate attempt: nudge clicks, re-issue otherwise."""
    if action.type in ("click", "double_click", "right_click", "move") and \
            action.x is not None and action.y is not None:
        return Action(type=action.type, x=action.x + NUDGE_PX, y=action.y + NUDGE_PX)
    if action.type == "wait":
        return Action(type="wait", seconds=min((action.seconds or 1) + 1.0, 5.0))
    return action.model_copy(deep=True)


@dataclass
class RecoveryState:
    policy: RecoveryPolicy = field(default_factory=RecoveryPolicy)
    pending: Action | None = None  # action the loop must try next (retry/alternate)
