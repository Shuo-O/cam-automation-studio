"""Repository-local launcher that works without installing the package."""

from __future__ import annotations

import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "src"))

from ugcam_ai.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

