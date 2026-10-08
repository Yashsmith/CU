"""Local Mac/PC body — screenshot via mss, input via pyautogui.

SAFETY: real input is disabled unless ALLOW_REAL_INPUT=1 in the environment.
Read-only methods (screenshot/size/health) always work. This backend exists so
V1 can demonstrate a visible e2e loop on a machine without Docker; on
Linux DevPod / CI the HTTPSandbox against desktop-sandbox remains primary.
"""
from __future__ import annotations

import os
from typing import Any


class RealInputDisabled(RuntimeError):
    pass


def real_input_enabled() -> bool:
    return os.environ.get("ALLOW_REAL_INPUT", "0") == "1"


class LocalSandbox:
    """Best-effort local desktop. Screenshot works without permission prompts
    on most setups; input needs OS accessibility permission + opt-in flag."""

    def __init__(self) -> None:
        self._actions: list[dict[str, Any]] = []

    async def health(self) -> dict[str, Any]:
        w, h = await self.size()
        return {"ok": True, "display": "local", "width": w, "height": h,
                "backend": "local", "real_input": real_input_enabled()}

    async def screenshot(self) -> bytes:
        try:
            import io

            import mss
            from PIL import Image
        except ImportError as e:
            raise RuntimeError("local backend needs: pip install 'ubs-computer-use[local]'") from e
        with mss.mss() as sct:
            mon = sct.monitors[1]
            shot = sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

    def _require_input(self) -> None:
        if not real_input_enabled():
            raise RealInputDisabled(
                "Refusing real mouse/keyboard input: set ALLOW_REAL_INPUT=1 to opt in.")

    async def act(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_input()
        try:
            import asyncio

            import pyautogui
        except ImportError as e:
            raise RuntimeError("local backend needs: pip install 'ubs-computer-use[local]'") from e
        self._actions.append(dict(payload))
        t = payload.get("type")
        pyautogui.FAILSAFE = True
        if t in ("move", "click", "double_click", "right_click", "click_type"):
            x, y = int(payload.get("x", 0)), int(payload.get("y", 0))
            if t == "move":
                pyautogui.moveTo(x, y, duration=0.25)
            elif t == "click":
                pyautogui.click(x, y)
            elif t == "double_click":
                pyautogui.doubleClick(x, y)
            elif t == "right_click":
                pyautogui.rightClick(x, y)
            elif t == "click_type":
                pyautogui.click(x, y)
                await asyncio.sleep(0.3)
                pyautogui.typewrite(str(payload.get("text", "")), interval=0.02)
        elif t == "type":
            pyautogui.typewrite(str(payload.get("text", "")), interval=0.02)
        elif t == "key":
            await self._press_combo(pyautogui, str(payload.get("combo", "")))
        elif t == "scroll":
            amt = int(payload.get("amount", 3))
            if payload.get("direction") == "up":
                amt = -abs(amt)
            pyautogui.scroll(abs(amt) * 100 * (1 if amt >= 0 else -1))
        elif t == "wait":
            await asyncio.sleep(float(payload.get("seconds", 0.5)))
        return {"ok": True, "result": "local-applied", "action": payload}

    async def _press_combo(self, pyautogui: Any, combo: str) -> None:
        parts = [p.strip() for p in combo.lower().split("+") if p.strip()]
        if len(parts) > 1:
            *mods, final = parts
            pyautogui.hotkey(*mods, final)
        elif parts:
            pyautogui.press(parts[0])

    async def size(self) -> tuple[int, int]:
        try:
            import mss

            with mss.mss() as sct:
                mon = sct.monitors[1]
                return int(mon["width"]), int(mon["height"])
        except Exception:
            return 1280, 800

    async def close(self) -> None:
        return None
