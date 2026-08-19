from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import sys
from pathlib import Path
from typing import Iterable

from .adapters import JsonlActivityAdapter, NxJournalAdapter
from .mining import SequenceMiner
from .scaffold import scaffold_nx_candidate
from .store import ActivityStore


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = Path(".ugcam") / "activities.db"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ugcam",
        description="Learn repeatable CAM workflows from NX journals or shared JSONL events.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Inspect the local NX and plugin environment.")

    ingest = sub.add_parser("ingest", help="Import NX journals or shared JSONL events.")
    ingest.add_argument("paths", nargs="+", type=Path)
    ingest.add_argument("--product", choices=["nx", "auto"], default="auto")
    ingest.add_argument("--db", type=Path, default=DEFAULT_DB)

    mine = sub.add_parser("mine", help="Find repeated contiguous action sequences.")
    _add_mining_arguments(mine)
    mine.add_argument("--json", action="store_true", dest="as_json")

    scaffold = sub.add_parser("scaffold", help="Generate a dry-run NX automation candidate.")
    _add_mining_arguments(scaffold)
    scaffold.add_argument("--rank", type=int, default=1)
    scaffold.add_argument("--out", type=Path, required=True)
    scaffold.add_argument("--force", action="store_true")

    demo = sub.add_parser("demo", help="Run the bundled offline end-to-end demonstration.")
    demo.add_argument("--out", type=Path, default=Path(".demo"))
    return parser


def _add_mining_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--product", choices=["nx"], default="nx")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--min-support", type=int, default=2)
    parser.add_argument("--min-length", type=int, default=3)
    parser.add_argument("--max-length", type=int, default=8)
    parser.add_argument("--limit", type=int, default=10)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return _doctor()
        if args.command == "ingest":
            return _ingest(args.paths, product=args.product, db=args.db)
        if args.command == "mine":
            patterns = _mine(args)
            if args.as_json:
                print(json.dumps([item.to_dict() for item in patterns], indent=2))
            else:
                _print_patterns(patterns)
            return 0 if patterns else 2
        if args.command == "scaffold":
            patterns = _mine(args)
            if not patterns:
                print("No pattern meets the requested support/length.", file=sys.stderr)
                return 2
            if args.rank < 1 or args.rank > len(patterns):
                print(f"--rank must be between 1 and {len(patterns)}", file=sys.stderr)
                return 2
            paths = scaffold_nx_candidate(
                patterns[args.rank - 1], args.out, force=args.force
            )
            for path in paths:
                print(path.resolve())
            return 0
        if args.command == "demo":
            return _demo(args.out)
    except (ValueError, FileNotFoundError, FileExistsError, SyntaxError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


def _doctor() -> int:
    ugii_base = os.environ.get("UGII_BASE_DIR")
    ugii_root = os.environ.get("UGII_ROOT_DIR")
    candidates = []
    for value in (ugii_root, ugii_base):
        if value:
            candidates.extend(
                [Path(value) / "run_journal.exe", Path(value) / "NXBIN" / "run_journal.exe"]
            )
    report = {
        "plugin_root": str(PLUGIN_ROOT),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "nxopen_importable": importlib.util.find_spec("NXOpen") is not None,
        "UGII_BASE_DIR": ugii_base,
        "UGII_ROOT_DIR": ugii_root,
        "run_journal": next(
            (str(path.resolve()) for path in candidates if path.is_file()), None
        ),
        "nx_adapter": "ready",
        "powermill_adapter": "browser demo and shared JSONL contract ready",
        "execution_policy": "dry-run scaffolds only",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _ingest(paths: list[Path], *, product: str, db: Path) -> int:
    sources = list(_expand_sources(paths, product=product))
    if not sources:
        raise FileNotFoundError("no supported .py or .jsonl source files found")
    total = 0
    session_count = 0
    with ActivityStore(db) as store:
        for source in sources:
            adapter = (
                JsonlActivityAdapter()
                if source.suffix.lower() == ".jsonl"
                else NxJournalAdapter()
            )
            events = list(adapter.parse(source))
            if not events:
                continue
            grouped: dict[str, list] = {}
            for event in events:
                grouped.setdefault(event.session_id, []).append(event)
            for batch in grouped.values():
                total += store.replace_session(batch)
                session_count += 1
    print(
        json.dumps(
            {
                "database": str(db.resolve()),
                "sources": len(sources),
                "sessions": session_count,
                "events": total,
            },
            ensure_ascii=False,
        )
    )
    return 0


def _mine(args: argparse.Namespace):
    with ActivityStore(args.db) as store:
        sessions = store.load_sessions(product=args.product)
    miner = SequenceMiner(
        min_support=args.min_support,
        min_length=args.min_length,
        max_length=args.max_length,
    )
    return miner.mine(sessions, product=args.product, limit=args.limit)


def _print_patterns(patterns) -> None:
    if not patterns:
        print("No repeated workflow found.")
        return
    for rank, pattern in enumerate(patterns, 1):
        print(
            f"{rank}. {pattern.pattern_id} support={pattern.support}/"
            f"{pattern.session_count} confidence={pattern.confidence:.0%}"
        )
        print("   " + " -> ".join(pattern.steps))


def _demo(output: Path) -> int:
    examples = PLUGIN_ROOT / "examples" / "nx_journals"
    db = output / "cam_learning.db"
    _ingest([examples], product="nx", db=db)
    namespace = argparse.Namespace(
        product="nx",
        db=db,
        min_support=2,
        min_length=3,
        max_length=8,
        limit=10,
    )
    patterns = _mine(namespace)
    _print_patterns(patterns)
    if not patterns:
        return 2
    generated = output / "generated_nx"
    paths = scaffold_nx_candidate(patterns[0], generated, force=True)
    print("Generated:")
    for path in paths:
        print(f"  {path.resolve()}")
    return 0


def _expand_sources(paths: Iterable[Path], *, product: str) -> Iterable[Path]:
    suffixes = {".py"} if product == "nx" else {".py", ".jsonl"}
    for value in paths:
        if value.is_dir():
            for child in sorted(value.rglob("*")):
                if child.is_file() and child.suffix.lower() in suffixes:
                    yield child
        elif value.is_file() and value.suffix.lower() in suffixes:
            yield value


if __name__ == "__main__":
    raise SystemExit(main())

