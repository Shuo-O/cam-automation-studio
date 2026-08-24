from __future__ import annotations

import copy
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from cam_automation.flow_service import (
    CancellationToken,
    FlowCancelledError,
    FlowConflictError,
    FlowDeadlineExceeded,
    FlowRequestContext,
    FlowServiceError,
    ZERO_EXECUTION_FIELDS,
)

from tests.flow_api._support import (
    FakeClock,
    build_service,
    load_fixture,
    register_fixture,
    second_revision,
)


class FlowServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.clock = FakeClock()
        self.service = build_service(self.directory.name, clock=self.clock)

    def test_create_operations_are_idempotent_and_payload_conflicts_fail(self) -> None:
        fixture = load_fixture()
        asset = copy.deepcopy(fixture["asset_request"])
        content = asset.pop("content").encode("utf-8")
        asset.pop("content_encoding")

        first = self.service.register_asset(
            content,
            idempotency_key="asset-create",
            **asset,
        )
        duplicate = self.service.register_asset(
            content,
            idempotency_key="asset-create",
            **asset,
        )
        self.assertEqual(first, duplicate)
        with self.assertRaises(FlowConflictError) as caught:
            self.service.register_asset(
                b"# changed fixture\n",
                idempotency_key="asset-create",
                **asset,
            )
        self.assertEqual("FLOW_IDEMPOTENCY_CONFLICT", caught.exception.code)

        capability = self.service.register_capability(
            fixture["manifest"],
            idempotency_key="manifest-create",
        )
        self.assertEqual(
            capability,
            self.service.register_capability(
                fixture["manifest"],
                idempotency_key="manifest-create",
            ),
        )
        graph = self.service.save_graph(
            fixture["graph"],
            idempotency_key="graph-create",
        )
        self.assertEqual(
            graph,
            self.service.save_graph(
                fixture["graph"],
                idempotency_key="graph-create",
            ),
        )

    def test_graph_revision_etag_and_stale_parent_conflicts(self) -> None:
        fixture = register_fixture(self.service)
        first_etag = self.service.graph_etag(
            fixture["graph"]["graph_id"],
            fixture["graph"]["revision_id"],
        )
        second = second_revision(fixture)
        saved = self.service.save_graph(
            second,
            expected_revision_id=fixture["graph"]["revision_id"],
            if_match=first_etag,
        )
        self.assertEqual("revision:nx:api:2", saved["revision_id"])
        self.assertNotEqual(first_etag, self.service.graph_etag(saved["graph_id"]))

        stale = copy.deepcopy(second)
        stale["revision_id"] = "revision:nx:api:stale"
        stale["parent_revision_id"] = fixture["graph"]["revision_id"]
        with self.assertRaises(FlowConflictError) as caught:
            self.service.save_graph(
                stale,
                expected_revision_id=fixture["graph"]["revision_id"],
                if_match=first_etag,
            )
        self.assertEqual("FLOW_REVISION_CONFLICT", caught.exception.code)
        self.assertEqual(
            "revision:nx:api:2",
            caught.exception.details["actual_revision_id"],
        )

    def test_cursor_is_query_bound_tamper_evident_and_snapshot_stable(self) -> None:
        fixture = register_fixture(self.service)
        second = second_revision(fixture)
        self.service.save_graph(second)
        first_page = self.service.list_graph_revisions(
            fixture["graph"]["graph_id"],
            limit=1,
        )
        self.assertTrue(first_page.has_more)
        self.assertIsNotNone(first_page.next_cursor)

        third = copy.deepcopy(second)
        third["revision_id"] = "revision:nx:api:3"
        third["parent_revision_id"] = second["revision_id"]
        third["semantic_hash"] = "sha256:" + ("5" * 64)
        self.service.save_graph(third)

        second_page = self.service.list_graph_revisions(
            fixture["graph"]["graph_id"],
            cursor=first_page.next_cursor,
            limit=1,
        )
        self.assertEqual(
            ["revision:nx:api:2"],
            [item["revision_id"] for item in second_page.items],
        )
        self.assertFalse(second_page.has_more)

        tampered = str(first_page.next_cursor)[:-1] + "A"
        with self.assertRaises(FlowServiceError) as caught:
            self.service.list_graph_revisions(
                fixture["graph"]["graph_id"],
                cursor=tampered,
                limit=1,
            )
        self.assertEqual("FLOW_CURSOR_INVALID", caught.exception.code)

        with self.assertRaises(FlowServiceError) as malformed:
            self.service.list_graph_revisions(
                fixture["graph"]["graph_id"],
                cursor="a",
                limit=1,
            )
        self.assertEqual("FLOW_CURSOR_INVALID", malformed.exception.code)

    def test_unknown_contract_fields_round_trip_but_edit_validation_fails_closed(self) -> None:
        fixture = load_fixture()
        self.service.register_capability(fixture["manifest"])
        graph = copy.deepcopy(fixture["graph"])
        graph["future_semantic_option"] = {"revision": 2}
        saved = self.service.save_graph(graph)
        self.assertEqual(
            {"revision": 2},
            self.service.get_graph(
                graph["graph_id"],
                graph["revision_id"],
            )["future_semantic_option"],
        )
        result = self.service.validate(
            saved,
            require_known_semantics=True,
        )
        self.assertFalse(result["valid"])
        self.assertIn(
            "FLOW_UNKNOWN_SEMANTICS",
            {item["code"] for item in result["diagnostics"]},
        )

    def test_diff_ignores_layout_by_default_and_is_stably_ordered(self) -> None:
        fixture = register_fixture(self.service)
        layout_only = copy.deepcopy(fixture["graph"])
        layout_only["revision_id"] = "revision:nx:api:layout"
        layout_only["parent_revision_id"] = fixture["graph"]["revision_id"]
        layout_only["layout"] = {"nodes": {"node:inspect": {"x": 120, "y": 40}}}
        self.service.save_graph(layout_only)
        without_layout = self.service.diff_graphs(
            fixture["graph"]["graph_id"],
            fixture["graph"]["revision_id"],
            layout_only["revision_id"],
        )
        with_layout = self.service.diff_graphs(
            fixture["graph"]["graph_id"],
            fixture["graph"]["revision_id"],
            layout_only["revision_id"],
            include_layout=True,
        )
        self.assertEqual("changes_detected", without_layout["status"])
        self.assertEqual(
            ["/parent_revision_id", "/revision_id"],
            [item["path"] for item in without_layout["changes"]],
        )
        self.assertGreater(len(with_layout["changes"]), len(without_layout["changes"]))

    def test_diff_rejects_bulk_changes_past_the_configured_limit(self) -> None:
        fixture = register_fixture(self.service)
        changed = copy.deepcopy(fixture["graph"])
        changed["revision_id"] = "revision:nx:api:limit"
        changed["parent_revision_id"] = fixture["graph"]["revision_id"]
        changed["z_future_1"] = True
        changed["z_future_2"] = True
        self.service.save_graph(changed)
        self.service.max_diff_changes = 3

        with self.assertRaises(FlowServiceError) as caught:
            self.service.diff_graphs(
                fixture["graph"]["graph_id"],
                fixture["graph"]["revision_id"],
                changed["revision_id"],
            )
        self.assertEqual("RESOURCE_LIMIT_EXCEEDED", caught.exception.code)

    def test_version_hashes_are_bound_to_the_exact_graph_revision(self) -> None:
        fixture = register_fixture(self.service)
        version = self.service.get_version(
            fixture["version_request"]["version_id"]
        )
        changed = copy.deepcopy(version)
        changed["version_id"] = "flow-version:nx:api:wrong-hash"
        changed["semantic_hash"] = "sha256:" + ("9" * 64)
        with self.assertRaises(FlowConflictError) as caught:
            self.service.create_version(changed)
        self.assertEqual("PREVIEW_HASH_MISMATCH", caught.exception.code)

    def test_compatibility_requires_one_explicit_registered_fixture(self) -> None:
        fixture = register_fixture(self.service)
        ambiguous = copy.deepcopy(fixture["target"])
        ambiguous.pop("target_instance_id")
        with self.assertRaises(FlowConflictError) as caught:
            self.service.check_compatibility(
                fixture["graph"]["graph_id"],
                ambiguous,
            )
        self.assertEqual("PREVIEW_TARGET_AMBIGUOUS", caught.exception.code)

    def test_review_required_target_is_readable_but_not_preview_eligible(self) -> None:
        fixture = load_fixture()
        asset = copy.deepcopy(fixture["asset_request"])
        content = asset.pop("content").encode("utf-8")
        asset.pop("content_encoding")
        self.service.register_asset(content, **asset)

        capability = copy.deepcopy(fixture["manifest"])
        capability["extensions"] = {
            "cam_automation": {
                "version_compatibility": {
                    "policy_version": 1,
                    "aliases": ["NX", "Siemens NX", "UG NX"],
                    "review_required_ranges": ["NX >=1847,<2406"],
                    "opaque_only_ranges": ["NX >=8,<1847"],
                    "unsupported_ranges": [],
                }
            }
        }
        self.service.register_capability(capability)

        graph = copy.deepcopy(fixture["graph"])
        graph["target_versions"] = ["NX 2312"]
        self.service.save_graph(graph)
        target = copy.deepcopy(fixture["target"])
        target["target_version"] = "Siemens NX 2312"
        target["target_instance_id"] = "fixture:nx:api:2312"
        self.service.register_fixture_target(target)

        report = self.service.check_compatibility(
            graph["graph_id"],
            target,
        )
        self.assertEqual("needs_review", report["status"])
        self.assertFalse(report["preview_eligible"])
        self.assertEqual([], report["blocker_codes"])
        self.assertEqual(
            "review_required",
            report["extensions"]["cam.flow_compatibility"][
                "version_results"
            ][0]["tier"],
        )
        self.assertTrue(
            all(
                item["status"] in {"needs_review", "disabled"}
                for item in report["node_results"]
            )
        )

        mismatched = copy.deepcopy(fixture["target"])
        mismatched["project_id"] = "another-project"
        with self.assertRaises(FlowConflictError) as caught:
            self.service.check_compatibility(
                fixture["graph"]["graph_id"],
                mismatched,
            )
        self.assertEqual("PREVIEW_TARGET_AMBIGUOUS", caught.exception.code)

    def test_preview_plan_state_machine_never_changes_zero_execution_fields(self) -> None:
        fixture = register_fixture(self.service)
        ready = self.service.create_preview_plan(fixture["preview_request"])
        self.assertEqual("ready", ready["status"])
        self.assertEqual("none", ready["transport"])
        for key, expected in ZERO_EXECUTION_FIELDS.items():
            self.assertEqual(expected, ready[key])

        with self.assertRaises(FlowConflictError) as caught:
            self.service.transition_preview_plan(
                ready["plan_id"],
                "succeeded",
                expected_status="ready",
            )
        self.assertEqual("FLOW_STATE_CONFLICT", caught.exception.code)

        succeeded = self.service.execute_preview_plan(ready["plan_id"])
        self.assertEqual("succeeded", succeeded["status"])
        for key, expected in ZERO_EXECUTION_FIELDS.items():
            self.assertEqual(expected, succeeded[key])
        self.assertEqual(
            ["draft", "validating", "ready", "previewing", "succeeded"],
            [
                item["status"]
                for item in succeeded["extensions"][
                    "cam.flow.api/state_history"
                ]["value"]
            ],
        )

    def test_hash_mismatch_is_rejected_before_preview_plan_is_saved(self) -> None:
        fixture = register_fixture(self.service)
        request = copy.deepcopy(fixture["preview_request"])
        request["plan_id"] = "preview:nx:api:wrong-hash"
        request["hashes"]["semantic_hash"] = "sha256:" + ("8" * 64)
        with self.assertRaises(FlowConflictError) as caught:
            self.service.create_preview_plan(request)
        self.assertEqual("PREVIEW_HASH_MISMATCH", caught.exception.code)
        with self.assertRaises(FlowServiceError) as missing:
            self.service.get_preview_plan(request["plan_id"])
        self.assertEqual("PREVIEW_PLAN_NOT_FOUND", missing.exception.code)

    def test_permission_revocation_cancels_without_auto_resume(self) -> None:
        fixture = register_fixture(self.service)
        ready = self.service.create_preview_plan(fixture["preview_request"])
        revoked = self.service.revoke_fixture_permission(
            fixture["target"]["target_instance_id"],
            "read:selected-files",
        )
        self.assertEqual([ready["plan_id"]], revoked["cancelled_plan_ids"])
        self.assertEqual(
            "cancelled",
            self.service.get_preview_plan(ready["plan_id"])["status"],
        )

        granted = self.service.grant_fixture_permission(
            fixture["target"]["target_instance_id"],
            "read:selected-files",
        )
        self.assertFalse(granted["auto_resumed"])
        self.assertEqual([], granted["resumed_plan_ids"])
        self.assertEqual(
            "cancelled",
            self.service.get_preview_plan(ready["plan_id"])["status"],
        )

    def test_cancellation_and_deadline_are_cooperative_and_structured(self) -> None:
        fixture = register_fixture(self.service)
        ready = self.service.create_preview_plan(fixture["preview_request"])
        token = CancellationToken()
        token.cancel("operator_cancelled")
        with self.assertRaises(FlowCancelledError) as cancelled:
            self.service.execute_preview_plan(
                ready["plan_id"],
                context=FlowRequestContext(cancellation=token),
            )
        self.assertEqual("REQUEST_CANCELLED", cancelled.exception.code)
        self.assertEqual(
            "ready",
            self.service.get_preview_plan(ready["plan_id"])["status"],
        )

        with self.assertRaises(FlowDeadlineExceeded):
            self.service.test_graph(
                fixture["graph"]["graph_id"],
                context=FlowRequestContext(deadline=self.clock.now()),
            )

    def test_unexpected_preview_failure_reaches_failed_without_execution(self) -> None:
        fixture = register_fixture(self.service)
        ready = self.service.create_preview_plan(fixture["preview_request"])

        def fail_rights_check(_graph: object) -> None:
            raise RuntimeError("fixture failure")

        self.service._require_asset_preview_rights = fail_rights_check
        with self.assertRaises(RuntimeError):
            self.service.execute_preview_plan(ready["plan_id"])

        failed = self.service.get_preview_plan(ready["plan_id"])
        self.assertEqual("failed", failed["status"])
        for key, expected in ZERO_EXECUTION_FIELDS.items():
            self.assertEqual(expected, failed[key])

    def test_concurrent_duplicate_requests_materialize_one_graph_revision(self) -> None:
        fixture = load_fixture()
        self.service.register_capability(fixture["manifest"])

        def save() -> str:
            return self.service.save_graph(
                fixture["graph"],
                idempotency_key="concurrent-graph",
            )["revision_id"]

        with ThreadPoolExecutor(max_workers=8) as executor:
            revisions = list(executor.map(lambda _: save(), range(32)))
        self.assertEqual({"revision:nx:api:1"}, set(revisions))
        self.assertEqual(
            1,
            self.service.list_graph_revisions(
                fixture["graph"]["graph_id"]
            ).returned_count,
        )


if __name__ == "__main__":
    unittest.main()
