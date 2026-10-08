"""V2 adapter matrix tests — offline (mocked transports) except gated live ones."""
import os

import pytest

from agent.adapters import (
    AstraCodeAdapter,
    AstraComputerAdapter,
    GeminiAdapter,
    GroqVisionAdapter,
    ModelResult,
    Observation,
    OpenAICompatibleAdapter,
    astra_call_output,
    astra_computer_actions,
    create_model,
)
from agent.model import GroqModel


def _obs() -> Observation:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (1, 2, 3)).save(buf, format="PNG")
    return Observation(task="t", screenshot=buf.getvalue(), width=1600, height=900)


# ------------------------------------------------------- registry (PRD §38) --
def test_registry_defaults_to_groq_vision():
    m = create_model(groq_api_key="k")
    assert isinstance(m, GroqVisionAdapter) and m.mode == "vision_actions"


def test_registry_matrix():
    assert isinstance(create_model("groq", "qwen/qwen3.8-27b", "vision_actions",
                                   groq_api_key="k"), GroqVisionAdapter)
    assert isinstance(create_model("groq", "m", "code_execution", groq_api_key="k"),
                      AstraCodeAdapter)
    assert isinstance(create_model("gemini", "gemini-2.5-flash", "vision_actions",
                                   gemini_api_key="g"), GeminiAdapter)
    assert isinstance(create_model("openai_compat", "openai/gpt-oss-20b",
                                   "vision_actions", groq_api_key="k"),
                      OpenAICompatibleAdapter)
    assert isinstance(create_model("astra", "a", "computer", openai_api_key="o"),
                      AstraComputerAdapter)
    assert isinstance(create_model("astra", "a", "code_execution", openai_api_key="o"),
                      AstraCodeAdapter)
    with pytest.raises(ValueError):
        create_model("nope", groq_api_key="k")


def test_gemini_defaults():
    g = GeminiAdapter(api_key="g")
    assert g.model == "gemini-2.5-flash" and "googleapis" in g.base_url


# ------------------------------------------------- Groq wrap (V1 compat) -----
async def test_groq_adapter_passthrough(monkeypatch):
    gm = GroqModel(api_key="k")
    monkeypatch.setattr(gm, "_complete", lambda messages: '{"type":"done"}')
    res = await GroqVisionAdapter(gm).run("t", _obs())
    assert res.done and res.actions == []


# ------------------------------------------- OpenAI-compat (GPT OSS path) ----
async def test_openai_compat_parses_action(monkeypatch):
    m = OpenAICompatibleAdapter(api_key="k", model="openai/gpt-oss-20b",
                                base_url="http://x/v1")
    monkeypatch.setattr(m, "_complete", lambda messages: '{"type":"click","x":5,"y":6}')
    res = await m.run("t", _obs())
    assert res.single_action and (res.single_action.x, res.single_action.y) == (5, 6)


async def test_openai_compat_retry_then_fail(monkeypatch):
    m = OpenAICompatibleAdapter(api_key="k", model="m", base_url="http://x/v1")
    monkeypatch.setattr(m, "_complete", lambda messages: "garbage")
    with pytest.raises(ValueError, match="invalid action twice"):
        await m.run("t", _obs())


def test_openai_compat_requires_key():
    with pytest.raises(ValueError):
        OpenAICompatibleAdapter(api_key="", model="m")


# --------------------------------------------- Astra computer mode (PRD §25) --
def test_astra_translate_all_types():
    item = {"type": "computer_call", "call_id": "call_1",
            "actions": [
                {"type": "click", "button": "left", "x": 10, "y": 20},
                {"type": "click", "button": "right", "x": 30, "y": 40},
                {"type": "double_click", "x": 1, "y": 2},
                {"type": "move", "x": 3, "y": 4},
                {"type": "type", "text": "hi"},
                {"type": "keypress", "keys": ["ctrl", "l"]},
                {"type": "scroll", "scroll_x": 0, "scroll_y": -5, "x": 1, "y": 1},
                {"type": "drag", "path": [{"x": 1, "y": 1}, {"x": 9, "y": 9}]},
                {"type": "wait"},
                {"type": "screenshot"},
            ]}
    acts = astra_computer_actions(item)
    kinds = [a.type for a in acts]
    assert kinds == ["click", "right_click", "double_click", "move", "type",
                     "press", "scroll", "move", "click", "wait"]
    assert acts[5].key == "ctrl+l"
    assert acts[6].amount == -5


def test_astra_translate_rejects():
    with pytest.raises(ValueError):
        astra_computer_actions({"type": "message"})
    with pytest.raises(ValueError):
        astra_computer_actions({"type": "computer_call",
                                "actions": [{"type": "teleport"}]})


def test_astra_call_output_shape():
    out = astra_call_output("call_9", _obs().screenshot)
    assert out["type"] == "computer_call_output" and out["call_id"] == "call_9"
    assert out["output"]["image_url"].startswith("data:image/png;base64,")


def test_astra_adapter_helpers_offline():
    ad = AstraComputerAdapter()
    acts = ad.translate({"type": "computer_call",
                         "actions": [{"type": "click", "x": 1, "y": 2}]})
    assert len(acts) == 1
    with pytest.raises(RuntimeError):
        import asyncio
        asyncio.get_event_loop().run_until_complete(ad.run("t", _obs()))


# ------------------------------------------------- Astra code mode (PRD §26) --
def test_extract_code_fenced_raw_and_json():
    assert AstraCodeAdapter.extract_code(
        ModelResult(raw="```python\nprint(1)\n```")).code == "print(1)"
    assert AstraCodeAdapter.extract_code(
        ModelResult(raw="x = 1")).code == "x = 1"
    assert AstraCodeAdapter.extract_code(
        ModelResult(raw='{"code": "y = 2"}')).code == "y = 2"
    with pytest.raises(ValueError):
        AstraCodeAdapter.extract_code(ModelResult(raw="   "))


async def test_code_adapter_wraps_inner():
    class FakeInner(OpenAICompatibleAdapter):
        async def _complete(self, messages):
            return "```python\ncomputer.click(1, 2)\n```"

    inner = FakeInner(api_key="k", model="m", base_url="http://x")
    res = await AstraCodeAdapter(inner).run("t", _obs())
    assert res.code and res.code.code == "computer.click(1, 2)"


# ------------------------------------------------------------------ live ----
@pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1" or not os.environ.get("GROQ_API_KEY"),
                    reason="needs RUN_LIVE=1 + GROQ_API_KEY")
async def test_live_gpt_oss_text_generation():
    """GPT OSS via Groq OpenAI-compat endpoint (text-only model: JSON code task)."""
    import httpx

    key = os.environ["GROQ_API_KEY"]
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post("https://api.groq.com/openai/v1/chat/completions",
                         headers={"Authorization": f"Bearer {key}"},
                         json={"model": "openai/gpt-oss-20b",
                               "messages": [{"role": "user",
                                             "content": 'Reply with exactly {"code": "x = 40 + 2"} and nothing else.'}],
                               "temperature": 0, "max_tokens": 100,
                               "response_format": {"type": "json_object"}})
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
    code = AstraCodeAdapter.extract_code(ModelResult(raw=text))
    assert code.code.strip() == "x = 40 + 2"
    print(f"\nLIVE gpt-oss-20b code: {code.code!r}")
