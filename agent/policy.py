"""PRD §36-37 V2 security: policy gates, secret isolation, audit, injection defense.

Two layers (defense in depth):
  1. Prompt: screen content is declared UNTRUSTED in every system prompt.
  2. Enforcement (the real defense): every action/code/goto passes the policy
     in the application BEFORE execution — a jailbroken model still cannot
     escape, because permissions live in code, not in prose.
"""
from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from .actions import Action

BLOCKED_MODULES = frozenset({
    "os", "sys", "subprocess", "socket", "shutil", "ctypes", "importlib",
    "pathlib", "pty", "signal", "threading", "multiprocessing", "urllib",
    "http", "requests", "ssl",
})
BLOCKED_CALLS = frozenset({"eval", "exec", "open", "__import__", "compile", "input"})
# Combos that close/kill apps or sessions — confirmation-gated, never silent.
# (Both Linux and macOS spellings: the local Mac lane must never cmd+q you.)
DESTRUCTIVE_COMBOS = frozenset({"alt+f4", "ctrl+q", "ctrl+shift+q",
                                "cmd+q", "command+q", "cmd+shift+q"})
# Shell-shaped payloads inside code strings — confirmation-gated.
DANGEROUS_CODE_RE = re.compile(
    r"rm\s+-rf|mkfs|:\(\)\s*\{|shutdown|reboot|halt|poweroff|dd\s+if=|"
    r"chmod\s+-R\s+777\s+/|curl.*\|\s*(ba)?sh|wget.*\|\s*(ba)?sh",
    re.IGNORECASE)
_SECRET_RES = [
    re.compile(r"gsk_[A-Za-z0-9]+"),
    re.compile(r"sk-[A-Za-z0-9\-_]{8,}"),
    re.compile(r"xai-[A-Za-z0-9\-_]{8,}"),
    re.compile(r"AIza[A-Za-z0-9\-_]{8,}"),
]


class PolicyViolation(ValueError):
    pass


@dataclass
class SecurityPolicy:
    """Allowed domains/actions, shell restrictions, confirmation gates (§36)."""

    allowed_actions: set[str] = field(default_factory=lambda: {
        "click", "double_click", "right_click", "move", "type", "press",
        "scroll", "wait", "click_type", "goto", "activate", "done"})
    allowed_domains: list[str] | None = None  # None = any https domain
    auto_confirm_destructive: bool = False  # CONFIRM_DESTRUCTIVE=1 to allow
    max_exec_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "SecurityPolicy":
        domains = os.environ.get("ALLOWED_DOMAINS", "")
        return cls(
            allowed_domains=[d.strip() for d in domains.split(",") if d.strip()] or None,
            auto_confirm_destructive=os.environ.get("CONFIRM_DESTRUCTIVE", "0") == "1")

    # ------------------------------------------------------------- actions --
    def check_action(self, action: Action) -> str:
        """Return 'allow' | 'confirm:<why>' | raise PolicyViolation."""
        if action.type not in self.allowed_actions:
            raise PolicyViolation(f"action not allowed: {action.type}")
        if action.type == "press" and (action.key or "").lower() in DESTRUCTIVE_COMBOS:
            return self._confirm(f"destructive key combo: {action.key}")
        if action.type == "goto":
            host = (urlparse(action.text or "").hostname or "").lower()
            if self.allowed_domains and not any(
                    host == d.lower() or host.endswith("." + d.lower())
                    for d in self.allowed_domains):
                raise PolicyViolation(f"domain not allowed: {host}")
        return "allow"

    def _confirm(self, why: str) -> str:
        if self.auto_confirm_destructive:
            return "allow"
        return f"confirm:{why}"

    def enforce(self, action: Action) -> None:
        verdict = self.check_action(action)
        if verdict.startswith("confirm:"):
            raise PolicyViolation(
                f"confirmation required ({verdict[8:]}); "
                "set CONFIRM_DESTRUCTIVE=1 to allow")

    # ---------------------------------------------------------------- code --
    def check_code(self, code: str) -> None:
        """AST gate for model-generated code (callable as executor policy_check)."""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            raise PolicyViolation(f"code does not parse: {e}") from e
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in BLOCKED_MODULES:
                        raise PolicyViolation(f"blocked import: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in BLOCKED_MODULES:
                    raise PolicyViolation(f"blocked import: {node.module}")
            elif isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else (
                    func.attr if isinstance(func, ast.Attribute) else "")
                if name in BLOCKED_CALLS:
                    raise PolicyViolation(f"blocked call: {name}()")
        if DANGEROUS_CODE_RE.search(code):
            raise PolicyViolation("destructive shell pattern in code "
                                  "(confirmation required)")

    # ---------------------------------------------------------------- audit --
    @staticmethod
    def redact(text: str, extra: list[str] | None = None) -> str:
        out = text
        for rx in _SECRET_RES:
            out = rx.sub("***REDACTED***", out)
        secrets = list(extra or [])
        for var in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY",
                    "SANDBOX_TOKEN"):
            val = os.environ.get(var, "")
            if val:
                secrets.append(val)
        for secret in secrets:
            if secret and len(secret) >= 8:
                out = out.replace(secret, "***REDACTED***")
        return out

    @classmethod
    def redact_event(cls, event: dict[str, Any]) -> dict[str, Any]:
        def walk(value: Any) -> Any:
            if isinstance(value, str):
                return cls.redact(value)
            if isinstance(value, dict):
                return {k: walk(v) for k, v in value.items()}
            if isinstance(value, list):
                return [walk(v) for v in value]
            return value

        return walk(event)

    def audit(self, log: Any, decision: str, detail: str) -> None:
        """Append a security audit event to a session store."""
        log.log_event({"kind": "audit", "detail": f"{decision}: {self.redact(detail)}"[:500]})
