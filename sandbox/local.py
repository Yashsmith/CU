"""Local Mac/PC body — screenshot via mss, input via pyautogui, focus via osascript.

VISIBLE MODE: this backend drives YOUR screen — cursor glides, apps open in
front of you. Read-only methods always work; real input needs BOTH:
  1. ALLOW_REAL_INPUT=1 in the environment (opt-in), AND
  2. macOS Accessibility permission for your terminal app
     (System Settings > Privacy & Security > Accessibility), AND
  3. macOS Screen Recording permission for screenshots (same pane).

Coordinate contract: the model always works in POINTS (pyautogui space).
Screenshots are downscaled to points when the capture comes back in physical
pixels (Retina), so screenshot pixels == action coords 1:1. Verified 1:1 on
the dev Mac (1470x956 both); anything else auto-adapts.
"""
from __future__ import annotations

import os
import subprocess
from typing import Any

# pyautogui key names differ per OS; normalize the combos models emit.
MAC_KEY_ALIASES = {"cmd": "command", "command": "command", "opt": "option",
                   "option": "option", "ctl": "control", "ctrl": "control",
                   "control": "control", "alt": "option", "shift": "shift",
                   "enter": "enter", "return": "enter", "tab": "tab",
                   "escape": "esc", "esc": "esc", "space": "space",
                   "backspace": "backspace", "delete": "delete"}


class RealInputDisabled(RuntimeError):
    pass


def real_input_enabled() -> bool:
    return os.environ.get("ALLOW_REAL_INPUT", "0") == "1"


def to_points(img: Any, points_w: int, points_h: int) -> Any:
    """Downscale a physical-pixel capture to logical points (no-op if equal)."""
    if img.width == points_w and img.height == points_h:
        return img
    return img.resize((points_w, points_h))


def frontmost_app() -> str:
    """Name of the frontmost app (read-only, no permission needed)."""
    out = subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to get name of first application '
         'process whose frontmost is true'],
        capture_output=True, text=True, timeout=10)
    return out.stdout.strip()


def activate_app(name: str, timeout: int = 15) -> str:
    """Launch (if needed) + bring to front. Returns frontmost app name after."""
    safe = name.replace('"', "")
    if not safe.strip():
        raise ValueError("activate needs an application name")
    proc = subprocess.run(
        ["osascript", "-e", f'tell application "{safe}" to activate'],
        capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"could not activate {safe!r}: {proc.stderr.strip()[:200]}")
    return frontmost_app()


class LocalSandbox:
    supports_local_actions = True  # activate/frontmost lane (cf. Computer.execute)

    def __init__(self, glide: float = 0.3) -> None:
        self._actions: list[dict[str, Any]] = []
        self.glide = glide  # visible cursor glide time per move (seconds)

    async def health(self) -> dict[str, Any]:
        w, h = await self.size()
        try:
            front = frontmost_app()
        except Exception:
            front = "?"
        return {"ok": True, "display": "local", "width": w, "height": h,
                "backend": "local", "real_input": real_input_enabled(),
                "frontmost": front}

    def _points(self) -> tuple[int, int]:
        try:
            import pyautogui

            s = pyautogui.size()
            return int(s.width), int(s.height)
        except Exception:
            return 1470, 956

    async def screenshot(self) -> bytes:
        try:
            import io
            from mss import MSS
            from PIL import Image
        except ImportError as e:
            raise RuntimeError("local backend needs: pip install 'ubs-computer-use[local]'") from e
        with MSS() as sct:
            mon = sct.monitors[1]
            shot = sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        img = to_points(img, *self._points())
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
        if t == "activate":
            front = activate_app(str(payload.get("text", "")))
            return {"ok": True, "result": f"frontmost={front}", "action": payload}
        if t in ("move", "click", "double_click", "right_click", "click_type"):
            x, y = int(payload.get("x", 0)), int(payload.get("y", 0))
            if t == "move":
                pyautogui.moveTo(x, y, duration=self.glide)
            elif t == "click":
                pyautogui.moveTo(x, y, duration=self.glide)
                pyautogui.click()
            elif t == "double_click":
                pyautogui.moveTo(x, y, duration=self.glide)
                pyautogui.doubleClick()
            elif t == "right_click":
                pyautogui.moveTo(x, y, duration=self.glide)
                pyautogui.rightClick()
            elif t == "click_type":
                pyautogui.moveTo(x, y, duration=self.glide)
                pyautogui.click()
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
        parts = [MAC_KEY_ALIASES.get(p.strip().lower(), p.strip())
                 for p in combo.split("+") if p.strip()]
        if len(parts) > 1:
            *mods, final = parts
            pyautogui.hotkey(*mods, final)
        elif parts:
            pyautogui.press(parts[0])

    async def size(self) -> tuple[int, int]:
        try:
            from mss import MSS

            with MSS() as sct:
                mon = sct.monitors[1]
                return int(mon["width"]), int(mon["height"])
        except Exception:
            return 1280, 800

    async def close(self) -> None:
        return None
