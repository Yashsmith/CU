"""V1 runtime config — env-driven, PRD §20-21 limits included."""
from __future__ import annotations

import os


def _getenv(key: str, default: str) -> str:
    return os.environ.get(key, default)


class Settings:
    def __init__(self) -> None:
        self.groq_api_key: str = _getenv("GROQ_API_KEY", "")
        self.model: str = _getenv("MODEL", "qwen/qwen3.8-27b")
        self.groq_base_url: str = _getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
        self.sandbox_backend: str = _getenv("SANDBOX_BACKEND", "mock")
        self.sandbox_url: str = _getenv("SANDBOX_URL", "http://127.0.0.1:7090")
        self.sandbox_token: str = _getenv("SANDBOX_TOKEN", "")
        self.max_steps: int = int(_getenv("MAX_STEPS", "30"))
        self.max_runtime: float = float(_getenv("MAX_RUNTIME", "300"))
        self.step_wait: float = float(_getenv("STEP_WAIT", "0.5"))
        self.session_dir: str = _getenv("SESSION_DIR", "sessions")
        # V2 model matrix (PRD §38). V1 fallback: MODEL + GROQ_API_KEY.
        self.provider: str = _getenv("MODEL_PROVIDER", "groq")
        self.model_name: str = _getenv("MODEL_NAME", _getenv("MODEL", "qwen/qwen3.8-27b"))
        self.model_mode: str = _getenv("MODEL_MODE", "vision_actions")
        self.gemini_api_key: str = _getenv("GEMINI_API_KEY", "")
        self.openai_api_key: str = _getenv("OPENAI_API_KEY", "")
        self.openai_base_url: str = _getenv(
            "OPENAI_BASE_URL", _getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"))

    def validate_for_live(self) -> None:
        if not self.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is not set (see .env.example)")
