from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cam_automation.manufacturing_context import (
    ManufacturingContextService,
    resolve_snapshot_selector,
)
from host_bridges import (
    NxOpenSnapshotExporter,
    NxReadOnlyBridge,
    PowerMillComReadOnlyExporter,
    PowerMillReadOnlyBridge,
)
from host_bridges._base import HostBridgeUnavailable


def _snapshot(**overrides):
    value = {
        "product": "powermill",
        "instance_id": "pm-fixture",
        "project_id": "demo-cavity",
        "target_version": "PowerMill 2026",
        "units": "mm",
        "material": "P20",
        "machine": {"axes": 3},
        "objects": [
            {
                "object_id": "cavity-1",
                "kind": "pocket",
                "name": "cavity",
                "attributes": {"role": "cavity", "zone": "upper"},
            },
            {
                "object_id": "cavity-2",
                "kind": "pocket",
                "name": "cavity",
                "attributes": {"role": "cavity", "zone": "lower"},
            },
        ],
    }
    value.update(overrides)
    return value


class ManufacturingContextTests(unittest.TestCase):
    def test_fixture_snapshot_is_persisted_and_hashes_objects_and_optional_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ManufacturingContextService(Path(directory))
            snapshot = service.import_snapshot(
                _snapshot(
                    source="fixture",
                    fixture_id="fixture:pm-demo",
                    tools=[{"tool_id": "T1", "diameter": 10}],
                    fixtures=[{"fixture_id": "vise-1"}],
                    geometry_digest="sha256:geometry",
                )
            )
            self.assertTrue(snapshot["snapshot_id"].startswith("snapshot-"))
            self.assertTrue(snapshot["content_hash"].startswith("sha256:"))
            self.assertTrue(snapshot["objects"][0]["object_hash"].startswith("sha256:"))
            self.assertIn("fixture snapshot", " ".join(snapshot["warnings"]))
            self.assertEqual(snapshot, service.get_snapshot(snapshot["snapshot_id"]))
            restarted = ManufacturingContextService(Path(directory))
            self.assertEqual([snapshot], restarted.list_snapshots())

            changed = restarted.import_snapshot(
                _snapshot(
                    source="fixture",
                    fixture_id="fixture:pm-demo-2",
                    tools=[{"tool_id": "T1", "diameter": 12}],
                )
            )
            self.assertNotEqual(snapshot["content_hash"], changed["content_hash"])
            self.assertEqual(snapshot, restarted.import_snapshot({**snapshot}))
            with self.assertRaises(ValueError):
                restarted.import_snapshot({**snapshot, "material": "H13"})
            with self.assertRaises(ValueError):
                restarted.import_snapshot({**snapshot, "objects": [
                    {**snapshot["objects"][0], "manufacturing_note": "changed"}
                ]})

    def test_selector_resolution_is_unique_and_never_picks_first(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ManufacturingContextService(Path(directory))
            snapshot = service.import_snapshot(_snapshot())
            resolved = service.resolve_selector(
                snapshot["snapshot_id"],
                {"object_id": "cavity-1", "attributes": {"role": "cavity"}},
            )
            self.assertEqual("resolved", resolved["status"])
            self.assertEqual("cavity-1", resolved["matches"][0]["object_id"])
            self.assertEqual(snapshot["content_hash"], resolved["snapshot_hash"])

            ambiguous = resolve_snapshot_selector(snapshot, {"name": "cavity"})
            self.assertEqual("ambiguous", ambiguous["status"])
            self.assertEqual(2, len(ambiguous["matches"]))
            unresolved = resolve_snapshot_selector(
                snapshot, {"attributes": {"role": "not-present"}}
            )
            self.assertEqual("unresolved", unresolved["status"])

    def test_validation_rejects_product_units_axes_and_duplicate_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ManufacturingContextService(Path(directory))
            for field, value in (("product", "cimatron"), ("units", "cm")):
                payload = _snapshot(**{field: value})
                with self.subTest(field=field):
                    with self.assertRaises(ValueError):
                        service.import_snapshot(payload)
            with self.assertRaises(ValueError):
                service.import_snapshot(_snapshot(machine={"axes": 5}))
            duplicate = _snapshot()
            duplicate["objects"][1]["object_id"] = "cavity-1"
            with self.assertRaises(ValueError):
                service.import_snapshot(duplicate)
            generated = service.import_snapshot(_snapshot(source="fixture"))
            self.assertTrue(generated["fixture_id"].startswith("fixture-"))

    def test_host_bridges_require_explicit_provider_and_do_not_claim_connection(self):
        with self.assertRaises(HostBridgeUnavailable):
            PowerMillReadOnlyBridge(api=object()).export_snapshot()

        payload = _snapshot(objects=[_snapshot()["objects"][0]])

        class Provider:
            def read_only_snapshot(self):
                return payload

        bridge = PowerMillReadOnlyBridge(api=Provider())
        exported = bridge.export_snapshot()
        self.assertEqual("unverified", exported["connection_status"])
        self.assertFalse(exported["live_connected"])
        self.assertFalse(bridge.capabilities()["live_connection"])

        evidence = {
            "verified": True,
            "verification_status": "runtime_observed",
            "evidence_type": "official_api_runtime",
            "instance_id": "pm-fixture",
            "project_id": "demo-cavity",
            "target_version": "PowerMill 2026",
        }
        verified = PowerMillReadOnlyBridge(api=Provider(), evidence=evidence).export_snapshot()
        self.assertEqual("unverified", verified["connection_status"])
        self.assertFalse(verified["live_connected"])

    def test_nx_capability_matrix_is_explicitly_unverified_without_sdk(self):
        capabilities = NxReadOnlyBridge().capabilities()
        self.assertEqual("nx", capabilities["product"])
        self.assertEqual("unverified", capabilities["status"])
        self.assertFalse(capabilities["external_python_attach"])
        self.assertFalse(capabilities["execution"])

    def test_powermill_com_exporter_uses_selected_object_without_starting_process(self):
        class DebugInfo:
            ProcessId = 4812

        class Automation:
            constructed_with = None
            Version = "2026"
            ProcessId = 4812
            Units = "MM"
            Debug = DebugInfo()

            def __init__(self, com_object):
                type(self).constructed_with = com_object

        selected_com_object = object()
        exporter = PowerMillComReadOnlyExporter(selected_com_object, Automation)
        snapshot = exporter.export_snapshot(
            instance_id="powermill:4812",
            project_id="fixture-project",
            material="P20",
            machine={"axes": 3},
        )
        self.assertIs(selected_com_object, Automation.constructed_with)
        self.assertEqual("2026", snapshot["target_version"])
        self.assertEqual("mm", snapshot["units"])
        self.assertEqual(4812, snapshot["host_evidence"]["process_id"])
        self.assertEqual("unavailable", snapshot["object_inventory_status"])
        self.assertFalse(snapshot["host_evidence"]["verified"])
        self.assertFalse(snapshot["live_connection"])
        for value in ("1", "2", "unknown"):
            with self.assertRaises(ValueError):
                PowerMillComReadOnlyExporter._units(value)
        with self.assertRaises(ValueError):
            PowerMillComReadOnlyExporter(
                selected_com_object, Automation
            ).export_snapshot(
                instance_id="powermill:4812",
                project_id="fixture-project",
                material="P20",
                machine={"axes": 3.0},
            )

    def test_schema_and_machine_shapes_are_value_errors_and_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ManufacturingContextService(Path(directory))
            for schema_version in (None, [], {}):
                with self.assertRaises(ValueError):
                    service.import_snapshot(_snapshot(schema_version=schema_version))
            for axes in (True, 3.0, "3", None):
                with self.assertRaises(ValueError):
                    service.import_snapshot(_snapshot(machine={"axes": axes}))
            snapshot = service.import_snapshot(_snapshot())
            path = Path(directory) / "snapshots" / f"{snapshot['snapshot_id']}.json"
            value = snapshot.copy()
            value["material"] = "tampered"
            path.write_text(__import__("json").dumps(value), encoding="utf-8")
            with self.assertRaises(ValueError):
                service.get_snapshot(snapshot["snapshot_id"])

    def test_nx_open_exporter_uses_official_session_and_work_bodies_shape(self):
        class Body:
            Tag = 17
            Name = "fixture-body"

        class Session:
            ReleaseNumber = "NX 2406"
            Parts = type(
                "Parts",
                (),
                {"Work": type("Part", (), {"Bodies": [Body()], "PartUnits": "Millimeters"})()},
            )()

        class NXOpenFixture:
            Session = type("SessionFactory", (), {"GetSession": staticmethod(lambda: Session())})

        exported = NxOpenSnapshotExporter(NXOpenFixture()).export_snapshot(
            instance_id="nx-fixture",
            project_id="fixture-part",
            material="fixture-material",
            machine={"axes": 3},
        )
        self.assertEqual("nx", exported["product"])
        self.assertEqual("nx-session-body:17", exported["objects"][0]["object_id"])
        self.assertEqual("fixture", exported["host_evidence"]["verification_status"])
        self.assertFalse(exported["host_evidence"]["verified"])
        self.assertFalse(exported["geometry_complete"])


if __name__ == "__main__":
    unittest.main()
