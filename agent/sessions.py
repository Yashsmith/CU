"""PRD §33 session state — filesystem store, no database.

Layout (extends V1, same files):
  sessions/<id>/meta.json      id, task, model, step, status, started, ended
  sessions/<id>/events.jsonl   append-only, seq-numbered (source of truth)
  sessions/<id>/N.png ...      one screenshot per step
  sessions/<id>/final.png      screen at completion
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FINAL_STATUSES = ("done", "complete", "stopped", "timeout", "max_steps",
                  "loop_detected", "model_error", "recovery_exhausted", "paused")


@dataclass
class Session:
    id: str
    task: str = ""
    model: str = ""
    step: int = 0
    status: str = "running"
    started: float = 0.0
    ended: float = 0.0
    resumed_from: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)


class SessionStore:
    """Append-only session store (V1 API preserved, V2 state added)."""

    def __init__(self, root: str | Path, session_id: str | None = None) -> None:
        self.root = Path(root)
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.dir = self.root / self.session_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._seq = 0

    # ------------------------------------------------------------ write ----
    def write_meta(self, task: str, model: str, extra: dict[str, Any] | None = None) -> None:
        meta: dict[str, Any] = {"id": self.session_id, "task": task,
                                "model": model, "step": 0,
                                "started": time.time(), "status": "running"}
        if extra:
            meta.update(extra)
        (self.dir / "meta.json").write_text(json.dumps(meta, indent=2))

    def set_step(self, step: int) -> None:
        self._patch_meta({"step": step})

    def log_event(self, event: dict[str, Any]) -> dict[str, Any]:
        from .policy import SecurityPolicy  # local import: policy never imports sessions

        self._seq += 1
        event = {"seq": self._seq, "t": time.time(), **event}
        event = SecurityPolicy.redact_event(event)  # secret isolation (PRD §36)
        with open(self.dir / "events.jsonl", "a") as f:
            f.write(json.dumps(event) + "\n")
        return event

    def save_shot(self, step: int, png: bytes, final: bool = False) -> str:
        shots = self.dir / "screenshots"  # PRD §33 layout
        shots.mkdir(exist_ok=True)
        name = "final.png" if final else f"{step}.png"
        (shots / name).write_bytes(png)
        return name

    def finish(self, status: str) -> None:
        meta_path = self.dir / "meta.json"
        try:
            meta = json.loads(meta_path.read_text())
        except FileNotFoundError:
            meta = {"id": self.session_id}
        meta.update({"status": status, "ended": time.time()})
        meta_path.write_text(json.dumps(meta, indent=2))

    def _patch_meta(self, patch: dict[str, Any]) -> None:
        meta_path = self.dir / "meta.json"
        try:
            meta = json.loads(meta_path.read_text())
        except FileNotFoundError:
            meta = {"id": self.session_id}
        meta.update(patch)
        meta_path.write_text(json.dumps(meta, indent=2))

    # ------------------------------------------------------------ read -----
    @staticmethod
    def _read_events(session_dir: Path) -> list[dict[str, Any]]:
        path = session_dir / "events.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    @classmethod
    def load(cls, root: str | Path, session_id: str) -> Session:
        """Rebuild session state from disk (meta + replayed history)."""
        d = Path(root) / session_id
        if not (d / "meta.json").exists():
            raise FileNotFoundError(f"no such session: {session_id}")
        meta = json.loads((d / "meta.json").read_text())
        events = cls._read_events(d)
        history: list[dict[str, Any]] = []
        step = int(meta.get("step", 0))
        for ev in events:
            if ev.get("kind") == "decide" and isinstance(ev.get("detail"), dict):
                detail = ev["detail"]
                if "action" in detail or "code" in detail or "recovery" in detail:
                    history.append(detail)
                elif detail.get("type") in ("click", "double_click",
                                            "right_click", "move", "type",
                                            "press", "scroll", "wait",
                                            "click_type", "goto", "done"):
                    history.append({"action": detail})  # V1 flat form
            step = max(step, int(ev.get("step", 0)))
        return Session(id=session_id, task=meta.get("task", ""),
                       model=meta.get("model", ""), step=step,
                       status=meta.get("status", "running"),
                       started=meta.get("started", 0.0),
                       ended=meta.get("ended", 0.0),
                       resumed_from=meta.get("resumed_from", ""),
                       history=history)

    @classmethod
    def list(cls, root: str | Path) -> list[Session]:
        """Newest-first session summaries (meta only, cheap)."""
        root = Path(root)
        if not root.exists():
            return []
        sessions: list[Session] = []
        for d in root.iterdir():
            if d.is_dir() and (d / "meta.json").exists():
                try:
                    meta = json.loads((d / "meta.json").read_text())
                except (OSError, ValueError):
                    continue
                sessions.append(Session(
                    id=d.name, task=meta.get("task", ""),
                    model=meta.get("model", ""),
                    step=int(meta.get("step", 0)),
                    status=meta.get("status", "?"),
                    started=meta.get("started", 0.0),
                    ended=meta.get("ended", 0.0)))
        sessions.sort(key=lambda s: s.started, reverse=True)
        for s in sessions:
            if s.step == 0:
                # pre-step-tracking metas: backfill from step PNGs on disk
                try:
                    nums = [int(p.stem) for p in (root / s.id).glob("*.png")
                            if p.stem.isdigit()]
                    if nums:
                        s.step = max(nums)
                except (OSError, ValueError):
                    pass
        return sessions

    def shots(self) -> list[str]:
        found = [p.name for p in (self.dir / "screenshots").glob("*.png")]
        if not found:  # pre-V2 sessions kept PNGs at the top level
            found = [p.name for p in self.dir.glob("*.png")]
        return sorted(found)
