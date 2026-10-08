"""PRD §15-16 + §20-21 agent loop: observe -> decide -> act -> observe ..."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .actions import Action
from .computer import Computer

LOOP_GUARD_RADIUS = 10
LOOP_GUARD_REPEATS = 3


@dataclass
class LoopResult:
    status: str  # done | max_steps | timeout | loop_detected | model_error | stopped
    steps: int
    task: str
    session_id: str
    session_dir: str
    history: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)


class SessionStore:
    """Append-only session: meta.json + events.jsonl + step PNGs (cf. desktop-use)."""

    def __init__(self, root: str | Path, session_id: str | None = None) -> None:
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.dir = Path(root) / self.session_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._seq = 0

    def write_meta(self, task: str, model: str) -> None:
        (self.dir / "meta.json").write_text(json.dumps(
            {"id": self.session_id, "task": task, "model": model,
             "started": time.time(), "status": "running"}, indent=2))

    def log_event(self, event: dict[str, Any]) -> dict[str, Any]:
        self._seq += 1
        event = {"seq": self._seq, "t": time.time(), **event}
        with open(self.dir / "events.jsonl", "a") as f:
            f.write(json.dumps(event) + "\n")
        return event

    def save_shot(self, step: int, png: bytes, final: bool = False) -> str:
        name = "final.png" if final else f"{step}.png"
        (self.dir / name).write_bytes(png)
        return name

    def finish(self, status: str) -> None:
        meta_path = self.dir / "meta.json"
        try:
            meta = json.loads(meta_path.read_text())
        except FileNotFoundError:
            meta = {"id": self.session_id}
        meta.update({"status": status, "ended": time.time()})
        meta_path.write_text(json.dumps(meta, indent=2))


def _same_spot(a: Action, b: Action, radius: int = LOOP_GUARD_RADIUS) -> bool:
    if a.type != b.type or a.x is None or b.x is None or a.y is None or b.y is None:
        return False
    return abs(a.x - b.x) <= radius and abs(a.y - b.y) <= radius


async def run(
    task: str,
    model: Any,
    computer: Computer,
    max_steps: int = 30,
    max_runtime: float = 300,
    settle_wait: float = 0.5,
    session_root: str | Path = "sessions",
    session_id: str | None = None,
    model_name: str = "",
    verbose: bool = True,
) -> LoopResult:
    """Minimal V1 loop (PRD §15). Returns when done / limits / loop / error."""
    store = SessionStore(session_root, session_id)
    store.write_meta(task, model_name or getattr(model, "model", "?"))
    history: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    recent_clicks: list[Action] = []
    start = time.monotonic()
    width, height = 1280, 800
    try:
        width, height = await computer.size()
    except Exception:
        pass

    def emit(ev: dict[str, Any]) -> None:
        events.append(store.log_event(ev))
        if verbose:
            step = ev.get("step", "-")
            print(f"[step {step}] {ev.get('kind')}: {ev.get('detail', '')}", flush=True)

    step = 0
    previous_shot: bytes | None = None
    while True:
        if time.monotonic() - start > max_runtime:
            store.finish("timeout")
            emit({"kind": "limit", "step": step, "detail": f"MAX_RUNTIME {max_runtime}s exceeded"})
            return LoopResult("timeout", step, task, store.session_id, str(store.dir),
                              history, events)
        if step >= max_steps:
            store.finish("max_steps")
            emit({"kind": "limit", "step": step, "detail": f"MAX_STEPS {max_steps} exceeded"})
            return LoopResult("max_steps", step, task, store.session_id, str(store.dir),
                              history, events)
        step += 1
        try:
            shot = await computer.screenshot()
        except Exception as e:
            store.finish("model_error")
            emit({"kind": "error", "step": step, "detail": f"screenshot failed: {e}"})
            return LoopResult("model_error", step, task, store.session_id, str(store.dir),
                              history, events)
        store.save_shot(step, shot)
        try:
            action = await model.decide(task=task, screenshot=shot, history=history,
                                        width=width, height=height,
                                        previous_screenshot=previous_shot)
        except Exception as e:
            store.finish("model_error")
            emit({"kind": "error", "step": step, "detail": f"model decide failed: {e}"})
            return LoopResult("model_error", step, task, store.session_id, str(store.dir),
                              history, events)
        previous_shot = shot
        emit({"kind": "decide", "step": step, "detail": action.model_dump()})

        if action.is_terminal:
            store.save_shot(step, shot, final=True)
            store.finish("done")
            history.append({"action": action.model_dump()})
            emit({"kind": "done", "step": step, "detail": "task complete"})
            return LoopResult("done", step, task, store.session_id, str(store.dir),
                              history, events)

        # Anti-loop guard: same click 3x in a row within 10px (desktop-use pattern).
        if action.type in ("click", "double_click", "right_click"):
            recent_clicks.append(action)
            recent_clicks = recent_clicks[-LOOP_GUARD_REPEATS:]
            if (len(recent_clicks) == LOOP_GUARD_REPEATS
                    and all(_same_spot(recent_clicks[0], c) for c in recent_clicks[1:])):
                store.finish("loop_detected")
                emit({"kind": "limit", "step": step,
                      "detail": f"same {action.type} {LOOP_GUARD_REPEATS}x at "
                                f"({action.x},{action.y}) — stopping"})
                return LoopResult("loop_detected", step, task, store.session_id,
                                  str(store.dir), history, events)
        else:
            recent_clicks.clear()

        try:
            result = await computer.execute(action)
        except Exception as e:
            emit({"kind": "error", "step": step, "detail": f"execute {action.type} failed: {e}"})
            history.append({"action": action.model_dump(), "error": str(e)})
            continue
        history.append({"action": action.model_dump(), "result": str(result)[:200]})
        emit({"kind": "act", "step": step,
              "detail": f"{action.type} -> {str(result)[:120]}"})
        if action.needs_settle and settle_wait > 0:
            await asyncio.sleep(settle_wait)
