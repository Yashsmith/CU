"""Backend factory + PRD-compat entry point (PRD §8: sandbox/client.py).

SANDBOX_BACKEND=http  -> HTTPSandbox (real desktop-sandbox over HTTP)
SANDBOX_BACKEND=mock -> MockSandbox  (default: tests + Docker-less dev)
SANDBOX_BACKEND=local-> LocalSandbox (Mac/PC screen, guarded input)
"""
from __future__ import annotations

import os
from typing import Any

from .base import DesktopBackend  # noqa: F401  (public re-export)
from .http_client import HTTPSandbox, SandboxHTTPError  # noqa: F401
from .local import LocalSandbox  # noqa: F401
from .mock import MockSandbox  # noqa: F401


def create_sandbox(
    backend: str | None = None,
    base_url: str | None = None,
    token: str | None = None,
    **kwargs: Any,
):
    backend = (backend or os.environ.get("SANDBOX_BACKEND", "mock")).lower()
    if backend in ("http", "remote", "desktop-sandbox", "docker"):
        return HTTPSandbox(
            base_url=base_url or os.environ.get("SANDBOX_URL", "http://127.0.0.1:7090"),
            token=token if token is not None else os.environ.get("SANDBOX_TOKEN"),
            **kwargs,
        )
    if backend == "local":
        return LocalSandbox()
    if backend == "mock":
        return MockSandbox(**kwargs)
    raise ValueError(f"unknown SANDBOX_BACKEND={backend!r} (want http|mock|local)")


# PRD §11 compat alias: `from sandbox.client import Computer` historically meant
# the HTTP desktop client. The V1 Computer facade now lives in agent/computer.py.
SandboxClient = HTTPSandbox
