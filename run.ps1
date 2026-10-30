# Computer Use — Windows PowerShell Launcher
# Usage:
#   .\run.ps1                 -> launches interactive TUI
#   .\run.ps1 "task"          -> runs task directly in CLI
#   .\run.ps1 --probe         -> checks health
#   .\run.ps1 --doctor        -> checks permissions & resolution

$env:ALLOW_REAL_INPUT = "1"

if ($args.Count -eq 0) {
    python tui.py
} else {
    python -m agent.main @args
}
