"""PRD §15-16 + §20-21 agent loop: observe -> decide -> act -> observe ...

V1 path (model.decide, desktop lane, verify=False) is behavior-identical.
V2 additions (all opt-in, all default-off): ModelAdapter (+code results),
browser lane, verification + recovery (PRD §§28-32).
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .actions import Action
from .computer import Computer

LOOP_GUARD_RADIUS = 10
LOOP_GUARD_REPEATS = 3


@dataclass
class LoopResult:
    status: str  # done | max_steps | timeout | loop_detected | model_error
    #            | recovery_exhausted | stopped | paused
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
    # ---- V2 opt-ins (PRD §§28-32, 34). All default to V1 behavior. ----
    adapter: Any | None = None,  # ModelAdapter (V2 brain); else model.decide
    lane: str = "desktop",  # "desktop" | "browser"
    browser: Any | None = None,  # PlaywrightBrowser for lane="browser"/goto
    verify: bool = False,  # PRD §31 verification after significant actions
    judge: Callable[[list[dict[str, Any]]], Awaitable[str]] | None = None,
    max_recovery: int = 2,  # PRD §32 MAX_RETRIES (only when verify=True)
    control: Any | None = None,  # ControlState for pause/takeover (PRD §34)
) -> LoopResult:
    """Run until done / limits / loop / error (V1) / recovery-exhausted / paused (V2)."""
    from .recovery import RecoveryPolicy, alternate_action
    from .verify import verify_action

    store = SessionStore(session_root, session_id)
    store.write_meta(task, model_name or getattr(model or adapter, "model", "?"))
    history: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    recent_clicks: list[Action] = []
    state: dict[str, Any] = {}
    policy = RecoveryPolicy(max_retries=max_recovery)
    pending: Action | None = None
    recovering = verify  # recovery engages only in V2 verify mode (V1: legacy continue)
    start = time.monotonic()
    width, height = 1280, 800
    try:
        if lane == "browser" and browser is not None:
            width, height = browser.width, browser.height
        else:
            width, height = await computer.size()
    except Exception:
        pass

    def emit(ev: dict[str, Any]) -> None:
        events.append(store.log_event(ev))
        if verbose:
            step = ev.get("step", "-")
            print(f"[step {step}] {ev.get('kind')}: {ev.get('detail', '')}", flush=True)

    async def observe() -> bytes:
        if lane == "browser":
            if browser is None:
                raise RuntimeError("lane=browser but no browser attached")
            return await browser.screenshot()
        return await computer.screenshot()

    async def do_execute(action: Action) -> dict[str, Any]:
        if action.type == "goto" or lane == "browser":
            if browser is None:
                raise RuntimeError("browser action but no browser attached")
            return await browser.exec(action)
        return await computer.execute(action)

    def finish(status: str, step: int) -> LoopResult:
        store.finish(status)
        return LoopResult(status, step, task, store.session_id, str(store.dir),
                          history, events)

    step = 0
    previous_shot: bytes | None = None
    while True:
        if control is not None and control.paused():
            emit({"kind": "control", "step": step, "detail": "paused by operator"})
            store.finish("paused")
            return LoopResult("paused", step, task, store.session_id, str(store.dir),
                              history, events)
        if time.monotonic() - start > max_runtime:
            emit({"kind": "limit", "step": step, "detail": f"MAX_RUNTIME {max_runtime}s exceeded"})
            return finish("timeout", step)
        if step >= max_steps:
            emit({"kind": "limit", "step": step, "detail": f"MAX_STEPS {max_steps} exceeded"})
            return finish("max_steps", step)
        step += 1
        try:
            shot = await observe()
        except Exception as e:
            emit({"kind": "error", "step": step, "detail": f"screenshot failed: {e}"})
            return finish("model_error", step)
        store.save_shot(step, shot)

        # ---- decide (fresh) or pending recovery action ----
        code_request = None
        batched: list[Action] = []
        from_pending = pending is not None
        if pending is not None:
            action = pending
            pending = None
            emit({"kind": "decide", "step": step,
                  "detail": {"recovery": action.model_dump()}})
        else:
            try:
                if adapter is not None:
                    from .adapters import Observation

                    obs = Observation(task=task, screenshot=shot, width=width,
                                      height=height, history=history,
                                      previous_screenshot=previous_shot)
                    res = await adapter.run(task, obs, state)
                    if res.reasoning.startswith("previous_response_id="):
                        state["previous_response_id"] = res.reasoning.split("=", 1)[1]
                    if res.done:
                        store.save_shot(step, shot, final=True)
                        history.append({"done": True})
                        emit({"kind": "done", "step": step, "detail": "task complete"})
                        return finish("done", step)
                    if res.code is not None:
                        code_request = res.code
                        action = Action(type="wait", seconds=0.1)  # placeholder
                    else:
                        if not res.actions:
                            raise ValueError("adapter returned no actions")
                        action, batched = res.actions[0], res.actions[1:]
                else:
                    action = await model.decide(
                        task=task, screenshot=shot, history=history,
                        width=width, height=height, previous_screenshot=previous_shot)
            except Exception as e:
                emit({"kind": "error", "step": step, "detail": f"model decide failed: {e}"})
                return finish("model_error", step)
        previous_shot = shot
        if code_request is None:
            emit({"kind": "decide", "step": step, "detail": action.model_dump()})

        if code_request is None and action.is_terminal:
            store.save_shot(step, shot, final=True)
            store.finish("done")
            history.append({"action": action.model_dump()})
            emit({"kind": "done", "step": step, "detail": "task complete"})
            return finish("done", step)

        # Anti-loop guard on freshly decided clicks (recovery retries exempt).
        if code_request is None and not from_pending and \
                action.type in ("click", "double_click", "right_click"):
            recent_clicks.append(action)
            recent_clicks = recent_clicks[-LOOP_GUARD_REPEATS:]
            if (len(recent_clicks) == LOOP_GUARD_REPEATS
                    and all(_same_spot(recent_clicks[0], c) for c in recent_clicks[1:])):
                emit({"kind": "limit", "step": step,
                      "detail": f"same {action.type} {LOOP_GUARD_REPEATS}x at "
                                f"({action.x},{action.y}) — stopping"})
                return finish("loop_detected", step)
        elif code_request is None:
            recent_clicks.clear()

        # ---- act ----
        before = shot
        exec_error: str | None = None
        try:
            if code_request is not None:
                emit({"kind": "decide", "step": step,
                      "detail": {"code": code_request.code[:300]}})
                result = await computer.execute_python(code_request.code)
                state["last_exec"] = (
                    f"ok={result.ok} stdout={result.stdout[:500]} "
                    f"error={result.error[:500]} result={result.result[:200]}")
                history.append({"code": code_request.code[:500],
                                "result": state["last_exec"]})
                emit({"kind": "act", "step": step,
                      "detail": f"code -> {state['last_exec'][:160]}"})
                if not result.ok and not result.timed_out:
                    exec_error = f"code failed: {result.error[:200]}"
            else:
                result = await do_execute(action)
                for extra in batched:
                    await do_execute(extra)
                history.append({"action": action.model_dump(),
                                "result": str(result)[:200]})
                emit({"kind": "act", "step": step,
                      "detail": f"{action.type} -> {str(result)[:120]}"})
        except Exception as e:
            exec_error = f"execute failed: {e}"
            history.append({"action": (action.model_dump()
                                       if code_request is None else {"code": True}),
                            "error": str(e)})
            emit({"kind": "error", "step": step, "detail": exec_error})

        if exec_error and not recovering:
            continue  # V1 legacy: log and carry on

        # ---- verify + recover (V2, PRD §§31-32) ----
        if recovering:
            if exec_error:
                ok, reason = False, exec_error
            else:
                if action.needs_settle and settle_wait > 0:
                    await asyncio.sleep(settle_wait)
                try:
                    after = await observe()
                except Exception as e:
                    after = before
                    ok, reason = False, f"re-observe failed: {e}"
                else:
                    if code_request is not None:
                        from .actions import Action as _A

                        vaction = _A(type="type", text="code")
                        vres = await verify_action(vaction, before, after, task,
                                                   judge, force=True)
                    else:
                        vres = await verify_action(action, before, after, task, judge)
                    ok, reason = vres.ok, vres.reason
                    emit({"kind": "verify", "step": step,
                          "detail": f"ok={ok} changed={vres.changed} "
                                    f"score={vres.score} {reason}"[:300]})
            if ok:
                policy.note_success()
            else:
                decision, detail = policy.next(reason)
                emit({"kind": "recover", "step": step,
                      "detail": f"{decision}: {detail}"[:300]})
                history.append({"recover": decision, "detail": detail[:200]})
                if decision == "give_up":
                    return finish("recovery_exhausted", step)
                if decision == "retry" and code_request is None:
                    pending = action
                elif decision == "alternate" and code_request is None:
                    pending = alternate_action(action)
                # replan: loop continues; model sees failure in history
            continue

        if action.needs_settle and settle_wait > 0:
            await asyncio.sleep(settle_wait)
