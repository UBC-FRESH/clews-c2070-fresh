#!/usr/bin/env python3
"""Compatibility CLI for solving the BC mini ws3-smoke supply variant."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fresh_fibre.ws3_variant import main


if __name__ == "__main__":
    main()
