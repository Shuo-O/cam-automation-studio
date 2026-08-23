from __future__ import annotations

import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path

from cam_automation.adapters.powermill_macro import FixturePowerMillTransport
from cam_automation.command_tasks import CommandTask, CommandTaskService
from cam_automation.recipes import (
    REQUIRED_PRODUCTION_GATES,
    PreviewRequest,
    Recipe,
    RecipeParameter,
    RecipeService,
    RecipeStep,
    compute_recipe_hash,
)
from cam_automation.sessions import EventRef


NOW = "2026-08-24T00:00:00Z"


def instance(
    instance_id: str,
    *,
    product: str = "nx",
    version: str = "NX 2406",
    project: str = "project-a",
    connection: str = "connected",
) -> dict:
    return {
        "instance_id": instance_id,
        "product": product,
        "target_version": version,
        "project_id": project,
        "connection_status": connection,
        "last_seen_at": NOW,
    }


def task(task_id: str, instance_id: str = "instance-a", **overrides: object) -> dict:
    value = {
        "schema_version": 1,
        "task_id": task_id,
        "task_type": "query",
        "execution_mode": "read_only",
        "product": "nx",
        "target_version": "NX 2406",
        "target_instance_id": instance_id,
        "project_id": "project-a",
        "recipe_hash": None,
        "operation": "nx.session.describe",
        "arguments": {},
        "status": "queued",
        "submitted_at": NOW,
        "timeout_ms": 1000,
        "requested_by": "operator:test",
        "review": None,
    }
    value.update(overrides)
    return value


class ControlledTransport:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls: list[tuple[str, int | None]] = []
        self.active_by_instance: dict[str, int] = {}
        self.max_by_instance: dict[str, int] = {}
        self.global_active = 0
        self.max_global_active = 0
        self.closed = False
        self.controls: dict[str, dict[str, object]] = {}

    def control(self, task_id: str, **values: object) -> None:
        self.controls[task_id] = values

    def query(
        self,
        instance_id: str,
        operation: str,
        arguments: dict,
        *,
        task_id: str,
    ) -> dict:
        control = self.controls.get(task_id, {})
        with self.lock:
            self.calls.append((instance_id, arguments.get("index")))
            active = self.active_by_instance.get(instance_id, 0) + 1
            self.active_by_instance[instance_id] = active
            self.max_by_instance[instance_id] = max(
                active,
                self.max_by_instance.get(instance_id, 0),
            )
            self.global_active += 1
            self.max_global_active = max(self.max_global_active, self.global_active)
        try:
            barrier = control.get("barrier")
            if barrier is not None:
                barrier.wait(1)
            started = control.get("started")
            if started is not None:
                started.set()
            gate = control.get("gate")
            if gate is not None:
                gate.wait(1)
            delay = float(arguments.get("delay", 0))
            if delay:
                time.sleep(delay)
            error = arguments.get("error")
            if error == "runtime":
                raise RuntimeError("fixture transport failure")
            if error == "disconnect":
                raise ConnectionError("fixture disconnected")
            if error == "sensitive":
                raise RuntimeError(
                    r"password=do-not-store at C:\Users\operator\secret.prt"
                )
            target_instance = arguments.get("response_instance", instance_id)
            return {
                "schema_version": 1,
                "response_id": f"response:{task_id}",
                "task_id": task_id,
                "product": "nx",
                "target_version": "NX 2406",
                "target_instance_id": target_instance,
                "project_id": "project-a",
                "status": "succeeded",
                "started_at": NOW,
                "completed_at": NOW,
                "duration_ms": 0,
                "raw_response": arguments.get("raw_response", "fixture response"),
                "structured_response": {
                    "index": arguments.get("index"),
                    "secret": arguments.get("secret"),
                    "path": arguments.get("path"),
                },
                "diff_report": None,
                "error": None,
            }
        finally:
            with self.lock:
                self.active_by_instance[instance_id] -= 1
                self.global_active -= 1

    def close(self) -> None:
        self.closed = True


class PreviewAdapter:
    product = "nx"
    target_version = "NX 2406"
    target_instance_id = "instance-a"
    project_id = "project-a"
    reviewer = "operator:test"

    def __init__(self) -> None:
        self.calls: list[PreviewRequest] = []

    def preview(self, request: PreviewRequest) -> dict:
        self.calls.append(request)
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
            "summary": "Fixture preview only.",
        }


