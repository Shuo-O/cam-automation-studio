from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

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


def _load_json_object(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Flow input must be a JSON object.")
    return value


def _emit_json(value: Mapping[str, Any] | dict[str, Any], output: str | None) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def _flow_graph(value: dict[str, Any]) -> dict[str, Any]:
    graph = value.get("graph", value)
    if not isinstance(graph, dict) or graph.get("contract") != "cam.flowgraph.v1":
        raise ValueError("Flow input must contain a cam.flowgraph.v1 graph.")
    return graph


def _run_flow_command(args: argparse.Namespace) -> int:
    if args.flow_command == "import":
        from .fixture_runtime import build_fixture_flow_service
        from .integrations import OfflineFlowIntegration, register_product_flow_capability

        with tempfile.TemporaryDirectory(prefix="cam-flow-cli-") as directory:
            service = build_fixture_flow_service(directory)
            register_product_flow_capability(service, args.product)
            result = OfflineFlowIntegration(service).import_source(
                product=args.product,
                source=Path(args.input).read_bytes(),
                source_name=Path(args.input).name,
                target_versions=args.target_version,
            )
        _emit_json(result, args.output)
        return 0

    value = _load_json_object(args.input)
    if args.flow_command in {"validate", "test"}:
        from .flow_validation import validate_flow_graph

        result = validate_flow_graph(_flow_graph(value))
        payload = {
            "schema_version": 1,
            "mode": "offline",
            "status": "passed" if result.valid else "blocked",
            "valid": result.valid,
            "diagnostics": [item.to_dict() for item in result.diagnostics],
            "transport": "none",
            "commands_sent": 0,
            "journal_executed": False,
            "macro_executed": False,
            "machine_output_count": 0,
        }
        _emit_json(payload, args.output)
        return 0 if result.valid else 2

    from .asset_registry import AssetRegistry
    from .capability_registry import CapabilityRegistry
    from .integrations import ReviewGatedFlowService

    required = {
        "asset_request",
        "manifest",
        "graph",
        "version_request",
        "target",
        "preview_request",
    }
    if not required.issubset(value):
        raise ValueError(
            "Offline preview requires a reviewed bundle with asset_request, manifest, "
            "graph, version_request, target, and preview_request."
        )
    with tempfile.TemporaryDirectory(prefix="cam-flow-preview-") as directory:
        service = ReviewGatedFlowService(
            asset_registry=AssetRegistry(Path(directory) / "assets"),
            capability_registry=CapabilityRegistry(Path(directory) / "capabilities"),
            fixture_targets=[value["target"]],
        )
        asset_request = dict(value["asset_request"])
        content = asset_request.pop("content")
        asset_request.pop("content_encoding", None)
        service.register_asset(str(content).encode("utf-8"), **asset_request)
        service.register_capability(value["manifest"])
        service.save_graph(value["graph"])
        service.create_version(**value["version_request"])
        plan = service.create_preview_plan(value["preview_request"])
        plan = service.execute_preview_plan(plan["plan_id"])
    _emit_json(plan, args.output)
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

    flow = subparsers.add_parser(
        "flow",
        help="Offline CAM Flow import, validation, test, and fixture preview.",
    )
    flow_commands = flow.add_subparsers(dest="flow_command", required=True)
    flow_import = flow_commands.add_parser("import", help="Statically import one CAM asset.")
    flow_import.add_argument("product", choices=("nx", "powermill"))
    flow_import.add_argument("input")
    flow_import.add_argument("--target-version", action="append", default=[])
    flow_import.add_argument("--output")
    for command in ("validate", "test", "preview"):
        current = flow_commands.add_parser(
            command,
            help=f"Run offline Flow {command}.",
        )
        current.add_argument("input")
        current.add_argument("--output")

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
        if args.command == "flow":
            return _run_flow_command(args)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
