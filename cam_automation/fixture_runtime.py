from __future__ import annotations

import copy
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from .adapters.powermill_macro import FixturePowerMillTransport
from .asset_registry import AssetRegistry
from .capability_registry import CapabilityRegistry
from .command_tasks import CommandTaskService
from .connection_monitor import ConnectionMonitor
from .diagnostics import DiagnosticsService
from .flow_contracts import canonical_hash
from .integrations import (
    ApiServices,
    OfflineFlowIntegration,
    ReviewGatedFlowService,
)
from .recipes import PreviewRequest, RecipeService
from .recorder import RecorderService
from .sessions import SessionService


_ROOT = Path(__file__).resolve().parents[1]
_UG_SRC = _ROOT / "plugins" / "ug-cam-copilot" / "src"
_FIXTURE_ROOT = Path(__file__).with_name("fixtures")
_NX_TRANSPORT = (
    _ROOT
    / "plugins"
    / "ug-cam-copilot"
    / "examples"
    / "nx_transport"
    / "two_instances.json"
)
_PM_TRANSPORT = _FIXTURE_ROOT / "powermill-runtime.json"
_EVENT_SOURCE = _FIXTURE_ROOT / "studio-demo-events.jsonl"
_AUTHORIZED_TARGETS = ("nx:3101:A1", "powermill:4101:C1")
_FLOW_TARGETS = (
    ("nx", "NX 2406", "nx:3101:A1", "nx-project-a", "fixture-nx-project-a"),
    ("nx", "NX 2312", "nx:3102:B2", "nx-project-b", "fixture-nx-project-b"),
    (
        "powermill",
        "PowerMill 2026",
        "powermill:4101:C1",
        "pm-project-a",
        "fixture-powermill-project-a",
    ),
    (
        "powermill",
        "PowerMill 2025",
        "powermill:4102:process",
        "pm-project-b",
        "fixture-powermill-project-b",
    ),
)

if str(_UG_SRC) not in sys.path:
    sys.path.insert(0, str(_UG_SRC))

from ugcam_ai.transport import FixtureNxTransport  # noqa: E402


class OfflineNxFixtureTransport:
    """Add explicit fixture connection semantics around the NX fixture transport."""

    name = "fixture"
    offline = True

    def __init__(self, delegate: FixtureNxTransport) -> None:
        self._delegate = delegate
        self._connected: set[str] = set()

    @classmethod
    def from_path(cls, path: str | Path) -> OfflineNxFixtureTransport:
        return cls(FixtureNxTransport.from_path(path))

    def connect(self, descriptor: Mapping[str, Any]) -> None:
        instance_id = str(descriptor.get("instance_id", ""))
        self._delegate.snapshot(instance_id)
        self._connected.add(instance_id)

    def disconnect(self, instance_id: str) -> None:
        self._connected.discard(instance_id)

    def is_connected(self, instance_id: str) -> bool:
        return instance_id in self._connected

    def capabilities(self, instance_id: str) -> tuple[str, ...]:
        self._delegate.snapshot(instance_id)
        return ("fixture.query", "fixture.snapshot", "journal.parse")

    def query(
        self,
        instance_id: str,
        operation: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        task_id: str | None = None,
    ) -> Mapping[str, Any]:
        if instance_id not in self._connected:
            raise ConnectionError("No offline NX fixture connection exists for this instance.")
        delay_ms = (arguments or {}).get("fixture_delay_ms", 0)
        if isinstance(delay_ms, (int, float)) and not isinstance(delay_ms, bool):
            time.sleep(max(0.0, min(float(delay_ms), 1_000.0)) / 1_000.0)
        return self._delegate.query(
            instance_id,
            operation,
            arguments,
            task_id=task_id,
        ).to_dict()

    def snapshot(self, instance_id: str) -> Mapping[str, Any]:
        if instance_id not in self._connected:
            raise ConnectionError("No offline NX fixture connection exists for this instance.")
        return self._delegate.snapshot(instance_id).to_dict()


