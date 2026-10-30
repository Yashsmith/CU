# Computer Use — Cross-Platform Architecture

Direct visual desktop control on **macOS & Windows**.
No Docker, no VM, no DOM scraping, and no background shell tricks.
The agent perceives the real screen and operates it like a human sitting at the computer.

```text
User task (CLI / TUI)
  │
  ▼
agent/loop.py             — observe → decide → act → observe …
  │                          limits: MAX_STEPS=30, MAX_RUNTIME=300s
  │                          guards: repeat-click loop guard + consecutive-wait killer
  │
  ├─► GroqModel.decide()  — screenshot in, one visual Action JSON out
  │                          model: qwen/qwen3.8-27b (vision)
  │                          prompt: cross-platform OS awareness (macOS / Windows)
  │
  └─► sandbox/local.py    — drives host desktop (macOS & Windows)
         screenshot: mss       → PNG (Retina downscaled on Mac, DPI-aware on Windows)
         cursor:     pyautogui → smooth visible glide to target coordinates (x, y)
         mouse:      pyautogui → click / double_click / right_click / drag
         keyboard:   pyautogui → typewrite / hotkey (auto-maps Cmd ⇄ Ctrl)
```

---

## Visual-Only Interaction Principles

1. **No DOM Bypassing**:
   - The agent never inspects HTML or accessibility trees directly.
   - It looks at screen pixels and calculates coordinates $(x, y)$ to move the cursor to buttons, links, or fields.
2. **No Secret Shell/Terminal Commands**:
   - To open an application or file, the agent moves the cursor to the icon in the Dock/Taskbar, Desktop, or File Manager (Finder / File Explorer) and clicks/double-clicks, or uses the system search launcher (`Cmd+Space` on Mac, `Win` key on Windows).
   - In Chrome, it moves to the address bar, clicks to focus, types the search query, and presses Enter.
3. **Smooth Cursor Gliding**:
   - When moving between elements, the cursor visibly glides across the screen with configurable duration (`CURSOR_GLIDE=0.35s`), so the user can watch the mouse travel and act in real time.

---

## Cross-Platform Differences Handled

| Feature | macOS | Windows |
|---|---|---|
| **Primary Modifier** | `Cmd` (`command`) | `Ctrl` (`control`) |
| **System Launcher** | `Cmd+Space` (Spotlight) | `Win` key (Start menu / Search) |
| **File Manager** | Finder | File Explorer |
| **DPI / Resolution** | Retina 2x scaled to logical points | Per-Monitor DPI awareness via `ctypes` |
| **Launcher Script** | `./run.sh` | `run.bat` / `run.ps1` |

---

## Running the Agent

### Interactive TUI (Mac & Windows)
```bash
# macOS / Linux
./run.sh

# Windows (CMD)
run.bat

# Windows (PowerShell)
.\run.ps1
```

### CLI Direct Mode
```bash
# macOS
./run.sh "Open Chrome and search for Python docs"

# Windows
run.bat "Open Chrome and search for Python docs"
```
