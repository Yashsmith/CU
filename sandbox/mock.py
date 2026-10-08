"""In-memory mock desktop — deterministic body for unit/integration/e2e tests.

Renders a synthetic 1280x800 Linux-desktop-like PNG (taskbar, Chromium icon,
terminal icon, address bar) and records every action. No Docker, no display.
"""
from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageDraw

WIDTH = 1280
HEIGHT = 800


class MockSandbox:
    def __init__(self, width: int = WIDTH, height: int = HEIGHT) -> None:
        self.width = width
        self.height = height
        self.actions: list[dict[str, Any]] = []
        self.typed_text: list[str] = []
        self.cursor: tuple[int, int] = (width // 2, height // 2)
        self.clicks: list[tuple[int, int]] = []
        self.closed = False

    async def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "display": ":99",
            "width": self.width,
            "height": self.height,
            "browser": "chromium",
            "version": "mock-0.1.0",
            "backend": "mock",
        }

    def render(self) -> bytes:
        img = Image.new("RGB", (self.width, self.height), (45, 52, 64))
        d = ImageDraw.Draw(img)
        # wallpaper gradient band
        d.rectangle([0, 0, self.width, 120], fill=(52, 73, 94))
        d.text((24, 24), "UBS Computer Use — mock desktop :99", fill=(255, 255, 255))
        d.text((24, 48), f"{self.width}x{self.height}  openbox + chromium + xterm", fill=(200, 210, 220))
        # Chromium icon (circle) + label
        d.ellipse([80, 220, 180, 320], fill=(66, 133, 244), outline=(255, 255, 255), width=3)
        d.text((88, 330), "Chromium", fill=(255, 255, 255))
        # Terminal icon
        d.rectangle([260, 230, 370, 310], fill=(20, 20, 20), outline=(0, 200, 120), width=3)
        d.text((270, 240), ">_ term", fill=(0, 230, 140))
        # Address bar
        d.rectangle([80, 420, 900, 460], fill=(255, 255, 255), outline=(150, 150, 150))
        d.text((92, 430), "Address bar — type to search", fill=(120, 120, 120))
        # typed text echo
        y = 490
        for line in self.typed_text[-8:]:
            d.text((80, y), line[:100], fill=(255, 235, 150))
            y += 24
        # cursor crosshair
        cx, cy = self.cursor
        d.ellipse([cx - 8, cy - 8, cx + 8, cy + 8], outline=(255, 80, 80), width=2)
        d.line([cx - 14, cy, cx + 14, cy], fill=(255, 80, 80), width=2)
        d.line([cx, cy - 14, cx, cy + 14], fill=(255, 80, 80), width=2)
        # clicks trail
        for kx, ky in self.clicks[-20:]:
            d.ellipse([kx - 5, ky - 5, kx + 5, ky + 5], outline=(120, 255, 120), width=2)
        # taskbar
        d.rectangle([0, self.height - 40, self.width, self.height], fill=(30, 34, 42))
        d.text((12, self.height - 28), "Applications   Terminal   Chromium", fill=(220, 220, 220))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    async def screenshot(self) -> bytes:
        return self.render()

    async def act(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.actions.append(dict(payload))
        t = payload.get("type")
        if t in ("click", "double_click", "right_click", "move", "click_type"):
            x = int(payload.get("x", self.cursor[0]))
            y = int(payload.get("y", self.cursor[1]))
            self.cursor = (x, y)
            if t in ("click", "double_click", "right_click", "click_type"):
                self.clicks.append((x, y))
        if t == "type" and payload.get("text"):
            self.typed_text.append(str(payload["text"]))
        if t == "click_type":
            if payload.get("text"):
                self.typed_text.append(str(payload["text"]))
        if t == "key" and payload.get("combo"):
            self.typed_text.append(f"<{payload['combo']}>")
        return {"ok": True, "result": "mock-applied", "action": payload}

    async def size(self) -> tuple[int, int]:
        return self.width, self.height

    async def close(self) -> None:
        self.closed = True
