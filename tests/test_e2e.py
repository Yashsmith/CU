"""Smoke + integration + e2e (PRD §17-19). No network except gated live tests."""
import json
import os
from pathlib import Path

import pytest

from agent.actions import Action
from agent.computer import Computer
from agent.loop import run
from agent.main import DEMO_TASKS, build_parser, load_dotenv, probe
from sandbox.mock import MockSandbox


def test_demo_tasks_defined():
    assert len(DEMO_TASKS) == 5
    assert "Chromium" in DEMO_TASKS["1"] and "UBS" in DEMO_TASKS["2"]


def test_cli_parser_defaults():
    args = build_parser().parse_args([])
    assert args.max_steps == 30 and args.backend in ("http", "mock", "local")


def test_load_dotenv_does_not_override(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("FOO_TEST_XYZ=fromfile\n")
    monkeypatch.setenv("FOO_TEST_XYZ", "fromenv")
    load_dotenv(str(f))
    assert os.environ["FOO_TEST_XYZ"] == "fromenv"
    monkeypatch.delenv("FOO_TEST_XYZ")
    load_dotenv(str(f))
    assert os.environ["FOO_TEST_XYZ"] == "fromfile"


async def test_probe_mock_ok(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = await probe("mock", "http://127.0.0.1:7090", "", save_to="probe.png")
    assert rc == 0 and (tmp_path / "probe.png").exists()


# ---- e2e: scripted brain + mock body, PRD demo task 2 -----------------------

class ScriptedBrain:
    """Deterministic stand-in that performs demo task 2 end to end."""

    def __init__(self):
        self.n = 0

    async def decide(self, task, screenshot, history, width=1280, height=800,
                     previous_screenshot=None):
        self.n += 1
        assert "UBS" in task
        if self.n == 1:
            return Action(type="click", x=130, y=270)      # Chromium icon
        if self.n == 2:
            return Action(type="click", x=490, y=440)      # address bar
        if self.n == 3:
            return Action(type="type", text="UBS")
        if self.n == 4:
            return Action(type="press", key="Enter")
        return Action(type="done")


async def test_e2e_demo_task_2_mock(tmp_path):
    """V1 milestone on a synthetic desktop: click browser, focus bar, type, submit."""
    sb = MockSandbox()
    comp = Computer(sb, settle_wait=0)
    brain = ScriptedBrain()
    res = await run(DEMO_TASKS["2"], brain, comp, max_steps=30, max_runtime=60,
                    settle_wait=0, session_root=tmp_path, session_id="e2e2", verbose=False)
    assert res.status == "done" and res.steps == 5
    wire = [a["type"] for a in sb.actions]
    assert wire == ["click", "click", "type", "key"]
    assert sb.typed_text[0] == "UBS"
    # V1 success criteria evidence: cursor moved, click landed, text appeared,
    # agent observed again (4 screenshots + final), then stopped.
    shots = sorted((tmp_path / "e2e2" / "screenshots").glob("*.png"))
    assert len(shots) >= 5
    events = (tmp_path / "e2e2" / "events.jsonl").read_text().splitlines()
    assert len(events) >= 9  # decide+act per step + done


async def test_e2e_all_demo_tasks_smoke(tmp_path):
    """Every PRD §18 task runs to done with a trivial scripted brain."""
    for tid, task in DEMO_TASKS.items():
        sb = MockSandbox()
        comp = Computer(sb, settle_wait=0)

        class trivial:
            done = False

            async def decide(self, task, screenshot, history, width=1280,
                             height=800, previous_screenshot=None):
                if not trivial.done:
                    trivial.done = True
                    return Action(type="wait", seconds=0.1)
                return Action(type="done")

        trivial.done = False
        res = await run(task, trivial(), comp, max_steps=5, max_runtime=30,
                        settle_wait=0, session_root=tmp_path,
                        session_id=f"smoke{tid}", verbose=False)
        assert res.status == "done", tid


@pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1" or not os.environ.get("GROQ_API_KEY"),
                    reason="live e2e needs RUN_LIVE=1 + GROQ_API_KEY")
async def test_e2e_live_groq_mock(tmp_path):
    """Real brain (Groq vision) + mock body: model drives ≥1 real action then we stop it."""
    from agent.model import GroqModel

    key = os.environ["GROQ_API_KEY"]
    model = GroqModel(api_key=key, model=os.environ.get("MODEL", "qwen/qwen3.8-27b"))
    sb = MockSandbox()
    comp = Computer(sb, settle_wait=0)
    res = await run("Click the Chromium icon once, then you are done.", model, comp,
                    max_steps=3, max_runtime=120, settle_wait=0,
                    session_root=tmp_path, session_id="livee2e", verbose=True)
    print(f"\nLIVE e2e: status={res.status} steps={res.steps} "
          f"actions={[a['action']['type'] for a in res.history]}")
    assert res.steps >= 1 and len(sb.actions) >= 1
    meta = json.loads((Path(res.session_dir) / "meta.json").read_text())
    assert meta["status"] in ("done", "max_steps")


def _pixels_differ(a: bytes, b: bytes) -> int:
    import io as _io

    from PIL import Image as _Image

    ia = _Image.open(_io.BytesIO(a)).convert("L")
    ib = _Image.open(_io.BytesIO(b)).convert("L")
    ha, hb = ia.histogram(), ib.histogram()
    return sum(abs(x - y) for x, y in zip(ha, hb)) // 1000


@pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1" or not os.environ.get("GROQ_API_KEY"),
                    reason="live code-exec needs RUN_LIVE=1 + GROQ_API_KEY + real sandbox")
