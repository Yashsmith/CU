"""Computer — thin typed facade over LocalSandbox.

No model logic here. Coordinates are absolute display points matching the
latest screenshot (Retina-corrected, so 1:1 with action coords).
"""
from __future__ import annotations

import asyncio
from typing import Any

from .actions import Action


class Computer:
    def __init__(self, backend: Any, settle_wait: float = 0.5) -> None:
        self.backend = backend
        self.settle_wait = settle_wait

    async def health(self) -> dict[str, Any]:
        return await self.backend.health()

    async def size(self) -> tuple[int, int]:
        return await self.backend.size()

    async def screenshot(self) -> bytes:
        shot = await self.backend.screenshot()
        if not shot.startswith(b"\x89PNG"):
            raise RuntimeError("backend did not return valid PNG bytes")
        return shot

    # ── Convenience wrappers ─────────────────────────────────────────────────

    async def click(self, x: int, y: int) -> dict[str, Any]:
        return await self.execute(Action(type="click", x=x, y=y))

    async def double_click(self, x: int, y: int) -> dict[str, Any]:
        return await self.execute(Action(type="double_click", x=x, y=y))

    async def right_click(self, x: int, y: int) -> dict[str, Any]:
        return await self.execute(Action(type="right_click", x=x, y=y))

    async def move(self, x: int, y: int) -> dict[str, Any]:
        return await self.execute(Action(type="move", x=x, y=y))

    async def type(self, text: str) -> dict[str, Any]:
        return await self.execute(Action(type="type", text=text))

    async def press(self, key: str) -> dict[str, Any]:
        return await self.execute(Action(type="press", key=key))

    async def scroll(self, amount: int) -> dict[str, Any]:
        return await self.execute(Action(type="scroll", amount=amount))

    async def wait(self, seconds: float) -> dict[str, Any]:
        return await self.execute(Action(type="wait", seconds=seconds))

    async def activate(self, app: str) -> dict[str, Any]:
        return await self.execute(Action(type="activate", text=app))

    # ── Core execute ─────────────────────────────────────────────────────────

    async def execute(self, action: Action) -> dict[str, Any]:
        """Execute one action. Raises if action is 'done'."""
        if action.is_terminal:
            raise ValueError("'done' is control-plane only and cannot be executed")
        if action.type == "click_type":
            # Split into click + settle + type so backend stays simple.
            await self.backend.act({"type": "click", "x": action.x, "y": action.y})
            await asyncio.sleep(self.settle_wait)
            return await self.backend.act({"type": "type", "text": action.text})
        return await self.backend.act(action.to_payload())

    async def close(self) -> None:
        close = getattr(self.backend, "close", None)
        if close is not None:
            await close()
