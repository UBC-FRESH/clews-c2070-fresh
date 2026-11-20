"""Small atomic JSON helpers shared by the API server and runner."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

__all__ = ["atomic_write_json", "read_json", "write_json_to_temp"]


def _fsync_directory(directory: Path) -> None:
    """Best-effort directory fsync so a rename survives a crash."""

    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def write_json_to_temp(path: Path, payload: Any) -> Path:
    """Write ``payload`` to a sibling temporary file and return its path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def atomic_write_json(path: Path, payload: Any) -> None:
    """Atomically replace ``path`` with pretty-printed JSON."""

    temporary = write_json_to_temp(path, payload)
    try:
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    _fsync_directory(path.parent)


def read_json(path: Path) -> Any:
    """Read JSON, raising a descriptive error for malformed files."""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON file {path}: {exc}") from exc
