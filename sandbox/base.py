"""Shared backend contract + helpers for all sandbox bodies."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DesktopBackend(Protocol):
    """Minimal data-plane surface. All backends (HTTP/Mock/Local) implement this."""

    async def health(self) -> dict[str, Any]:
        ...

    async def screenshot(self) -> bytes:
        """Return raw PNG bytes of the current desktop."""
        ...

    async def act(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute one wire-level action payload (see Action.to_sandbox_payload)."""
        ...

    async def size(self) -> tuple[int, int]:
        """Return (width, height) of the desktop."""
        ...

    async def close(self) -> None:
        ...


def chunk_wait(seconds: float, cap: float = 5.0) -> list[float]:
    """Split a wait into sandbox-safe chunks (server caps wait at 5s)."""
    if seconds <= 0:
        return []
    chunks: list[float] = []
    remaining = seconds
    while remaining > 0:
        take = min(cap, remaining)
        chunks.append(take)
        remaining -= take
    return chunks
