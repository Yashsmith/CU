"""PRD §26-27 persistent Python runtime for code-execution mode.

One process, one namespace, many calls: variables, imports and bound handles
(`computer`, `browser`) survive between executions — unlike one-shot
`exec()` where every call starts amnesiac. Model code runs in a worker
thread so the event loop stays responsive; blocking async desktop calls are
bridged back via `run_coroutine_threadsafe` (no deadlock: worker != loop).
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import threading
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ExecResult:
    ok: bool
    stdout: str = ""
    error: str = ""
    result: str = ""  # repr of `_` if the code set it
    timed_out: bool = False


class ExecTimeout(RuntimeError):
    pass


class SyncComputer:
    """Blocking facade over the async Computer, for model code threads.

    Must be used from a non-loop thread (the executor worker). Each call
    blocks until the coroutine finishes on the captured event loop.
    """

    def __init__(self, computer: Any, loop: asyncio.AbstractEventLoop,
                 timeout: float = 30.0) -> None:
        self._computer = computer
        self._loop = loop
        self._timeout = timeout

    def _call(self, coro: Any) -> Any:
        fut: Future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=self._timeout)

    def click(self, x: int, y: int) -> Any:
        return self._call(self._computer.click(x, y))

    def double_click(self, x: int, y: int) -> Any:
        return self._call(self._computer.double_click(x, y))

    def right_click(self, x: int, y: int) -> Any:
        return self._call(self._computer.right_click(x, y))

    def move(self, x: int, y: int) -> Any:
        return self._call(self._computer.move(x, y))

    def type(self, text: str) -> Any:
        return self._call(self._computer.type(text))

    def press(self, key: str) -> Any:
        return self._call(self._computer.press(key))

    def scroll(self, amount: int) -> Any:
        return self._call(self._computer.scroll(amount))

    def wait(self, seconds: float) -> Any:
        return self._call(self._computer.wait(seconds))

    def screenshot(self) -> bytes:
        return self._call(self._computer.screenshot())


class PersistentExecutor:
    """Long-lived namespace + worker thread (PRD §27)."""

    def __init__(self, policy_check: Callable[[str], None] | None = None,
                 max_workers: int = 1) -> None:
        self.ns: dict[str, Any] = {"__name__": "desktop_runtime"}
        self.policy_check = policy_check
        self._pool = ThreadPoolExecutor(max_workers=max_workers,
                                        thread_name_prefix="exec")
        self._lock = threading.Lock()
        self.calls = 0

    def bind(self, name: str, value: Any) -> None:
        self.ns[name] = value

    def bind_computer(self, computer: Any,
                      loop: asyncio.AbstractEventLoop | None = None) -> None:
        loop = loop or asyncio.get_running_loop()
        self.bind("computer", SyncComputer(computer, loop))

    def reset(self) -> None:
        with self._lock:
            self.ns = {"__name__": "desktop_runtime"}
            self.calls = 0

    def _run_sync(self, code: str) -> ExecResult:
        buf = io.BytesIO()
        # NOTE: keep the wrapper alive until after getvalue(). An inline
        # `with redirect_stdout(TextIOWrapper(buf))` lets the wrapper get
        # GC'd at block exit, which closes `buf` (classic gotcha).
        wrapper = io.TextIOWrapper(buf, write_through=True)
        try:
            with contextlib.redirect_stdout(wrapper):
                exec(compile(code, "<desktop>", "exec"), self.ns)
        except Exception:
            out = self._drain(wrapper, buf)
            return ExecResult(ok=False, stdout=out, error=traceback.format_exc())
        out = self._drain(wrapper, buf)
        result = ""
        if "_" in self.ns:
            try:
                result = repr(self.ns["_"])
            except Exception:
                result = "<unrepresentable>"
        return ExecResult(ok=True, stdout=out, result=result)

    @staticmethod
    def _drain(wrapper: io.TextIOWrapper, buf: io.BytesIO) -> str:
        try:
            wrapper.flush()
            wrapper.detach()  # keep `buf` open; wrapper is discarded
        except Exception:
            pass
        return buf.getvalue().decode(errors="replace")

    async def execute(self, code: str, timeout: float = 60.0) -> ExecResult:
        if self.policy_check is not None:
            self.policy_check(code)  # raises on violation
        loop = asyncio.get_running_loop()
        with self._lock:
            self.calls += 1
        fut = self._pool.submit(self._run_sync, code)
        try:
            return await asyncio.wait_for(loop.run_in_executor(None, fut.result),
                                          timeout=timeout)
        except asyncio.TimeoutError:
            return ExecResult(ok=False, timed_out=True,
                              error=f"execution exceeded {timeout}s (worker abandoned)")

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    @property
    def names(self) -> list[str]:
        return sorted(k for k in self.ns if not k.startswith("__"))
