"""Agent loop: observe → decide → act → observe …

Simple, robust, no V2 complexity. Runs until:
  done        — model signals task complete
  max_steps   — step limit hit
  timeout     — time limit hit
  loop        — same click 3× at the same spot
  error       — unrecoverable model or execution error
  failsafe    — human grabbed the mouse (pyautogui corner abort)
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from .actions import Action
from .computer import Computer

LOOP_GUARD_RADIUS = 15
LOOP_GUARD_REPEATS = 3


@dataclass
class LoopResult:
    status: str          # done | max_steps | timeout | loop | error | failsafe
    steps: int
    task: str
    session_dir: str
    history: list[dict[str, Any]] = field(default_factory=list)


def _same_spot(a: Action, b: Action, r: int = LOOP_GUARD_RADIUS) -> bool:
    if a.type != b.type or None in (a.x, b.x, a.y, b.y):
        return False
    return abs(a.x - b.x) <= r and abs(a.y - b.y) <= r   # type: ignore[operator]


def _is_failsafe(err: Exception) -> bool:
    return type(err).__name__ == "FailSafeException"


async def run(
    task: str,
    model: Any,
    computer: Computer,
    *,
    max_steps: int = 30,
    max_runtime: float = 300.0,
    settle_wait: float = 0.5,
    session_root: Union[str, Path] = "sessions",
    verbose: bool = True,
) -> LoopResult:
    """Main agent loop. model must implement decide(task, screenshot, …)."""

    # ── Session directory (simple: just mkdir + events.jsonl) ────────────────
    session_root = Path(session_root)
    session_dir = session_root / _new_session_id()
    session_dir.mkdir(parents=True, exist_ok=True)
    events_path = session_dir / "events.jsonl"

    def log(ev: dict[str, Any]) -> None:
        with events_path.open("a") as f:
            f.write(json.dumps(ev) + "\n")
        if verbose:
            print(f"[step {ev.get('step','-')}] {ev.get('kind')}: {ev.get('detail','')}", flush=True)

    history: list[dict[str, Any]] = []
    recent_clicks: list[Action] = []
    consecutive_waits = 0
    previous_shot: Optional[bytes] = None
    step = 0
    start = time.monotonic()

    try:
        width, height = await computer.size()
    except Exception:
        width, height = 1280, 800

    while True:
        # ── Guard: limits ────────────────────────────────────────────────────
        if time.monotonic() - start > max_runtime:
            log({"kind": "limit", "step": step, "detail": f"MAX_RUNTIME {max_runtime}s"})
            return LoopResult("timeout", step, task, str(session_dir), history)
        if step >= max_steps:
            log({"kind": "limit", "step": step, "detail": f"MAX_STEPS {max_steps}"})
            return LoopResult("max_steps", step, task, str(session_dir), history)

        step += 1

        # ── Observe ──────────────────────────────────────────────────────────
        try:
            shot = await computer.screenshot()
        except Exception as e:
            log({"kind": "error", "step": step, "detail": f"screenshot: {e}"})
            return LoopResult("error", step, task, str(session_dir), history)

        # Save screenshot
        shot_path = session_dir / f"step_{step:03d}.png"
        shot_path.write_bytes(shot)

        # ── Decide ───────────────────────────────────────────────────────────
        try:
            action = await model.decide(
                task=task,
                screenshot=shot,
                history=history,
                width=width,
                height=height,
                previous_screenshot=previous_shot,
            )
        except Exception as e:
            log({"kind": "error", "step": step, "detail": f"model: {e}"})
            return LoopResult("error", step, task, str(session_dir), history)

        log({"kind": "decide", "step": step, "detail": action.model_dump()})
        previous_shot = shot

        # ── Terminal ─────────────────────────────────────────────────────────
        if action.is_terminal:
            # Save final screenshot
            (session_dir / "final.png").write_bytes(shot)
            history.append({"action": action.model_dump()})
            log({"kind": "done", "step": step, "detail": "task complete"})
            return LoopResult("done", step, task, str(session_dir), history)

        # ── Loop guard ───────────────────────────────────────────────────────
        if action.type == "wait":
            consecutive_waits += 1
            if consecutive_waits >= 5:
                log({"kind": "limit", "step": step,
                     "detail": f"stopped after {consecutive_waits} consecutive waits"})
                (session_dir / "final.png").write_bytes(shot)
                return LoopResult("timeout", step, task, str(session_dir), history)
        else:
            consecutive_waits = 0

        if action.type in ("click", "double_click", "right_click"):
            recent_clicks.append(action)
            recent_clicks = recent_clicks[-LOOP_GUARD_REPEATS:]
            if (len(recent_clicks) == LOOP_GUARD_REPEATS
                    and all(_same_spot(recent_clicks[0], c) for c in recent_clicks[1:])):
                log({"kind": "limit", "step": step,
                     "detail": f"loop: same {action.type} ×{LOOP_GUARD_REPEATS} "
                               f"at ({action.x},{action.y})"})
                return LoopResult("loop", step, task, str(session_dir), history)
        else:
            recent_clicks.clear()

        # ── Act ──────────────────────────────────────────────────────────────
        try:
            result = await computer.execute(action)
            history.append({"action": action.model_dump(), "result": str(result)[:200]})
            log({"kind": "act", "step": step,
                 "detail": f"{action.type} → {str(result)[:120]}"})
        except Exception as e:
            if _is_failsafe(e):
                log({"kind": "failsafe", "step": step,
                     "detail": "human grabbed mouse — stopping"})
                return LoopResult("failsafe", step, task, str(session_dir), history)
            history.append({"action": action.model_dump(), "error": str(e)})
            log({"kind": "error", "step": step, "detail": f"execute: {e}"})
            # Don't abort on single action errors — let model adapt
            continue

        # ── Settle ───────────────────────────────────────────────────────────
        if action.needs_settle and settle_wait > 0:
            await asyncio.sleep(settle_wait)


def _new_session_id() -> str:
    import uuid
    return uuid.uuid4().hex[:12]
