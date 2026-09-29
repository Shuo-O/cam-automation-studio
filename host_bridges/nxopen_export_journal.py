"""NXOpen journal entry point for a read-only manufacturing snapshot.

Run this file with the Python interpreter supplied by the target NX
installation.  It calls only ``Session.GetSession()``, ``Parts.Work`` and the
work part's ``Bodies``/``PartUnits`` properties; it never commits a builder or
queues a command.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export an NXOpen read-only body inventory")
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--material", required=True)
    parser.add_argument("--target-version")
    parser.add_argument("--units", choices=("mm", "inch"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    # A journal is normally launched outside the repository; make the package
    # importable without relying on a global installation.
    repository = Path(__file__).resolve().parents[1]
    if str(repository) not in sys.path:
        sys.path.insert(0, str(repository))
    from host_bridges.nx import NxOpenSnapshotExporter

    payload = NxOpenSnapshotExporter().export_snapshot(
        instance_id=args.instance_id,
        project_id=args.project_id,
        material=args.material,
        machine={"axes": 3},
        units=args.units,
        target_version=args.target_version,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"read-only NX snapshot written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
