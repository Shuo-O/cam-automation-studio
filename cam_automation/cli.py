from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .generator import write_artifacts
from .learning import learn_file, learn_workflow
from .sample import SAMPLE_LOG


def _read_parameters(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Parameter overrides must be a JSON object.")
    return value


def _write_result(
    *,
    text: str,
    name: str,
    output: str,
    parameter_path: str | None,
    allow_review_steps: bool,
) -> int:
    parsed, recipe = learn_workflow(text, name=name)
    paths = write_artifacts(
        output,
        parsed_count=parsed.event_count,
        recipe=recipe,
        overrides=_read_parameters(parameter_path),
        allow_review_steps=allow_review_steps,
        activity_events=parsed.to_activity_events(),
    )
    print(f"Learned {len(recipe.steps)} steps from {len(parsed.sessions)} session(s).")
    for label, path in paths.items():
        print(f"{label}: {path.resolve()}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cam-automation",
        description="Learn reviewed PowerMill automation recipes from command logs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    learn = subparsers.add_parser("learn", help="Learn from a PowerMill command log.")
    learn.add_argument("input", help="UTF-8 macro or JSONL log.")
    learn.add_argument("--name", help="Recipe name; defaults to the input filename.")
    learn.add_argument("--output", default="build/powermill-demo", help="Artifact directory.")
    learn.add_argument("--parameters", help="JSON object containing parameter overrides.")
    learn.add_argument(
        "--allow-review-steps",
        action="store_true",
        help="Emit review-classified steps as active macro lines.",
    )

    demo = subparsers.add_parser("demo", help="Run the bundled two-session demo.")
    demo.add_argument("--output", default="build/powermill-demo", help="Artifact directory.")
    demo.add_argument("--parameters", help="JSON object containing parameter overrides.")
    demo.add_argument("--allow-review-steps", action="store_true")

    serve = subparsers.add_parser("serve", help="Start the local workflow review UI.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8765, type=int)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "learn":
            path = Path(args.input)
            parsed, recipe = learn_file(path, name=args.name)
            paths = write_artifacts(
                args.output,
                parsed_count=parsed.event_count,
                recipe=recipe,
                overrides=_read_parameters(args.parameters),
                allow_review_steps=args.allow_review_steps,
                activity_events=parsed.to_activity_events(),
            )
            print(f"Learned {len(recipe.steps)} steps from {len(parsed.sessions)} session(s).")
            for label, artifact in paths.items():
                print(f"{label}: {artifact.resolve()}")
            return 0
        if args.command == "demo":
            return _write_result(
                text=SAMPLE_LOG,
                name="powermill-import-review",
                output=args.output,
                parameter_path=args.parameters,
                allow_review_steps=args.allow_review_steps,
            )
        if args.command == "serve":
            from .web_server import serve

            serve(args.host, args.port)
            return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
