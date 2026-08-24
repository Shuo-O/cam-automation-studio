from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cam_automation.asset_registry import AssetRegistry
from cam_automation.capability_registry import CapabilityRegistry
from cam_automation.flow_service import FlowService


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "cam-flow"
    / "api"
    / "flow-api-fixture.json"
)


def load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 24, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.value

    def advance(self, milliseconds: int) -> None:
        self.value += timedelta(milliseconds=milliseconds)


def build_service(directory: str, *, clock: Any | None = None) -> FlowService:
    fixture = load_fixture()
    return FlowService(
        asset_registry=AssetRegistry(
            directory,
            clock=clock or (lambda: datetime.now(timezone.utc)),
        ),
        capability_registry=CapabilityRegistry(directory, clock=clock),
        fixture_targets=[fixture["target"]],
        clock=clock,
        cursor_secret=b"flow-api-test-secret",
    )


def register_fixture(service: FlowService) -> dict[str, Any]:
    fixture = load_fixture()
    asset_request = copy.deepcopy(fixture["asset_request"])
    content = asset_request.pop("content").encode("utf-8")
    asset_request.pop("content_encoding")
    service.register_asset(content, **asset_request)
    service.register_capability(fixture["manifest"])
    service.save_graph(fixture["graph"])
    service.create_version(**fixture["version_request"])
    return fixture


def second_revision(fixture: dict[str, Any]) -> dict[str, Any]:
    graph = copy.deepcopy(fixture["graph"])
    graph["revision_id"] = "revision:nx:api:2"
    graph["parent_revision_id"] = fixture["graph"]["revision_id"]
    graph["semantic_hash"] = "sha256:" + ("4" * 64)
    graph["flows"][0]["nodes"][0]["enabled"] = False
    return graph
