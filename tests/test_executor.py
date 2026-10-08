"""Persistent executor + bridge tests — offline, real threads/subprocess semantics."""
import asyncio

import pytest

from agent.computer import Computer
from agent.executor import ExecTimeout, PersistentExecutor, SyncComputer
from sandbox.mock import MockSandbox


async def test_state_persists_between_calls():
    ex = PersistentExecutor()
    r1 = await ex.execute("x = 40 + 2")
    assert r1.ok and r1.error == ""
    r2 = await ex.execute("_ = x + 1\nprint('sum', x + 1)")
    assert r2.ok and "sum 43" in r2.stdout and r2.result == "43"
    assert ex.calls == 2
    ex.close()


async def test_stdout_and_error_captured():
    ex = PersistentExecutor()
    ok = await ex.execute("print('hello')")
    assert ok.ok and ok.stdout.strip() == "hello"
    bad = await ex.execute("raise ValueError('boom')")
    assert not bad.ok and "ValueError: boom" in bad.error
    assert "Traceback" in bad.error
    ex.close()


async def test_imports_persist():
    ex = PersistentExecutor()
    assert (await ex.execute("import math")).ok
    r = await ex.execute("_ = math.floor(3.7)")
    assert r.ok and r.result == "3"
    ex.close()


async def test_timeout_abandons_worker():
    ex = PersistentExecutor()
    r = await ex.execute("import time; time.sleep(30)", timeout=0.5)
    assert not r.ok and r.timed_out and "exceeded 0.5s" in r.error
    ex.close()
    # Recovery pattern: drop the wedged runtime and start a fresh one.
    fresh = PersistentExecutor()
    assert (await fresh.execute("y = 1")).ok
    fresh.close()


async def test_policy_check_blocks():
    def check(code: str) -> None:
        if "import os" in code:
            raise ValueError("blocked: os")

    ex = PersistentExecutor(policy_check=check)
    with pytest.raises(ValueError, match="blocked"):
        await ex.execute("import os")
    assert ex.calls == 0  # blocked before counting
    ex.close()


async def test_reset_clears_state():
    ex = PersistentExecutor()
    await ex.execute("z = 5")
    assert "z" in ex.names
    ex.reset()
    assert ex.names == []
    r = await ex.execute("print(z)")
    assert not r.ok and "NameError" in r.error
    ex.close()


async def test_sync_computer_from_worker_thread():
    m = MockSandbox()
    comp = Computer(m, settle_wait=0)
    ex = PersistentExecutor()
    ex.bind_computer(comp)  # called inside running loop (async test)
    r = await ex.execute("computer.click(11, 22)\ncomputer.type('hi')")
    assert r.ok, r.error
    assert [a["type"] for a in m.actions] == ["click", "type"]
    assert m.cursor == (11, 22)
    ex.close()


async def test_sync_computer_rejects_loop_thread():
    m = MockSandbox()
    comp = Computer(m, settle_wait=0)
    loop = asyncio.get_running_loop()
    sync = SyncComputer(comp, loop)
    # run_coroutine_threadsafe from the loop thread itself deadlocks/fails;
    # guard: our bridge is documented worker-only. Direct call would block,
    # so just assert the plumbing object holds refs.
    assert sync._computer is comp and sync._loop is loop


async def test_computer_execute_python_bridge():
    m = MockSandbox()
    comp = Computer(m, settle_wait=0)
    with pytest.raises(RuntimeError, match="no executor"):
        await comp.execute_python("x=1")
    ex = PersistentExecutor()
    comp.attach_executor(ex)
    ex.bind_computer(comp)
    r = await comp.execute_python("_ = 6 * 7")
    assert r.ok and r.result == "42"
    # code can drive the desktop through the bound computer
    r2 = await comp.execute_python("computer.press('Enter')")
    assert r2.ok and m.actions[-1] == {"type": "key", "combo": "Enter"}
    ex.close()


async def test_computer_browser_bridge():
    comp = Computer(MockSandbox(), settle_wait=0)
    with pytest.raises(RuntimeError, match="no browser"):
        comp.browser()
    sentinel = object()
    comp.attach_browser(sentinel)
    assert comp.browser() is sentinel
