from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sys
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, runtime_checkable

from .adapters.cimatron_journal import CimatronJournalAdapter
from .flow_service import FlowConflictError, FlowService, FlowServiceError
from .generator import generate_macro, generate_report
from .learning import learn_workflow
from .models import LegacyRecipeStep, WorkflowRecipe
from .product_catalog import (
    PRODUCT_CATALOG,
    PRODUCT_KEYS,
    PRODUCT_PLUGINS,
    products_for_plugin_ids,
    require_product,
)
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


SUPPORTED_PRODUCTS = tuple(PRODUCT_CATALOG)
SUPPORTED_FORMATS = {
    product.key: product.source_formats for product in PRODUCT_CATALOG.values()
}
_WINDOWS_PATH = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/]|\\\\)[^\r\n\t\"'<>|]*"
)
_POSIX_HOME_PATH = re.compile(r"(?<![A-Za-z0-9_])/(?:home|Users)/[^\r\n\t\"'<>|]*")
_PATH_KEYS = frozenset(
    {
        "source_file",
        "source_path",
        "log_file",
        "log_path",
        "file_path",
        "directory",
        "working_directory",
        "local_path",
        "raw_path",
    }
)


@runtime_checkable
class CommandTaskService(Protocol):
    """Stable Wave 2 injection boundary owned by A-CMD."""

    def submit(self, task: Mapping[str, Any]) -> Any:
        ...

    def get(self, task_id: str) -> Any:
        ...

    def cancel(self, task_id: str) -> Any:
        ...


@runtime_checkable
class DiagnosticsService(Protocol):
    """Stable diagnostics injection boundary owned by A-CMD."""

    def snapshot(self) -> Any:
        ...


@dataclass
class ApiServices:
    """Injected service graph; the HTTP layer only coordinates these services."""

    recorder: Any | None = None
    connections: Any | None = None
    sessions: Any | None = None
    workflows: Any | None = None
    recipes: Any | None = None
    commands: CommandTaskService | None = None
    diagnostics: DiagnosticsService | None = None
    flow: Any | None = None
    flow_imports: Any | None = None
    workflow_factory: Callable[[Any], Any] | None = None
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


