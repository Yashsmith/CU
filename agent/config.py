"""Runtime config — loaded from environment / .env file."""
from __future__ import annotations

import os


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


class Settings:
    def __init__(self) -> None:
        self.groq_api_key: str   = _env("GROQ_API_KEY", "")
        self.model: str          = _env("MODEL", "qwen/qwen3.8-27b")
        self.groq_base_url: str  = _env("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
        self.max_steps: int      = int(_env("MAX_STEPS", "30"))
        self.max_runtime: float  = float(_env("MAX_RUNTIME", "300"))
        self.settle_wait: float  = float(_env("STEP_WAIT", "0.8"))
        self.session_dir: str    = _env("SESSION_DIR", "sessions")
        self.glide: float        = float(_env("CURSOR_GLIDE", "0.25"))

    def validate(self) -> None:
        if not self.groq_api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. "
                "Copy .env.example to .env and fill in your key.")
