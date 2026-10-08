"""Model adapter tests — offline parsing + mocked decide + 1 gated live call."""
import base64
import os

import pytest

from agent.actions import Action
from agent.model import GroqModel, build_messages, extract_json
from sandbox.mock import MockSandbox


def _tiny_png() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (9, 9, 9)).save(buf, format="PNG")
    return buf.getvalue()


def test_extract_json_plain_fenced_and_noisy():
    assert extract_json('{"type":"done"}') == {"type": "done"}
    assert extract_json('```json\n{"type": "click", "x": 1, "y": 2}\n```')["x"] == 1
    assert extract_json('Sure! {"type":"wait","seconds":1} doing it')["type"] == "wait"
    with pytest.raises(ValueError):
        extract_json("no json here at all")


def test_build_messages_shape_and_resolution():
    msgs = build_messages("Open Chromium", base64.b64encode(_tiny_png()).decode(),
                          [{"action": {"type": "click", "x": 1, "y": 2}}],
                          width=1280, height=800)
    assert msgs[0]["role"] == "system" and "1280x800" in msgs[0]["content"]
    user_content = msgs[1]["content"]
    assert any(p.get("type") == "image_url" for p in user_content)
    assert "Previous actions" in user_content[0]["text"]


async def test_decide_parses_first_try(monkeypatch):
    m = GroqModel(api_key="k")
    monkeypatch.setattr(m, "_complete", lambda messages: '{"type":"click","x":130,"y":270}')
    a = await m.decide("Open Chromium", _tiny_png())
    assert isinstance(a, Action) and (a.x, a.y) == (130, 270)


async def test_decide_corrective_retry_on_bad_first_reply(monkeypatch):
    m = GroqModel(api_key="k")
    calls = {"n": 0}

    def fake(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return "not json at all"
        return '{"type": "done"}'

    monkeypatch.setattr(m, "_complete", fake)
    a = await m.decide("t", _tiny_png())
    assert a.type == "done" and calls["n"] == 2


async def test_decide_raises_after_two_failures(monkeypatch):
    m = GroqModel(api_key="k")
    monkeypatch.setattr(m, "_complete", lambda messages: "garbage!!!")
    with pytest.raises(ValueError, match="invalid action twice"):
        await m.decide("t", _tiny_png())


async def test_decide_transport_retry(monkeypatch):
    """API-level 400 (e.g. tool-call instead of JSON) retries warmer once."""
    m = GroqModel(api_key="k")
    calls = {"n": 0}

    def fake(messages, temperature=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("400 json_validate_failed")
        assert temperature == 0.4
        return '{"type": "done"}'

    monkeypatch.setattr(m, "_complete", fake)
    a = await m.decide("t", _tiny_png())
    assert a.type == "done" and calls["n"] == 2


async def test_decide_transport_double_failure(monkeypatch):
    m = GroqModel(api_key="k")
    monkeypatch.setattr(m, "_complete", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("down")))
    with pytest.raises(ValueError, match="model API failed twice"):
        await m.decide("t", _tiny_png())


def test_model_requires_key():
    with pytest.raises(ValueError):
        GroqModel(api_key="")


@pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1" or not os.environ.get("GROQ_API_KEY"),
                    reason="live Groq call needs RUN_LIVE=1 + GROQ_API_KEY")
async def test_live_groq_vision_on_mock_desktop():
    """Smoke: real qwen/qwen3.8-27b sees the synthetic desktop and returns an Action."""
    key = os.environ["GROQ_API_KEY"]
    model_name = os.environ.get("MODEL", "qwen/qwen3.8-27b")
    m = GroqModel(api_key=key, model=model_name)
    shot = await MockSandbox().screenshot()
    a = await m.decide("Open Chromium by clicking its icon.", shot)
    assert isinstance(a, Action)
    print(f"\nLIVE model action: {a.model_dump()}")
