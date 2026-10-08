"""HTTP client for the real desktop-sandbox data plane (PRD §10-11).

Speaks: GET /health, GET /screenshot, POST /action (+ /v1/* aliases).
Auth: X-Sandbox-Token (and Authorization: Bearer) when SANDBOX_TOKEN is set.
Docs: https://github.com/arthurkatcher/desktop-sandbox
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .base import chunk_wait

_HEALTH_PATHS = ["/health"]
_SCREENSHOT_PATHS = ["/screenshot", "/v1/screenshot", "/shot"]
_ACTION_PATHS = ["/action", "/v1/action", "/input"]
_SIZE_PATHS = ["/v1/size", "/size"]


class SandboxHTTPError(RuntimeError):
    pass


class HTTPSandbox:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:7090",
        token: str | None = None,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None

    def _headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        return {"X-Sandbox-Token": self.token, "Authorization": f"Bearer {self.token}"}

    async def _client_or_create(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
            self._owns_client = True
        return self._client

    async def _get(self, paths: list[str]) -> httpx.Response:
        client = await self._client_or_create()
        last: Exception | None = None
        for p in paths:
            try:
                r = await client.get(self.base_url + p, headers=self._headers())
                if r.status_code == 404:
                    last = SandboxHTTPError(f"GET {p} -> 404")
                    continue
                r.raise_for_status()
                return r
            except SandboxHTTPError as e:
                last = e
                continue
        raise SandboxHTTPError(f"GET {paths} failed: {last}")

    async def health(self) -> dict[str, Any]:
        r = await self._get(_HEALTH_PATHS)
        try:
            return dict(r.json())
        except Exception:
            return {"ok": True, "status": r.status_code}

    async def screenshot(self) -> bytes:
        r = await self._get(_SCREENSHOT_PATHS)
        data = r.content
        if not data or not data.startswith(b"\x89PNG"):
            raise SandboxHTTPError(f"screenshot did not return PNG ({len(data)} bytes)")
        return data

    async def act(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Chunk long waits client-side (server caps at 5s).
        if payload.get("type") == "wait" and (payload.get("seconds") or 0) > 5:
            results: list[dict[str, Any]] = []
            for piece in chunk_wait(float(payload["seconds"])):
                results.append(await self.act({"type": "wait", "seconds": piece}))
            return {"ok": True, "chunked": results, "action": payload}
        client = await self._client_or_create()
        last: Exception | None = None
        for p in _ACTION_PATHS:
            try:
                r = await client.post(
                    self.base_url + p, json=payload, headers=self._headers())
                if r.status_code == 404:
                    last = SandboxHTTPError(f"POST {p} -> 404")
                    continue
                r.raise_for_status()
                try:
                    return dict(r.json())
                except Exception:
                    return {"ok": True, "result": r.text, "action": payload}
            except SandboxHTTPError as e:
                last = e
                continue
        raise SandboxHTTPError(f"POST action failed: {last} payload={payload}")

    async def size(self) -> tuple[int, int]:
        try:
            r = await self._get(_SIZE_PATHS)
            data = r.json()
            return int(data.get("width", 1280)), int(data.get("height", 800))
        except Exception:
            pass
        # Fallback: decode screenshot bounds.
        from PIL import Image
        import io

        shot = await self.screenshot()
        with Image.open(io.BytesIO(shot)) as im:
            return im.width, im.height

    async def wait(self, seconds: float) -> dict[str, Any]:
        if seconds <= 0:
            return {"ok": True, "action": {"type": "wait", "seconds": 0}}
        if seconds <= 5:
            return await self.act({"type": "wait", "seconds": seconds})
        await asyncio.sleep(seconds)
        return {"ok": True, "action": {"type": "wait", "seconds": seconds}}

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
