# UBS Computer Use — V1 Architecture

V1 is two things: a **brain** (vision model + tiny loop) and a **body**
(desktop sandbox). They meet at exactly one contract: `Action`.

```text
USER task
  |
  v
Groq qwen/qwen3.8-27b  (vision: screenshot in, ONE Action JSON out)
  |
  v
agent/loop.py   observe -> decide -> act -> wait -> observe ...
  |
  v  Action (click / double_click / right_click / move / type / press / scroll / wait / done)
agent/computer.py  (typed facade, no model logic)
  |
  v  POST /action / GET /screenshot  (+ token)
sandbox/*  (HTTP = real desktop-sandbox | mock = synthetic | local = guarded Mac/PC)
  |
  v
Linux desktop (Xvfb + openbox + Chromium + xterm)  ->  noVNC live view
```

## Files

| Path | Role | PRD |
|---|---|---|
| `agent/actions.py` | `Action` contract + wire mapping | §12 |
| `agent/computer.py` | `Computer`: screenshot/click/type/... + `execute()` | §11 |
| `agent/model.py` | `GroqModel.decide()` + prompt + corrective retry | §13–14 |
| `agent/loop.py` | `run()`: limits, settle, anti-loop guard, session store | §15–16, §20–21 |
| `agent/config.py` | `Settings` from env | §20–21 |
| `agent/main.py` | CLI: `--probe`, task, `--task-id`, `--list-tasks` | §8 |
| `sandbox/client.py` | `create_sandbox()` factory (http/mock/local) | §8, §10 |
| `sandbox/http_client.py` | `HTTPSandbox` for desktop-sandbox API | §10–11 |
| `sandbox/mock.py` | `MockSandbox`: deterministic 1280x800 PNG body | dev/test |
| `sandbox/local.py` | `LocalSandbox`: mss + pyautogui, input needs `ALLOW_REAL_INPUT=1` | dev/test |
| `sandbox/base.py` | `DesktopBackend` protocol + wait chunking | — |
| `docker/agent.Dockerfile` | control-plane image | §8 |
| `docker/docker-compose.yml` | sandbox + agent on Linux/Docker | §10 |

## Key decisions

- **Same contract everywhere.** The model returns absolute screenshot pixels;
  `Action.to_sandbox_payload()` translates to the sandbox wire shape
  (`press` -> `{"type":"key","combo":...}`, signed `scroll` -> direction+amount).
  `done` never leaves the control plane.
- **Sandbox caps `wait` at 5s** — the HTTP client chunks longer waits.
- **Backend swap changes no brain code.** `Computer` takes any `DesktopBackend`;
  `mock` is default so V1 runs without Docker; `http` is primary on DevPod.
- **Before/after vision:** the loop passes the previous screenshot alongside the
  current one so the model sees what its last action did (PRD §16).
- **Guards:** `MAX_STEPS=30`, `MAX_RUNTIME=300s`, same-click-3x loop detector,
  one corrective retry on malformed model JSON, append-only
  `sessions/<id>/{meta.json,events.jsonl,*.png}`.
- **Security (V1):** Docker isolation, loopback-only ports, sandbox token,
  step/time bounds, no real accounts. `local` input is opt-in and refuses by default.

## What V1 does NOT do

DOM / a11y tree, Playwright, memory, multi-agent, model router, K8s,
operator console, takeover/replay — all V2+. See PRD §6, §22.
