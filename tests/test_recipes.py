from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from cam_automation.recipes import (
    REQUIRED_PRODUCTION_GATES,
    PreviewRequest,
    Recipe,
    RecipeParameter,
    RecipeService,
    RecipeStep,
    canonical_json,
    compute_recipe_hash,
)
from cam_automation.sessions import EventRef


FIXED_TIME = "2026-08-23T09:00:00Z"


def recipe(
    *,
    default: object = 0.02,
    arguments: dict | None = None,
    action: str = "cam.operation.create",
    status: str = "review_required",
    value_type: str = "number",
    enabled: bool = True,
    risk: str = "review",
) -> Recipe:
    parameter = RecipeParameter(
        name="tolerance",
        value_type=value_type,
        required=True,
        default=default,
        samples=(default,),
        description="Reviewed tolerance.",
        source_event_refs=(EventRef("source-a", 0),),
    )
    step = RecipeStep(
        step_id="step-001",
        order=1,
        action=action,
        enabled=enabled,
        risk=risk,
        review_status="needs_review",
        arguments=arguments
        or {"operation_type": "mill_planar", "tolerance": {"parameter": "tolerance"}},
        condition=None,
        source_event_refs=(EventRef("source-a", 0),),
    )
    value = Recipe(
        recipe_id="recipe:nx:test",
        recipe_hash="",
        name="Review recipe",
        product="nx",
        status=status,
        target_versions=("NX 2406",),
        source_session_ids=("session:nx:a", "session:nx:b"),
        support={"matched_sessions": 2, "total_sessions": 2, "ratio": 1.0},
        parameters=(parameter,),
        steps=(step,),
        required_gates=REQUIRED_PRODUCTION_GATES,
        created_at=FIXED_TIME,
        updated_at=FIXED_TIME,
        project_conditions={"test_copy_required": True, "units": "mm"},
    )
    return replace(value, recipe_hash=compute_recipe_hash(value))


class FixtureAdapter:
    product = "nx"
    target_version = "NX 2406"
    target_instance_id = "opaque-instance-a"
    project_id = "project-a"
    reviewer = "operator:reviewer-a"

    def __init__(self, response: dict | None = None) -> None:
        self.calls: list[PreviewRequest] = []
        self.response = response

    def preview(self, request: PreviewRequest) -> dict:
        self.calls.append(request)
        if self.response is not None:
            return self.response
        return {
            "status": "changes_detected",
            "changes": [
                {
                    "path": "operations/op/tolerance",
                    "kind": "proposed_update",
                    "before": 0.03,
                    "after": request.parameters["tolerance"],
                    "severity": "review",
                }
            ],
            "gate_results": [],
            "summary": "One dry-run change is proposed.",
        }