def reviewed_recipe() -> Recipe:
    parameter = RecipeParameter(
        name="tolerance",
        value_type="number",
        required=True,
        default=0.02,
        samples=(0.02,),
        description="Reviewed tolerance.",
        source_event_refs=(EventRef("source-a", 0),),
    )
    step = RecipeStep(
        step_id="step-001",
        order=1,
        action="cam.operation.create",
        enabled=True,
        risk="review",
        review_status="accepted",
        arguments={"tolerance": {"parameter": "tolerance"}},
        condition=None,
        source_event_refs=(EventRef("source-a", 0),),
    )
    value = Recipe(
        recipe_id="recipe:nx:command-task-test",
        recipe_hash="",
        name="Command task preview",
        product="nx",
        status="approved_for_simulation",
        target_versions=("NX 2406",),
        source_session_ids=("session-a",),
        support={"matched_sessions": 1, "total_sessions": 1, "ratio": 1.0},
        parameters=(parameter,),
        steps=(step,),
        required_gates=REQUIRED_PRODUCTION_GATES,
        created_at=NOW,
        updated_at=NOW,
        project_conditions={"test_copy_required": True},
    )
    return replace(value, recipe_hash=compute_recipe_hash(value))


class CommandTaskServiceTests(unittest.TestCase):
    def service(
        self,
        transport: ControlledTransport | None = None,
        **kwargs: object,
    ) -> tuple[CommandTaskService, ControlledTransport]:
        current = transport or ControlledTransport()
        instances = kwargs.pop(
            "instances",
            {
                "instance-a": instance("instance-a"),
                "instance-b": instance("instance-b"),
            },
        )
        service = CommandTaskService(
            {"nx": current},
            instances=instances,
            timeout_poll_interval=0.002,
            **kwargs,
        )
        self.addCleanup(service.close)
        return service, current

    def test_two_instances_parallel_and_each_instance_serial(self) -> None:
        service, transport = self.service()
        barrier = threading.Barrier(2)
        transport.control("parallel-a", barrier=barrier)
        transport.control("parallel-b", barrier=barrier)
        service.submit(task("parallel-a"))
        service.submit(task("parallel-b", "instance-b"))

        self.assertEqual("succeeded", service.wait("parallel-a", 2).status)
        self.assertEqual("succeeded", service.wait("parallel-b", 2).status)
        self.assertGreaterEqual(transport.max_global_active, 2)
        self.assertEqual(1, transport.max_by_instance["instance-a"])
        self.assertEqual(1, transport.max_by_instance["instance-b"])

    def test_same_instance_fifty_five_tasks_remain_ordered_and_serial(self) -> None:
        service, transport = self.service(result_cache_size=80)
        for index in range(55):
            service.submit(
                task(
                    f"queue-{index:02d}",
                    arguments={"index": index, "delay": 0.001},
                )
            )

        for index in range(55):
            self.assertEqual(
                "succeeded",
                service.wait(f"queue-{index:02d}", 3).status,
            )
        self.assertEqual(
            list(range(55)),
            [index for instance_id, index in transport.calls if instance_id == "instance-a"],
        )
        self.assertEqual(1, transport.max_by_instance["instance-a"])

    def test_required_target_fields_and_exact_target_binding(self) -> None:
        missing = task("missing")
        del missing["target_instance_id"]
        with self.assertRaisesRegex(ValueError, "target_instance_id"):
            CommandTask.from_dict(missing)

        service, transport = self.service()
        wrong_version = service.submit(task("wrong-version", target_version="NX 2312"))
        wrong_project = service.submit(task("wrong-project", project_id="project-b"))
        wrong_product = service.submit(
            task(
                "wrong-product",
                product="powermill",
                target_version="NX 2406",
                operation="powermill.project.info",
            )
        )
        self.assertEqual("rejected", wrong_version.status)
        self.assertEqual("rejected", wrong_project.status)
        self.assertEqual("rejected", wrong_product.status)
        self.assertEqual([], transport.calls)

    def test_submit_is_idempotent_and_conflicts_fail(self) -> None:
        service, transport = self.service()
        payload = task("idem", idempotency_key="same-operation")
        first = service.submit(payload)
        second = service.submit(payload)
        self.assertEqual(first.task_id, second.task_id)
        self.assertEqual("succeeded", service.wait("idem", 2).status)
        self.assertEqual(1, len(transport.calls))

        equivalent = task(
            "idem-alias",
            idempotency_key="same-operation",
            submitted_at="2026-08-24T00:00:01Z",
        )
        self.assertEqual("idem", service.submit(equivalent).task_id)
        conflict = task(
            "idem-conflict",
            idempotency_key="same-operation",
            operation="nx.part.describe",
        )
        with self.assertRaisesRegex(ValueError, "payload conflict"):
            service.submit(conflict)

    def test_cancel_queued_running_and_completed_tasks(self) -> None:
        service, transport = self.service()
        first_started = threading.Event()
        gate = threading.Event()
        transport.control("cancel-running", started=first_started, gate=gate)
        service.submit(task("cancel-running"))
        self.assertTrue(first_started.wait(1))
        service.submit(task("cancel-queued"))
        self.assertEqual("cancelled", service.cancel("cancel-queued").status)
        self.assertEqual("cancelled", service.cancel("cancel-running").status)
        gate.set()

        service.submit(task("complete-before-cancel"))
        complete = service.wait("complete-before-cancel", 2)
        self.assertEqual("succeeded", complete.status)
        self.assertEqual(
            "succeeded",
            service.cancel("complete-before-cancel").status,
        )

    def test_queue_and_execution_timeouts_discard_late_results(self) -> None:
        service, transport = self.service()
        gate = threading.Event()
        started = threading.Event()
        transport.control("timeout-running", gate=gate, started=started)
        service.submit(
            task(
                "timeout-running",
                execution_timeout_ms=20,
            )
        )
        self.assertTrue(started.wait(1))
        service.submit(
            task(
                "timeout-queued",
                queue_timeout_ms=20,
                execution_timeout_ms=500,
            )
        )
        self.assertEqual("timed_out", service.wait("timeout-running", 1).status)
        self.assertEqual("timed_out", service.wait("timeout-queued", 1).status)
        gate.set()
        time.sleep(0.03)
        self.assertEqual("timed_out", service.get("timeout-running").status)

    def test_disconnect_reconnect_and_transport_exceptions_do_not_replay(self) -> None:
        descriptors = {"instance-a": instance("instance-a", connection="disconnected")}
        service, transport = self.service(instances=descriptors)
        service.submit(task("disconnected-first"))
        self.assertEqual(
            "disconnected",
            service.wait("disconnected-first", 1).status,
        )
        descriptors["instance-a"]["connection_status"] = "connected"
        service.submit(task("after-reconnect"))
        self.assertEqual("succeeded", service.wait("after-reconnect", 1).status)
        self.assertEqual(1, len(transport.calls))

        service.submit(task("transport-error", arguments={"error": "runtime"}))
        service.submit(task("transport-disconnect", arguments={"error": "disconnect"}))
        self.assertEqual("failed", service.wait("transport-error", 1).status)
        self.assertEqual(
            "disconnected",
            service.wait("transport-disconnect", 1).status,
        )

    def test_instance_provider_failure_does_not_kill_the_instance_worker(self) -> None:
        transport = ControlledTransport()
        descriptor = instance("instance-a")
        call_count = 0

        def provider(_: str) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("provider fixture failure")
            return descriptor

        service = CommandTaskService(
            {"nx": transport},
            instance_provider=provider,
            timeout_poll_interval=0.002,
        )
        self.addCleanup(service.close)
        service.submit(task("provider-fails"))
        failed = service.wait("provider-fails", 1)
        self.assertEqual("failed", failed.status)
        self.assertEqual("worker_error", failed.error["code"])

        service.submit(task("provider-recovers"))
        self.assertEqual("succeeded", service.wait("provider-recovers", 1).status)

    def test_response_attribution_and_machine_output_are_fail_closed(self) -> None:
        service, _ = self.service()
        service.submit(
            task(
                "wrong-owner",
                arguments={"response_instance": "instance-b"},
            )
        )
        service.submit(
            task(
                "machine-output",
                arguments={"raw_response": "N10 G01 X1.0"},
            )
        )
        owner = service.wait("wrong-owner", 1)
        output = service.wait("machine-output", 1)
        self.assertEqual("failed", owner.status)
        self.assertEqual("response_attribution_mismatch", owner.error["code"])
        self.assertEqual("rejected", output.status)
        self.assertIsNone(output.response.raw_response)

    def test_preview_revalidates_review_recipe_hash_and_target(self) -> None:
        adapter = PreviewAdapter()
        recipe_service = RecipeService(adapters={"nx": adapter})
        version = recipe_service.save(reviewed_recipe())
        service, _ = self.service(recipe_service=recipe_service)
        preview = task(
            "preview-ok",
            task_type="recipe_preview",
            execution_mode="dry_run",
            recipe_hash=version.recipe_hash,
            operation="cam.recipe.preview",
            arguments={"parameters": {"tolerance": 0.015}},
            review={
                "status": "accepted",
                "reviewer": "operator:test",
                "scope": "dry_run_only",
            },
        )
        service.submit(preview)
        result = service.wait("preview-ok", 2)
        self.assertEqual("succeeded", result.status)
        self.assertEqual("changes_detected", result.response.diff_report["status"])
        gates = {
            item["gate"]: item["status"]
            for item in result.response.diff_report["gate_results"]
        }
        self.assertEqual("not_run", gates["cam_simulation"])
        self.assertEqual("required", gates["collision_check"])
        self.assertEqual(1, len(adapter.calls))

        tampered = dict(preview)
        tampered["task_id"] = "preview-tampered"
        tampered["recipe_hash"] = "sha256:" + ("0" * 64)
        self.assertEqual("rejected", service.submit(tampered).status)
        missing_review = dict(preview)
        missing_review["task_id"] = "preview-unreviewed"
        missing_review["review"] = {
            "status": "unreviewed",
            "reviewer": "",
            "scope": "dry_run_only",
        }
        self.assertEqual("rejected", service.submit(missing_review).status)

    def test_audit_is_execution_mode_and_redacts_paths_and_credentials(self) -> None:
        sink: list[dict] = []
        service, _ = self.service(audit_sink=sink)
        service.submit(
            task(
                "audit",
                arguments={
                    "path": r"C:\Users\operator\secret.prt",
                    "password": "do-not-store",
                },
            )
        )
        self.assertEqual("succeeded", service.wait("audit", 1).status)
        service.submit(task("audit-error", arguments={"error": "sensitive"}))
        self.assertEqual("failed", service.wait("audit-error", 1).status)
        service.close()
        audits = service.audit_events()
        self.assertGreaterEqual(len(audits), 3)
        terminal = audits[-1]
        self.assertEqual("execution_audit", terminal["source_mode"])
        self.assertEqual("automation", terminal["mode"])
        rendered = str(audits)
        self.assertNotIn(r"C:\Users\operator", rendered)
        self.assertNotIn("do-not-store", rendered)
        self.assertIn("<redacted-local-path>", rendered)
        self.assertTrue(sink)

    def test_bounded_result_cache_and_close_release_resources(self) -> None:
        service, transport = self.service(result_cache_size=2)
        for index in range(3):
            service.submit(task(f"evict-{index}"))
            self.assertEqual("succeeded", service.wait(f"evict-{index}", 1).status)
        with self.assertRaises(KeyError):
            service.get("evict-0")
        calls_before_retry = len(transport.calls)
        with self.assertRaisesRegex(KeyError, "will not be replayed"):
            service.submit(task("evict-0"))
        self.assertEqual(calls_before_retry, len(transport.calls))

        gate = threading.Event()
        started = threading.Event()
        transport.control("close-running", gate=gate, started=started)
        service.submit(task("close-running"))
        self.assertTrue(started.wait(1))
        service.submit(task("close-queued"))
        service.close()
        self.assertEqual("cancelled", service.get("close-running").status)
        self.assertEqual("cancelled", service.get("close-queued").status)
        self.assertTrue(transport.closed)
        with self.assertRaises(RuntimeError):
            service.submit(task("after-close"))
        gate.set()

    def test_contract_snapshots_are_immutable_and_power_mill_fixture_is_adapted(self) -> None:
        service, _ = self.service()
        submitted = service.submit(task("immutable", arguments={"nested": {"value": 1}}))
        with self.assertRaises(TypeError):
            submitted.task.arguments["other"] = 2
        with self.assertRaises(TypeError):
            submitted.task.arguments["nested"]["value"] = 2
        self.assertEqual("succeeded", service.wait("immutable", 1).status)

        root = Path(__file__).resolve().parents[1]
        descriptor = instance(
            "powermill:fixture:A",
            product="powermill",
            version="PowerMill 2026",
            project="pm-a",
        )
        transport = FixturePowerMillTransport.from_file(
            root / "plugins" / "powermill-cam-copilot" / "fixtures" / "transport.json"
        )
        transport.connect(descriptor)
        pm_service = CommandTaskService(
            {"powermill": transport},
            instances={"powermill:fixture:A": descriptor},
        )
        self.addCleanup(pm_service.close)
        pm_service.submit(
            task(
                "pm-fixture-query",
                "powermill:fixture:A",
                product="powermill",
                target_version="PowerMill 2026",
                project_id="pm-a",
                operation="powermill.project.info",
            )
        )
        response = pm_service.wait("pm-fixture-query", 1).response
        self.assertEqual("succeeded", response.status)
        self.assertEqual("A", response.structured_response["fixture_marker"])

    def test_dangerous_command_variants_are_rejected_before_transport(self) -> None:
        service, transport = self.service(result_cache_size=32)
        variants = [
            "N C P R O G R A M   W R I T E",
            "POST%20PROCESS",
            "UG9zdFByb2Nlc3M=",
            "4e313020473031205831",
            r"\u0047\u0030\u0031 X1",
            "N10 G01 X1.0",
            "Ｇ ０ １ X1.0",
            "MACHINE   JOG",
            "PROJECT SAVE",
            "machine-ready output",
        ]
        for index, payload in enumerate(variants):
            with self.subTest(payload=payload):
                status = service.submit(
                    task(
                        f"danger-{index}",
                        arguments={"payload": payload},
                    )
                )
                self.assertEqual("rejected", status.status)
        encoded_operation = service.submit(
            task(
                "encoded-operation",
                operation="nx%2Esession%2Edescribe",
            )
        )
        self.assertEqual("rejected", encoded_operation.status)
        self.assertEqual([], transport.calls)


if __name__ == "__main__":
    unittest.main()
