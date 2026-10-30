"""Cross-platform host desktop body — macOS + Windows.

Drives your real screen visually:
- Screen capture via mss (Retina-scaled on macOS, DPI-aware on Windows)
- Real mouse movement via pyautogui with smooth glide (you see cursor move)
- Real keystrokes & hotkeys (auto maps cmd ⇄ ctrl across Mac and Windows)
- App detection & activation without requiring virtual machines or Docker

Requirements:
- macOS: Terminal granted Accessibility & Screen Recording permissions.
- Windows: Runs in any standard terminal (Command Prompt, PowerShell, Windows Terminal).
- Set ALLOW_REAL_INPUT=1 to enable mouse & keyboard movements.
"""
from __future__ import annotations

import asyncio
import os
import platform
import subprocess
import sys
from typing import Any

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"

# Enable Per-Monitor DPI awareness on Windows so mss & pyautogui coords are 1:1
if IS_WINDOWS:
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# Canonical key mappings
KEY_ALIASES = {
    "enter": "enter", "return": "enter",
    "tab": "tab",
    "escape": "esc", "esc": "esc",
    "space": "space",
    "backspace": "backspace",
    "delete": "delete",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "shift": "shift",
}

# Backward compatibility alias
MAC_KEY_ALIASES = {
    "cmd": "command", "command": "command",
    "opt": "option", "option": "option", "alt": "option",
    "ctl": "control", "ctrl": "control", "control": "control",
    "shift": "shift", "enter": "enter", "return": "enter",
    "tab": "tab", "escape": "esc", "esc": "esc",
    "space": "space", "backspace": "backspace", "delete": "delete",
    "up": "up", "down": "down", "left": "left", "right": "right",
}


def _normalize_key(key: str) -> str:
    """Map modifier keys to host OS conventions."""
    k = key.strip().lower()
    if IS_WINDOWS:
        if k in ("cmd", "command"):
            return "ctrl"
        if k in ("opt", "option"):
            return "alt"
        if k in ("win", "windows", "super"):
            return "win"
        if k in ("ctrl", "control", "ctl"):
            return "ctrl"
    else:
        if k in ("cmd", "command"):
            return "command"
        if k in ("opt", "option", "alt"):
            return "option"
        if k in ("ctrl", "control", "ctl"):
            return "control"
        if k in ("win", "windows", "super"):
            return "command"
    return KEY_ALIASES.get(k, k)


class RealInputDisabled(RuntimeError):
    pass


def real_input_enabled() -> bool:
    return os.environ.get("ALLOW_REAL_INPUT", "0") == "1"


def _points_size() -> tuple[int, int]:
    try:
        import pyautogui
        s = pyautogui.size()
        return int(s.width), int(s.height)
    except Exception:
        return 1470, 956


def frontmost_app() -> str:
    """Get the active/frontmost application title across macOS and Windows."""
    if IS_MAC:
        try:
            out = subprocess.run(
                ["osascript", "-e",
                 "tell application \"System Events\" to get name of first application "
                 "process whose frontmost is true"],
                capture_output=True, text=True, timeout=5)
            return out.stdout.strip() or "Desktop"
        except Exception:
            return "Desktop"
    elif IS_WINDOWS:
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return "Desktop"
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value or "Desktop"
        except Exception:
            return "Desktop"
    else:
        return "Desktop"


def activate_app(name: str) -> str:
    """Launch or focus application across macOS and Windows."""
    safe = name.replace('"', "").strip()
    if not safe:
        raise ValueError("activate_app: name must not be empty")

    if IS_MAC:
        proc = subprocess.run(
            ["osascript", "-e", f'tell application "{safe}" to activate'],
            capture_output=True, text=True, timeout=10)
        if proc.returncode != 0:
            # Fallback to open -a
            subprocess.run(["open", "-a", safe], capture_output=True, timeout=10)
    elif IS_WINDOWS:
        try:
            subprocess.run(f'start "" "{safe}"', shell=True, timeout=5)
        except Exception as e:
            raise RuntimeError(f"cannot activate {safe!r}: {e}")
    return frontmost_app()


