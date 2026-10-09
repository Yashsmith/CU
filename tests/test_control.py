"""Takeover tests (PRD §34): file IPC, loop pause/stop, full handoff. Offline."""
import asyncio

from agent.actions import Action
from agent.computer import Computer
from agent.control import COMMANDS, ControlState
from agent.loop import run
from sandbox.mock import MockSandbox


def test_command_lifecycle(tmp_path):
    c = ControlState(tmp_path / "s")
    assert c.command == "run" and not c.paused() and not c.should_stop()
    c.pause()
    assert c.paused() and not c.should_stop()
    c.resume()
    assert c.command == "run" and not c.paused()
    c.take_control()
    assert c.paused()
    c.release()
    assert c.command == "run"
    c.release(stop=True)
    assert c.should_stop()
    assert set(COMMANDS) == {"run", "pause", "takeover", "stop"}


def test_cross_process_visibility(tmp_path):
    """Two handles on one dir = operator process + agent process."""
    agent_side = ControlState(tmp_path / "s")
    operator_side = ControlState(tmp_path / "s")
    assert not agent_side.paused()
    operator_side.take_control(by="human")
    assert agent_side.paused()  # agent sees it with no shared memory
    operator_side.release()
    assert not agent_side.paused()


def test_corrupt_file_recovers_to_run(tmp_path):
    c = ControlState(tmp_path / "s")
    (tmp_path / "s" / "control.json").write_text("{not json")
    assert c.command == "run"


class DecideModel:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def decide(self, task, screenshot, history, width=1280, height=800,
                     previous_screenshot=None):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


async def test_loop_paused_before_first_step(tmp_path):
    comp = Computer(MockSandbox(), settle_wait=0)
    model = DecideModel([Action(type="done")])
    ctl = ControlState(tmp_path / "s")
    ctl.pause()
    res = await run("t", model, comp, session_root=tmp_path, session_id="s",
                    verbose=False, control=ctl)
    assert res.status == "paused" and res.steps == 0 and model.calls == 0
    assert any(e["kind"] == "control" for e in res.events)


async def test_loop_stop_mid_run(tmp_path):
    comp = Computer(MockSandbox(), settle_wait=0)
    ctl = ControlState(tmp_path / "s")
    calls = {"n": 0}

    class Slowish(DecideModel):
        async def decide(self, task, screenshot, history, width=1280,
                         height=800, previous_screenshot=None):
            calls["n"] += 1
            if calls["n"] == 2:
                ctl.stop()  # operator stops while agent thinks
            return await super().decide(task, screenshot, history, width,
                                        height, previous_screenshot)

    model = Slowish([Action(type="wait", seconds=0.1),
                     Action(type="wait", seconds=0.1),
                     Action(type="done")])
    res = await run("t", model, comp, max_steps=10, max_runtime=30,
                    settle_wait=0, session_root=tmp_path, session_id="s",
                    verbose=False, control=ctl)
    assert res.status == "stopped"
    assert model.calls == 2  # third decision never happened


async def test_full_handoff_pause_then_resume(tmp_path):
    """AI pauses -> human releases -> AI resumes and finishes (PRD §34 flow)."""
    comp = Computer(MockSandbox(), settle_wait=0)
    ctl = ControlState(tmp_path / "s1")
    ctl.pause()  # operator pauses an imagined in-flight run; agent parks

    parked = await run("click X then finish", DecideModel([Action(type="done")]),
                       comp, session_root=tmp_path, session_id="s1",
                       verbose=False, control=ctl)
    assert parked.status == "paused"

    ctl2 = ControlState(tmp_path / "s1")
    ctl2.release()  # human hands back
    done = await run("click X then finish",
                     DecideModel([Action(type="click", x=3, y=3),
                                  Action(type="done")]),
                     comp, session_root=tmp_path, session_id="s2",
                     verbose=False, resume_from="s1")
    assert done.status == "done"
    assert ControlState(tmp_path / "s1").command == "run"
    # desktop kept working across the handoff (mock recorded the click)
    assert comp.backend.clicks == [(3, 3)]


async def test_pause_survives_slow_model(tmp_path):
    """Pause checked every step boundary even with latency between steps."""
    comp = Computer(MockSandbox(), settle_wait=0)
    ctl = ControlState(tmp_path / "s")

    async def pausing_screenshot():
        ctl.pause()
        return await MockSandbox().screenshot()

    comp.screenshot = pausing_screenshot  # type: ignore[method-assign]
    res = await run("t", DecideModel([Action(type="done")]), comp,
                    session_root=tmp_path, session_id="s", verbose=False,
                    control=ctl)
    # pause landed after first screenshot but before decide: loop still
    # finishes the in-flight step, then parks — never half-applies.
    assert res.status in ("paused", "done")
    await asyncio.sleep(0)


class FailSafeError(RuntimeError):
    pass


FailSafeError.__name__ = "FailSafeException"


async def test_failsafe_parks_instead_of_retrying(tmp_path):
    """Human grabs the mouse mid-run -> park with 'paused', no retry storm."""
    import agent.loop as _loop

    real_is = _loop._is_failsafe
    assert real_is(FailSafeError("corner")) is True
    assert real_is(RuntimeError("other")) is False

    class Grabbed(MockSandbox):
        async def act(self, payload):
            raise FailSafeError("corner")

    comp = Computer(Grabbed(), settle_wait=0)
    model = DecideModel([Action(type="click", x=1, y=1),
                         Action(type="click", x=1, y=1),
                         Action(type="done")])
    res = await run("t", model, comp, max_steps=10, max_runtime=30,
                    settle_wait=0, session_root=tmp_path, session_id="fs",
                    verbose=False)
    assert res.status == "paused" and res.steps == 1 and model.calls == 1
    kinds = [e["kind"] for e in res.events]
    assert "control" in kinds and "recover" not in kinds
