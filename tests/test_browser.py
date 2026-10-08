"""Browser lane + router tests. Browser tests use REAL headless Chromium (local)."""
import pytest

from agent.actions import Action
from agent.browser import BrowserLaneError, PlaywrightBrowser
from agent.computer import Computer
from agent.router import route
from sandbox.mock import MockSandbox


# ------------------------------------------------------------------ router --
def test_v1_tasks_stay_on_desktop():
    from agent.main import DEMO_TASKS

    for tid, task in DEMO_TASKS.items():
        r = route(task)
        assert r.lane == "desktop", (tid, task, r)


def test_url_tasks_go_browser():
    assert route("Open https://example.com and read it.").lane == "browser"
    assert route("Go to example.com and find pricing.").lane == "browser"
    assert route("Navigate to the UBS website.").lane == "browser"


def test_override_wins():
    assert route("Open Chromium.", lane_override="browser").lane == "browser"
    assert route("Open https://example.com", lane_override="desktop").lane == "desktop"


# ------------------------------------------------------------------ lane ----
async def test_browser_goto_title_text_screenshot():
    b = PlaywrightBrowser(headless=True)
    await b.start()
    try:
        url = await b.goto("https://example.com")
        assert url.startswith("https://example.com")
        assert "Example" in await b.title()
        assert "This domain is for use" in await b.text()
        shot = await b.screenshot()
        assert shot.startswith(b"\x89PNG") and len(shot) > 5000
    finally:
        await b.close()


async def test_browser_exec_form_actions():
    b = PlaywrightBrowser(headless=True)
    await b.start()
    try:
        await b.goto("data:text/html,<input id='q' value='' "
                     "style='position:absolute;left:50px;top:40px;width:200px;height:24px'>")
        out = await b.exec(Action(type="click_type", x=150, y=52, text="UBS"))
        assert out["ok"]
        val = await b._page.eval_on_selector("#q", "el => el.value")
        assert val == "UBS"
        out = await b.exec(Action(type="press", key="ctrl+l"))
        assert out["ok"]
        out = await b.exec(Action(type="wait", seconds=0.1))
        assert out["ok"]
        with pytest.raises(BrowserLaneError):
            await b.exec(Action(type="done"))
    finally:
        await b.close()


async def test_browser_requires_start():
    b = PlaywrightBrowser()
    with pytest.raises(BrowserLaneError):
        await b.goto("https://example.com")


async def test_browser_domain_policy():
    b = PlaywrightBrowser(headless=True, allowed_domains=["example.com"])
    await b.start()
    try:
        assert (await b.goto("https://example.com")).startswith("https://")
        with pytest.raises(BrowserLaneError, match="not allowed"):
            await b.goto("https://evil.test/page")
    finally:
        await b.close()


def test_key_mapping():
    assert PlaywrightBrowser._playwright_key("ctrl+l") == "Control+l"
    assert PlaywrightBrowser._playwright_key("Enter") == "Enter"
    assert PlaywrightBrowser._playwright_key("Escape") == "Escape"


# ------------------------------------------------- contract/router interplay --
def test_goto_rejected_by_desktop_computer():
    import asyncio

    async def go() -> None:
        comp = Computer(MockSandbox(), settle_wait=0)
        with pytest.raises(ValueError):
            await comp.execute(Action(type="goto", text="https://example.com"))

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(go())


def test_goto_validation():
    assert Action(type="goto", text="https://example.com").needs_settle
    with pytest.raises(Exception):
        Action(type="goto", text="not-a-url")
    with pytest.raises(Exception):
        Action(type="goto", text="https://example.com").to_sandbox_payload()
