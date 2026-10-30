"""Action contract — the only language the brain speaks to the body.

Actions:
  click(x,y)          – left-click
  double_click(x,y)   – double-click
  right_click(x,y)    – right-click
  move(x,y)           – hover without clicking
  type(text)          – type into focused field
  press(key)          – key or combo, e.g. "enter", "cmd+t", "ctrl+l"
  scroll(amount)      – positive = down, negative = up
  wait(seconds)       – pause
  activate(app)       – launch + bring Mac app to front
  done                – task complete (never sent to backend)
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, field_validator, model_validator

ActionType = Literal[
    "click",
    "double_click",
    "right_click",
    "move",
    "type",
    "press",
    "key",          # wire alias → press
    "scroll",
    "wait",
    "click_type",   # atomic click + type
    "activate",     # Mac: launch + bring app to front
    "done",
]


class Action(BaseModel):
    type: ActionType
    x: Optional[int] = None
    y: Optional[int] = None
    text: Optional[str] = None
    key: Optional[str] = None
    combo: Optional[str] = None   # alias for key in some model outputs
    amount: Optional[int] = None
    seconds: Optional[float] = None
    direction: Optional[str] = None

    model_config = {"extra": "ignore"}

    @field_validator("x", "y")
    @classmethod
    def _non_negative(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 0:
            raise ValueError("coordinates must be >= 0")
        return v

    @model_validator(mode="after")
    def _check(self) -> "Action":
        t = self.type
        if t in ("click", "double_click", "right_click", "move"):
            if self.x is None or self.y is None:
                raise ValueError(f"{t} requires x and y")
        elif t == "type":
            if not self.text:
                raise ValueError("type requires text")
        elif t in ("press", "key"):
            effective = self.key or self.combo
            if not effective:
                raise ValueError("press requires key")
            self.key = effective
            self.combo = None
            self.type = "press"
        elif t == "click_type":
            if self.x is None or self.y is None or not self.text:
                raise ValueError("click_type requires x, y and text")
        elif t == "scroll":
            if self.amount is None and self.direction is None:
                raise ValueError("scroll requires amount or direction")
            if self.amount is None:
                self.amount = 3 if (self.direction or "down") == "down" else -3
        elif t == "wait":
            if self.seconds is None:
                raise ValueError("wait requires seconds")
            if self.seconds < 0 or self.seconds > 300:
                raise ValueError("wait seconds must be in [0, 300]")
        elif t == "activate":
            if not (self.text or "").strip():
                raise ValueError("activate requires an application name in text")
        elif t == "done":
            pass
        return self

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "Action":
        """Parse one model-produced dict, unwrapping {\"action\": {...}}."""
        if isinstance(data, dict) and "action" in data and isinstance(data["action"], dict):
            data = data["action"]
        return cls.model_validate(data)

    def to_payload(self) -> dict[str, Any]:
        """Translate to LocalSandbox act() payload."""
        if self.type in ("click", "double_click", "right_click", "move"):
            return {"type": self.type, "x": self.x, "y": self.y}
        if self.type == "type":
            return {"type": "type", "text": self.text}
        if self.type == "press":
            return {"type": "key", "combo": self.key}
        if self.type == "scroll":
            amt = self.amount or 0
            return {"type": "scroll", "direction": "down" if amt >= 0 else "up",
                    "amount": abs(amt)}
        if self.type == "wait":
            return {"type": "wait", "seconds": self.seconds}
        if self.type == "click_type":
            return {"type": "click_type", "x": self.x, "y": self.y, "text": self.text}
        if self.type == "activate":
            return {"type": "activate", "text": self.text}
        raise ValueError(f"action {self.type!r} has no payload mapping")

    to_sandbox_payload = to_payload

    @property
    def is_terminal(self) -> bool:
        return self.type == "done"

    @property
    def needs_settle(self) -> bool:
        """Actions after which we must wait + re-observe."""
        return self.type in ("click", "double_click", "right_click", "type",
                             "press", "click_type", "activate")