class RecipeContractTests(unittest.TestCase):
    def test_rfc8785_numbers_key_order_and_unicode(self) -> None:
        self.assertEqual(
            '{"a":0.000001,"b":1,"c":1e+21,"d":0}',
            canonical_json({"d": -0.0, "c": 1e21, "b": 1.0, "a": 1e-6}),
        )
        self.assertEqual(
            canonical_json({"😀": 1, "a": 2, "€": 3}),
            canonical_json({"€": 3, "😀": 1, "a": 2}),
        )
        self.assertNotEqual(canonical_json({"é": 1}), canonical_json({"e\u0301": 1}))
        with self.assertRaisesRegex(ValueError, "IEEE-754"):
            canonical_json(9_007_199_254_740_992)

    def test_semantic_hash_ignores_map_order_and_nonsemantic_metadata(self) -> None:
        first = recipe(arguments={"b": 2, "a": 1})
        second = replace(
            recipe(arguments={"a": 1, "b": 2}),
            name="Renamed",
            status="approved_for_simulation",
            source_session_ids=("other-source",),
            created_at="2026-08-23T10:00:00Z",
            updated_at="2026-08-23T10:00:00Z",
        )

        self.assertEqual(compute_recipe_hash(first), compute_recipe_hash(second))
        self.assertEqual(
            compute_recipe_hash(recipe(default=1)),
            compute_recipe_hash(recipe(default=1.0)),
        )
        self.assertNotEqual(
            compute_recipe_hash(recipe(default=True, value_type="boolean")),
            compute_recipe_hash(recipe(default=1, value_type="integer")),
        )

    def test_save_is_versioned_content_addressed_and_idempotent(self) -> None:
        service = RecipeService(clock=lambda: FIXED_TIME)
        first = service.save(recipe())
        duplicate = service.save(recipe())
        changed = service.save(recipe(arguments={"tolerance": 0.025}))

        self.assertIs(first, duplicate)
        self.assertEqual(1, first.version)
        self.assertEqual(first.recipe_hash, first.content_address)
        self.assertEqual(2, changed.version)
        self.assertNotEqual(first.recipe_hash, changed.recipe_hash)
        self.assertEqual((first, changed), service.versions(first.recipe_id))

    def test_preview_uses_only_adapter_and_marks_unfinished_gates(self) -> None:
        adapter = FixtureAdapter()
        service = RecipeService(adapter=adapter, clock=lambda: FIXED_TIME)
        version = service.save(recipe())

        report = service.preview(version.recipe_hash, {"tolerance": 0.015})

        self.assertEqual(1, len(adapter.calls))
        self.assertEqual("dry_run", adapter.calls[0].execution_mode)
        self.assertEqual(0.015, adapter.calls[0].parameters["tolerance"])
        gates = {item["gate"]: item["status"] for item in report.gate_results}
        self.assertEqual("passed", gates["recipe_review"])
        self.assertEqual("not_run", gates["cam_simulation"])
        self.assertEqual("required", gates["collision_check"])
        self.assertEqual("required", gates["shop_approval"])
        self.assertIn("dry-run only", report.summary)

    def test_preview_is_isolated_from_product_parsers(self) -> None:
        adapter = FixtureAdapter()
        service = RecipeService(adapter=adapter, clock=lambda: FIXED_TIME)
        version = service.save(recipe())

        with patch(
            "cam_automation.parser.parse_log",
            side_effect=AssertionError("product parser must not be called"),
        ):
            report = service.preview(version.recipe_hash, {})

        self.assertEqual("changes_detected", report.status)
        self.assertEqual(1, len(adapter.calls))

    def test_parameter_types_and_target_version_are_validated(self) -> None:
        adapter = FixtureAdapter()
        service = RecipeService(adapter=adapter, clock=lambda: FIXED_TIME)
        version = service.save(recipe())

        with self.assertRaisesRegex(ValueError, "value_type number"):
            service.preview(version.recipe_hash, {"tolerance": True})
        with self.assertRaisesRegex(ValueError, "outside"):
            service.preview(
                version.recipe_hash,
                {"tolerance": 0.02},
                target_version="NX 9999",
            )
        self.assertEqual([], adapter.calls)

    def test_blocked_and_machine_ready_nc_content_never_reaches_adapter(self) -> None:
        adapter = FixtureAdapter()
        service = RecipeService(adapter=adapter, clock=lambda: FIXED_TIME)
        blocked = service.save(recipe(risk="blocked"))

        with self.assertRaisesRegex(ValueError, "Blocked step"):
            service.preview(blocked.recipe_hash, {})
        self.assertEqual([], adapter.calls)

        machine_output = service.save(recipe(action="cam.output.postprocess"))
        with self.assertRaisesRegex(ValueError, "Machine-ready NC"):
            service.preview(machine_output.recipe_hash, {})
        self.assertEqual([], adapter.calls)

    def test_adapter_machine_output_is_rejected_and_not_returned(self) -> None:
        adapter = FixtureAdapter(
            {
                "status": "changes_detected",
                "changes": [],
                "gate_results": [],
                "nc_code": "G01 X1.0",
            }
        )
        service = RecipeService(adapter=adapter, clock=lambda: FIXED_TIME)
        version = service.save(recipe())

        with self.assertRaisesRegex(ValueError, "machine-ready NC"):
            service.preview(version.recipe_hash, {})
        self.assertEqual(1, len(adapter.calls))

    def test_adapter_cannot_escalate_preview_out_of_dry_run(self) -> None:
        adapter = FixtureAdapter(
            {
                "execution_mode": "live",
                "status": "no_change",
                "changes": [],
                "gate_results": [],
            }
        )
        service = RecipeService(adapter=adapter, clock=lambda: FIXED_TIME)
        version = service.save(recipe())

        with self.assertRaisesRegex(ValueError, "dry-run"):
            service.preview(version.recipe_hash, {})

    def test_disabled_blocked_step_is_not_sent(self) -> None:
        adapter = FixtureAdapter()
        service = RecipeService(adapter=adapter, clock=lambda: FIXED_TIME)
        version = service.save(recipe(enabled=False, risk="blocked"))

        service.preview(version.recipe_hash, {})

        self.assertEqual((), adapter.calls[0].recipe.steps)


if __name__ == "__main__":
    unittest.main()
