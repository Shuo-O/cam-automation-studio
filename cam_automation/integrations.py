from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .generator import generate_macro, generate_report
from .learning import learn_workflow
from .models import RecipeStep, WorkflowRecipe
from .sample import SAMPLE_LOG


_PLUGIN_SRC = (
    Path(__file__).resolve().parents[1] / "plugins" / "ug-cam-copilot" / "src"
)
if str(_PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SRC))

try:
    from ugcam_ai.adapters.jsonl import JsonlActivityAdapter
    from ugcam_ai.adapters.nx_journal import NxJournalAdapter
except ImportError:  # pragma: no cover - only relevant to incomplete source checkouts
    JsonlActivityAdapter = None  # type: ignore[assignment,misc]
    NxJournalAdapter = None  # type: ignore[assignment,misc]


SUPPORTED_PRODUCTS = ("nx", "powermill")
SUPPORTED_FORMATS = {
    "nx": ("nx_journal", "jsonl"),
    "powermill": ("powermill_log", "jsonl"),
}


@dataclass(frozen=True)
class ConnectionStatus:
    key: str
    plugin_id: str
    label: str
    status: str
    detail: str
    capabilities: tuple[str, ...]
    instances: tuple[Mapping[str, Any], ...] = ()
    active_instance_id: str | None = None
    process_count: int = 0
    monitoring: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "plugin_id": self.plugin_id,
            "label": self.label,
            "status": self.status,
            "detail": self.detail,
            "capabilities": list(self.capabilities),
            "instance_count": len(self.instances),
            "process_count": self.process_count,
            "active_instance_id": self.active_instance_id,
            "monitoring": self.monitoring,
            "instances": [dict(instance) for instance in self.instances],
        }


def _first_existing(*candidates: str | None) -> Path | None:
    for candidate in candidates:
        if candidate:
            path = Path(candidate)
            if path.is_file():
                return path
            if path.is_dir():
                return path
    return None


