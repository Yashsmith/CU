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

### Mac without Docker Desktop: Colima (verified working, Apple Silicon)

```bash
brew install colima docker docker-compose docker-buildx
colima start --cpu 4 --memory 8 --disk 60
docker context use colima
# then follow "real desktop" above; sandbox runs linux/arm64 natively.
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
python -m pytest tests/ -q                       # offline (no network, no keys)
RUN_LIVE=1 python -m pytest tests/ -q            # + live Groq/GPT-OSS/lanes (uses API credit)
```

Layers: `test_actions` (contract) → `test_sandbox` (bodies) → `test_computer`
(facade) → `test_model` (brain parsing + 1 live vision) → `test_loop` (loop,
limits, guards) → `test_adapters` (matrix) → `test_executor` (runtime) →
`test_browser` (lane+router) → `test_verify` (verify/recovery/loop) →
`test_sessions` (state/resume) → `test_control` (takeover) → `test_policy`
(security) → `test_e2e` (demo tasks + live brain+body+lanes).

## V1 success checklist (PRD §43)

Desktop isolation, Chromium + terminal, screenshot/mouse/keyboard API, noVNC,
model sees screenshot, model picks actions, cursor visibly moves, click/type
occur, multi-step runs, clean stop, step limit. All verified on a real
Docker desktop (Colima, Apple Silicon).

## V2 (PRD §44)

```bash
python -m agent.main --provider groq --mode vision_actions --verify "task"
python -m agent.main --mode code_execution "task"     # persistent Python + computer
python -m agent.main --lane browser "Read https://example.com"
python -m agent.main --provider openai_compat --model openai/gpt-oss-20b --mode code_execution "task"
python -m agent.main --list-sessions / --show-session ID / --resume ID "follow-up"
python -m agent.main --take-control ID                # human drives via noVNC
python -m agent.main --release ID                     # ...then hand back
```

Matrix: `MODEL_PROVIDER` (groq|gemini|openai_compat|astra) × `MODEL_MODE`
(vision_actions|computer|code_execution). Gemini needs `GEMINI_API_KEY`;
Astra needs UBS access (adapters implemented + mock-tested, live pending).
GPT OSS is text-only: code mode yes, vision lanes no.
