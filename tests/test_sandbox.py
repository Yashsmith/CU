"""Tests for sandbox bodies (PRD §10-11): mock determinism, HTTP mapping, guards."""
import io

import httpx
import pytest
from PIL import Image

from agent.actions import Action
from sandbox.base import chunk_wait
from sandbox.client import create_sandbox
from sandbox.http_client import HTTPSandbox
from sandbox.local import LocalSandbox, RealInputDisabled
from sandbox.mock import MockSandbox


def test_chunk_wait_respects_5s_cap():
    assert chunk_wait(0) == []
    assert chunk_wait(2) == [2]
    assert chunk_wait(12) == [5.0, 5.0, 2.0]


async def test_mock_screenshot_is_png_1280x800():
    m = MockSandbox()
    shot = await m.screenshot()
    assert shot.startswith(b"\x89PNG")
    with Image.open(io.BytesIO(shot)) as im:
        assert (im.width, im.height) == (1280, 800)
    h = await m.health()
    assert h["ok"] and h["width"] == 1280


async def test_mock_records_actions_and_moves_cursor():
    m = MockSandbox()
    await m.act(Action(type="click", x=130, y=270).to_sandbox_payload())
    await m.act(Action(type="type", text="UBS").to_sandbox_payload())
    await m.act(Action(type="press", key="Enter").to_sandbox_payload())
    assert m.cursor == (130, 270)
    assert m.clicks == [(130, 270)]
    assert "UBS" in m.typed_text
    assert len(m.actions) == 3
    # cursor visible in next frame
    shot2 = await m.screenshot()
    assert shot2.startswith(b"\x89PNG")


async def test_http_client_screenshot_and_act_with_mock_transport():
    import io as _io

    from PIL import Image as _Image

    buf = _io.BytesIO()
    _Image.new("RGB", (1280, 800), (1, 2, 3)).save(buf, format="PNG")
    png = buf.getvalue()
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/screenshot", "/health"):
            if request.url.path == "/health":
                assert request.headers.get("X-Sandbox-Token") == "tok123"
                return httpx.Response(200, json={"ok": True, "width": 1280, "height": 800})
            return httpx.Response(200, content=png, headers={"content-type": "image/png"})
        if request.url.path == "/action":
            import json as _json

            seen["body"] = _json.loads(request.content.decode())
            return httpx.Response(200, json={"ok": True, "result": "applied"})
        return httpx.Response(404, json={"error": "nope"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        sb = HTTPSandbox(base_url="http://x:7090", token="tok123", client=raw)
        assert (await sb.screenshot()) == png
        out = await sb.act({"type": "click", "x": 5, "y": 6})
        assert out["ok"]
        assert seen["body"] == {"type": "click", "x": 5, "y": 6}
        h = await sb.health()
        assert h["width"] == 1280


async def test_http_client_chunks_long_wait():
    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        calls.append(_json.loads(request.content.decode()))
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        sb = HTTPSandbox(base_url="http://x:7090", client=raw)
        out = await sb.act({"type": "wait", "seconds": 12})
        assert out["ok"] and len(calls) == 3  # 5 + 5 + 2


def test_factory_selects_backend(monkeypatch):
    assert isinstance(create_sandbox("mock"), MockSandbox)
    assert isinstance(create_sandbox("local"), LocalSandbox)
    http = create_sandbox("http", base_url="http://127.0.0.1:7090", token="t")
    assert isinstance(http, HTTPSandbox)
    monkeypatch.setenv("SANDBOX_BACKEND", "mock")
    assert isinstance(create_sandbox(), MockSandbox)
    with pytest.raises(ValueError):
        create_sandbox("nope")


async def test_local_backend_refuses_input_by_default(monkeypatch):
    monkeypatch.delenv("ALLOW_REAL_INPUT", raising=False)
    loc = LocalSandbox()
    with pytest.raises(RealInputDisabled):
        await loc.act({"type": "click", "x": 1, "y": 1})
    # read-only still fine
    w, h = await loc.size()
    assert w > 0 and h > 0