def connection_statuses(
    installed_plugins: Iterable[str] | None = None,
    *,
    capture_status: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Report local bridges and consented CAM instances without attaching to them."""

    codex_cli = shutil.which(os.environ.get("CAM_CODEX_COMMAND", "codex"))
    nx_root = _first_existing(os.environ.get("UGII_ROOT_DIR"), os.environ.get("UGII_BASE_DIR"))
    nx_runner = None
    if nx_root:
        nx_runner = _first_existing(
            str(nx_root / "run_journal.exe"),
            str(nx_root / "NXBIN" / "run_journal.exe"),
        )
    pm_root = _first_existing(
        os.environ.get("POWERMILL_HOME"),
        os.environ.get("PMILL_HOME"),
        os.environ.get("POWERMILL_ROOT"),
    )
    captured_instances = (
        capture_status.get("instances", {})
        if capture_status and capture_status.get("consent")
        else {}
    )
    if not isinstance(captured_instances, Mapping):
        captured_instances = {}
    monitoring = bool(capture_status and capture_status.get("state") == "recording")

    def product_connection(
        *,
        key: str,
        plugin_id: str,
        label: str,
        root: Path | None,
        unconfigured_detail: str,
        capabilities: tuple[str, ...],
        root_detail: str,
    ) -> ConnectionStatus:
        raw_instances = captured_instances.get(key, [])
        instances = tuple(
            dict(item)
            for item in raw_instances
            if isinstance(item, Mapping)
        ) if isinstance(raw_instances, list) else ()
        process_count = len(
            {
                int(instance.get("pid", 0) or 0)
                for instance in instances
                if instance.get("pid")
            }
        )
        foreground = next(
            (
                str(instance.get("instance_id"))
                for instance in instances
                if instance.get("is_foreground")
            ),
            None,
        )
        if instances:
            state = "connected" if monitoring else "detected"
            verb = "正在监测" if monitoring else "已检测"
            detail = (
                f"{verb} {len(instances)} 个窗口/进程"
                f"（{process_count} 个宿主进程）"
            )
        elif capture_status and not capture_status.get("consent"):
            state = "awaiting_consent"
            detail = "本地记录插件待授权；授权后才会检测软件窗口"
        elif capture_status and capture_status.get("consent"):
            state = "disconnected"
            detail = f"未发现运行中的 {label} 窗口"
        elif root:
            state = "available"
            detail = f"{root_detail}: {root}；安装并授权本地记录后检测窗口"
        else:
            state = "unconfigured"
            detail = unconfigured_detail
        return ConnectionStatus(
            key=key,
            plugin_id=plugin_id,
            label=label,
            status=state,
            detail=detail,
            capabilities=capabilities,
            instances=instances,
            active_instance_id=foreground,
            process_count=process_count,
            monitoring=monitoring and bool(instances),
        )

    statuses = [
        ConnectionStatus(
            "codex",
            "cam-codex-review",
            "Codex",
            "ready",
            (
                f"本地 CLI: {codex_cli}"
                if codex_cli
                else "上下文交换协议已就绪；未检测到 codex CLI"
            ),
            ("context.export", "review.import", "recipe.explain"),
        ).to_dict(),
        product_connection(
            key="nx",
            plugin_id="ug-cam-copilot",
            label="UG / NX",
            root=nx_root,
            unconfigured_detail=(
                "设置 UGII_ROOT_DIR 或 UGII_BASE_DIR 后可定位 NX Journal runner"
            ),
            capabilities=("journal.parse", "recipe.preview", "simulation.gate"),
            root_detail=(
                f"检测到 NX 根目录"
                f"{f'，Journal runner {nx_runner}' if nx_runner else ''}"
            ),
        ).to_dict(),
        product_connection(
            key="powermill",
            plugin_id="powermill-cam-copilot",
            label="PowerMill",
            root=pm_root,
            unconfigured_detail=(
                "设置 POWERMILL_HOME 或 PMILL_HOME 后可定位 PowerMill 宿主"
            ),
            capabilities=("macro.parse", "macro.export", "project.review"),
            root_detail="检测到 PowerMill 目录",
        ).to_dict(),
    ]
    if installed_plugins is None:
        return statuses
    installed = set(installed_plugins)
    enabled_keys = {
        "codex" if "cam-codex-review" in installed else "",
        "nx" if "ug-cam-copilot" in installed else "",
        "powermill" if "powermill-cam-copilot" in installed else "",
    }
    return [item for item in statuses if item["key"] in enabled_keys]


def capability_manifest(
    installed_plugins: Iterable[str] | None = None,
) -> dict[str, Any]:
    installed = (
        {
            "ug-cam-copilot",
            "powermill-cam-copilot",
            "cam-local-capture",
            "cam-execution-gateway",
            "cam-codex-review",
        }
        if installed_plugins is None
        else set(installed_plugins)
    )
    products = [
        product
        for product, plugin_id in (
            ("nx", "ug-cam-copilot"),
            ("powermill", "powermill-cam-copilot"),
        )
        if plugin_id in installed
    ]
    capture_installed = "cam-local-capture" in installed
    execution_installed = "cam-execution-gateway" in installed
    codex_installed = "cam-codex-review" in installed
    return {
        "module": "CAM Automation Studio Core",
        "version": "0.5.0",
        "execution_mode": "dry-run" if execution_installed else "unavailable",
        "products": [
            {
                "key": product,
                "label": "UG / NX" if product == "nx" else "PowerMill",
                "formats": list(SUPPORTED_FORMATS[product]),
            }
            for product in products
        ],
        "codex": {
            "installed": codex_installed,
            "protocol": "cam.codex.bridge.v1",
            "direction": "context-export-and-review-import",
            "automatic_execution": False,
        },
        "capture": {
            "installed": capture_installed,
            "local_only": True,
            "consent_required": True,
            "auto_connect_after_consent": True,
            "redaction": "redacted-local-v1",
            "operator_labels": ["unlabeled", "routine", "expert"],
        },
        "execution": {
            "installed": execution_installed,
            "default_transport": "dry-run",
            "live_transports": [],
            "records_requests_and_results": True,
            "requires": [
                "reviewed_recipe_hash",
                "target_cam_version",
                "snapshotted_test_project",
                "identified_human_approver_for_live_or_review_actions",
            ],
        },
        "safety": {
            "nc_output": "blocked",
            "cad_cam_mutation": "dry-run-until-versioned-live-transport-is-registered",
            "required_gates": [
                "recipe_review",
                "project_copy",
                "toolpath_and_collision_check",
                "machine_simulation",
                "human_approval",
            ],
        },
    }


def _risk_for_nx_action(action: str) -> tuple[str, list[str]]:
    lowered = action.lower()
    if any(marker in lowered for marker in ("postprocess", "output", "part.save")):
        return "review", ["writes project or machine output"]
    if any(marker in lowered for marker in ("delete", "remove", "reset")):
        return "blocked", ["destructive NX action"]
    if any(marker in lowered for marker in ("toolpath.generate", "operation.create")):
        return "review", ["changes or generates CAM data"]
    return "safe", []


def _nx_preview(events: Iterable[Any], recipe: WorkflowRecipe) -> str:
    lines = [
        '"""CAM Automation Studio NX preview (dry-run only)."""',
        f"# Recipe: {recipe.name}",
        "# This preview does not import NXOpen, change objects, generate NC, or save parts.",
        "",
    ]
    for step in recipe.steps:
        reason = f" -- {', '.join(step.reasons)}" if step.reasons else ""
        lines.append(f"# {step.step_id} [{step.risk}] {step.action}{reason}")
        lines.append(f"#   {step.template}")
    lines.extend(
        [
            "",
            "# Required before implementation: stable selection, target NX release stubs,",
            "# toolpath generation status, collision/gouge checks, machine simulation, approval.",
        ]
    )
    return "\n".join(lines) + "\n"


def _nx_recipe_from_events(
    events: list[Any],
    *,
    name: str,
    sessions_analyzed: int = 1,
    sessions_matched: list[str] | None = None,
) -> WorkflowRecipe:
    parameters: list[Any] = []
    steps: list[RecipeStep] = []
    for index, event in enumerate(events):
        risk, reasons = _risk_for_nx_action(event.action)
        api = event.params.get("api", event.action) if isinstance(event.params, Mapping) else event.action
        steps.append(
            RecipeStep(
                step_id=f"step-{index + 1:03d}",
                operation=event.action,
                action=event.action,
                template=f"{event.action} :: {api}",
                risk=risk,
                reasons=reasons,
                source_lines=[event.source_line],
            )
        )
    return WorkflowRecipe(
        name=name,
        profile="nx",
        sessions_analyzed=sessions_analyzed,
        sessions_matched=sessions_matched or [events[0].session_id if events else "nx-session"],
        parameters=parameters,
        steps=steps,
        diagnostics=[
            "NX Journal was statically parsed; source code was never imported or executed.",
            "Preview is dry-run only and requires target-release NXOpen review.",
        ],
    )


def _parse_nx(
    source: str,
    source_name: str,
    source_format: str,
) -> tuple[dict[str, Any], WorkflowRecipe, str, list[dict[str, Any]]]:
    if NxJournalAdapter is None:
        raise ValueError("NX adapter is unavailable in this checkout.")
    if source_format == "jsonl":
        if JsonlActivityAdapter is None:
            raise ValueError("Shared JSONL adapter is unavailable in this checkout.")
        events = []
        for line_number, line in enumerate(source.splitlines(), 1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"JSONL line {line_number}: {error.msg}") from error
            if not isinstance(data, dict):
                raise ValueError(f"JSONL line {line_number} must be an object.")
            from ugcam_ai.models import ActivityEvent

            events.append(ActivityEvent.from_dict(data, fallback_source=source_name, fallback_line=line_number))
        if not events:
            raise ValueError("No NX ActivityEvent records were found.")
        sessions: dict[str, list[Any]] = {}
        for event in events:
            sessions.setdefault(event.session_id, []).append(event)
        selected_session = max(sessions.values(), key=len)
        session_names = [event.session_id for event in sessions.values() if event]
        recipe = _nx_recipe_from_events(
            selected_session,
            name=Path(source_name).stem or "nx-workflow",
            sessions_analyzed=len(sessions),
            sessions_matched=session_names,
        )
    else:
        events = list(
            NxJournalAdapter().parse_source(
                source,
                source_file=source_name,
                session_name=Path(source_name).stem or "pasted",
            )
        )
        if not events:
            raise ValueError("No NX Open actions were found in the supplied Journal.")
        recipe = _nx_recipe_from_events(events, name=Path(source_name).stem or "nx-workflow")
    parse = {
        "input_lines": len(source.splitlines()),
        "commands": len(events),
        "ignored_lines": max(0, len(source.splitlines()) - len(events)),
        "diagnostics": [],
    }
    return parse, recipe, _nx_preview(events, recipe), [
        event.to_dict() for event in events
    ]


def analyze(
    *,
    product: str,
    source: str,
    source_format: str | None = None,
    name: str | None = None,
    parameters: Mapping[str, Any] | None = None,
    allow_review_steps: bool = False,
    source_name: str = "pasted-input",
) -> dict[str, Any]:
    product = product.lower().strip()
    if product not in SUPPORTED_PRODUCTS:
        raise ValueError(f"Unsupported product: {product}. Choose nx or powermill.")
    selected_format = source_format or ("nx_journal" if product == "nx" else "powermill_log")
    if selected_format not in SUPPORTED_FORMATS[product]:
        raise ValueError(f"Unsupported {product} input format: {selected_format}.")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("A non-empty source is required.")
    recipe_name = (name or f"{product}-workflow").strip()

    if product == "powermill":
        parsed, recipe = learn_workflow(source, name=recipe_name)
        preview = generate_macro(
            recipe,
            overrides=parameters or {},
            allow_review_steps=allow_review_steps,
        )
        parse = {
            "input_lines": parsed.input_lines,
            "commands": parsed.event_count,
            "ignored_lines": parsed.ignored_lines,
            "diagnostics": [
                {"line": item.line_number, "message": item.message}
                for item in parsed.diagnostics
            ],
        }
        events = parsed.to_activity_events()
        report = generate_report(parsed.event_count, recipe)
        output_kind = "powermill_macro"
    else:
        parse, recipe, preview, events = _parse_nx(
            source,
            source_name,
            selected_format,
        )
        report = generate_report(parse["commands"], recipe)
        output_kind = "nx_preview"

    return {
        "product": product,
        "source_format": selected_format,
        "adapter": {
            "name": "PowerMill profile" if product == "powermill" else "NX Open Journal adapter",
            "execution_mode": "dry-run",
        },
        "parse": parse,
        "activity_events": events,
        "recipe": recipe.to_dict(),
        "output": {"kind": output_kind, "text": preview},
        "macro": preview,
        "report": report,
        "codex_context": build_codex_context(
            product=product,
            source_format=selected_format,
            recipe=recipe.to_dict(),
            activity_events=events,
        ),
    }


def build_codex_context(
    *,
    product: str,
    source_format: str,
    recipe: Mapping[str, Any],
    activity_events: list[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "protocol": "cam.codex.bridge.v1",
        "purpose": "review_cam_workflow",
        "product": product,
        "source_format": source_format,
        "execution_mode": "dry-run",
        "instruction": (
            "Review the recipe for correctness, missing preconditions, unsafe actions, "
            "and target-version assumptions. Return review_status, findings, and required_gates. "
            "Do not produce or execute machine-ready NC code."
        ),
        "recipe": dict(recipe),
        "activity_events": list(activity_events),
    }


def sample_for(product: str) -> str:
    if product == "powermill":
        return SAMPLE_LOG
    return """# NX Open Journal sample
session = NXOpen.Session.GetSession()
part = session.Parts.Work
program = part.CAMSetup.CAMGroupCollection.FindObject("PROGRAM")
operation = part.CAMSetup.CAMOperationCollection.Create("mill_planar", program)
operation.GenerateToolPath()
"""