def open_new_chrome_tab(url: str = "") -> None:
    """Open a new tab in the existing Chrome browser."""
    if IS_MAC:
        script = f'''
tell application "Google Chrome"
    activate
    if (count of windows) = 0 then
        make new window
    end if
    tell front window
        make new tab{"" if not url else f' with properties {{URL:"{url}"}}'}
    end tell
end tell
'''
        subprocess.run(["osascript", "-e", script], timeout=10)
    else:
        target = url or "about:blank"
        subprocess.run(f'start chrome "{target}"', shell=True, timeout=5)


class LocalSandbox:
    """Drive the real Mac / Windows display via pyautogui + mss with smooth glide."""

    supports_local_actions = True

    def __init__(self, glide: float = 0.35) -> None:
        self._actions: list[dict[str, Any]] = []
        self.glide = glide  # cursor move duration in seconds (smooth visible motion)

    def _require_input(self) -> None:
        if not real_input_enabled():
            raise RealInputDisabled(
                "Real mouse/keyboard input is disabled. "
                "Set ALLOW_REAL_INPUT=1 to enable.")

    async def health(self) -> dict[str, Any]:
        w, h = await self.size()
        try:
            front = frontmost_app()
        except Exception:
            front = "?"
        return {
            "ok": True,
            "backend": "local",
            "os": platform.system(),
            "display": f"{w}x{h}",
            "real_input": real_input_enabled(),
            "frontmost": front,
        }

    async def size(self) -> tuple[int, int]:
        try:
            from mss import MSS
            with MSS() as sct:
                m = sct.monitors[1]
                return int(m["width"]), int(m["height"])
        except Exception:
            return _points_size()

    async def screenshot(self) -> bytes:
        try:
            import io
            from mss import MSS
            from PIL import Image
        except ImportError as e:
            raise RuntimeError(
                "Missing dependencies. Run: pip install mss pillow pyautogui") from e
        with MSS() as sct:
            mon = sct.monitors[1]
            raw = sct.grab(mon)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

        # Downscale Retina captures to logical points so coordinates are 1:1
        pw, ph = _points_size()
        if img.width != pw or img.height != ph:
            img = img.resize((pw, ph))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    async def act(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_input()
        try:
            import pyautogui
        except ImportError as e:
            raise RuntimeError("Missing pyautogui. Run: pip install pyautogui") from e

        self._actions.append(dict(payload))
        pyautogui.FAILSAFE = True
        t = payload.get("type")

        # ── Visual cursor movements ───────────────────────────────────────────
        if t in ("move", "click", "double_click", "right_click", "click_type"):
            x, y = int(payload.get("x", 0)), int(payload.get("y", 0))
            # Smoothly glide cursor so user watches it move to target
            pyautogui.moveTo(x, y, duration=self.glide)

            if t == "click":
                pyautogui.click()
            elif t == "double_click":
                pyautogui.doubleClick()
            elif t == "right_click":
                pyautogui.rightClick()
            elif t == "click_type":
                pyautogui.click()
                await asyncio.sleep(0.2)
                pyautogui.typewrite(str(payload.get("text", "")), interval=0.03)

        elif t == "type":
            pyautogui.typewrite(str(payload.get("text", "")), interval=0.03)

        elif t == "key":
            combo = str(payload.get("combo", ""))
            await self._press_combo(pyautogui, combo)

        elif t == "scroll":
            amt = int(payload.get("amount", 3))
            if payload.get("direction") == "up":
                amt = -abs(amt)
            pyautogui.scroll(amt * 3)

        elif t == "wait":
            await asyncio.sleep(float(payload.get("seconds", 0.5)))

        elif t == "activate":
            name = str(payload.get("text", ""))
            front = activate_app(name)
            return {"ok": True, "result": f"frontmost={front}"}

        elif t == "open_chrome_tab":
            url = str(payload.get("url", ""))
            open_new_chrome_tab(url)
            return {"ok": True, "result": "chrome tab opened"}

        return {"ok": True, "result": "applied", "action": payload}

    async def _press_combo(self, pyautogui: Any, combo: str) -> None:
        parts = [
            _normalize_key(p)
            for p in combo.split("+") if p.strip()
        ]
        if len(parts) > 1:
            *mods, final = parts
            pyautogui.hotkey(*mods, final)
        elif parts:
            pyautogui.press(parts[0])

    async def close(self) -> None:
        pass
