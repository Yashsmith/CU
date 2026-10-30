"""CLI entry point.

Usage:
  # Health / readiness check (no model):
  python -m agent.main --probe
  python -m agent.main --doctor

  # Run a task (real Mac control):
  ALLOW_REAL_INPUT=1 python -m agent.main "Open Chrome and search for Python"
  ALLOW_REAL_INPUT=1 python -m agent.main --task-id 2

  # Canned demo tasks:
  python -m agent.main --list-tasks
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

DEMO_TASKS = {
    "1": "Open a new tab in Chrome and go to https://google.com",
    "2": "Open Chrome, open a new tab, search Google for 'Python tutorials'",
    "3": "Open the Terminal application and run: echo 'Hello from Computer Use'",
    "4": "Open Chrome, open a new tab, navigate to https://news.ycombinator.com",
    "5": "Open TextEdit and type: 'Computer Use is working!'",
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


# ── Probe / Doctor ────────────────────────────────────────────────────────────

async def probe() -> int:
    """No-model check: screenshot + health."""
    from agent.computer import Computer
    from sandbox.local import LocalSandbox

    sb = LocalSandbox()
    comp = Computer(sb, settle_wait=0)
    try:
        h = await comp.health()
        print(f"health:  {h}")
        w, hh = await comp.size()
        print(f"size:    {w}×{hh}")
        shot = await comp.screenshot()
        Path("probe.png").write_bytes(shot)
        print(f"screenshot: {len(shot):,} bytes → probe.png")
        print("PROBE OK ✓")
        return 0
    except Exception as e:
        print(f"PROBE FAILED: {e}")
        return 1
    finally:
        await comp.close()


async def doctor() -> int:
    """Visible-mode readiness: screenshot, scale, permissions."""
    from agent.computer import Computer
    from sandbox.local import LocalSandbox, real_input_enabled, frontmost_app
    import io
    from PIL import Image

    sb = LocalSandbox()
    comp = Computer(sb, settle_wait=0)
    try:
        h = await comp.health()
        print(f"health:       {h}")
        w, hh = await comp.size()
        print(f"logical size: {w}×{hh}")
        shot = await comp.screenshot()
        img = Image.open(io.BytesIO(shot))
        print(f"shot pixels:  {img.width}×{img.height} "
              f"(1:1 with coords: {img.width == w and img.height == hh})")
        extrema = img.convert("L").getextrema()
        ok = extrema != (0, 0)
        print(f"luma range:   {extrema} "
              f"({'✓ has content' if ok else '✗ BLACK — Screen Recording permission missing'})")
        try:
            import pyautogui as pg
            print(f"pyautogui:    {pg.size().width}×{pg.size().height}")
        except Exception as e:
            print(f"pyautogui:    MISSING ({e}) — run: pip install pyautogui")
        print(f"frontmost:    {frontmost_app()}")
        print(f"real_input:   {'ENABLED ✓' if real_input_enabled() else 'DISABLED (set ALLOW_REAL_INPUT=1)'}")
        if sys.platform == "darwin":
            try:
                import Quartz
                has_screen_perm = Quartz.CGPreflightScreenCaptureAccess()
                if not has_screen_perm:
                    print("\n⚠️  SCREEN RECORDING PERMISSION MISSING:")
                    print("   macOS is blocking screen capture for your terminal application.")
                    print("   This causes screenshots to be blank wallpaper, so the model cannot see Chrome or windows.")
                    print("   Fix:")
                    print("   1. Open: System Settings → Privacy & Security → Screen Recording")
                    print("   2. Toggle ON permission for your Terminal / iTerm / IDE.")
                    print("   3. Restart your terminal.")
                    try:
                        Quartz.CGRequestScreenCaptureAccess()
                    except Exception:
                        pass
                else:
                    print("screen record: GRANTED ✓")
            except Exception:
                pass
        if not real_input_enabled():
            print("\nTo enable real input:")
            print("  1. System Settings → Privacy & Security → Accessibility → add your terminal")
            print("  2. System Settings → Privacy & Security → Screen Recording → add your terminal")
            print("  3. Quit and reopen your terminal")
            print("  4. Set ALLOW_REAL_INPUT=1 in your .env or shell")
        print("\nDOCTOR done.")
        return 0
    except Exception as e:
        print(f"DOCTOR FAILED: {e}")
        return 1
    finally:
        await comp.close()


# ── Run task ──────────────────────────────────────────────────────────────────

async def run_task(task: str, args: argparse.Namespace) -> int:
    from agent.computer import Computer
    from agent.config import Settings
    from agent.loop import run
    from agent.model import GroqModel
    from sandbox.local import LocalSandbox, real_input_enabled

    settings = Settings()
    settings.validate()

    sb = LocalSandbox(glide=settings.glide)
    comp = Computer(sb, settle_wait=settings.settle_wait)
    model = GroqModel(
        api_key=settings.groq_api_key,
        model=args.model or settings.model,
        base_url=settings.groq_base_url,
    )

    if sys.platform == "darwin":
        try:
            import Quartz
            if not Quartz.CGPreflightScreenCaptureAccess():
                print("\n⚠️  WARNING: macOS Screen Recording permission is NOT enabled for this app.")
                print("   macOS will hide all windows (including Chrome) and send blank screenshots.")
                print("   Fix: System Settings → Privacy & Security → Screen Recording (toggle ON & restart terminal)\n")
                try:
                    Quartz.CGRequestScreenCaptureAccess()
                except Exception:
                    pass
        except Exception:
            pass

    if real_input_enabled():
        print("\n" + "=" * 60)
        print("  COMPUTER USE — REAL MAC CONTROL")
        print("  The cursor is about to move on its own.")
        print("  ABORT: slam cursor into any screen corner, or Ctrl+C")
        print("=" * 60)
        for i in (3, 2, 1):
            print(f"  starting in {i}…", flush=True)
            await asyncio.sleep(1)
    else:
        print("NOTE: ALLOW_REAL_INPUT=0 — screenshots only, no real input.")
        print("      Set ALLOW_REAL_INPUT=1 to enable real mouse/keyboard.")

    print(f"\ntask:  {task}")
    print(f"model: {args.model or settings.model}")
    print(f"steps: {args.max_steps}  runtime: {args.max_runtime}s\n")

    result = await run(
        task=task,
        model=model,
        computer=comp,
        max_steps=args.max_steps,
        max_runtime=args.max_runtime,
        settle_wait=settings.settle_wait,
        session_root=settings.session_dir,
    )
    await comp.close()

    print(f"\nRESULT: status={result.status}  steps={result.steps}")
    print(f"        session: {result.session_dir}")
    return 0 if result.status == "done" else 3


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Computer Use Agent — drives your real Mac via vision + pyautogui")
    p.add_argument("task", nargs="?", default="",
                   help="Natural-language task to execute")
    p.add_argument("--task-id", choices=sorted(DEMO_TASKS),
                   help="Run a canned demo task")
    p.add_argument("--list-tasks", action="store_true",
                   help="List canned demo tasks and exit")
    p.add_argument("--probe",  action="store_true",
                   help="Health + screenshot check (no model)")
    p.add_argument("--doctor", action="store_true",
                   help="Permissions + scale readiness check")
    p.add_argument("--model",
                   default=os.environ.get("MODEL", "qwen/qwen3.8-27b"),
                   help="Groq model id")
    p.add_argument("--max-steps", type=int,
                   default=int(os.environ.get("MAX_STEPS", "30")))
    p.add_argument("--max-runtime", type=float,
                   default=float(os.environ.get("MAX_RUNTIME", "300")))
    return p


def main(argv: Optional[list[str]] = None) -> int:
    load_dotenv()
    parser = build_parser()
    # Re-apply env defaults after .env is loaded
    parser.set_defaults(
        model=os.environ.get("MODEL", "qwen/qwen3.8-27b"),
        max_steps=int(os.environ.get("MAX_STEPS", "30")),
        max_runtime=float(os.environ.get("MAX_RUNTIME", "300")),
    )
    args = parser.parse_args(argv)

    if args.list_tasks:
        for k, v in DEMO_TASKS.items():
            print(f"  [{k}] {v}")
        return 0

    if args.probe:
        return asyncio.run(probe())

    if args.doctor:
        return asyncio.run(doctor())

    task = DEMO_TASKS[args.task_id] if args.task_id else args.task
    if not task:
        parser.print_help()
        return 2

    return asyncio.run(run_task(task, args))


if __name__ == "__main__":
    sys.exit(main())
