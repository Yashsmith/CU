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

DOM / a11y tree, memory, multi-agent, model router, K8s,
operator console — V3+. Everything else below is V2 and implemented.

---

# V2 — one desktop, many brains (PRD §§23–44)

The body (sandbox API, Xvfb, apps, VNC) is byte-identical. Only the brain
interface got upgraded:

```text
USER task
  |
  v  MODEL_PROVIDER / MODEL_NAME / MODEL_MODE
ModelAdapter.run(task, observation, state) -> ModelResult
  |  actions[] (vision/computer)  |  code (code_execution)  |  done
  v
router -> lane
  | browser: PlaywrightBrowser (goto + same pixel actions, PRD §28)
  | desktop: Computer -> sandbox HTTP (unchanged V1 path)
  v
verify (rule pixels + model judge, PRD §31) -> recover (retry/alternate/replan, §32)
  v
sessions/<id>/ (state machine + resume, §33) + control.json (takeover, §34)
```

## Files (V2)

| Path | Role | PRD |
|---|---|---|
| `agent/adapters.py` | Adapter protocol, `ModelResult`/`Observation`, registry, Groq/Gemini/GPT-OSS/Astra adapters | §38–39, §24–26 |
| `agent/executor.py` | Persistent Python runtime + sync `computer` bridge | §26–27 |
| `agent/browser.py` | Playwright lane (same Action contract + `goto`) | §28 |
| `agent/router.py` | Heuristic lane routing (URL → browser, else desktop) | §28, §30 |
| `agent/verify.py` | Pixel rule + model judge (judge overrides on stacked windows) | §31 |
| `agent/recovery.py` | `MAX_RETRIES=2` retry → alternate(nudge) → replan → give_up | §32 |
| `agent/sessions.py` | Session state machine, list/load/replay, resume seeding | §33 |
| `agent/control.py` | File-backed pause/takeover/release/stop (step-boundary only) | §34 |
| `agent/policy.py` | Allow-lists, domain gate, code AST gate, confirmations, redaction, audit | §36–37 |
| `agent/loop.py` | V1 loop + opt-in adapter/lane/verify/recovery/control/resume | §15–16 → V2 |

## Key V2 decisions

- **One contract, three lanes.** `Action` (+V2 `goto`) executes on desktop
  (Computer), browser (Playwright, 1:1 pixels), or via code (executor with
  `computer`/`browser` bound). Code mode ends with a fenced `done` block.
- **Pixels are a hint, the judge decides.** A window opening at identical
  geometry is pixel-invisible yet successful (found live) — so with a judge
  attached, its verdict overrides the pixel rule either way.
- **Recovery only in verify mode.** `verify=False` keeps exact V1
  continue-on-error semantics; `verify=True` engages retry → alternate →
  give_up (`recovery_exhausted`).
- **Takeover is a file.** `control.json` in the session dir; the loop reads it
  at step boundaries and never half-applies a decision. Resume continues a
  paused/taken-over session with history seeded and step numbers continuing.
- **Permissions live in code.** The prompt declares screens UNTRUSTED, but the
  real defense is `policy.enforce()` inside `Computer.execute()` — even a
  jailbroken model choice dies before any effect. Secrets never hit disk
  (event redaction incl. env keys).
- **Text-only models** (GPT OSS) work in code mode with `include_screenshot=False`;
  they cannot do vision lanes — documented, not hidden.
