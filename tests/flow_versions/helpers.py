from __future__ import annotations

import copy
import json
from pathlib import Path

from cam_automation.flow_compatibility import build_compatibility_report
from cam_automation.flow_versions import prepare_graph_snapshot


FIXED_TIME = "2026-08-24T08:00:00Z"
FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "cam-flow" / "versions"
)


def fixture() -> dict:
    return json.loads((FIXTURE_ROOT / "linear-flow.json").read_text(encoding="utf-8"))


def graph() -> dict:
    return prepare_graph_snapshot(fixture()["graph"]).to_dict()


def target_graph(default: int = 2) -> dict:
    value = copy.deepcopy(graph())
    value["graph_parameters"][0]["default"] = default
    return prepare_graph_snapshot(value).to_dict()


def manifest() -> dict:
    return copy.deepcopy(fixture()["manifest"])


def compatibility(value: dict | None = None, capability: dict | None = None):
    return build_compatibility_report(
        value or graph(),
        [capability or manifest()],
        target_profile={
            "target_version": "NX 2406",
            "context": {"project_id": "fixture-project"},
        },
        required_context=("project_id",),
        checked_at=FIXED_TIME,
    )
