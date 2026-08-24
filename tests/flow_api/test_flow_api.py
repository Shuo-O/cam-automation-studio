from __future__ import annotations

import copy
import tempfile
import unittest

from cam_automation.flow_api import (
    FLOW_ROUTES,
    FlowApi,
    FlowApiRequest,
    register_flow_routes,
)
from cam_automation.flow_service import CancellationToken

from tests.flow_api._support import (
    FakeClock,
    build_service,
    load_fixture,
    register_fixture,
    second_revision,
)


class FlowApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.clock = FakeClock()
        self.service = build_service(self.directory.name, clock=self.clock)
        self.api = FlowApi(self.service)
        self.fixture = load_fixture()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: object = None,
        headers: dict[str, str] | None = None,
        **kwargs: object,
    ):
        return self.api.handle(
            FlowApiRequest(
                method=method,
                path=path,
                body=body,
                headers=headers or {},
                correlation_id="corr:test-flow-api",
                **kwargs,
            )
        )

    def prepare_through_api(self) -> None:
        asset = self.request(
            "POST",
            "/api/flow/assets",
            body=self.fixture["asset_request"],
            headers={"Idempotency-Key": "asset-fixture"},
        )
        self.assertEqual(201, asset.status)
        capability = self.request(
            "POST",
            "/api/flow/capabilities",
            body={"manifest": self.fixture["manifest"]},
            headers={"Idempotency-Key": "capability-fixture"},
        )
        self.assertEqual(201, capability.status)
        graph = self.request(
            "POST",
            "/api/flow/graphs",
            body={"graph": self.fixture["graph"]},
            headers={"Idempotency-Key": "graph-fixture"},
        )
        self.assertEqual(201, graph.status)
        version = self.request(
            "POST",
            "/api/flow/versions",
            body=self.fixture["version_request"],
            headers={"Idempotency-Key": "version-fixture"},
        )
        self.assertEqual(201, version.status)

    def assert_problem(self, response, status: int, code: str) -> None:
        self.assertEqual(status, response.status)
        self.assertEqual(1, response.body["schema_version"])
        self.assertEqual(code, response.body["error"]["code"])
        self.assertEqual(
            "corr:test-flow-api",
            response.body["error"]["correlation_id"],
        )
        self.assertEqual(
            "corr:test-flow-api",
            response.headers["X-Correlation-ID"],
        )

    def test_single_registration_entry_exposes_route_neutral_handlers(self) -> None:
        calls: list[tuple[str, str, object]] = []

        class Registrar:
            def add_route(self, method: str, path: str, handler: object) -> None:
                calls.append((method, path, handler))

        api = register_flow_routes(self.service, Registrar())
        self.assertIsInstance(api, FlowApi)
        self.assertEqual(len(FLOW_ROUTES), len(calls))
        self.assertTrue(all(callable(item[2]) for item in calls))
        self.assertIn(
            ("POST", "/api/flow/preview-plans"),
            {(item[0], item[1]) for item in calls},
        )

    def test_full_fixture_api_reaches_succeeded_zero_execution_plan(self) -> None:
        self.prepare_through_api()
        compatibility = self.request(
            "POST",
            "/api/flow/compatibility",
            body={
                "graph_id": self.fixture["graph"]["graph_id"],
                "revision_id": self.fixture["graph"]["revision_id"],
                "target": self.fixture["target"],
            },
        )
        self.assertEqual(200, compatibility.status)
        self.assertTrue(
            compatibility.body["compatibility"]["preview_eligible"]
        )

        preview = self.request(
            "POST",
            "/api/flow/preview-plans",
            body=self.fixture["preview_request"],
            headers={"Idempotency-Key": "preview-fixture"},
        )
        self.assertEqual(201, preview.status)
        self.assertEqual("ready", preview.body["preview_plan"]["status"])
        self.assertEqual("none", preview.body["preview_plan"]["transport"])

        executed = self.request(
            "POST",
            "/api/flow/preview-plans/preview:nx:api:1/run",
        )
        self.assertEqual(200, executed.status)
        plan = executed.body["preview_plan"]
        self.assertEqual("succeeded", plan["status"])
        self.assertEqual(0, plan["commands_sent"])
        self.assertFalse(plan["journal_executed"])
        self.assertFalse(plan["macro_executed"])
        self.assertEqual(0, plan["machine_output_count"])

    def test_etag_supports_conditional_get_and_stale_if_match_conflict(self) -> None:
        self.prepare_through_api()
        fetched = self.request(
            "GET",
            "/api/flow/graphs/graph:nx:api",
        )
        self.assertEqual(200, fetched.status)
        etag = fetched.headers["ETag"]
        not_modified = self.request(
            "GET",
            "/api/flow/graphs/graph:nx:api",
            headers={"If-None-Match": etag},
        )
        self.assertEqual(304, not_modified.status)
        self.assertIsNone(not_modified.body)
        wildcard = self.request(
            "GET",
            "/api/flow/graphs/graph:nx:api",
            headers={"If-None-Match": "*"},
        )
        self.assertEqual(304, wildcard.status)
        weak = self.request(
            "GET",
            "/api/flow/graphs/graph:nx:api",
            headers={"If-None-Match": f'W/{etag}, "another"'},
        )
        self.assertEqual(304, weak.status)

        revision = second_revision(self.fixture)
        updated = self.request(
            "POST",
            "/api/flow/graphs",
            body={
                "graph": revision,
                "expected_revision_id": "revision:nx:api:1",
            },
            headers={"If-Match": etag},
        )
        self.assertEqual(201, updated.status)

        stale = copy.deepcopy(revision)
        stale["revision_id"] = "revision:nx:api:stale"
        stale["parent_revision_id"] = "revision:nx:api:1"
        conflict = self.request(
            "POST",
            "/api/flow/graphs",
            body={
                "graph": stale,
                "expected_revision_id": "revision:nx:api:1",
            },
            headers={"If-Match": etag},
        )
        self.assert_problem(conflict, 409, "FLOW_REVISION_CONFLICT")

    def test_list_cursor_is_stable_and_tampering_maps_to_400(self) -> None:
        first_asset = copy.deepcopy(self.fixture["asset_request"])
        second_asset = copy.deepcopy(self.fixture["asset_request"])
        second_asset["asset_id"] = "asset:nx:api:second"
        second_asset["content"] = "# second fixture\n"
        for index, asset in enumerate((first_asset, second_asset), start=1):
            response = self.request(
                "POST",
                "/api/flow/assets",
                body=asset,
                headers={"Idempotency-Key": f"asset-{index}"},
            )
            self.assertEqual(201, response.status)

        first = self.request(
            "GET",
            "/api/flow/assets?limit=1",
        )
        self.assertEqual(1, first.body["returned_count"])
        cursor = first.body["next_cursor"]
        second = self.request(
            "GET",
            f"/api/flow/assets?limit=1&cursor={cursor}",
        )
        self.assertEqual(1, second.body["returned_count"])
        self.assertFalse(second.body["has_more"])

        tampered = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
        invalid = self.request(
            "GET",
            f"/api/flow/assets?limit=1&cursor={tampered}",
        )
        self.assert_problem(invalid, 400, "FLOW_CURSOR_INVALID")

    def test_unknown_request_field_and_duplicate_json_key_map_to_400(self) -> None:
        unknown = self.request(
            "POST",
            "/api/flow/graphs",
            body={
                "graph": self.fixture["graph"],
                "unexpected": True,
            },
        )
        self.assert_problem(unknown, 400, "FLOW_REQUEST_INVALID")
        self.assertEqual(["unexpected"], unknown.body["error"]["details"]["fields"])

        duplicate = self.request(
            "POST",
            "/api/flow/test",
            body='{"graph_id":"first","graph_id":"second"}',
        )
        self.assert_problem(duplicate, 400, "FLOW_REQUEST_INVALID")

    def test_permission_revocation_maps_to_403_without_auto_resume(self) -> None:
        self.prepare_through_api()
        self.service.revoke_fixture_permission(
            self.fixture["target"]["target_instance_id"],
            "read:selected-files",
        )
        request = copy.deepcopy(self.fixture["preview_request"])
        request["plan_id"] = "preview:nx:api:permission-revoked"
        denied = self.request(
            "POST",
            "/api/flow/preview-plans",
            body=request,
        )
        self.assert_problem(denied, 403, "PREVIEW_PERMISSION_REVOKED")
        self.assertNotIn("target", denied.body["error"]["details"])
        self.assertEqual(
            "blocked",
            self.service.get_preview_plan(request["plan_id"])["status"],
        )

    def test_missing_resource_maps_to_404(self) -> None:
        missing = self.request(
            "GET",
            "/api/flow/graphs/graph:does-not-exist",
        )
        self.assert_problem(missing, 404, "FLOW_GRAPH_NOT_FOUND")

    def test_expired_deadline_maps_to_408(self) -> None:
        register_fixture(self.service)
        expired = self.request(
            "POST",
            "/api/flow/test",
            body={"graph_id": self.fixture["graph"]["graph_id"]},
            deadline=self.clock.now(),
        )
        self.assert_problem(expired, 408, "FLOW_DEADLINE_EXCEEDED")
        expired_get = self.request(
            "GET",
            "/api/flow/assets",
            deadline=self.clock.now(),
        )
        self.assert_problem(expired_get, 408, "FLOW_DEADLINE_EXCEEDED")

    def test_idempotency_payload_conflict_and_ambiguous_target_map_to_409(self) -> None:
        first = self.request(
            "POST",
            "/api/flow/capabilities",
            body={"manifest": self.fixture["manifest"]},
            headers={"Idempotency-Key": "same-capability"},
        )
        self.assertEqual(201, first.status)
        changed = copy.deepcopy(self.fixture["manifest"])
        changed["provider"]["display_name"] = "Changed fixture"
        conflict = self.request(
            "POST",
            "/api/flow/capabilities",
            body={"manifest": changed},
            headers={"Idempotency-Key": "same-capability"},
        )
        self.assert_problem(conflict, 409, "FLOW_IDEMPOTENCY_CONFLICT")

        register_fixture(self.service)
        ambiguous_target = copy.deepcopy(self.fixture["target"])
        ambiguous_target.pop("target_instance_id")
        ambiguous = self.request(
            "POST",
            "/api/flow/compatibility",
            body={
                "graph_id": self.fixture["graph"]["graph_id"],
                "target": ambiguous_target,
            },
        )
        self.assert_problem(ambiguous, 409, "PREVIEW_TARGET_AMBIGUOUS")

    def test_cancelled_request_maps_to_409_and_does_not_start_plan(self) -> None:
        register_fixture(self.service)
        ready = self.service.create_preview_plan(self.fixture["preview_request"])
        token = CancellationToken()
        token.cancel("operator_cancelled")
        cancelled = self.request(
            "POST",
            f"/api/flow/preview-plans/{ready['plan_id']}/run",
            cancellation=token,
        )
        self.assert_problem(cancelled, 409, "REQUEST_CANCELLED")
        self.assertEqual(
            "ready",
            self.service.get_preview_plan(ready["plan_id"])["status"],
        )

    def test_request_size_limit_maps_to_413(self) -> None:
        oversized = self.request(
            "POST",
            "/api/flow/test",
            body={"graph_id": "graph:nx:api"},
            body_size=self.api.max_request_bytes + 1,
        )
        self.assert_problem(oversized, 413, "RESOURCE_LIMIT_EXCEEDED")

    def test_unexpected_failure_maps_to_redacted_500(self) -> None:
        def explode(*args: object, **kwargs: object) -> None:
            raise RuntimeError("N10 G01 X1 secret machine payload")

        self.service.test_graph = explode
        failed = self.request(
            "POST",
            "/api/flow/test",
            body={"graph_id": "graph:nx:api"},
        )
        self.assert_problem(failed, 500, "FLOW_INTERNAL_ERROR")
        rendered = str(failed.body)
        self.assertNotIn("G01", rendered)
        self.assertNotIn("secret", rendered)


if __name__ == "__main__":
    unittest.main()
