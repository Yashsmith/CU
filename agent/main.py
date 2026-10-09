"""CLI entry point (PRD §8: agent/main.py; V2 matrix/sessions/control in §38/§33/§34).

V1 usage (unchanged):
  python -m agent.main --probe
  python -m agent.main "Open Chromium and search for UBS"
  python -m agent.main --task-id 2
V2 usage:
  python -m agent.main --provider gemini --mode vision_actions "task"
  python -m agent.main --mode code_execution "task"
  python -m agent.main --lane browser "Read https://example.com"
  python -m agent.main --verify "task"
  python -m agent.main --list-sessions / --show-session ID / --resume ID "follow-up"
  python -m agent.main --pause ID | --resume-id ID | --take-control ID | --release ID [--stop]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

DEMO_TASKS = {
    "1": "Open Chromium.",
    "2": "Open Chromium and search for UBS.",
    "3": 'Open the terminal and run: echo "Hello UBS"',
    "4": "Open Chromium. Search for UBS. Then open the terminal.",
    "5": "Open the terminal. Create a file called test.txt. Then open it.",
}


def load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


async def probe(backend: str, sandbox_url: str, token: str, save_to: str = "probe.png") -> int:
    """No-model check: health + size + screenshot + safe wait (PRD Phase 1 gate)."""
    from agent.computer import Computer
    from sandbox.client import create_sandbox

    sb = create_sandbox(backend, base_url=sandbox_url,
                        token=token or None) if backend == "http" else create_sandbox(backend)
    comp = Computer(sb, settle_wait=0)
    try:
        h = await comp.health()
        print(f"health: {h}")
        w, hh = await comp.size()
        print(f"size: {w}x{hh}")
        shot = await comp.screenshot()
        Path(save_to).write_bytes(shot)
        print(f"screenshot: {len(shot)} bytes -> {save_to}")
        # Safe input-path proof that moves nothing: a short wait.
        try:
            r = await comp.wait(0.2)
            print(f"input path (wait): {r}")
        except Exception as e:
            print(f"input path (wait) failed: {e}")
            return 1
        print("PROBE OK — observe path works, input path works.")
        return 0
    except Exception as e:
        print(f"PROBE FAILED: {e}")
        return 1
    finally:
        await comp.close()


def _build_adapter(args: argparse.Namespace):
    """V2 model matrix (PRD §38). V1 path = provider groq + vision_actions."""
    from agent.adapters import create_model
    from agent.config import Settings

    settings = Settings()
    provider = args.provider or settings.provider
    model_name = args.model or settings.model_name
    mode = args.mode or settings.model_mode
    if provider == "groq" and not settings.groq_api_key:
        print("ERROR: GROQ_API_KEY is not set. Copy .env.example to .env.")
        raise SystemExit(2)
    if provider == "gemini" and not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY is not set.")
        raise SystemExit(2)
    adapter = create_model(
        provider, model_name, mode,
        groq_api_key=settings.groq_api_key,
        gemini_api_key=settings.gemini_api_key,
        openai_api_key=settings.openai_api_key,
        openai_base_url=settings.openai_base_url)
    return adapter, settings, model_name, mode


async def run_task(task: str, args: argparse.Namespace) -> int:
    from agent.browser import PlaywrightBrowser
    from agent.computer import Computer
    from agent.config import Settings
    from agent.control import ControlState
    from agent.executor import PersistentExecutor
    from agent.loop import run
    from agent.policy import SecurityPolicy
    from agent.router import route
    from sandbox.client import create_sandbox
    from sandbox.local import real_input_enabled

    adapter, settings, model_name, mode = _build_adapter(args)
    sb = create_sandbox(args.backend, base_url=args.sandbox_url,
                        token=args.sandbox_token or None)
    comp = Computer(sb, settle_wait=args.step_wait)
    policy = SecurityPolicy.from_env()  # PRD §36 on every run
    comp.attach_policy(policy)

    lane = args.lane or route(task).lane
    if args.lane:
        lane = args.lane
    browser = None
    executor = None
    try:
        if lane == "browser":
            w, h = 1600, 900
            try:
                w, h = await comp.size()
            except Exception:
                pass
            browser = PlaywrightBrowser(width=w, height=h, headless=not args.show_browser,
                                        allowed_domains=policy.allowed_domains)
            await browser.start()
            comp.attach_browser(browser)
        if mode == "code_execution":
            executor = PersistentExecutor(policy_check=policy.check_code)
            comp.attach_executor(executor)
            executor.bind_computer(comp)
            if browser is not None:
                executor.bind("browser", browser)

        judge = None
        if args.verify:            async def judge(messages, _adapter=adapter):  # noqa: B023
                complete = getattr(_adapter, "complete_text", None)
                if complete is None:
                    for attr in ("inner", "model"):  # code adapters nest; groq wraps
                        sub = getattr(_adapter, attr, None)
                        complete = getattr(sub, "complete_text", None)
                        if complete is not None:
                            break
                if complete is None:
                    raise RuntimeError("this adapter cannot judge (no text completion)")
                return await complete(messages)

        control = None
        if args.session_id:
            control = ControlState(Path(settings.session_dir) / args.session_id)
        if args.backend == "local" and real_input_enabled():
            import asyncio as _aio

            print("\n*** VISIBLE TAKEOVER: your cursor is about to move on its own. ***")
            print("Hands off mouse + keyboard. ABORT any time: slam the cursor")
            print("into any screen corner (failsafe), or press Ctrl+C here.\n")
            for i in (3, 2, 1):
                print(f"  starting in {i}...", flush=True)
                await _aio.sleep(1)
        print(f"task: {task}\nbackend: {args.backend}  provider: {args.provider or settings.provider}  "
              f"model: {model_name}  mode: {mode}  lane: {lane}  verify: {args.verify}  "
              f"max_steps: {args.max_steps}  max_runtime: {args.max_runtime}s")
        res = await run(task, None, comp, max_steps=args.max_steps,
                        max_runtime=args.max_runtime, settle_wait=args.step_wait,
                        session_root=settings.session_dir, session_id=args.session_id,
                        model_name=f"{model_name} [{mode}/{lane}]",
                        adapter=adapter, lane=lane, browser=browser,
                        verify=args.verify, judge=judge,
                        control=control, resume_from=args.resume)
    finally:
        if browser is not None:
            await browser.close()
        if executor is not None:
            executor.close()
        await comp.close()
    print(f"\nRESULT: status={res.status} steps={res.steps} session={res.session_dir}")
    return 0 if res.status == "done" else 3


def _terminal_host_app() -> str:
    """Which macOS app must hold Accessibility/Screen-Recording for THIS shell."""
    import subprocess as _sp

    try:
        pid = os.getppid()
        for _ in range(6):
            out = _sp.run(["ps", "-p", str(pid), "-o", "comm="],
                          capture_output=True, text=True, timeout=5)
            comm = out.stdout.strip()
            low = comm.lower()
            if "code" in low and "helper" not in low:
                return "Code (Visual Studio Code)"
            for name in ("Terminal", "iTerm2", "Alacritty", "Warp", "Kitty",
                         "WezTerm", "Ghostty"):
                if name.lower() in low:
                    return name
            ppid = _sp.run(["ps", "-p", str(pid), "-o", "ppid="],
                           capture_output=True, text=True, timeout=5)
            pid = int(ppid.stdout.strip())
    except Exception:
        pass
    return "your terminal app (see System Settings)"


async def doctor(backend: str, sandbox_url: str, token: str) -> int:
    """Visible-mode readiness: screenshots, scale, injection, focus (PRD: watch it)."""
    from agent.computer import Computer
    from sandbox.client import create_sandbox

    print(f"backend: {backend}")
    sb = create_sandbox(backend, base_url=sandbox_url, token=token or None) \
        if backend == "http" else create_sandbox(backend)
    comp = Computer(sb, settle_wait=0)
    try:
        h = await comp.health()
        print(f"health: {h}")
        w, hh = await comp.size()
        print(f"size: {w}x{hh}")
        shot = await comp.screenshot()
        print(f"screenshot: {len(shot)} bytes")
        if backend == "local":
            from PIL import Image as _Image

            import io as _io

            img = _Image.open(_io.BytesIO(shot))
            print(f"shot pixels: {img.width}x{img.height} (model coords == points 1:1 "
                  f"-> {img.width == w and img.height == hh})")
            extrema = img.convert("L").getextrema()
            print(f"luma range: {extrema} "
                  f"({'BLACK — Screen Recording missing' if extrema == (0, 0) else 'has content ✓'})")
            try:
                import pyautogui as _pg

                print(f"pyautogui points: {_pg.size().width}x{_pg.size().height}")
            except Exception as e:
                print(f"pyautogui unavailable: {e}")
            from sandbox.local import frontmost_app, real_input_enabled
            try:
                print(f"frontmost app: {frontmost_app()}")
            except Exception as e:
                print(f"frontmost query failed: {e}")
            host = _terminal_host_app()
            print(f"terminal host app (needs the macOS permissions): {host}")
            print(f"ALLOW_REAL_INPUT={'1' if real_input_enabled() else '0 (input will refuse)'}")
            print("\nIf injection is dead, grant + RESTART the terminal:")
            print("  System Settings > Privacy & Security > Accessibility > + "
                  + host)
            print("  System Settings > Privacy & Security > Screen Recording > + "
                  + host)
            print("  ...then Quit + reopen the terminal app completely.")
        print("DOCTOR done.")
        return 0
    except Exception as e:
        print(f"DOCTOR FAILED: {e}")
        return 1
    finally:
        await comp.close()


def _sessions_cmd(args: argparse.Namespace) -> int:
    from agent.config import Settings
    from agent.control import ControlState
    from agent.sessions import SessionStore

    settings = Settings()
    root = Path(settings.session_dir)
    if args.list_sessions:
        for s in SessionStore.list(root):
            print(f"{s.id}  {s.status:18s} step={s.step:<3d} model={s.model}  {s.task[:70]}")
        return 0
    if args.show_session:
        s = SessionStore.load(root, args.show_session)
        print(f"id: {s.id}\ntask: {s.task}\nmodel: {s.model}\n"
              f"status: {s.status}  step: {s.step}")
        for line in (root / s.id / "events.jsonl").read_text().splitlines():
            import json as _json

            e = _json.loads(line)
            print(f"  [{e['seq']}] step {e.get('step', '-')} {e['kind']}: "
                  f"{str(e.get('detail', ''))[:160]}")
        return 0
    sid = (args.pause or args.resume_id or args.take_control or args.release
           or args.control_status)
    ctl = ControlState(root / sid)
    if args.pause:
        ctl.pause()
        print(f"{sid}: paused (agent parks at next step boundary)")
    elif args.resume_id:
        ctl.resume()
        print(f"{sid}: resumed")
    elif args.take_control:
        ctl.take_control()
        print(f"{sid}: TAKE CONTROL — model stopped, desktop stays alive.")
        try:
            from agent.computer import Computer  # noqa
            print("Drive it live at the sandbox noVNC URL "
                  "(see health stream_http), then --release.")
        except Exception:
            pass
    elif args.release:
        ctl.release(stop=args.stop)
        print(f"{sid}: {'stopped' if args.stop else 'released — resume with --resume ' + sid}")
    elif args.control_status:
        print(f"{sid}: {ctl.read()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="UBS Computer Use Agent (V1+V2)")
    p.add_argument("task", nargs="?", default="", help="Task string (or use --task-id)")
    p.add_argument("--task-id", choices=sorted(DEMO_TASKS), default=None,
                   help="Run one of the 5 PRD demo tasks")
    p.add_argument("--list-tasks", action="store_true")
    p.add_argument("--probe", action="store_true", help="No-model health/screenshot/input check")
    p.add_argument("--doctor", action="store_true",
                   help="Visible-mode readiness (screenshots, scale, perms)")
    p.add_argument("--backend", default=os.environ.get("SANDBOX_BACKEND", "mock"),
                   choices=["http", "mock", "local"])
    p.add_argument("--sandbox-url", default=os.environ.get("SANDBOX_URL", "http://127.0.0.1:7090"))
    p.add_argument("--sandbox-token", default=os.environ.get("SANDBOX_TOKEN", ""))
    p.add_argument("--model", default=os.environ.get("MODEL", os.environ.get(
        "MODEL_NAME", "qwen/qwen3.8-27b")), help="Model id (V1 MODEL or V2 MODEL_NAME)")
    # V2 matrix (PRD §38)
    p.add_argument("--provider", default=os.environ.get("MODEL_PROVIDER", "groq"),
                   help="groq | gemini | openai_compat | astra")
    p.add_argument("--mode", default=os.environ.get("MODEL_MODE", "vision_actions"),
                   help="vision_actions | computer | code_execution")
    p.add_argument("--lane", default=None, choices=["browser", "desktop", "local"],
                   help="Force lane (default: auto-route; local = your Mac screen)")
    p.add_argument("--verify", action="store_true", help="PRD §31 verification + §32 recovery")
    p.add_argument("--show-browser", action="store_true",
                   help="Headed browser lane (needs display)")
    # V2 sessions (PRD §33)
    p.add_argument("--session-id", default=None, help="Fixed session id for this run")
    p.add_argument("--resume", default=None, metavar="ID",
                   help="Seed history from past session ID (+ optional new task)")
    p.add_argument("--list-sessions", action="store_true")
    p.add_argument("--show-session", default=None, metavar="ID")
    # V2 takeover (PRD §34)
    p.add_argument("--pause", default=None, metavar="ID")
    p.add_argument("--resume-id", default=None, metavar="ID")
    p.add_argument("--take-control", default=None, metavar="ID")
    p.add_argument("--release", default=None, metavar="ID")
    p.add_argument("--stop", action="store_true", help="With --release: end instead of resume")
    p.add_argument("--control-status", default=None, metavar="ID")
    p.add_argument("--max-steps", type=int, default=int(os.environ.get("MAX_STEPS", "30")))
    p.add_argument("--max-runtime", type=float, default=float(os.environ.get("MAX_RUNTIME", "300")))
    p.add_argument("--step-wait", type=float, default=float(os.environ.get("STEP_WAIT", "0.5")))
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    # refresh defaults post-dotenv (parser was built before .env was loaded)
    parser.set_defaults(
        backend=os.environ.get("SANDBOX_BACKEND", "mock"),
        sandbox_url=os.environ.get("SANDBOX_URL", "http://127.0.0.1:7090"),
        sandbox_token=os.environ.get("SANDBOX_TOKEN", ""),
        model=os.environ.get("MODEL", os.environ.get("MODEL_NAME", "qwen/qwen3.8-27b")),
        provider=os.environ.get("MODEL_PROVIDER", "groq"),
        mode=os.environ.get("MODEL_MODE", "vision_actions"),
        max_steps=int(os.environ.get("MAX_STEPS", "30")),
        max_runtime=float(os.environ.get("MAX_RUNTIME", "300")),
        step_wait=float(os.environ.get("STEP_WAIT", "0.5")),
    )
    args = parser.parse_args(argv)
    if args.list_tasks:
        for k, v in DEMO_TASKS.items():
            print(f"[{k}] {v}")
        return 0
    if args.probe:
        return asyncio.run(probe(args.backend, args.sandbox_url, args.sandbox_token))
    if args.doctor:
        return asyncio.run(doctor(args.backend, args.sandbox_url, args.sandbox_token))
    if args.list_sessions or args.show_session or args.pause or args.resume_id \
            or args.take_control or args.release or args.control_status:
        return _sessions_cmd(args)
    task = DEMO_TASKS[args.task_id] if args.task_id else args.task
    if args.resume and not task:
        from agent.config import Settings
        from agent.sessions import SessionStore

        prev = SessionStore.load(Settings().session_dir, args.resume)
        task = prev.task
    if not task:
        parser.print_help()
        return 2
    return asyncio.run(run_task(task, args))


if __name__ == "__main__":
    sys.exit(main())
