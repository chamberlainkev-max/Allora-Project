"""
execution/atomic_json.py
========================
F03 — crash-safe JSON persistence for sniper position / pending files.

Write path:
  1. Write + fsync a .tmp file
  2. Move the previous good file aside as .bak (if present)
  3. Atomically replace the target with .tmp

Load path:
  Prefer the primary file; on missing/corrupt JSON fall back to .bak;
  otherwise return {}.
"""

from __future__ import annotations

import json
import os
from typing import Any


def write_json_atomic(path: str, data: Any) -> None:
    """Persist JSON so a crash mid-write cannot leave a truncated primary file."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp"
    bak = f"{path}.bak"
    payload = json.dumps(data)

    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())

    if os.path.exists(path):
        # Keep last-known-good bytes for corrupt/crash recovery.
        os.replace(path, bak)
    os.replace(tmp, path)

    # Best-effort: fsync the directory entry on Unix so the rename itself is durable.
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def load_json_dict(path: str) -> dict:
    """Load a JSON object; recover from .bak if the primary file is missing/corrupt."""
    for candidate in (path, f"{path}.bak"):
        if not os.path.exists(candidate):
            continue
        try:
            with open(candidate, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return {}
