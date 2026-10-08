"""PRD §11 + §29 Computer — tiny typed facade over any sandbox backend.

V1 surface (screenshot/click/.../execute) is unchanged. V2 bridge adds:
  attach_executor() + execute_python()  — persistent code runtime (PRD §26)
  attach_browser()  + browser()         — Playwright lane handle (PRD §28)

No model logic belongs here. Coordinates are absolute desktop pixels
matching the latest screenshot.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .actions import Action


class Computer:
    def __init__(self, backend: Any, settle_wait: float = 0.5) -> None:
        self.backend = backend
        self.settle_wait = settle_wait
        self._executor: Any | None = None
        self._browser: Any | None = None

    async def health(self) -> dict[str, Any]:
        return await self.backend.health()

    async def size(self) -> tuple[int, int]:
        return await self.backend.size()

    async def screenshot(self) -> bytes:
        shot = await self.backend.screenshot()
        if not shot.startswith(b"\x89PNG"):
            raise RuntimeError("backend did not return PNG bytes")
        return shot

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

    async def execute(self, action: Action) -> dict[str, Any]:
        """Execute one canonical action (PRD §15-16). done is rejected here."""
        if action.is_terminal:
            raise ValueError("done is control-plane only and cannot be executed")
        if action.type == "click_type":
            # Split into click + type so every backend stays simple.
            assert action.x is not None and action.y is not None and action.text
            await self.backend.act({"type": "click", "x": action.x, "y": action.y})
            await asyncio.sleep(self.settle_wait)
            return await self.backend.act({"type": "type", "text": action.text})
        if action.type == "wait" and (action.seconds or 0) > 5 and hasattr(self.backend, "wait"):
            return await self.backend.wait(float(action.seconds))
        return await self.backend.act(action.to_sandbox_payload())

    async def close(self) -> None:
        close = getattr(self.backend, "close", None)
        if close is not None:
            await close()

    # ------------------------------------------------- V2 bridge (PRD §29) ---
    def attach_executor(self, executor: Any) -> None:
        """Bind a PersistentExecutor; also exposes this Computer inside it."""
        self._executor = executor
        try:
            executor.bind_computer(self, asyncio.get_running_loop())
        except RuntimeError:
            pass  # no running loop (tests); bind explicitly later

    def attach_browser(self, browser: Any) -> None:
        self._browser = browser
        if self._executor is not None:
            try:
                self._executor.bind("browser", browser)
            except Exception:
                pass

    async def execute_python(self, code: str, timeout: float = 60.0) -> Any:
        """Run code in the persistent runtime (PRD §26)."""
        if self._executor is None:
            raise RuntimeError("no executor attached (attach_executor first)")
        return await self._executor.execute(code, timeout=timeout)

    def browser(self) -> Any:
        """Playwright lane handle (PRD §28)."""
        if self._browser is None:
            raise RuntimeError("no browser attached (attach_browser first)")
        return self._browser
