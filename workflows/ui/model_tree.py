"""Deterministic hashing for the reduced OSeCAN model tree.

The screening runner copies only the entries below into a run directory.  The
same entry list is used by ``setup_osecan_model`` to record a reproducible tree
hash in ``model_manifest.json``, so a run can prove which model bytes it used.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

__all__ = ["MODEL_COPY_ENTRIES", "tree_sha256"]

MODEL_COPY_ENTRIES = (
    "src",
    "SETs",
    "config",
    "data/params",
    "resources",
    "pyproject.toml",
    "README.md",
    "LICENSE",
)

_IGNORED_PARTS = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)


def tree_sha256(
    root: Path,
    entries: Sequence[str] = MODEL_COPY_ENTRIES,
) -> str:
    """Hash the relative names and bytes of a reduced model tree.

    Args:
        root: Model tree root.
        entries: Relative files or directories to include.

    Returns:
        Lower-case SHA-256 hex digest. Missing entries are skipped so the same
        function can hash both a source tree and a reduced run copy.
    """

    digest = hashlib.sha256()
    for entry in entries:
        path = root / entry
        if not path.exists():
            continue
        files = [path] if path.is_file() else sorted(
            candidate for candidate in path.rglob("*") if candidate.is_file()
        )
        for file_path in files:
            if _IGNORED_PARTS.intersection(file_path.parts):
                continue
            if file_path.suffix == ".pyc":
                continue
            digest.update(file_path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(file_path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()
