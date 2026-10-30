# Computer Use — Real Mac Agent

Direct, fast computer use on your real Mac. **No Docker, no VM, no extra profiles.**
A Groq vision model perceives your real screen, decides mouse/keyboard actions, and executes them live.

```text
User Task (TUI / CLI)
       │
       ▼
  agent.loop ── captures screen ──► Groq Vision (qwen/qwen3.8-27b)
       ▲                                 │
       │                                 ▼
  LocalSandbox ◄── executes action ── Action JSON
  (PyAutoGUI + Quartz)
```

---

## Quickstart

### 1. Launch the Interactive TUI
The simplest way to use it — interactive terminal UI where you can prompt tasks and watch colored execution logs in real time:

```bash
./run.sh
```
*(or `python3 tui.py`)*

- Type your task in the input bar at the bottom and press `Enter`
- Watch actions stream live: app activations, mouse coordinates, typed text, keystrokes
- Press `Ctrl+L` to clear logs, `Ctrl+C` or `q` to quit

---

### 2. Direct CLI Run

```bash
# Run free-form task
./run.sh "Open Google Chrome and search for Python tutorials"

# Run canned demo task
./run.sh --task-id 2

# Health / diagnostics
./run.sh --probe
./run.sh --doctor
```

---

## Safety & Controls

1. **Mouse Failsafe**: Slam the mouse cursor into any corner of the screen to instantly abort execution (via PyAutoGUI failsafe).
2. **Consecutive Wait Guard**: Stops automatically if the model keeps waiting unnecessarily once results appear.
3. **Loop Guard**: Aborts if the agent attempts the exact same click 3× in a row at the same coordinates.
4. **Step & Time Limits**: Configurable via `--max-steps` and `--max-runtime`.

---

## Running Tests

```bash
python3 -m pytest tests/
```
All 17 tests verify actions schema, model parsing/retries, and the interactive TUI.
