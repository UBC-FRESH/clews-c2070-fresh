"""CLI wrapper for the national economics v2.1 UI export."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from workflows.national.harvest.national_economics_ui import main

if __name__ == "__main__":
    sys.exit(main())
