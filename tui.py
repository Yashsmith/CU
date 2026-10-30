"""
Computer Use — Interactive TUI
Run:  python3 tui.py

Layout:
  ┌─────────────────────────────────────┐
  │  COMPUTER USE                        │
  ├─────────────────────────────────────┤
  │                                     │
  │   LOG PANEL  (live, scrollable)     │
  │                                     │
  ├─────────────────────────────────────┤
  │  ▶  type your task and press Enter  │
  └─────────────────────────────────────┘

Keys:
  Enter       — run the typed task
  Ctrl+C / q  — quit
  Ctrl+L      — clear log
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
from pathlib import Path
from typing import Any

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Input, RichLog, Static

# ── Load .env before importing agent modules ──────────────────────────────────
def _load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

_load_dotenv()

# ── CSS ───────────────────────────────────────────────────────────────────────
CSS = """
Screen {
    background: #0d1117;
}

Header {
    background: #161b22;
    color: #58a6ff;
    text-style: bold;
    height: 1;
    dock: top;
}

#status-bar {
    height: 1;
    background: #161b22;
    color: #8b949e;
    padding: 0 2;
    dock: top;
}

#log {
    background: #0d1117;
    color: #c9d1d9;
    border: solid #30363d;
    scrollbar-color: #388bfd #161b22;
    scrollbar-size: 1 1;
    margin: 0 1;
    padding: 0 1;
}

#input-row {
    height: 3;
    dock: bottom;
    background: #161b22;
    border-top: solid #30363d;
    padding: 0 1;
    align: left middle;
}

#prompt-label {
    color: #3fb950;
    width: auto;
    padding: 0 1;
    text-style: bold;
}

#task-input {
    background: #0d1117;
    color: #e6edf3;
    border: solid #30363d;
    width: 1fr;
    height: 1;
}

#task-input:focus {
    border: solid #388bfd;
}

