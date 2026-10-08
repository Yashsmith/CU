# UBS Computer Use Agent — V1

Minimal computer-use loop: **Groq vision → tiny agent → real desktop**,
watchable over noVNC. Brain and body stay independent behind one `Action` contract.

```text
User task -> Vision model -> Screenshot understanding -> Action
  -> Actual mouse/keyboard -> Linux desktop -> noVNC live view
```

## Quickstart (no Docker needed)

```bash
cp .env.example .env        # add GROQ_API_KEY (MODEL is qwen/qwen3.8-27b)
pip install -e ".[dev]"

python -m agent.main --probe                 # Phase 1 gate: health + screenshot + input path
python -m agent.main --list-tasks            # 5 PRD demo tasks
python -m agent.main --task-id 2             # live: Groq drives the mock desktop
python -m agent.main "Open Chromium."        # free-form task
```

## With the real desktop (Linux DevPod / Docker host)

```bash
export GROQ_API_KEY=... SANDBOX_TOKEN=$(openssl rand -hex 16)
docker compose -f docker/docker-compose.yml up --build -d
# noVNC live view: http://127.0.0.1:6080/vnc.html
curl -sf -H "X-Sandbox-Token: $SANDBOX_TOKEN" http://127.0.0.1:7090/health

SANDBOX_BACKEND=http SANDBOX_URL=http://127.0.0.1:7090 \
  python -m agent.main --task-id 2            # watch the cursor move in noVNC
```

## Backends

| `SANDBOX_BACKEND` | Body | Use |
|---|---|---|
| `mock` (default) | Synthetic 1280x800 desktop, in-memory | Dev without Docker, all tests |
| `http` | Real `desktop-sandbox` over HTTP Desktop API | DevPod / Linux / CI e2e |
| `local` | Your screen via `mss`; input via `pyautogui` | Visible Mac/PC demo |

`local` input is **refused** unless `ALLOW_REAL_INPUT=1` is set (read-only
screenshot always works). Needs `pip install -e ".[local]"` + OS accessibility permission.

## Tests

```bash
python -m pytest tests/ -q                       # offline: 37 tests
RUN_LIVE=1 python -m pytest tests/ -q            # + live Groq vision (uses API credit)
```

Layers: `test_actions` (contract) → `test_sandbox` (bodies) → `test_computer`
(facade) → `test_model` (brain parsing + 1 live vision) → `test_loop` (loop,
limits, guards) → `test_e2e` (PRD demo tasks + live brain+body).

## V1 success checklist (PRD §43)

Desktop isolation, Chromium + terminal, screenshot/mouse/keyboard API, noVNC,
model sees screenshot, model picks actions, cursor visibly moves, click/type
occur, multi-step runs, clean stop, step limit. Nothing more — V2 adds
multi-model adapters, Playwright, verification/recovery, sessions UI.
