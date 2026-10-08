"""Computer facade tests — every PRD §11 method hits the wire exactly once."""
import pytest

from agent.actions import Action
from agent.computer import Computer
from sandbox.mock import MockSandbox


async def test_all_methods_execute():
    m = MockSandbox()
    c = Computer(m, settle_wait=0)
    await c.move(10, 20)
    await c.click(130, 270)
    await c.double_click(200, 200)
    await c.right_click(300, 300)
    await c.type("hello UBS")
    await c.press("Enter")
    await c.scroll(3)
    await c.scroll(-2)
    await c.wait(0.1)
    types = [a["type"] for a in m.actions]
    assert types == ["move", "click", "double_click", "right_click", "type",
                     "key", "scroll", "scroll", "wait"]
    assert m.cursor == (300, 300)
    await c.close()
    assert m.closed


async def test_execute_rejects_done():
    c = Computer(MockSandbox(), settle_wait=0)
    with pytest.raises(ValueError):
        await c.execute(Action(type="done"))


async def test_screenshot_validates_png():
    class Bad:
        async def screenshot(self):
            return b"not-a-png"
        async def close(self):
            pass
    with pytest.raises(RuntimeError):
        await Computer(Bad()).screenshot()


async def test_click_type_splits():
    m = MockSandbox()
    c = Computer(m, settle_wait=0)
    await c.execute(Action(type="click_type", x=100, y=440, text="UBS"))
    assert [a["type"] for a in m.actions] == ["click", "type"]
    assert "UBS" in m.typed_text


async def test_size_and_health_passthrough():
    c = Computer(MockSandbox(), settle_wait=0)
    assert await c.size() == (1280, 800)
    assert (await c.health())["ok"]
