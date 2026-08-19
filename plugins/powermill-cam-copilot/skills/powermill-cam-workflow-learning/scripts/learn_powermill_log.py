#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def _find_repository_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "cam_automation").is_dir():
            return parent
    raise RuntimeError(
        "The shared cam_automation package was not found. Run this skill from its source repository."
    )


def main() -> int:
    root = _find_repository_root()
    sys.path.insert(0, str(root))
    from cam_automation.cli import main as cli_main

    if len(sys.argv) == 1:
        print(
            "usage: learn_powermill_log.py <log-path> "
            "[--name NAME] [--output DIRECTORY] [--parameters JSON]",
            file=sys.stderr,
        )
        return 2
    return cli_main(["learn", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
