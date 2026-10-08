"""V1 action contract — the only language the brain may speak to the body.

PRD §12 defines: click, double_click, move, type, press, scroll, wait, done.
We additionally accept right_click / click_type / key as wire-level aliases
because desktop-sandbox speaks them natively; they are normalised into the
canonical 8 on parse so the model prompt and loop only ever see V1 actions.

Wire mapping (to POST /action):
  click(x,y)         -> {"type":"click","x":x,"y":y}
  double_click(x,y)  -> {"type":"double_click","x":x,"y":y}
  right_click(x,y)   -> {"type":"right_click","x":x,"y":y}
  move(x,y)          -> {"type":"move","x":x,"y":y}
  type(text)         -> {"type":"type","text":text}
  press(key)         -> {"type":"key","combo":key}        (sandbox names it key/combo)
  scroll(amount)     -> {"type":"scroll","direction":...,"amount":N}
  wait(seconds)      -> {"type":"wait","seconds":s}       (sandbox caps at 5s; client chunks)
  done               -> control-plane only, never sent
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, field_validator, model_validator

ActionType = Literal[
    "click",
    "double_click",
    "right_click",
    "move",
    "type",
    "press",
    "key",
    "scroll",
    "wait",
    "click_type",
    "goto",  # V2 browser lane only (PRD §28): navigate to a URL
    "done",
]

CANONICAL_TYPES = (
    "click",
    "double_click",
    "right_click",
    "move",
    "type",
    "press",
    "scroll",
    "wait",
    "done",
)

_ALIAS_TO_CANONICAL = {
    "key": "press",
    "click_type": "click",  # handled specially: click + type split by Computer
}


class Action(BaseModel):
    type: ActionType
    x: int | None = None
    y: int | None = None
    text: str | None = None
    key: str | None = None
    combo: str | None = None  # sandbox wire alias for key
    amount: int | None = None
    seconds: float | None = None
    direction: str | None = None  # sandbox wire form for scroll

    model_config = {"extra": "ignore"}

    @field_validator("x", "y")
    @classmethod
    def _non_negative(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("coordinates must be >= 0")
        return v

    @model_validator(mode="after")
    def _check_fields(self) -> "Action":
        t = self.type
        if t in ("click", "double_click", "right_click", "move"):
            if self.x is None or self.y is None:
                raise ValueError(f"{t} requires x and y")
        elif t == "type":
            if not self.text:
                raise ValueError("type requires text")
        elif t in ("press", "key"):
            if not (self.key or self.combo):
                raise ValueError("press requires key")
            if self.key is None and self.combo is not None:
                self.key = self.combo
            self.combo = None
            self.type = "press"
        elif t == "click_type":
            # normalise at Computer level; keep marker here
            if self.x is None or self.y is None or not self.text:
                raise ValueError("click_type requires x, y and text")
        elif t == "goto":
            if not self.text or not self.text.startswith(("http://", "https://")):
                raise ValueError("goto requires an http(s) URL in text")
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
        elif t == "done":
            pass
        return self

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "Action":
        """Parse one model-produced dict, unwrapping {"action": {...}}."""
        if isinstance(data, dict) and "action" in data and isinstance(data["action"], dict):
            data = data["action"]
        return cls.model_validate(data)

    def to_sandbox_payload(self) -> dict[str, Any]:
        """Translate canonical action to desktop-sandbox POST /action body."""
        if self.type == "click":
            return {"type": "click", "x": self.x, "y": self.y}
        if self.type == "double_click":
            return {"type": "double_click", "x": self.x, "y": self.y}
        if self.type == "right_click":
            return {"type": "right_click", "x": self.x, "y": self.y}
        if self.type == "move":
            return {"type": "move", "x": self.x, "y": self.y}
        if self.type == "type":
            return {"type": "type", "text": self.text}
        if self.type == "press":
            return {"type": "key", "combo": self.key}
        if self.type == "scroll":
            amt = self.amount or 0
            direction = "down" if amt >= 0 else "up"
            return {"type": "scroll", "direction": direction, "amount": abs(amt)}
        if self.type == "wait":
            return {"type": "wait", "seconds": self.seconds}
        if self.type == "click_type":
            return {"type": "click_type", "x": self.x, "y": self.y, "text": self.text}
        if self.type == "goto":
            raise ValueError("goto is browser-lane only (no desktop-sandbox mapping)")
        raise ValueError(f"action {self.type!r} is control-plane only (done)")

    @property
    def is_terminal(self) -> bool:
        return self.type == "done"

    @property
    def needs_settle(self) -> bool:
        """Actions after which the loop must wait + re-observe (PRD §16)."""
        return self.type in ("click", "double_click", "right_click", "type",
                             "press", "click_type", "goto")
