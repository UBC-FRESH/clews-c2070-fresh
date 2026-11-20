#!/usr/bin/env python3
"""Build the provincial harvest-allocation LP artifacts.

Writes ``data/derived/national/harvest_basis/provincial_allocation`` with the
100M, 119M, and 150M allocations, the summary JSON, the report, and a manifest.
The 200M case is diagnostic-only and is reported as infeasible.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from workflows.national.harvest.provincial_allocation import run


def main() -> int:
    summary = run()
    for label, payload in summary["cases"].items():
        binding = ", ".join(payload["binding_provinces"]) or "-"
        print(f"{label}: {payload['status']} binding={binding}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
