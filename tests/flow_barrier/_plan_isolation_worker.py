from __future__ import annotations

import copy
import json
import tempfile

from cam_automation.flow_service import FlowConflictError, FlowService

from ._support import (
    BarrierHarness,
    ZERO_EXECUTION,
    samples,
)


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        harness = BarrierHarness(directory)
        targets = harness.service.list_fixture_targets()
        target_counts = {
            product: sum(target["product"] == product for target in targets)
            for product in ("nx", "powermill")
        }

        prepared = {}
        for sample in (samples()[0], samples()[3]):
            imported = harness.import_sample(sample)
            _original, graph = harness.edit_graph(imported["graph"], sample)
            _compatibility, _version, request = harness.prepare_preview(
                graph,
                sample,
            )
            prepared[sample["product"]] = (sample, request)

        cross_wires = 0
        zero_execution_plans = 0
        for index in range(1_000):
            product = "nx" if index % 2 else "powermill"
            sample, template = prepared[product]
            request = copy.deepcopy(template)
            request["plan_id"] = f"preview:t10:isolation:{index:04d}"
            plan = FlowService.create_preview_plan(harness.service, request)
            if (
                plan["target"]["product"] != product
                or plan["target"]["target_instance_id"]
                != sample["target_instance_id"]
                or plan["target"]["project_id"] != request["target"]["project_id"]
            ):
                cross_wires += 1
            if (
                plan["status"] == "ready"
                and all(plan[field] == expected for field, expected in ZERO_EXECUTION.items())
            ):
                zero_execution_plans += 1

        plan_ids = []
        cursor = None
        while True:
            page = harness.service.list_preview_plans(
                cursor=cursor,
                limit=200,
            )
            plan_ids.extend(item["plan_id"] for item in page.items)
            if not page.has_more:
                break
            cursor = page.next_cursor

        graph_id = prepared["powermill"][1]["graph_id"]
        ambiguous_code = None
        ambiguous_candidates = None
        try:
            harness.service.check_compatibility(
                graph_id,
                {"product": "powermill", "target_kind": "fixture"},
            )
        except FlowConflictError as error:
            ambiguous_code = error.code
            ambiguous_candidates = error.details.get("candidate_count")

        print(
            json.dumps(
                {
                    "target_counts": target_counts,
                    "plan_count": len(plan_ids),
                    "unique_plan_count": len(set(plan_ids)),
                    "zero_execution_plans": zero_execution_plans,
                    "cross_wires": cross_wires,
                    "ambiguous_code": ambiguous_code,
                    "ambiguous_candidates": ambiguous_candidates,
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
