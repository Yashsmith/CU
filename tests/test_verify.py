"""V2 verify + recovery tests (PRD §§31-32), incl. loop integration. Offline."""
import io

import pytest
from PIL import Image

from agent.actions import Action
from agent.adapters import CodeRequest, ModelResult, Observation
from agent.computer import Computer
from agent.executor import PersistentExecutor
from agent.loop import run
from agent.recovery import MAX_RETRIES, RecoveryPolicy, alternate_action
from agent.verify import judge_messages, screenshots_differ, verify_action
from sandbox.mock import MockSandbox


def _png(color: tuple[int, int, int], box: tuple[int, int, int, int] | None = None) -> bytes:
    from PIL import ImageDraw

    img = Image.new("RGB", (160, 90), color)
    if box:
        ImageDraw.Draw(img).rectangle(box, fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ------------------------------------------------------------------ rules ---
def test_differ_identical_vs_changed():
    same = _png((10, 10, 10))
    changed, score = screenshots_differ(same, same)
    assert not changed and score == 0
    other = _png((10, 10, 10), box=(10, 10, 100, 60))
    changed2, score2 = screenshots_differ(same, other)
    assert changed2 and score2 > 0


async def test_verify_matrix():
    same, other = _png((5, 5, 5)), _png((5, 5, 5), box=(0, 0, 150, 80))
    # non-settle actions pass without scrutiny
    r = await verify_action(Action(type="move", x=1, y=1), same, same)
    assert r.ok
    # significant action, no change -> fail
    r = await verify_action(Action(type="click", x=1, y=1), same, same)
    assert not r.ok and "no visible change" in r.reason
    # significant action, changed, no judge -> ok
    r = await verify_action(Action(type="type", text="hi"), same, other)
    assert r.ok and r.changed

    async def yes(msgs):
        assert msgs[0]["role"] == "system" and len(msgs[1]["content"]) == 3
        return '{"confirmed": true, "reason": "search box shows UBS"}'

    async def no(msgs):
        return 'noise {"confirmed": false, "reason": "nothing happened"} trailing'

    async def broken(msgs):
        raise RuntimeError("judge down")

    r = await verify_action(Action(type="click", x=1, y=1), same, other, "t", yes)
    assert r.ok and "search box" in r.reason
    r = await verify_action(Action(type="click", x=1, y=1), same, other, "t", no)
    assert not r.ok
    r = await verify_action(Action(type="click", x=1, y=1), same, other, "t", broken)
    assert not r.ok and "judge error" in r.reason
    # force verifies even non-settle actions
    r = await verify_action(Action(type="wait", seconds=1), same, same, force=True)
    assert not r.ok


def test_judge_messages_shape():
    msgs = judge_messages("t", {"type": "click"}, "eA==", "eQ==")
    assert msgs[0]["role"] == "system"
    assert "UNTRUSTED" in msgs[0]["content"]


# --------------------------------------------------------------- recovery ---
def test_policy_transitions():
    p = RecoveryPolicy(max_retries=2)
    d, _ = p.next("boom")
    assert d == "retry" and not p.exhausted
    d, _ = p.next("boom")
    assert d == "alternate" and not p.exhausted
    d, detail = p.next("boom")
    assert d == "give_up" and p.exhausted and "exhausted" in detail
    p.note_success()
    assert not p.exhausted and p.failures == 0
    d, _ = p.next("x")
    assert d == "retry"


def test_alternate_nudges_clicks():
    alt = alternate_action(Action(type="click", x=100, y=100))
    assert (alt.x, alt.y) == (100 + 15, 100 + 15)
    w = alternate_action(Action(type="wait", seconds=1))
    assert w.seconds == 2.0
    t = alternate_action(Action(type="type", text="hi"))
    assert t.text == "hi"


# ------------------------------------------------------- loop integration ---
class DecideModel:
    def __init__(self, script):
        self.script = list(script)

    async def decide(self, task, screenshot, history, width=1280, height=800,
                     previous_screenshot=None):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


async def test_loop_verify_retry_then_success(tmp_path):
    class FlakyOnce(MockSandbox):
        def __init__(self):
            super().__init__()
            self.n = 0

        async def act(self, payload):
            if payload.get("type") == "click":
                self.n += 1
                if self.n == 1:
                    # succeed at wire level but change nothing observable:
                    return {"ok": True, "result": "no-op", "action": payload}
            return await super().act(payload)

    comp = Computer(FlakyOnce(), settle_wait=0)
    model = DecideModel([Action(type="click", x=5, y=5), Action(type="done")])
    res = await run("t", model, comp, max_steps=10, max_runtime=30, settle_wait=0,
                    session_root=tmp_path, session_id="v1", verbose=False,
                    verify=True)
    # first click changed nothing -> retry (pending) succeeds visibly -> done
    assert res.status == "done", [e for e in res.events if e["kind"] in ("verify", "recover")]
    kinds = [e["kind"] for e in res.events]
    assert "verify" in kinds and "recover" in kinds


async def test_loop_recovery_exhausted(tmp_path):
    class AlwaysFail(MockSandbox):
        async def act(self, payload):
            raise RuntimeError("wire down")

    comp = Computer(AlwaysFail(), settle_wait=0)
    model = DecideModel([Action(type="click", x=5, y=5)] * 10)
    res = await run("t", model, comp, max_steps=10, max_runtime=30, settle_wait=0,
                    session_root=tmp_path, session_id="v2", verbose=False,
                    verify=True, max_recovery=2)
    assert res.status == "recovery_exhausted" and res.steps == 3
    decisions = [e["detail"].split(":")[0] for e in res.events if e["kind"] == "recover"]
    assert decisions == ["retry", "alternate", "give_up"]


async def test_loop_v1_legacy_untouched_by_recovery(tmp_path):
    """verify=False keeps V1 continue-on-error semantics (no give_up)."""
    class AlwaysFail(MockSandbox):
        async def act(self, payload):
            raise RuntimeError("wire down")

    comp = Computer(AlwaysFail(), settle_wait=0)
    model = DecideModel([Action(type="click", x=1, y=1), Action(type="done")])
    res = await run("t", model, comp, max_steps=5, max_runtime=30, settle_wait=0,
                    session_root=tmp_path, session_id="v3", verbose=False)
    assert res.status == "done"


class FakeAdapter:
    def __init__(self, script):
        self.script = list(script)

    async def run(self, task, observation, state=None):
        assert observation.screenshot.startswith(b"\x89PNG")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


async def test_loop_code_mode(tmp_path):
    m = MockSandbox()
    comp = Computer(m, settle_wait=0)
    ex = PersistentExecutor()
    comp.attach_executor(ex)
    ex.bind_computer(comp)
    try:
        code = CodeRequest(code="computer.click(9, 9)\n_ = 'did click'")
        adapter = FakeAdapter([ModelResult(code=code), ModelResult(done=True)])
        res = await run("click at 9,9 via code", None, comp, max_steps=10,
                        max_runtime=30, settle_wait=0, session_root=tmp_path,
                        session_id="v4", verbose=False, adapter=adapter, verify=True)
        assert res.status == "done"
        assert m.clicks == [(9, 9)]
        assert any("code" in str(e.get("detail", "")) for e in res.events
                   if e["kind"] == "act")
    finally:
        ex.close()


async def test_loop_batched_astra_style(tmp_path):
    m = MockSandbox()
    comp = Computer(m, settle_wait=0)
    adapter = FakeAdapter([
        ModelResult(actions=[Action(type="click", x=1, y=1),
                             Action(type="type", text="UBS")]),
        ModelResult(done=True),
    ])
    res = await run("t", None, comp, max_steps=10, max_runtime=30, settle_wait=0,
                    session_root=tmp_path, session_id="v5", verbose=False,
                    adapter=adapter)
    assert res.status == "done"
    assert [a["type"] for a in m.actions] == ["click", "type"]


class FakeBrowser:
    width, height = 1600, 900

    def __init__(self, png: bytes):
        self.png = png
        self.execd: list = []

    async def screenshot(self):
        return self.png

    async def exec(self, action):
        self.execd.append(action.type)
        return {"ok": True}


async def test_loop_browser_lane(tmp_path):
    m = MockSandbox()
    comp = Computer(m, settle_wait=0)
    shot = await m.screenshot()
    fb = FakeBrowser(shot)
    model = DecideModel([Action(type="goto", text="https://example.com"),
                         Action(type="done")])
    res = await run("Read https://example.com", model, comp, max_steps=10,
                    max_runtime=30, settle_wait=0, session_root=tmp_path,
                    session_id="v6", verbose=False, lane="browser", browser=fb)
    assert res.status == "done" and fb.execd == ["goto"]
    assert m.actions == []  # desktop untouched
