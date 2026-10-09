"""Local visible-lane tests. Input-touching tests need macOS + ALLOW_REAL_INPUT
and are gated; everything else runs anywhere."""
import os
import sys

import pytest

from agent.actions import Action
from agent.computer import Computer
from agent.model import build_messages
from agent.policy import SecurityPolicy
from sandbox.local import MAC_KEY_ALIASES, LocalSandbox, frontmost_app, to_points
from sandbox.mock import MockSandbox

MAC_ONLY = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
NEEDS_INPUT = pytest.mark.skipif(
    os.environ.get("ALLOW_REAL_INPUT") != "1" or sys.platform != "darwin",
    reason="needs ALLOW_REAL_INPUT=1 on macOS (moves YOUR cursor)")


def test_activate_contract():
    a = Action(type="activate", text="TextEdit")
    assert a.needs_settle
    with pytest.raises(Exception):
        Action(type="activate", text="  ")
    with pytest.raises(ValueError, match="local-Mac-lane only"):
        a.to_sandbox_payload()


async def test_activate_flows_through_local_capable_backends():
    comp = Computer(MockSandbox(), settle_wait=0)
    out = await comp.execute(Action(type="activate", text="TextEdit"))
    assert out["ok"] and comp.backend.actions[-1]["type"] == "activate"


async def test_activate_rejected_off_local_lane():
    import httpx

    from sandbox.http_client import HTTPSandbox

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as raw:
        comp = Computer(HTTPSandbox(base_url="http://x:7090", client=raw),
                        settle_wait=0)
        with pytest.raises(ValueError, match="local-Mac-lane only"):
            await comp.execute(Action(type="activate", text="TextEdit"))


def test_scale_helper():
    from PIL import Image

    img = Image.new("RGB", (100, 50))
    assert to_points(img, 100, 50).size == (100, 50)
    assert to_points(Image.new("RGB", (200, 100)), 100, 50).size == (100, 50)


def test_key_aliases_cover_spotlight():
    assert MAC_KEY_ALIASES["cmd"] == "command"
    assert MAC_KEY_ALIASES["opt"] == "option"


def test_policy_allows_activate():
    assert SecurityPolicy().check_action(Action(type="activate", text="Safari")) == "allow"


def test_local_prompt_lines():
    msgs = build_messages("t", "eA==", lane="local")
    assert "activate" in msgs[0]["content"] and "Mac" in msgs[0]["content"]
    msgs = build_messages("t", "eA==", lane="desktop")
    assert "activate" not in msgs[0]["content"]


async def test_doctor_mock_backend():
    from agent.main import doctor

    assert await doctor("mock", "http://x", "") == 0


def test_terminal_host_app_detects():
    from agent.main import _terminal_host_app

    assert isinstance(_terminal_host_app(), str)


@MAC_ONLY
def test_frontmost_returns_app_name():
    assert len(frontmost_app()) > 0


@MAC_ONLY
def test_activate_bogus_app_fails_cleanly():
    from sandbox.local import activate_app

    with pytest.raises(RuntimeError):
        activate_app("NoSuchAppXYZ123")


@MAC_ONLY
async def test_local_screenshot_matches_points():
    loc = LocalSandbox()
    shot = await loc.screenshot()
    assert shot.startswith(b"\x89PNG")
    from PIL import Image
    import io

    img = Image.open(io.BytesIO(shot))
    assert (img.width, img.height) == await loc.size()


@NEEDS_INPUT
async def test_local_visible_wiggle():
    """Deliberately moves YOUR cursor ±3px and back. Watch it."""
    loc = LocalSandbox()
    before = await loc.screenshot()
    await loc.act({"type": "move", "x": 700, "y": 450})
    await loc.act({"type": "move", "x": 703, "y": 450})
    import pyautogui

    assert pyautogui.position().x == 703
    await loc.act({"type": "move", "x": 700, "y": 450})
    after = await loc.screenshot()
    assert before.startswith(b"\x89PNG") and after.startswith(b"\x89PNG")