def api_value(value: Any) -> Any:
    """Convert frozen service DTOs to JSON-compatible values."""

    if isinstance(value, Mapping):
        return {str(key): api_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [api_value(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return api_value(to_dict())
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Service returned a non-serializable value: {type(value).__name__}.")


def redact_api_value(value: Any, *, key: str = "") -> Any:
    """Remove raw local paths before a service response reaches HTTP."""

    if isinstance(value, Mapping):
        return {
            str(item_key): redact_api_value(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_api_value(item, key=key) for item in value]
    if isinstance(value, tuple):
        return [redact_api_value(item, key=key) for item in value]
    if not isinstance(value, str):
        return value
    if key.casefold() in _PATH_KEYS and (
        _WINDOWS_PATH.search(value) or _POSIX_HOME_PATH.search(value)
    ):
        return "<REDACTED_LOCAL_PATH>"
    redacted = _WINDOWS_PATH.sub("<REDACTED_LOCAL_PATH>", value)
    return _POSIX_HOME_PATH.sub("<REDACTED_LOCAL_PATH>", redacted)


def service_payload(value: Any) -> Any:
    return redact_api_value(api_value(value))


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
            # active_instance_id is retained as the legacy foreground hint.
            "active_instance_id": self.active_instance_id,
            "foreground_instance_id": self.active_instance_id,
            "selected_instance_id": None,
            "selection_required": bool(self.instances),
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
    connection_service: Any | None = None,
    refresh: bool = False,
    include_uninstalled: bool = False,
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
    cimatron_root = _first_existing(
        os.environ.get("CIMATRON_HOME"),
        os.environ.get("CIMATRON_ROOT"),
    )
    captured_instances = (
        capture_status.get("instances", {})
        if capture_status and capture_status.get("consent")
        else {}
    )
    if not isinstance(captured_instances, Mapping):
        captured_instances = {}
    service_instances: dict[str, list[dict[str, Any]]] = {
        "nx": [],
        "powermill": [],
        "cimatron": [],
    }
    if connection_service is not None:
        list_instances = getattr(connection_service, "list_instances", None)
        if not callable(list_instances):
            raise TypeError("Connection service must implement list_instances().")
        try:
            discovered = list_instances(refresh=refresh)
        except TypeError:
            discovered = list_instances()
        for descriptor in discovered:
            item = api_value(descriptor)
            if not isinstance(item, Mapping):
                raise TypeError("Connection descriptors must serialize to objects.")
            product = str(item.get("product", "")).casefold()
            if product in service_instances:
                service_instances[product].append(dict(item))
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
        raw_instances = (
            service_instances.get(key, [])
            if connection_service is not None
            else captured_instances.get(key, [])
        )
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
            states = {
                str(instance.get("connection_status", "detected"))
                for instance in instances
            }
            if "connected" in states:
                state = "connected"
            elif "error" in states:
                state = "error"
            elif states == {"disconnected"}:
                state = "disconnected"
            else:
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
        product_connection(
            key="cimatron",
            plugin_id="cimatron-cam-copilot",
            label="Cimatron",
            root=cimatron_root,
            unconfigured_detail=(
                "设置 CIMATRON_HOME 或 CIMATRON_ROOT 后可定位 Cimatron；"
                "首版只静态读取 Cimatron 2026 Journaling 文件"
            ),
            capabilities=("journal.parse", "workflow.learn", "project.review"),
            root_detail="检测到 Cimatron 目录",
        ).to_dict(),
    ]
    if installed_plugins is None or include_uninstalled:
        return statuses
    installed = set(installed_plugins)
    enabled_keys = {
        "codex" if "cam-codex-review" in installed else "",
        "nx" if "ug-cam-copilot" in installed else "",
        "powermill" if "powermill-cam-copilot" in installed else "",
        "cimatron" if "cimatron-cam-copilot" in installed else "",
    }
    return [item for item in statuses if item["key"] in enabled_keys]


def capability_manifest(
    installed_plugins: Iterable[str] | None = None,
) -> dict[str, Any]:
    installed = (
        {
            "ug-cam-copilot",
            "powermill-cam-copilot",
            "cimatron-cam-copilot",
            "cam-local-capture",
            "cam-execution-gateway",
            "cam-codex-review",
        }
        if installed_plugins is None
        else set(installed_plugins)
    )
    products = products_for_plugin_ids(installed)
    capture_installed = "cam-local-capture" in installed
    execution_installed = "cam-execution-gateway" in installed
    codex_installed = "cam-codex-review" in installed
    return {
        "module": "CAM Automation Studio Core",
        "version": "0.6.1",
        "execution_mode": "dry-run" if execution_installed else "unavailable",
        "products": [
            {
                "key": product.key,
                "label": product.label,
                "formats": list(product.source_formats),
                "proprietary_sources_supported": product.proprietary_sources_supported,
                "command_tasks_supported": product.command_tasks_supported,
                "flow_import_supported": product.flow_import_supported,
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
            "consent_required": False,
            "auto_authorize_on_install": True,
            "consent_reversible": True,
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


def plugin_api_status(
    status: Mapping[str, Any],
    *,
    recorder_status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Add explicit catalog, update, dependency, and authorization state."""

    result = api_value(status)
    if not isinstance(result, dict):
        raise TypeError("Plugin status must serialize to an object.")
    plugins = result.get("plugins", [])
    if not isinstance(plugins, list):
        raise TypeError("Plugin status plugins must be an array.")
    by_id = {
        str(plugin.get("id")): plugin
        for plugin in plugins
        if isinstance(plugin, dict)
    }
    installed_count = 0
    enabled_count = 0
    updates_available = 0
    capture = dict(recorder_status or {})
    capture_categories = capture.get("categories", {})
    if not isinstance(capture_categories, Mapping):
        capture_categories = {}

    for plugin_id, plugin in by_id.items():
        installed = bool(plugin.get("installed", False))
        enabled = bool(plugin.get("enabled", installed))
        dependencies = [
            str(item) for item in plugin.get("dependencies", []) if str(item)
        ]
        dependency_status = [
            {
                "plugin_id": dependency,
                "available": dependency in by_id,
                "installed": bool(by_id.get(dependency, {}).get("installed", False)),
                "enabled": bool(by_id.get(dependency, {}).get("enabled", False)),
            }
            for dependency in dependencies
        ]
        update = {
            "available": False,
            "installed_version": str(plugin.get("version")) if installed else None,
            "available_version": str(plugin.get("version", "")),
            "source": "local_catalog",
        }
        plugin.update(
            {
                "available": True,
                "installable": all(item["available"] for item in dependency_status),
                "installed": installed,
                "enabled": enabled,
                "dependency_status": dependency_status,
                "update": update,
            }
        )
        installed_count += int(installed)
        enabled_count += int(enabled)
        updates_available += int(update["available"])

        if plugin_id == "cam-local-capture":
            consent_source = str(capture.get("consent_source", "not_granted"))
            consent = bool(capture.get("consent", False))
            category_state = []
            for category_id, classification in (
                ("logs", "basic"),
                ("instances", "advanced"),
                ("execution_audit", "advanced"),
            ):
                category_enabled = bool(capture_categories.get(category_id, False))
                category_state.append(
                    {
                        "id": category_id,
                        "classification": classification,
                        "enabled": category_enabled,
                        "revoked": bool(
                            consent_source == "revoked"
                            or (installed and consent and not category_enabled)
                        ),
                    }
                )
            plugin["authorization"] = {
                "policy": "ON_INSTALL",
                "granted": consent,
                "granted_once": consent_source == "auto_install",
                "revoked": consent_source == "revoked",
                "source": consent_source,
                "reversible": True,
                "categories": category_state,
            }
            plugin["local_data"] = {
                "visible": installed,
                "local_only": True,
                "uploads_enabled": False,
                "redaction": str(capture.get("redaction", "redacted-local-v1")),
            }
        else:
            plugin["authorization"] = {
                "policy": "EXPLICIT"
                if plugin.get("consent_required")
                else "NONE",
                "granted": enabled,
                "revoked": False,
                "reversible": bool(plugin.get("consent_reversible", False)),
                "categories": [],
            }

    result["installed_count"] = installed_count
    result["enabled_count"] = enabled_count
    result["updates_available"] = updates_available
    result["available_count"] = len(plugins)
    return service_payload(result)


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
            LegacyRecipeStep(
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


def _cimatron_recipe_from_events(
    events: list[Any],
    *,
    name: str,
    sessions_analyzed: int = 1,
    sessions_matched: list[str] | None = None,
) -> WorkflowRecipe:
    steps: list[LegacyRecipeStep] = []
    for index, event in enumerate(events):
        params = event.params if isinstance(event.params, Mapping) else {}
        risk = str(params.get("risk", "review"))
        if risk not in {"safe", "review", "blocked"}:
            risk = "review"
        reasons = [
            str(item)
            for item in params.get("reasons", [])
            if str(item).strip()
        ]
        steps.append(
            LegacyRecipeStep(
                step_id=f"step-{index + 1:03d}",
                operation=str(params.get("api", event.action)),
                action=event.action,
                template=f"static-evidence::{event.action}",
                risk=risk,
                reasons=reasons,
                source_lines=[event.source_line],
            )
        )
    return WorkflowRecipe(
        name=name,
        profile="cimatron",
        sessions_analyzed=sessions_analyzed,
        sessions_matched=sessions_matched
        or [events[0].session_id if events else "cimatron-session"],
        parameters=[],
        steps=steps,
        diagnostics=[
            "Cimatron Journal was statically parsed; Python/C# source was never imported or executed.",
            "Only source evidence is available in the MVP; semantic editing and live execution remain unavailable.",
            "Cimatron 2026 Journaling covers supported UI tools only and must not be treated as a complete command log.",
        ],
    )


def _cimatron_preview(recipe: WorkflowRecipe) -> str:
    lines = [
        "Cimatron 2026 Journaling evidence report (offline only)",
        f"Recipe candidate: {recipe.name}",
        "No Journal, Hook, macro, postprocessor, or machine command was executed.",
        "",
    ]
    for step in recipe.steps:
        reason = f" -- {', '.join(step.reasons)}" if step.reasons else ""
        lines.append(f"{step.step_id} [{step.risk}] {step.action}{reason}")
    lines.extend(
        [
            "",
            "Before any implementation: verify the exact Cimatron release and license,",
            "review the generated recipe, then run CAM simulation, collision checks, and shop approval.",
        ]
    )
    return "\n".join(lines) + "\n"


def _normalize_cimatron_event(event: Any) -> Any:
    """Keep legacy cimatron.* evidence readable without granting an action."""

    action = str(getattr(event, "action", ""))
    if not action.casefold().startswith("cimatron."):
        return event
    params = dict(event.params) if isinstance(event.params, Mapping) else {}
    params.setdefault("raw_action", action)
    params.setdefault("api", action)
    params.setdefault("mapping_confidence", "opaque")
    params.setdefault(
        "reasons",
        ["Legacy Cimatron action namespace is preserved as opaque evidence."],
    )
    return replace(event, action="cam.source.call", params=params)


def _parse_cimatron(
    source: str,
    source_name: str,
    source_format: str,
) -> tuple[dict[str, Any], WorkflowRecipe, str, list[dict[str, Any]]]:
    if source_format == "jsonl":
        events: list[Any] = []
        for line_number, line in enumerate(source.splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"JSONL line {line_number}: {error.msg}") from error
            if not isinstance(value, Mapping):
                raise ValueError(f"JSONL line {line_number} must be an object.")
            from .models import ActivityEvent

            event = ActivityEvent.from_dict(value)
            if event.product != "cimatron":
                raise ValueError(
                    f"JSONL line {line_number} product must be cimatron."
                )
            events.append(_normalize_cimatron_event(event))
    else:
        events = list(
            CimatronJournalAdapter().parse_source(
                source,
                source_file=source_name,
                session_name=Path(source_name).stem or None,
            )
        )
    if not events:
        raise ValueError("No static Cimatron Journal API calls were found.")
    sessions: dict[str, list[Any]] = {}
    for event in events:
        sessions.setdefault(event.session_id, []).append(event)
    selected_session = max(sessions.values(), key=len)
    recipe = _cimatron_recipe_from_events(
        selected_session,
        name=Path(source_name).stem or "cimatron-workflow",
        sessions_analyzed=len(sessions),
        sessions_matched=sorted(sessions),
    )
    parse = {
        "input_lines": len(source.splitlines()),
        "commands": len(events),
        "ignored_lines": max(0, len(source.splitlines()) - len(events)),
        "diagnostics": [],
    }
    return (
        parse,
        recipe,
        _cimatron_preview(recipe),
        [event.to_dict() for event in events],
    )


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
    descriptor = require_product(product)
    selected_format = source_format or descriptor.default_source_format
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
    elif product == "nx":
        parse, recipe, preview, events = _parse_nx(
            source,
            source_name,
            selected_format,
        )
        report = generate_report(parse["commands"], recipe)
        output_kind = "nx_preview"
    else:
        parse, recipe, preview, events = _parse_cimatron(
            source,
            source_name,
            selected_format,
        )
        report = generate_report(parse["commands"], recipe)
        output_kind = "cimatron_evidence_report"

    return {
        "product": product,
        "source_format": selected_format,
        "adapter": {
            "name": {
                "powermill": "PowerMill profile",
                "nx": "NX Open Journal adapter",
                "cimatron": "Cimatron 2026 Journaling static adapter",
            }[product],
            "execution_mode": "dry-run",
            "source_execution": False,
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


_FLOW_MANIFESTS = {
    "nx": _PLUGIN_SRC.parent / "capabilities" / "flow-nodes.v1.json",
    "powermill": (
        Path(__file__).resolve().parents[1]
        / "plugins"
        / "powermill-cam-copilot"
        / "capabilities"
        / "flow-nodes.v1.json"
    ),
}
_FLOW_SAFETY_CODES = frozenset(
    {
        "SAFETY_LIVE_EXECUTION_FORBIDDEN",
        "SAFETY_MACHINE_OUTPUT_FORBIDDEN",
    }
)
REVIEW_EVIDENCE_REQUEST_EXTENSION = "cam.flow.studio/review_evidence_request"
REVIEW_EVIDENCE_EXTENSION = "cam.flow.studio/review_evidence"


class FlowIntegrationError(ValueError):
    """Sanitized failure raised by the public offline import boundary."""

    def __init__(self, code: str, message: str, *, http_status: int = 409) -> None:
        self.code = code
        self.http_status = http_status
        self.details: dict[str, Any] = {}
        super().__init__(message)


def _canonical_flow_namespace(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        return {
            item_key: _canonical_flow_namespace(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_canonical_flow_namespace(item, key=key) for item in value]
    if (
        isinstance(value, str)
        and value.startswith("flow.")
        and key in {"node_type", "port_contract_refs"}
    ):
        return f"cam.{value}"
    return value


def load_product_flow_manifest(product: str) -> dict[str, Any]:
    product_name = str(product).casefold()
    path = _FLOW_MANIFESTS.get(product_name)
    if path is None:
        raise FlowIntegrationError(
            "FLOW_PRODUCT_MIXED",
            "Offline Flow import requires product=nx or product=powermill.",
            http_status=400,
        )
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("product") != product_name:
        raise FlowIntegrationError(
            "CAPABILITY_MISSING",
            "The installed product capability manifest is invalid.",
        )
    return _canonical_flow_namespace(value)


def register_product_flow_capability(flow_service: Any, product: str) -> dict[str, Any]:
    return flow_service.register_capability(load_product_flow_manifest(product))


def _decode_offline_source(source: bytes) -> str:
    if source.startswith(b"\xef\xbb\xbf"):
        return source.decode("utf-8-sig")
    if source.startswith(b"\xff\xfe"):
        return source[2:].decode("utf-16-le")
    if source.startswith(b"\xfe\xff"):
        return source[2:].decode("utf-16-be")
    return source.decode("utf-8")


def _flow_diagnostics(graph: Mapping[str, Any]) -> list[dict[str, Any]]:
    extensions = graph.get("extensions", {})
    if not isinstance(extensions, Mapping):
        return []
    namespace = (
        extensions.get("nx", {})
        if graph.get("product") == "nx"
        else extensions.get("powermill.offline_import", {})
    )
    diagnostics = (
        namespace.get("diagnostics", [])
        if isinstance(namespace, Mapping)
        else []
    )
    return [dict(item) for item in diagnostics if isinstance(item, Mapping)]


def _replace_contract_value(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_contract_value(item, old, new)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_contract_value(item, old, new) for item in value]
    return new if value == old else value


def _bind_opaque_capability_blockers(graph: dict[str, Any]) -> None:
    from .flow_contracts import canonical_hash

    locks = graph.setdefault("capability_lock", [])
    identities = {
        (
            str(item.get("manifest_id", "")),
            str(item.get("manifest_hash", "")),
        )
        for item in locks
        if isinstance(item, Mapping)
    }
    for flow in graph.get("flows", []):
        if not isinstance(flow, Mapping):
            continue
        for node in flow.get("nodes", []):
            if not isinstance(node, Mapping) or not isinstance(
                node.get("opaque"),
                Mapping,
            ):
                continue
            fingerprint = canonical_hash(
                {
                    "product": graph.get("product"),
                    "node_type": node.get("node_type"),
                    "content_hash": node["opaque"].get("content_hash"),
                    "diagnostic_codes": node["opaque"].get("diagnostic_codes", []),
                }
            )
            manifest_id = (
                f"manifest:{graph.get('product', 'cam')}:opaque:{fingerprint[7:23]}"
            )
            identity = (manifest_id, fingerprint)
            if identity in identities:
                continue
            locks.append(
                {
                    "manifest_id": manifest_id,
                    "manifest_version": "0.0.0",
                    "manifest_hash": fingerprint,
                }
            )
            identities.add(identity)
    locks.sort(
        key=lambda item: (
            str(item.get("manifest_id", "")),
            str(item.get("manifest_version", "")),
            str(item.get("manifest_hash", "")),
        )
    )


def _map_offline_flow_source(
    source: bytes,
    *,
    product: str,
    source_name: str,
    target_versions: tuple[str, ...],
) -> dict[str, Any]:
    if product == "nx":
        from ugcam_ai.adapters.nx_flow import NxFlowMapper

        graph = NxFlowMapper().map_source(
            _decode_offline_source(source),
            source_file=source_name,
            target_version=target_versions[0] if len(target_versions) == 1 else None,
        )
    elif product == "powermill":
        from .adapters.powermill_flow import PowerMillFlowImporter

        graph = PowerMillFlowImporter(
            source_name=source_name,
            target_versions=target_versions,
        ).import_source(source)
    elif product == "cimatron":
        raise FlowIntegrationError(
            "CAPABILITY_UNAVAILABLE",
            "Cimatron supports static Journal analysis only; Flow import is unavailable.",
            http_status=409,
        )
    else:
        raise FlowIntegrationError(
            "FLOW_PRODUCT_MIXED",
            "Offline Flow import requires product=nx or product=powermill.",
            http_status=400,
        )
    return _canonical_flow_namespace(graph)


def _bind_imported_flow_asset(
    graph: Mapping[str, Any],
    asset: Mapping[str, Any],
) -> dict[str, Any]:
    value = copy.deepcopy(dict(graph))
    asset_ref = value["asset_refs"][0]
    old_revision_id = str(asset_ref["asset_revision_id"])
    value = _replace_contract_value(
        value,
        old_revision_id,
        str(asset["asset_revision_id"]),
    )
    value["asset_refs"][0] = {
        "asset_id": asset["asset_id"],
        "asset_revision_id": asset["asset_revision_id"],
        "content_hash": asset["content_hash"],
    }
    _bind_opaque_capability_blockers(value)

    from .flow_contracts import compute_semantic_hash, compute_source_snapshot_hash

    value["source_snapshot_hash"] = compute_source_snapshot_hash(value, [asset])
    value["semantic_hash"] = compute_semantic_hash(value)
    return value


class _OfflineImportRoundTripAdapter:
    """Reparse F0 candidates through the same product-owned static adapter."""

    def __init__(
        self,
        *,
        product: str,
        source_name: str,
        target_versions: tuple[str, ...],
        asset: Mapping[str, Any],
    ) -> None:
        self.product = product
        self.source_name = source_name
        self.target_versions = target_versions
        self.asset = copy.deepcopy(dict(asset))

    def validate_candidate(
        self,
        candidate: bytes,
        *,
        target_graph: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        del target_graph
        graph = _map_offline_flow_source(
            candidate,
            product=self.product,
            source_name=self.source_name,
            target_versions=self.target_versions,
        )
        return [
            item
            for item in _flow_diagnostics(graph)
            if str(item.get("code", "")) in _FLOW_SAFETY_CODES
        ]

    def reparse_candidate(
        self,
        candidate: bytes,
        *,
        target_graph: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        del target_graph
        graph = _map_offline_flow_source(
            candidate,
            product=self.product,
            source_name=self.source_name,
            target_versions=self.target_versions,
        )
        return _bind_imported_flow_asset(graph, self.asset)


class ReviewGatedFlowService(FlowService):
    """T09 public gate for structured, current review evidence."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._round_trip_reports: dict[str, dict[str, Any]] = {}
        self._round_trip_by_revision: dict[tuple[str, str], str] = {}
        self._projection_reports: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _review_required(
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> FlowConflictError:
        return FlowConflictError(
            "PROJECTION_REVIEW_REQUIRED",
            message,
            http_status=409,
            details=details,
        )

    def _current_graph(self, graph_id: str, revision_id: str | None) -> dict[str, Any]:
        graph = self.get_graph(graph_id, revision_id)
        head = self.get_graph(graph_id)
        if graph["revision_id"] != head["revision_id"]:
            raise self._review_required(
                "Review evidence must bind the current graph revision.",
                details={
                    "graph_id": graph_id,
                    "revision_id": graph["revision_id"],
                    "current_revision_id": head["revision_id"],
                },
            )
        return graph

    def _validate_round_trip_report(
        self,
        report: Mapping[str, Any],
        graph: Mapping[str, Any],
        *,
        result: Any | None = None,
    ) -> None:
        from .flow_contracts import compute_semantic_hash
        from .flow_validation import validate_contract

        value = copy.deepcopy(dict(report))
        validation = validate_contract(value, "round_trip_report", limits=self.limits)
        if not validation.valid:
            raise self._review_required(
                "RoundTripReport is not a valid frozen contract.",
                details={"report_id": value.get("report_id")},
            )
        semantic_hash = compute_semantic_hash(graph)
        asset_ref = next(
            (
                item
                for item in graph.get("asset_refs", [])
                if isinstance(item, Mapping)
                and item.get("asset_revision_id") == value.get("asset_revision_id")
            ),
            None,
        )
        passed = (
            value.get("graph_id") == graph.get("graph_id")
            and value.get("revision_id") == graph.get("revision_id")
            and value.get("status") == "passed"
            and value.get("fidelity") in {"F0", "F1", "F2", "F3"}
            and value.get("candidate_reparsed") is True
            and value.get("untouched_spans_exact") is True
            and value.get("opaque_spans_preserved") is True
            and value.get("issues") == []
            and value.get("original_semantic_hash") == semantic_hash
            and value.get("candidate_semantic_hash") == semantic_hash
            and asset_ref is not None
            and value.get("original_content_hash") == asset_ref.get("content_hash")
        )
        if not passed:
            raise self._review_required(
                "RoundTripReport is blocked, stale, or does not bind this graph.",
                details={"report_id": value.get("report_id")},
            )
        if result is not None:
            candidate = getattr(result, "candidate", None)
            reparsed = getattr(result, "reparsed_graph", None)
            reparsed_value = (
                reparsed.to_dict()
                if callable(getattr(reparsed, "to_dict", None))
                else reparsed
            )
            if (
                candidate is None
                or reparsed_value is None
                or candidate.content_hash != value.get("candidate_content_hash")
                or compute_semantic_hash(reparsed_value) != semantic_hash
            ):
                raise self._review_required(
                    "Round-trip evidence has no matching in-memory candidate reparse.",
                    details={"report_id": value.get("report_id")},
                )

    def register_round_trip_result(self, result: Any) -> dict[str, Any]:
        report_object = getattr(result, "report", None)
        to_dict = getattr(report_object, "to_dict", None)
        if not callable(to_dict):
            raise TypeError("result must contain a frozen RoundTripReport.")
        report = to_dict()
        graph = self._current_graph(
            str(report.get("graph_id", "")),
            str(report.get("revision_id", "")),
        )
        self._validate_round_trip_report(report, graph, result=result)
        identity = str(report["report_id"])
        key = (str(report["graph_id"]), str(report["revision_id"]))
        with self._lock:
            self._round_trip_reports[identity] = copy.deepcopy(report)
            self._round_trip_by_revision[key] = identity
        return copy.deepcopy(report)

    def _require_round_trip(
        self,
        report_id: str,
        graph: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            report = copy.deepcopy(self._round_trip_reports.get(report_id))
        if report is None:
            raise self._review_required(
                "Reviewed versions require a stored structured RoundTripReport.",
                details={"round_trip_report_id": report_id},
            )
        self._validate_round_trip_report(report, graph)
        return report

    def prepare_review_evidence(
        self,
        graph_id: str,
        target: Mapping[str, Any],
        *,
        revision_id: str | None = None,
        context: Any | None = None,
    ) -> dict[str, Any]:
        from .flow_compatibility import project_flow_to_recipe

        graph = self._current_graph(graph_id, revision_id)
        key = (str(graph["graph_id"]), str(graph["revision_id"]))
        with self._lock:
            round_trip_id = self._round_trip_by_revision.get(key)
        if round_trip_id is None:
            raise self._review_required(
                "Create current structured round-trip evidence before review.",
                details={"graph_id": graph["graph_id"], "revision_id": graph["revision_id"]},
            )
        round_trip = self._require_round_trip(round_trip_id, graph)
        compatibility = self.check_compatibility(
            graph["graph_id"],
            target,
            revision_id=graph["revision_id"],
            context=context,
        )
        projection = project_flow_to_recipe(
            graph,
            compatibility_report=compatibility,
            manifests=self._manifests(),
            checked_at=self._timestamp(),
        ).report.to_dict()
        with self._lock:
            self._projection_reports[str(projection["report_id"])] = copy.deepcopy(
                projection
            )
        return {
            "schema_version": 1,
            "graph_id": graph["graph_id"],
            "revision_id": graph["revision_id"],
            "status": (
                "ready"
                if projection.get("preview_eligible") is True
                else "blocked"
            ),
            "preview_eligible": projection.get("preview_eligible") is True,
            "round_trip_report": round_trip,
            "compatibility_report": compatibility,
            "projection_report": projection,
            "transport": "none",
            "commands_sent": 0,
            "journal_executed": False,
            "macro_executed": False,
            "machine_output_count": 0,
        }

    @staticmethod
    def _projection_id_from_extensions(
        extensions: Mapping[str, Any] | None,
    ) -> str:
        values = dict(extensions or {})
        for namespace in (
            REVIEW_EVIDENCE_REQUEST_EXTENSION,
            REVIEW_EVIDENCE_EXTENSION,
        ):
            item = values.get(namespace)
            if isinstance(item, Mapping) and item.get("projection_report_id"):
                return str(item["projection_report_id"])
        return ""

    def _verify_review_evidence(
        self,
        *,
        graph_id: str,
        revision_id: str | None,
        round_trip_report_id: str,
        compatibility_report_id: str,
        projection_report_id: str,
        expected_target: Mapping[str, Any] | None = None,
        context: Any | None = None,
    ) -> dict[str, Any]:
        from .flow_compatibility import project_flow_to_recipe
        from .flow_contracts import canonical_hash, compute_capability_lock_hash

        graph = self._current_graph(graph_id, revision_id)
        round_trip = self._require_round_trip(round_trip_report_id, graph)
        try:
            compatibility = self.get_compatibility_report(compatibility_report_id)
        except FlowServiceError as error:
            raise self._review_required(
                "Reviewed versions require a stored structured CompatibilityReport.",
                details={"compatibility_report_id": compatibility_report_id},
            ) from error
        target_profile = compatibility.get("target_profile", {})
        target = {
            "product": graph["product"],
            "target_version": target_profile.get("target_version"),
            "target_instance_id": target_profile.get("target_instance_id"),
            "project_id": target_profile.get("project_id"),
            "project_snapshot_hash": target_profile.get("project_snapshot_hash"),
            "target_kind": target_profile.get("target_kind"),
        }
        current_compatibility = self.check_compatibility(
            graph["graph_id"],
            target,
            revision_id=graph["revision_id"],
            context=context,
        )
        compatibility_valid = (
            compatibility.get("contract") == "cam.compatibility_report.v1"
            and compatibility.get("graph_id") == graph["graph_id"]
            and compatibility.get("revision_id") == graph["revision_id"]
            and compatibility.get("status") == "compatible"
            and compatibility.get("preview_eligible") is True
            and compatibility.get("blocker_codes") == []
            and compatibility.get("capability_lock_hash")
            == compute_capability_lock_hash(graph)
            and current_compatibility.get("report_id") == compatibility_report_id
            and canonical_hash(current_compatibility) == canonical_hash(compatibility)
        )
        if not compatibility_valid:
            raise self._review_required(
                "CompatibilityReport is blocked, stale, or does not bind this target.",
                details={"compatibility_report_id": compatibility_report_id},
            )
        if expected_target is not None:
            resolved_expected = self._resolve_target(expected_target).to_dict()
            resolved_report = self._resolve_target(target).to_dict()
            target_fields = (
                "product",
                "target_version",
                "target_instance_id",
                "project_id",
                "project_snapshot_hash",
                "target_kind",
            )
            if any(
                resolved_expected.get(field) != resolved_report.get(field)
                for field in target_fields
            ):
                raise self._review_required(
                    "Preview target does not match the reviewed CompatibilityReport.",
                    details={"compatibility_report_id": compatibility_report_id},
                )
        with self._lock:
            projection = copy.deepcopy(
                self._projection_reports.get(projection_report_id)
            )
        if projection is None:
            raise self._review_required(
                "Reviewed versions require a stored structured ProjectionReport.",
                details={"projection_report_id": projection_report_id},
            )
        recomputed = project_flow_to_recipe(
            graph,
            compatibility_report=compatibility,
            manifests=self._manifests(),
            checked_at=str(projection.get("checked_at", "")),
        ).report.to_dict()
        projection_valid = (
            projection.get("contract") == "cam.projection_report.v1"
            and projection.get("graph_id") == graph["graph_id"]
            and projection.get("revision_id") == graph["revision_id"]
            and projection.get("status") == "projected"
            and projection.get("preview_eligible") is True
            and projection.get("blockers") == []
            and isinstance(projection.get("recipe_hash"), str)
            and bool(projection.get("recipe_hash"))
            and recomputed.get("report_id") == projection_report_id
            and canonical_hash(recomputed) == canonical_hash(projection)
        )
        if not projection_valid:
            raise self._review_required(
                "ProjectionReport is blocked, stale, or not preview-eligible.",
                details={"projection_report_id": projection_report_id},
            )
        resolved_target = self._resolve_target(target).to_dict()
        return {
            "semantic": True,
            "graph_id": graph["graph_id"],
            "revision_id": graph["revision_id"],
            "semantic_hash": graph["semantic_hash"],
            "source_snapshot_hash": graph["source_snapshot_hash"],
            "capability_lock_hash": compute_capability_lock_hash(graph),
            "round_trip_report_id": round_trip_report_id,
            "round_trip_report_hash": canonical_hash(round_trip),
            "compatibility_report_id": compatibility_report_id,
            "compatibility_report_hash": canonical_hash(compatibility),
            "projection_report_id": projection_report_id,
            "projection_report_hash": canonical_hash(projection),
            "reviewed_recipe_hash": projection["recipe_hash"],
            "target": {
                field: resolved_target[field]
                for field in (
                    "product",
                    "target_version",
                    "target_instance_id",
                    "project_id",
                    "project_snapshot_hash",
                    "target_kind",
                )
            },
        }

    def create_version(
        self,
        version: Mapping[str, Any] | None = None,
        *,
        projection_report_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        value = copy.deepcopy(dict(version)) if version is not None else None
        status = value.get("status") if value is not None else kwargs.get("status", "draft")
        if status != "reviewed_for_fixture":
            return super().create_version(value, **kwargs)

        graph_id = str(
            value.get("graph_id", "") if value is not None else kwargs.get("graph_id", "")
        )
        revision_id = (
            str(value.get("revision_id", ""))
            if value is not None
            else kwargs.get("revision_id")
        )
        round_trip_report_id = str(
            value.get("round_trip_report_id", "")
            if value is not None
            else kwargs.get("round_trip_report_id", "")
        )
        compatibility_report_id = str(
            value.get("compatibility_report_id", "")
            if value is not None
            else kwargs.get("compatibility_report_id", "")
        )
        extensions = (
            value.get("extensions", {})
            if value is not None
            else kwargs.get("extensions", {})
        )
        projection_id = str(
            projection_report_id
            or self._projection_id_from_extensions(
                extensions if isinstance(extensions, Mapping) else None
            )
        )
        evidence = self._verify_review_evidence(
            graph_id=graph_id,
            revision_id=revision_id,
            round_trip_report_id=round_trip_report_id,
            compatibility_report_id=compatibility_report_id,
            projection_report_id=projection_id,
            context=kwargs.get("context"),
        )
        requested_recipe_hash = (
            self._reviewed_recipe_hash(value)
            if value is not None
            else kwargs.get("reviewed_recipe_hash")
        )
        if requested_recipe_hash not in {None, evidence["reviewed_recipe_hash"]}:
            raise self._review_required(
                "The caller-provided recipe hash does not match ProjectionReport.",
                details={"projection_report_id": projection_id},
            )
        extension_value = copy.deepcopy(
            dict(extensions) if isinstance(extensions, Mapping) else {}
        )
        extension_value.pop(REVIEW_EVIDENCE_REQUEST_EXTENSION, None)
        extension_value[REVIEW_EVIDENCE_EXTENSION] = evidence
        if value is not None:
            value["extensions"] = extension_value
            value.setdefault("extensions", {})[
                "cam.flow.api/reviewed_recipe_hash"
            ] = {
                "semantic": True,
                "value": evidence["reviewed_recipe_hash"],
            }
            return super().create_version(value, **kwargs)
        kwargs["extensions"] = extension_value
        kwargs["reviewed_recipe_hash"] = evidence["reviewed_recipe_hash"]
        return super().create_version(None, **kwargs)

    save_version = create_version

    def create_preview_plan(
        self,
        request: Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
        context: Any | None = None,
    ) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            return super().create_preview_plan(
                request,
                idempotency_key=idempotency_key,
                context=context,
            )
        version_id = str(request.get("flow_version_id", ""))
        version = self.get_version(version_id)
        extensions = version.get("extensions", {})
        evidence = (
            extensions.get(REVIEW_EVIDENCE_EXTENSION)
            if isinstance(extensions, Mapping)
            else None
        )
        if not isinstance(evidence, Mapping):
            raise self._review_required(
                "Preview requires structured review evidence bound to FlowVersion.",
                details={"flow_version_id": version_id},
            )
        self._verify_review_evidence(
            graph_id=str(version.get("graph_id", "")),
            revision_id=str(version.get("revision_id", "")),
            round_trip_report_id=str(evidence.get("round_trip_report_id", "")),
            compatibility_report_id=str(
                evidence.get("compatibility_report_id", "")
            ),
            projection_report_id=str(evidence.get("projection_report_id", "")),
            expected_target=(
                request.get("target")
                if isinstance(request.get("target"), Mapping)
                else None
            ),
            context=context,
        )
        return super().create_preview_plan(
            request,
            idempotency_key=idempotency_key,
            context=context,
        )

    create_preview = create_preview_plan


class OfflineFlowIntegration:
    """Join product-isolated static adapters to the canonical Flow service."""

    def __init__(self, flow_service: Any) -> None:
        self.flow_service = flow_service

    def import_source(
        self,
        *,
        product: str,
        source: bytes | bytearray | memoryview | str,
        source_name: str,
        target_versions: Iterable[str] = (),
        rights: Mapping[str, Any] | None = None,
        source_origin: str = "user_authored",
    ) -> dict[str, Any]:
        product_name = str(product).casefold()
        if product_name == "cimatron":
            raise FlowIntegrationError(
                "CAPABILITY_UNAVAILABLE",
                "Cimatron supports static Journal analysis only; Flow import is unavailable.",
                http_status=409,
            )
        if product_name not in {"nx", "powermill"}:
            raise FlowIntegrationError(
                "FLOW_PRODUCT_MIXED",
                "Offline Flow import requires product=nx or product=powermill.",
                http_status=400,
            )
        raw = source.encode("utf-8") if isinstance(source, str) else bytes(source)
        if not raw:
            raise FlowIntegrationError(
                "FLOW_REQUEST_INVALID",
                "Offline Flow import requires non-empty source bytes.",
                http_status=400,
            )
        display_name = Path(str(source_name)).name or {
            "nx": "journal.py",
            "powermill": "workflow.mac",
        }[product_name]
        versions = tuple(
            sorted({str(item).strip() for item in target_versions if str(item).strip()})
        )

        graph = _map_offline_flow_source(
            raw,
            product=product_name,
            source_name=display_name,
            target_versions=versions,
        )
        diagnostics = _flow_diagnostics(graph)
        blocked = next(
            (
                item
                for item in diagnostics
                if str(item.get("code", "")) in _FLOW_SAFETY_CODES
            ),
            None,
        )
        if blocked is not None:
            raise FlowIntegrationError(
                str(blocked["code"]),
                "The offline source was rejected by the permanent CAM safety boundary.",
            )
        asset_ref = graph["asset_refs"][0]
        asset = self.flow_service.register_asset(
            raw,
            asset_id=str(asset_ref["asset_id"]),
            product=product_name,
            asset_type={
                "nx": "nx_journal",
                "powermill": "powermill_macro",
            }[product_name],
            display_name=display_name,
            source_locator="selected-file",
            source_origin=source_origin,
            rights=rights,
            target_versions=versions,
            runtime_modes=("offline", "fixture_dry_run"),
            dependencies=(),
            extensions={"cam.flow.integration": {"semantic": False}},
        )
        graph = _bind_imported_flow_asset(graph, asset)
        saved = self.flow_service.save_graph(graph)
        from .flow_roundtrip import verify_round_trip

        round_trip_result = verify_round_trip(
            saved,
            saved,
            raw,
            (),
            _OfflineImportRoundTripAdapter(
                product=product_name,
                source_name=display_name,
                target_versions=versions,
                asset=asset,
            ),
            required_fidelity="F0",
            checked_at=self.flow_service._timestamp(),
        )
        round_trip_report = round_trip_result.report.to_dict()
        if round_trip_report.get("status") != "passed":
            raise FlowIntegrationError(
                "PROJECTION_REVIEW_REQUIRED",
                "The imported source did not produce current round-trip evidence.",
            )
        register_result = getattr(self.flow_service, "register_round_trip_result", None)
        if callable(register_result):
            round_trip_report = register_result(round_trip_result)
        return {
            "schema_version": 1,
            "mode": "offline",
            "asset": asset,
            "graph": saved,
            "round_trip_report": round_trip_report,
            "diagnostics": diagnostics,
            "transport": "none",
            "commands_sent": 0,
            "journal_executed": False,
            "macro_executed": False,
            "machine_output_count": 0,
        }


def sample_for(product: str) -> str:
    if product == "powermill":
        return SAMPLE_LOG
    if product == "cimatron":
        return """# Cimatron 2026 Journaling sample (static evidence only)
document = application.Documents.Active
command = document.Commands.Item("CreateOperation")
command.Execute("roughing")
"""
    return """# NX Open Journal sample
session = NXOpen.Session.GetSession()
part = session.Parts.Work
program = part.CAMSetup.CAMGroupCollection.FindObject("PROGRAM")
operation = part.CAMSetup.CAMOperationCollection.Create("mill_planar", program)
operation.GenerateToolPath()
"""