async def test_e2e_code_exec_real_desktop():
    """V2 code mode e2e: model writes code, persistent executor runs it with
    `computer` bound to the REAL sandbox, desktop visibly changes (pixel diff)."""
    import httpx

    from agent.adapters import AstraCodeAdapter, Observation, OpenAICompatibleAdapter
    from agent.computer import Computer
    from agent.executor import PersistentExecutor
    from sandbox.client import create_sandbox

    key = os.environ["GROQ_API_KEY"]
    sb = create_sandbox("http")
    try:
        await sb.health()
    except (httpx.HTTPError, OSError) as e:
        sbc = getattr(sb, "close", None)
        if sbc:
            await sbc()
        pytest.skip(f"real sandbox unreachable: {e}")
    comp = Computer(sb, settle_wait=0)
    ex = PersistentExecutor()
    comp.attach_executor(ex)
    ex.bind_computer(comp)
    try:
        before = await comp.screenshot()

        # 1. Deterministic part: hand-written code through the same path.
        r = await comp.execute_python(
            "computer.press('ctrl+alt+t')\n_ = 'terminal requested'")
        assert r.ok, r.error

        # 2. Model part: Groq writes the next step as code; we run it.
        inner = OpenAICompatibleAdapter(api_key=key, model="qwen/qwen3.8-27b",
                                        base_url="https://api.groq.com/openai/v1")
        obs = Observation(task="A terminal should now be open. Take a screenshot "
                               "and report its byte size.",
                          screenshot=before, width=1600, height=900)
        mres = await AstraCodeAdapter(inner).run(obs.task, obs)
        assert mres.code and "computer" in mres.code.code, mres.code
        print(f"\nLIVE model code:\n{mres.code.code}")
        r2 = await comp.execute_python(mres.code.code, timeout=60)
        assert r2.ok, r2.error

        after = await comp.screenshot()
        diff = _pixels_differ(before, after)
        print(f"\nLIVE code-exec: screenshot bytes {len(before)} -> {len(after)}, diff={diff}")
        assert diff > 0, "desktop did not visibly change"
    finally:
        ex.close()
        await comp.close()


@pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1" or not os.environ.get("GROQ_API_KEY"),
                    reason="live browser-lane needs RUN_LIVE=1 + GROQ_API_KEY")
async def test_e2e_browser_lane_with_vision():
    """V2 browser lane live: real Playwright Chromium + Groq vision sees the page."""
    from agent.actions import Action
    from agent.adapters import Observation, OpenAICompatibleAdapter
    from agent.browser import PlaywrightBrowser
    from agent.router import route

    assert route("Read https://example.com and summarize it.").lane == "browser"
    b = PlaywrightBrowser(headless=True)
    await b.start()
    try:
        await b.exec(Action(type="goto", text="https://example.com"))
        shot = await b.screenshot()
        key = os.environ["GROQ_API_KEY"]
        inner = OpenAICompatibleAdapter(api_key=key, model="qwen/qwen3.8-27b",
                                        base_url="https://api.groq.com/openai/v1")
        obs = Observation(task="What website is open? If it is example.com, you are done.",
                          screenshot=shot, width=1600, height=900)
        res = await inner.run(obs.task, obs)
        print(f"\nLIVE browser-lane vision: done={res.done} "
              f"action={res.single_action}")
        assert res.done or res.single_action is not None
    finally:
        await b.close()
