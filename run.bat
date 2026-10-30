@echo off
REM Computer Use — Windows Launcher
REM Usage:
REM   run.bat                 -> launches interactive TUI
REM   run.bat "task"          -> runs task directly in CLI
REM   run.bat --probe         -> checks health
REM   run.bat --doctor        -> checks permissions & resolution

set ALLOW_REAL_INPUT=1

if "%~1"=="" (
    python tui.py
) else (
    python -m agent.main %*
)