class FixturePreviewAdapter:
    """Produce a review-only proposal from an immutable fixture snapshot."""

    def __init__(self, monitor: ConnectionMonitor) -> None:
        self._monitor = monitor

    def preview(self, request: PreviewRequest) -> Mapping[str, Any]:
        before = self._monitor.snapshot(request.target_instance_id)
        if before.get("status") in {"disconnected", "failed", "timed_out"}:
            raise ConnectionError("The exact fixture target is unavailable for preview.")
        after = copy.deepcopy(before)
        digest = hashlib.sha256(
            json.dumps(
                {
                    "snapshot": before.get("content_hash"),
                    "recipe_hash": request.recipe_hash,
                    "parameters": dict(request.parameters),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        after["snapshot_id"] = f"fixture-preview:{digest}"
        after["status"] = "proposed_only"
        after["preview_only"] = True
        after["commands_sent"] = 0
        changes = [
            {
                "path": f"recipe/steps/{step.step_id}",
                "kind": "proposed_update",
                "before": "not_applied",
                "after": "previewed",
                "severity": "review",
            }
            for step in request.recipe.steps
        ]
        return {
            "schema_version": 1,
            "task_id": request.task_id,
            "product": request.product,
            "target_instance_id": request.target_instance_id,
            "project_id": request.project_id,
            "recipe_hash": request.recipe_hash,
            "execution_mode": "dry_run",
            "status": "changes_detected" if changes else "no_change",
            "before_snapshot": before,
            "after_snapshot": after,
            "changes": changes,
            "gate_results": [
                {"gate": "cam_simulation", "status": "not_run", "evidence_refs": []},
                {"gate": "collision_check", "status": "required", "evidence_refs": []},
                {"gate": "shop_approval", "status": "required", "evidence_refs": []},
            ],
            "summary": (
                "Offline fixture preview completed without sending CAM commands."
            ),
        }


def _fixture_processes() -> list[dict[str, Any]]:
    return [
        {
            "process_name": "ugraf.exe",
            "pid": 3101,
            "target_version": "NX 2406",
            "source_kind": "fixture_local",
            "metadata": {"log_lag_ms": 24},
        },
        {
            "process_name": "ugraf.exe",
            "pid": 3102,
            "target_version": "NX 2312",
            "source_kind": "fixture_local",
            "metadata": {"log_lag_ms": 31},
        },
        {
            "process_name": "pmill.exe",
            "pid": 4101,
            "target_version": "PowerMill 2026",
            "source_kind": "fixture_local",
            "metadata": {"log_lag_ms": 19},
        },
        {
            "process_name": "pmill.exe",
            "pid": 4102,
            "target_version": "PowerMill 2025",
            "source_kind": "fixture_local",
            "metadata": {"log_lag_ms": 42},
        },
    ]


def _fixture_windows() -> tuple[list[dict[str, Any]], int]:
    return (
        [
            {
                "pid": 3101,
                "handle": 0xA1,
                "title": "NX 2406 - Cavity A",
                "target_version": "NX 2406",
                "project_id": "nx-project-a",
                "project_name": "Cavity A",
            },
            {
                "pid": 3102,
                "handle": 0xB2,
                "title": "NX 2312 - Electrode B",
                "target_version": "NX 2312",
                "project_id": "nx-project-b",
                "project_name": "Electrode B",
            },
            {
                "pid": 4101,
                "handle": 0xC1,
                "title": "Cavity A - PowerMill 2026",
                "target_version": "PowerMill 2026",
                "project_id": "pm-project-a",
                "project_name": "Cavity A",
            },
        ],
        0xA1,
    )


def build_fixture_flow_service(data_dir: str | Path) -> ReviewGatedFlowService:
    """Build the transport-free canonical Flow service and its four exact targets."""

    root = Path(data_dir)
    targets = [
        {
            "product": product,
            "target_version": target_version,
            "target_instance_id": instance_id,
            "project_id": project_id,
            "project_snapshot_hash": canonical_hash(
                {
                    "fixture": snapshot_id,
                    "product": product,
                    "target_version": target_version,
                    "project_id": project_id,
                }
            ),
            "target_kind": "fixture",
            "permissions": ["read:selected-files"],
            "extensions": {
                "cam.flow.fixture": {
                    "semantic": False,
                    "offline": True,
                    "transport": "none",
                }
            },
        }
        for product, target_version, instance_id, project_id, snapshot_id in _FLOW_TARGETS
    ]
    return ReviewGatedFlowService(
        asset_registry=AssetRegistry(root / "assets"),
        capability_registry=CapabilityRegistry(root / "capabilities"),
        fixture_targets=targets,
        cursor_secret=b"cam-flow-fixture-runtime-v1",
    )


def build_fixture_api_services(capture_dir: str | Path) -> ApiServices:
    """Build the default local-only service graph used by the packaged studio."""

    nx_transport = OfflineNxFixtureTransport.from_path(_NX_TRANSPORT)
    pm_transport = FixturePowerMillTransport.from_file(_PM_TRANSPORT)
    transports = {"nx": nx_transport, "powermill": pm_transport}
    monitor = ConnectionMonitor(
        process_provider=_fixture_processes,
        window_provider=_fixture_windows,
        transports=transports,
        auto_connect=True,
        authorized_local_sources=False,
    )
    monitor.list_instances(refresh=True)
    monitor.default_authorized_instance_ids = _AUTHORIZED_TARGETS

    recorder = RecorderService(
        capture_dir,
        source_paths={"nx": [_EVENT_SOURCE]},
        start_background=True,
    )
    recipes = RecipeService(
        adapters={
            "nx": FixturePreviewAdapter(monitor),
            "powermill": FixturePreviewAdapter(monitor),
        }
    )
    commands = CommandTaskService(
        transports,
        instance_provider=monitor,
        recipe_service=recipes,
        audit_sink=recorder,
    )
    diagnostics = DiagnosticsService(
        commands,
        instance_provider=monitor,
    )
    flow = build_fixture_flow_service(Path(capture_dir).parent / "flow")
    return ApiServices(
        recorder=recorder,
        connections=monitor,
        sessions=SessionService(),
        recipes=recipes,
        commands=commands,
        diagnostics=diagnostics,
        flow=flow,
        flow_imports=OfflineFlowIntegration(flow),
    )
