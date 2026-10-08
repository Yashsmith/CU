"""CLI entry point (PRD §8: agent/main.py).

Usage:
  python -m agent.main --probe
  python -m agent.main "Open Chromium and search for UBS"
  python -m agent.main --task-id 2
  python -m agent.main --list-tasks
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


async def run_task(task: str, args: argparse.Namespace) -> int:
    from agent.computer import Computer
    from agent.config import Settings
    from agent.loop import run
    from agent.model import GroqModel
    from sandbox.client import create_sandbox

    settings = Settings()
    if not settings.groq_api_key:
        print("ERROR: GROQ_API_KEY is not set. Copy .env.example to .env.")
        return 2
    sb = create_sandbox(args.backend, base_url=args.sandbox_url,
                        token=args.sandbox_token or None)
    comp = Computer(sb, settle_wait=args.step_wait)
    model = GroqModel(api_key=settings.groq_api_key, model=args.model)
    print(f"task: {task}\nbackend: {args.backend}  model: {args.model}  "
          f"max_steps: {args.max_steps}  max_runtime: {args.max_runtime}s")
    try:
        res = await run(task, model, comp, max_steps=args.max_steps,
                        max_runtime=args.max_runtime, settle_wait=args.step_wait,
                        session_root=settings.session_dir, model_name=args.model)
    finally:
        await comp.close()
    print(f"\nRESULT: status={res.status} steps={res.steps} session={res.session_dir}")
    return 0 if res.status == "done" else 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="UBS Computer Use Agent V1")
    p.add_argument("task", nargs="?", default="", help="Task string (or use --task-id)")
    p.add_argument("--task-id", choices=sorted(DEMO_TASKS), default=None,
                   help="Run one of the 5 PRD demo tasks")
    p.add_argument("--list-tasks", action="store_true")
    p.add_argument("--probe", action="store_true", help="No-model health/screenshot/input check")
    p.add_argument("--backend", default=os.environ.get("SANDBOX_BACKEND", "mock"),
                   choices=["http", "mock", "local"])
    p.add_argument("--sandbox-url", default=os.environ.get("SANDBOX_URL", "http://127.0.0.1:7090"))
    p.add_argument("--sandbox-token", default=os.environ.get("SANDBOX_TOKEN", ""))
    p.add_argument("--model", default=os.environ.get("MODEL", "qwen/qwen3.8-27b"))
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
        model=os.environ.get("MODEL", "qwen/qwen3.8-27b"),
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
    task = DEMO_TASKS[args.task_id] if args.task_id else args.task
    if not task:
        parser.print_help()
        return 2
    return asyncio.run(run_task(task, args))


if __name__ == "__main__":
    sys.exit(main())
