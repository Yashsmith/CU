"""PRD §13-14 Model adapter — one vision model (Groq qwen/qwen3.8-27b).

Speaks OpenAI-compatible chat.completions with a base64 screenshot, returns
exactly one canonical Action. Includes one corrective retry on parse failure
(desktop-use pattern: feed the validation error back at temperature 0).
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
from typing import Any

from .actions import Action

SYSTEM_PROMPT = """You are controlling a Linux desktop (1280x800 unless told otherwise).

Your objective is:
{task}

You receive a screenshot of the current desktop.
Choose exactly one action.

Available actions (reply with ONE JSON object, no other text):
- {"type": "click", "x": <int>, "y": <int>} — left click at screenshot pixels
- {"type": "double_click", "x": <int>, "y": <int>}
- {"type": "right_click", "x": <int>, "y": <int>}
- {"type": "move", "x": <int>, "y": <int>} — hover without clicking
- {"type": "type", "text": "<text to type>"} — types into the focused field
- {"type": "press", "key": "<key or combo>"} — e.g. Enter, Tab, Escape, ctrl+l, ctrl+t
- {"type": "scroll", "amount": <int>} — positive scrolls down, negative up
- {"type": "wait", "seconds": <float>}
- {"type": "done"} — only when the task is fully complete and visible

Rules:
- Coordinates are absolute pixels in the screenshot: x in [0, {width}), y in [0, {height}).
- If you need text in a field, click the field first (a later step can type).
- Do not claim an action succeeded unless you can observe the resulting state.
- After important actions, inspect the new screenshot before finishing.
- Never emit done after merely opening something — verify the requested end state.
"""

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of free-form model output."""
    t = text.strip()
    m = _FENCE_RE.search(t)
    if m:
        t = m.group(1).strip()
    if t.startswith("{"):
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            pass
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(t[start:end + 1])
    raise ValueError(f"no JSON object found in model output: {text[:200]!r}")


def build_messages(
    task: str,
    screenshot_b64: str,
    history: list[dict[str, Any]] | None = None,
    width: int = 1280,
    height: int = 800,
    correction: str | None = None,
    previous_b64: str | None = None,
) -> list[dict[str, Any]]:
    system = (SYSTEM_PROMPT
                .replace("{task}", task)
                .replace("{width}", str(width))
                .replace("{height}", str(height)))
    hist_lines: list[str] = []
    for h in (history or [])[-5:]:
        a = h.get("action", h)
        if isinstance(a, dict):
            hist_lines.append(json.dumps(a))
        else:
            hist_lines.append(str(a))
    user_text = f"Task: {task}\n"
    if hist_lines:
        user_text += "Previous actions:\n" + "\n".join(f"- {line}" for line in hist_lines) + "\n"
    user_text += (
        f"Screenshot is {width}x{height}. Reply with exactly one action JSON object.")
    if correction:
        user_text += f"\nYour last reply was invalid ({correction}). Fix it: reply with valid action JSON only."
    content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    if previous_b64:
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{previous_b64}"}})
    content.append({"type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}})
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]


class GroqModel:
    """Thin async wrapper around Groq chat.completions (vision)."""

    def __init__(
        self,
        api_key: str,
        model: str = "qwen/qwen3.8-27b",
        base_url: str = "https://api.groq.com/openai/v1",
        temperature: float = 0.0,
        max_tokens: int = 300,
    ) -> None:
        if not api_key:
            raise ValueError("GROQ_API_KEY is required")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens

    def _client(self):  # lazy so unit tests never import groq without need
        from groq import Groq

        base = (self.base_url or "").rstrip("/")
        # Groq SDK appends /openai/v1 itself (default base is the host);
        # accept both "https://api.groq.com" and ".../openai/v1" forms.
        if base.endswith("/openai/v1"):
            base = base[: -len("/openai/v1")] or "https://api.groq.com"
        return Groq(api_key=self.api_key, base_url=base or None)

    def _complete(self, messages: list[dict[str, Any]]) -> str:
        resp = self._client().chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or ""
        return content.strip()

    async def decide(
        self,
        task: str,
        screenshot: bytes,
        history: list[dict[str, Any]] | None = None,
        width: int = 1280,
        height: int = 800,
        previous_screenshot: bytes | None = None,
    ) -> Action:
        b64 = base64.b64encode(screenshot).decode()
        prev_b64 = base64.b64encode(previous_screenshot).decode() if previous_screenshot else None
        messages = build_messages(task, b64, history, width, height,
                                  previous_b64=prev_b64)
        raw = await asyncio.to_thread(self._complete, messages)
        try:
            return Action.parse(extract_json(raw))
        except Exception as e1:
            # One corrective retry with the parse error fed back.
            retry_messages = build_messages(task, b64, history, width, height,
                                            correction=str(e1)[:300],
                                            previous_b64=prev_b64)
            raw2 = await asyncio.to_thread(self._complete, retry_messages)
            try:
                return Action.parse(extract_json(raw2))
            except Exception as e2:
                raise ValueError(
                    f"model returned invalid action twice: {raw[:200]!r} / {raw2[:200]!r}: {e2}"
                ) from e2
