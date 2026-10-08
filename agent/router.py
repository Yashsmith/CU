"""PRD §28/§30 routing: browser task -> Playwright, otherwise desktop.

Heuristic by design (predictable, testable): an explicit URL / domain intent
routes to the browser lane; everything else stays on the desktop so V1 demo
tasks ("Open Chromium and search for UBS") never get hijacked. `--lane`
overrides explicitly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Lane = Literal["browser", "desktop"]

_URL_RE = re.compile(r"https?://[^\s\"']+|www\.[^\s\"']+|\b[\w-]+\.(com|org|net|io|in|ch|dev|ai|gov|edu)\b",
                     re.IGNORECASE)
_NAVIGATE_RE = re.compile(
    r"\b(go to|navigate to|open (the )?(website|web ?site|page|url)|browse to|visit)\b",
    re.IGNORECASE)


@dataclass
class Route:
    lane: Lane
    reason: str


def route(task: str, lane_override: Lane | None = None) -> Route:
    if lane_override in ("browser", "desktop"):
        return Route(lane=lane_override, reason="explicit --lane override")
    m = _URL_RE.search(task)
    if m:
        return Route(lane="browser", reason=f"explicit URL/domain: {m.group(0)[:60]}")
    if _NAVIGATE_RE.search(task):
        return Route(lane="browser", reason="navigation intent")
    return Route(lane="desktop", reason="no URL — desktop interaction")
