#!/usr/bin/env bash
# Computer Use — convenience launcher
set -e
cd "$(dirname "$0")"

# Prefer active virtualenv, workspace .venv, or Homebrew Python over Apple's legacy Python 3.9
if [ -n "$VIRTUAL_ENV" ] && [ -x "$VIRTUAL_ENV/bin/python3" ]; then
    PYTHON_BIN="$VIRTUAL_ENV/bin/python3"
elif [ -x ".venv/bin/python3" ]; then
    PYTHON_BIN=".venv/bin/python3"
elif [ -x "../../.venv/bin/python3" ]; then
    PYTHON_BIN="../../.venv/bin/python3"
elif [ -x "../.venv/bin/python3" ]; then
    PYTHON_BIN="../.venv/bin/python3"
elif [ -x "/opt/homebrew/bin/python3" ]; then
    PYTHON_BIN="/opt/homebrew/bin/python3"
elif [ -x "/usr/local/bin/python3" ]; then
    PYTHON_BIN="/usr/local/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "Error: Python 3 is not installed or not in PATH."
    exit 1
fi

export ALLOW_REAL_INPUT=1

if [ $# -eq 0 ]; then
    # No args -> launch interactive TUI
    exec "$PYTHON_BIN" tui.py
else
    exec "$PYTHON_BIN" -m agent.main "$@"
fi
