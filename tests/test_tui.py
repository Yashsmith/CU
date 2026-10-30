"""Tests for the interactive TUI."""
import pytest
from tui import ComputerUseApp, StatusBar


@pytest.mark.asyncio
async def test_tui_app_lifecycle():
    app = ComputerUseApp()
    async with app.run_test() as pilot:
        assert app.query_one("#task-input") is not None
        assert app.query_one("#log") is not None
        assert app.query_one("#status-bar") is not None


@pytest.mark.asyncio
async def test_tui_status_bar():
    sb = StatusBar()
    sb.set_idle()
    assert "idle" in str(sb.render())

    sb.set_running("Navigate to website")
    assert "running:" in str(sb.render())
    assert "Navigate to website" in str(sb.render())

    sb.set_done("done", 4)
    assert "done" in str(sb.render())
    assert "steps=4" in str(sb.render())
