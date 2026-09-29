from __future__ import annotations

import unittest

from cam_automation.codex_bridge import validate_review
from cam_automation.connection_monitor import InstanceDescriptor
from cam_automation.integrations import (
    ApiServices,
    CommandTaskService,
    DiagnosticsService,
    FlowIntegrationError,
    OfflineFlowIntegration,
    analyze,
    capability_manifest,
    connection_statuses,
    plugin_api_status,
    service_payload,
)
from cam_automation.sample import SAMPLE_LOG


class _TaskService:
    def submit(self, task):
        return task

    def get(self, task_id):
        return {"task_id": task_id}

    def cancel(self, task_id):
        return {"task_id": task_id, "status": "cancelled"}


class _DiagnosticsService:
    def snapshot(self):
        return {"queue_depth": 0}


class _ConnectionService:
    def list_instances(self, *, refresh=True):
        return [
            InstanceDescriptor(
                instance_id=f"{product}:fixture:{suffix}",
                product=product,
                pid=pid,
                process_name="ugraf.exe" if product == "nx" else "PowerMill.exe",
                window_handle=f"0x{pid:X}",
                window_title=f"Fixture {suffix}",
                is_foreground=suffix == "A",
                window_state="foreground" if suffix == "A" else "visible",
                connection_status=status,
            )
            for product, pid, suffix, status in (
                ("nx", 101, "A", "connected"),
                ("nx", 102, "B", "disconnected"),
                ("powermill", 201, "A", "connected"),
                ("powermill", 202, "B", "detected"),
            )
        ]


