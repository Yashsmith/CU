"""V2 session state tests (PRD §33): store roundtrip, list, replay, resume."""
import json

import pytest

from agent.actions import Action
from agent.computer import Computer
from agent.loop import run
from agent.sessions import SessionStore
from sandbox.mock import MockSandbox


def _make_session(tmp_path, sid="s1", task="do things", n_steps=3):
    st = SessionStore(tmp_path, sid)
    st.write_meta(task, "m")
    shot = MockSandbox().render()
    for i in range(1, n_steps + 1):
        st.save_shot(i, shot)
        st.log_event({"kind": "decide", "step": i,
                      "detail": {"action": {"type": "click", "x": i, "y": i}}})
        st.log_event({"kind": "act", "step": i, "detail": "click -> ok"})
        st.set_step(i)
    st.finish("max_steps")
    return st


def test_roundtrip_and_replay(tmp_path):
    _make_session(tmp_path)
    s = SessionStore.load(tmp_path, "s1")
    assert (s.id, s.task, s.model, s.step, s.status) == (
        "s1", "do things", "m", 3, "max_steps")
    assert len(s.history) == 3
    assert s.history[0]["action"]["x"] == 1
    assert s.started > 0 and s.ended >= s.started


def test_load_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        SessionStore.load(tmp_path, "nope")


def test_list_newest_first(tmp_path):
    _make_session(tmp_path, "a", n_steps=1)
    _make_session(tmp_path, "b", n_steps=2)
    ids = [s.id for s in SessionStore.list(tmp_path)]
    assert ids == ["b", "a"]
    assert SessionStore.list(tmp_path / "empty") == []


def test_meta_step_tracking(tmp_path):
    st = _make_session(tmp_path)
    meta = json.loads((tmp_path / "s1" / "meta.json").read_text())
    assert meta["step"] == 3 and meta["status"] == "max_steps"
    assert sorted(p.name for p in (tmp_path / "s1" / "screenshots").glob("*.png")) == [
        "1.png", "2.png", "3.png"]
    assert [n for n in st.shots()] == ["1.png", "2.png", "3.png"]


class DecideModel:
    def __init__(self, script):
        self.script = list(script)
        self.seen_history_lens: list[int] = []

    async def decide(self, task, screenshot, history, width=1280, height=800,
                     previous_screenshot=None):
        self.seen_history_lens.append(len(history))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


async def test_loop_resume_seeds_history(tmp_path):
    m = MockSandbox()
    comp = Computer(m, settle_wait=0)
    first = DecideModel([Action(type="wait", seconds=0.1), Action(type="done")])
    r1 = await run("original task", first, comp, max_steps=5, max_runtime=30,
                   settle_wait=0, session_root=tmp_path, session_id="orig",
                   verbose=False)
    assert r1.status == "done"

    second = DecideModel([Action(type="done")])
    r2 = await run("follow-up task", second, comp, max_steps=5, max_runtime=30,
                   settle_wait=0, session_root=tmp_path, session_id="cont",
                   verbose=False, resume_from="orig")
    assert r2.status == "done"
    # model saw the prior session's history on its first decision
    assert second.seen_history_lens[0] >= 2
    meta = json.loads((tmp_path / "cont" / "meta.json").read_text())
    assert meta["resumed_from"] == "orig" and meta["task"] == "follow-up task"


async def test_loop_resume_missing_fails(tmp_path):
    comp = Computer(MockSandbox(), settle_wait=0)
    with pytest.raises(FileNotFoundError):
        await run("t", DecideModel([Action(type="done")]), comp,
                  session_root=tmp_path, session_id="x", verbose=False,
                  resume_from="ghost")
