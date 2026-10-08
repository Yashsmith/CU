"""Agent loop tests — scripted FakeModel + MockSandbox, no network."""
import json
from pathlib import Path

from agent.actions import Action
from agent.computer import Computer
from agent.loop import run
from sandbox.mock import MockSandbox


class FakeModel:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def decide(self, task, screenshot, history, width=1280, height=800,
                     previous_screenshot=None):
        self.calls.append({"task": task, "history_len": len(history or [])})
        assert screenshot.startswith(b"\x89PNG")
        if not self.script:
            return Action(type="done")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


async def test_loop_success_writes_session(tmp_path):
    m = MockSandbox()
    c = Computer(m, settle_wait=0)
    model = FakeModel([
        Action(type="click", x=130, y=270),
        Action(type="type", text="UBS"),
        Action(type="press", key="Enter"),
        Action(type="done"),
    ])
    res = await run("Open Chromium and search for UBS", model, c,
                    max_steps=30, max_runtime=60, settle_wait=0,
                    session_root=tmp_path, session_id="t1", verbose=False)
    assert res.status == "done" and res.steps == 4
    assert [a["type"] for a in m.actions] == ["click", "type", "key"]
    assert len(model.calls) == 4
    # session artifacts
    d = Path(res.session_dir)
    assert (d / "meta.json").exists() and (d / "events.jsonl").exists()
    assert (d / "screenshots" / "1.png").exists()
    assert (d / "screenshots" / "final.png").exists()
    meta = json.loads((d / "meta.json").read_text())
    assert meta["status"] == "done"
    kinds = [json.loads(line)["kind"] for line in (d / "events.jsonl").read_text().splitlines()]
    assert "decide" in kinds and "act" in kinds and "done" in kinds


async def test_loop_max_steps(tmp_path):
    m = MockSandbox()
    c = Computer(m, settle_wait=0)
    model = FakeModel([Action(type="move", x=i, y=i) for i in range(50)])
    res = await run("wander", model, c, max_steps=5, max_runtime=60,
                    settle_wait=0, session_root=tmp_path, verbose=False)
    assert res.status == "max_steps" and res.steps == 5


async def test_loop_detects_click_loop(tmp_path):
    m = MockSandbox()
    c = Computer(m, settle_wait=0)
    model = FakeModel([Action(type="click", x=100, y=100)] * 5)
    res = await run("stuck", model, c, max_steps=30, max_runtime=60,
                    settle_wait=0, session_root=tmp_path, verbose=False)
    assert res.status == "loop_detected" and res.steps == 3


async def test_loop_model_error(tmp_path):
    m = MockSandbox()
    c = Computer(m, settle_wait=0)
    model = FakeModel([ValueError("boom")])
    res = await run("t", model, c, max_steps=30, max_runtime=60,
                    settle_wait=0, session_root=tmp_path, verbose=False)
    assert res.status == "model_error"


async def test_loop_timeout(tmp_path):
    m = MockSandbox()
    c = Computer(m, settle_wait=0)
    model = FakeModel([Action(type="move", x=1, y=1)] * 50)
    res = await run("t", model, c, max_steps=30, max_runtime=0,
                    settle_wait=0, session_root=tmp_path, verbose=False)
    assert res.status == "timeout" and res.steps == 0


async def test_loop_execute_error_continues(tmp_path):
    class Flaky(MockSandbox):
        async def act(self, payload):
            if payload.get("type") == "click":
                raise RuntimeError("injected click failure")
            return await super().act(payload)

    c = Computer(Flaky(), settle_wait=0)
    model = FakeModel([
        Action(type="click", x=1, y=1),  # fails, loop continues
        Action(type="done"),
    ])
    res = await run("t", model, c, max_steps=30, max_runtime=60,
                    settle_wait=0, session_root=tmp_path, verbose=False)
    assert res.status == "done" and res.steps == 2
    assert "error" in res.history[0]