class IntegrationTests(unittest.TestCase):
    def test_powermill_uses_unified_analysis_shape(self) -> None:
        result = analyze(product="powermill", source=SAMPLE_LOG, name="pm-review")

        self.assertEqual("powermill", result["product"])
        self.assertEqual("powermill_macro", result["output"]["kind"])
        self.assertEqual("cam.codex.bridge.v1", result["codex_context"]["protocol"])
        self.assertTrue(result["activity_events"])

    def test_nx_journal_is_static_and_dry_run(self) -> None:
        source = """
import NXOpen
session = NXOpen.Session.GetSession()
part = session.Parts.Work
operation = part.CAMSetup.CAMOperationCollection.Create("mill_planar")
operation.GenerateToolPath()
"""
        result = analyze(product="nx", source=source, source_format="nx_journal")

        self.assertEqual("nx", result["recipe"]["profile"])
        self.assertEqual("nx_preview", result["output"]["kind"])
        self.assertEqual("dry-run", result["adapter"]["execution_mode"])
        self.assertGreaterEqual(result["parse"]["commands"], 2)
        self.assertEqual(
            result["parse"]["commands"],
            len(result["activity_events"]),
        )
        self.assertTrue(
            all(
                event["mode"] == "manual"
                and event["product"] == "nx"
                for event in result["activity_events"]
            )
        )
        self.assertIn("does not import NXOpen", result["output"]["text"])

    def test_cimatron_is_static_evidence_only(self) -> None:
        result = analyze(
            product="cimatron",
            source=(
                'application.Documents.Open("part.elt")\n'
                "postprocess.Run()\n"
            ),
        )
        self.assertEqual("cimatron_evidence_report", result["output"]["kind"])
        self.assertFalse(result["adapter"]["source_execution"])
        cimatron = next(
            item for item in capability_manifest()["products"]
            if item["key"] == "cimatron"
        )
        self.assertFalse(cimatron["flow_import_supported"])
        self.assertFalse(cimatron["command_tasks_supported"])
        self.assertEqual(
            {"cam.source.call"},
            {event["action"] for event in result["activity_events"]},
        )
        self.assertEqual("blocked", result["activity_events"][1]["params"]["risk"])
        with self.assertRaises(FlowIntegrationError) as raised:
            OfflineFlowIntegration(None).import_source(
                product="cimatron",
                source=b"application.Documents.Open('part.elt')",
                source_name="probe.py",
            )
        self.assertEqual("CAPABILITY_UNAVAILABLE", raised.exception.code)

    def test_unknown_product_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported product"):
            analyze(product="autocad", source="ignored")

    def test_manifest_and_review_validation_are_explicit(self) -> None:
        manifest = capability_manifest()
        self.assertEqual("dry-run", manifest["execution_mode"])
        self.assertEqual(
            {"nx", "powermill", "cimatron"},
            {item["key"] for item in manifest["products"]},
        )
        self.assertEqual(
            "approved_for_simulation",
            validate_review(
                {
                    "review_status": "approved_for_simulation",
                    "findings": ["check tool and holder"],
                    "required_gates": ["collision_check"],
                }
            )["review_status"],
        )

    def test_empty_plugin_set_exposes_only_the_core(self) -> None:
        manifest = capability_manifest(set())

        self.assertEqual("CAM Automation Studio Core", manifest["module"])
        self.assertEqual([], manifest["products"])
        self.assertEqual("unavailable", manifest["execution_mode"])
        self.assertFalse(manifest["capture"]["installed"])
        self.assertFalse(manifest["codex"]["installed"])
        self.assertEqual([], connection_statuses(set()))

    def test_connection_statuses_keep_all_three_bridges_visible(self) -> None:
        keys = {item["key"] for item in connection_statuses()}
        self.assertEqual({"codex", "nx", "powermill", "cimatron"}, keys)

    def test_connection_statuses_can_include_uninstalled_bridges(self) -> None:
        keys = {
            item["key"]
            for item in connection_statuses(set(), include_uninstalled=True)
        }
        self.assertEqual({"codex", "nx", "powermill", "cimatron"}, keys)

    def test_connection_statuses_expose_multiple_product_instances(self) -> None:
        statuses = connection_statuses(
            {"ug-cam-copilot", "powermill-cam-copilot"},
            capture_status={
                "state": "recording",
                "consent": True,
                "instances": {
                    "nx": [
                        {
                            "instance_id": "nx:101:A",
                            "pid": 101,
                            "window_title": "Part A - NX",
                            "is_foreground": True,
                        },
                        {
                            "instance_id": "nx:102:B",
                            "pid": 102,
                            "window_title": "Part B - NX",
                            "is_foreground": False,
                        },
                    ],
                    "powermill": [
                        {
                            "instance_id": "powermill:201:C",
                            "pid": 201,
                            "window_title": "Project C - PowerMill",
                            "is_foreground": False,
                        }
                    ],
                },
            },
        )
        by_key = {item["key"]: item for item in statuses}

        self.assertEqual("connected", by_key["nx"]["status"])
        self.assertEqual(2, by_key["nx"]["instance_count"])
        self.assertEqual(2, by_key["nx"]["process_count"])
        self.assertEqual("nx:101:A", by_key["nx"]["active_instance_id"])
        self.assertEqual(1, by_key["powermill"]["instance_count"])

    def test_service_injection_protocols_are_stable_and_structural(self) -> None:
        tasks = _TaskService()
        diagnostics = _DiagnosticsService()
        services = ApiServices(commands=tasks, diagnostics=diagnostics)

        self.assertIs(services.commands, tasks)
        self.assertIsInstance(tasks, CommandTaskService)
        self.assertIsInstance(diagnostics, DiagnosticsService)

    def test_injected_connections_keep_all_instances_without_selection(self) -> None:
        statuses = connection_statuses(
            {"ug-cam-copilot", "powermill-cam-copilot"},
            connection_service=_ConnectionService(),
            refresh=True,
        )
        by_key = {item["key"]: item for item in statuses}

        self.assertEqual(2, by_key["nx"]["instance_count"])
        self.assertEqual(2, by_key["powermill"]["instance_count"])
        self.assertIsNone(by_key["nx"]["selected_instance_id"])
        self.assertTrue(by_key["nx"]["selection_required"])
        self.assertEqual("nx:fixture:A", by_key["nx"]["foreground_instance_id"])

    def test_plugin_status_exposes_update_dependencies_and_local_authorization(self) -> None:
        status = plugin_api_status(
            {
                "schema_version": 1,
                "core": {},
                "installed_count": 1,
                "available_count": 2,
                "features": [],
                "plugins": [
                    {
                        "id": "cam-local-capture",
                        "version": "1.0.0",
                        "installed": True,
                        "enabled": True,
                        "dependencies": [],
                        "consent_reversible": True,
                    },
                    {
                        "id": "dependent",
                        "version": "1.0.0",
                        "installed": False,
                        "enabled": False,
                        "dependencies": ["cam-local-capture"],
                    },
                ],
            },
            recorder_status={
                "consent": False,
                "consent_source": "revoked",
                "redaction": "redacted-local-v1",
                "categories": {
                    "logs": False,
                    "instances": False,
                    "execution_audit": False,
                },
            },
        )
        capture = status["plugins"][0]
        dependent = status["plugins"][1]

        self.assertEqual("ON_INSTALL", capture["authorization"]["policy"])
        self.assertTrue(capture["authorization"]["revoked"])
        self.assertTrue(
            all(
                item["revoked"]
                for item in capture["authorization"]["categories"]
                if item["classification"] == "advanced"
            )
        )
        self.assertFalse(capture["local_data"]["uploads_enabled"])
        self.assertFalse(capture["update"]["available"])
        self.assertTrue(dependent["dependency_status"][0]["installed"])

    def test_service_payload_redacts_raw_local_paths(self) -> None:
        payload = service_payload(
            {
                "source_file": r"C:\Users\Operator\secret\journal.log",
                "message": r"failed to read C:\Users\Operator\secret\journal.log",
                "alias": "nx-log-001",
            }
        )

        serialized = str(payload)
        self.assertNotIn("Operator", serialized)
        self.assertIn("<REDACTED_LOCAL_PATH>", serialized)
        self.assertEqual("nx-log-001", payload["alias"])


if __name__ == "__main__":
    unittest.main()