Footer {
    background: #161b22;
    color: #8b949e;
    height: 1;
}
"""

# ── Status bar widget ─────────────────────────────────────────────────────────
class StatusBar(Static):
    def set_idle(self) -> None:
        self.update("[dim]idle — type a task and press Enter[/dim]")

    def set_running(self, task: str) -> None:
        short = task[:60] + "..." if len(task) > 60 else task
        self.update(f"[yellow]running:[/yellow] [white]{short}[/white]")

    def set_done(self, status: str, steps: int) -> None:
        colour = "green" if status == "done" else "yellow"
        self.update(f"[{colour}]{status}[/{colour}]  [dim]steps={steps}[/dim]")


# ── Main app ──────────────────────────────────────────────────────────────────
class ComputerUseApp(App):
    TITLE = "Computer Use"
    CSS = CSS
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+l", "clear_log", "Clear"),
        Binding("q", "quit", "Quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._running = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield StatusBar(id="status-bar")
        yield RichLog(id="log", highlight=True, markup=True, wrap=False,
                      auto_scroll=True)
        with Horizontal(id="input-row"):
            yield Static("▶", id="prompt-label")
            yield Input(placeholder="type your task and press Enter...",
                        id="task-input")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(StatusBar).set_idle()
        model = os.environ.get("MODEL", "qwen/qwen3.8-27b")
        real_input = os.environ.get("ALLOW_REAL_INPUT", "0") == "1"
        self._log("[bold #58a6ff]Computer Use TUI[/bold #58a6ff]")
        self._log(f"[dim]Model : {model}[/dim]")
        self._log(f"[dim]Input : {'ENABLED' if real_input else 'DISABLED — set ALLOW_REAL_INPUT=1'}[/dim]")
        self._log("[dim]" + "─" * 60 + "[/dim]")
        self.query_one("#task-input", Input).focus()

    # ── Log helpers ───────────────────────────────────────────────────────────

    def _log(self, text: str) -> None:
        self.query_one("#log", RichLog).write(text)

    def _log_event(self, line: str) -> None:
        """Colour-code an agent log line."""
        log = self.query_one("#log", RichLog)
        if "] done:" in line or "task complete" in line:
            log.write(f"[bold green]{line}[/bold green]")
        elif "] decide:" in line:
            log.write(f"[#d2a8ff]{line}[/#d2a8ff]")
        elif "] act:" in line:
            log.write(f"[#3fb950]{line}[/#3fb950]")
        elif "] error:" in line:
            log.write(f"[bold red]{line}[/bold red]")
        elif "] limit:" in line or "] failsafe:" in line:
            log.write(f"[yellow]{line}[/yellow]")
        elif line.startswith("RESULT:"):
            log.write(f"[bold]{line}[/bold]")
        elif "COMPUTER USE" in line or "=" * 10 in line:
            log.write(f"[bold #58a6ff]{line}[/bold #58a6ff]")
        elif line.startswith("  starting in") or line.startswith("task:") or line.startswith("model:"):
            log.write(f"[dim]{line}[/dim]")
        else:
            log.write(line)

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()
        self._log("[dim]Log cleared.[/dim]")

    @on(Input.Submitted, "#task-input")
    def on_task_submitted(self, event: Input.Submitted) -> None:
        task = event.value.strip()
        if not task:
            return
        if self._running:
            self._log("[yellow]Agent is already running — wait for it to finish.[/yellow]")
            return
        event.input.clear()
        self._start_task(task)

    @work(exclusive=True, thread=True)
    def _start_task(self, task: str) -> None:
        """Run the agent in a background worker thread."""
        self._running = True
        self.call_from_thread(self.query_one(StatusBar).set_running, task)
        self.call_from_thread(self._log, "[dim]" + "─" * 60 + "[/dim]")
        self.call_from_thread(
            self._log, f"[bold #58a6ff]TASK:[/bold #58a6ff] {task}")

        # Redirect stdout to capture agent prints
        old_stdout = sys.stdout
        sys.stdout = _LineCapture(self._emit_line)

        result = None
        try:
            result = asyncio.run(self._run_agent(task))
        except Exception as e:
            self.call_from_thread(self._log, f"[bold red]FATAL: {e}[/bold red]")
        finally:
            sys.stdout = old_stdout
            self._running = False

        if result is not None:
            colour = "green" if result.status == "done" else "yellow"
            self.call_from_thread(
                self._log,
                f"\n[bold {colour}]RESULT: {result.status}[/bold {colour}]"
                f"  [dim]steps={result.steps}[/dim]")
            self.call_from_thread(
                self._log, f"[dim]session: {result.session_dir}[/dim]")
            self.call_from_thread(
                self.query_one(StatusBar).set_done,
                result.status, result.steps)
        else:
            self.call_from_thread(self.query_one(StatusBar).set_idle)

    def _emit_line(self, line: str) -> None:
        self.call_from_thread(self._log_event, line)

    async def _run_agent(self, task: str) -> Any:
        from agent.computer import Computer
        from agent.config import Settings
        from agent.loop import run
        from agent.model import GroqModel
        from sandbox.local import LocalSandbox

        settings = Settings()
        settings.validate()

        sb = LocalSandbox(glide=settings.glide)
        comp = Computer(sb, settle_wait=settings.settle_wait)
        model = GroqModel(
            api_key=settings.groq_api_key,
            model=settings.model,
            base_url=settings.groq_base_url,
        )
        try:
            return await run(
                task=task,
                model=model,
                computer=comp,
                max_steps=settings.max_steps,
                max_runtime=settings.max_runtime,
                settle_wait=settings.settle_wait,
                session_root=settings.session_dir,
            )
        finally:
            await comp.close()


# ── Line-capture stdout shim ──────────────────────────────────────────────────
class _LineCapture(io.TextIOBase):
    """Routes print() output line-by-line to a callback."""

    def __init__(self, callback) -> None:
        self._cb = callback
        self._buf = ""

    def write(self, text: str) -> int:
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line:
                self._cb(line)
        return len(text)

    def flush(self) -> None:
        if self._buf.strip():
            self._cb(self._buf)
            self._buf = ""


if __name__ == "__main__":
    ComputerUseApp().run()
