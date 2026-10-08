"""Unit tests for the V1 action contract (PRD §12)."""
import pytest
from pydantic import ValidationError

from agent.actions import Action


def test_all_canonical_types_validate():
    assert Action(type="click", x=10, y=20).to_sandbox_payload() == {"type": "click", "x": 10, "y": 20}
    assert Action(type="double_click", x=1, y=2).to_sandbox_payload()["type"] == "double_click"
    assert Action(type="move", x=0, y=0).to_sandbox_payload()["type"] == "move"
    assert Action(type="type", text="UBS").to_sandbox_payload() == {"type": "type", "text": "UBS"}
    assert Action(type="press", key="Enter").to_sandbox_payload() == {"type": "key", "combo": "Enter"}
    assert Action(type="wait", seconds=1.5).to_sandbox_payload() == {"type": "wait", "seconds": 1.5}
    assert Action(type="done").is_terminal


def test_press_alias_key_normalised():
    a = Action(type="key", combo="ctrl+l")
    assert a.type == "press" and a.key == "ctrl+l"
    assert a.to_sandbox_payload() == {"type": "key", "combo": "ctrl+l"}


def test_scroll_sign_to_direction():
    assert Action(type="scroll", amount=3).to_sandbox_payload() == {
        "type": "scroll", "direction": "down", "amount": 3}
    assert Action(type="scroll", amount=-2).to_sandbox_payload() == {
        "type": "scroll", "direction": "up", "amount": 2}


def test_parse_unwraps_action_envelope():
    a = Action.parse({"action": {"type": "click", "x": 5, "y": 6}})
    assert (a.x, a.y) == (5, 6)


def test_missing_fields_rejected():
    with pytest.raises(ValidationError):
        Action(type="click", x=1)
    with pytest.raises(ValidationError):
        Action(type="type", text="")
    with pytest.raises(ValidationError):
        Action(type="press")
    with pytest.raises(ValidationError):
        Action(type="wait")
    with pytest.raises(ValidationError):
        Action(type="click", x=-1, y=0)


def test_done_never_has_wire_payload():
    with pytest.raises(ValueError):
        Action(type="done").to_sandbox_payload()


def test_needs_settle_flags():
    assert Action(type="click", x=1, y=1).needs_settle
    assert Action(type="type", text="hi").needs_settle
    assert not Action(type="move", x=1, y=1).needs_settle
    assert not Action(type="wait", seconds=1).needs_settle
    assert not Action(type="done").needs_settle
