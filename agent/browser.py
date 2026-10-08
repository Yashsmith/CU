"""PRD §28 V2 browser lane — Playwright for browser tasks, desktop otherwise.

Same Action contract, 1:1 pixel coords (viewport matches the desktop size).
`goto` is browser-only; every other action translates to mouse/keyboard/page.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .actions import Action


class BrowserLaneError(RuntimeError):
    pass


class PlaywrightBrowser:
    def __init__(self, width: int = 1600, height: int = 900,
                 headless: bool = True,
                 allowed_domains: list[str] | None = None) -> None:
        self.width = width
        self.height = height
        self.headless = headless
        self.allowed_domains = allowed_domains
        self._pw: Any = None
        self._browser: Any = None
        self._page: Any = None

    async def start(self) -> "PlaywrightBrowser":
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        self._page = await self._browser.new_page(
            viewport={"width": self.width, "height": self.height})
        return self

    @property
    def started(self) -> bool:
        return self._page is not None

    def _require(self) -> Any:
        if self._page is None:
            raise BrowserLaneError("browser not started (await start() first)")
        return self._page

    def _check_domain(self, url: str) -> None:
        if not self.allowed_domains:
            return
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
        if not any(host == d.lower() or host.endswith("." + d.lower())
                   for d in self.allowed_domains):
            raise BrowserLaneError(f"domain not allowed: {host}")

    async def goto(self, url: str) -> str:
        self._check_domain(url)
        page = self._require()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return page.url

    async def title(self) -> str:
        return await self._require().title()

    async def text(self) -> str:
        return await self._require().inner_text("body")

    async def screenshot(self) -> bytes:
        shot = await self._require().screenshot()
        assert shot.startswith(b"\x89PNG")
        return shot

    async def exec(self, action: Action) -> dict[str, Any]:
        """Execute one canonical Action on the page (PRD §28-30)."""
        page = self._require()
        t = action.type
        if t == "goto":
            assert action.text
            url = await self.goto(action.text)
            return {"ok": True, "url": url}
        if t == "click":
            await page.mouse.click(action.x or 0, action.y or 0)
        elif t == "double_click":
            await page.mouse.dblclick(action.x or 0, action.y or 0)
        elif t == "right_click":
            await page.mouse.click(action.x or 0, action.y or 0, button="right")
        elif t == "move":
            await page.mouse.move(action.x or 0, action.y or 0)
        elif t == "type":
            await page.keyboard.type(action.text or "")
        elif t == "press":
            await page.keyboard.press(self._playwright_key(action.key or "Enter"))
        elif t == "scroll":
            await page.mouse.wheel(0, (action.amount or 3) * 100)
        elif t == "wait":
            await asyncio.sleep(action.seconds or 0.5)
        elif t == "click_type":
            await page.mouse.click(action.x or 0, action.y or 0)
            await asyncio.sleep(0.3)
            await page.keyboard.type(action.text or "")
        else:
            raise BrowserLaneError(f"browser lane cannot execute {t!r}")
        return {"ok": True, "action": action.model_dump()}

    @staticmethod
    def _playwright_key(combo: str) -> str:
        parts = [p.strip() for p in combo.split("+") if p.strip()]
        mapping = {"ctrl": "Control", "control": "Control", "alt": "Alt",
                   "shift": "Shift", "meta": "Meta", "cmd": "Meta",
                   "enter": "Enter", "tab": "Tab", "escape": "Escape",
                   "esc": "Escape", "space": " ", "backspace": "Backspace",
                   "delete": "Delete", "up": "ArrowUp", "down": "ArrowDown",
                   "left": "ArrowLeft", "right": "ArrowRight"}
        return "+".join(mapping.get(p.lower(), p) for p in parts)

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
            self._page = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
