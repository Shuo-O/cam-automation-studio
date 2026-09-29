from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cam_automation.case_library import CaseLibraryService


SNAPSHOT = {
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
            "name": "型腔",
            "attributes": {"role": "cavity"},
        }
    ],
    "source": "fixture",
}

CASE = {
    "name": "三轴型腔示例",
    "product": "powermill",
    "material": "P20",
    "units": "mm",
    "machine": {"axes": 3},
    "tags": ["cavity"],
    "parameters": {"stepover": 0.5},
    "steps": [
        {
            "action": "cam.operation.plan",
            "selector": {"kind": "pocket", "attributes": {"role": "cavity"}},
            "parameters": {},
        }
    ],
    "provenance": {"source": "fixture", "note": "演示案例，未验证加工"},
}


class CaseLibraryTests(unittest.TestCase):
    def test_case_search_is_product_isolated_and_explains_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = CaseLibraryService(Path(directory))
            saved = service.add_case(CASE)
            self.assertTrue(saved["case_id"].startswith("case:"))
            self.assertEqual(saved, service.add_case(CASE))
            results = service.search_cases(SNAPSHOT, "cavity")
            self.assertEqual([saved["case_id"]], [item["case_id"] for item in results])
            self.assertIn("machine.axes", results[0]["match"]["matched_conditions"])
            self.assertIn("score", results[0]["match"])
            self.assertIn("score", results[0]["match"]["explanation"])

            nx_case = dict(CASE, name="NX", product="nx")
            service.add_case(nx_case)
            self.assertEqual(1, len(service.search_cases(SNAPSHOT)))

    def test_proposal_rebinds_selector_and_is_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = CaseLibraryService(Path(directory), clock=lambda: "2026-09-29T00:00:00Z")
            case = service.add_case(CASE)
            proposal = service.create_proposal(SNAPSHOT, case["case_id"], {"stepover": 0.4})
            self.assertTrue(proposal["dry_run"])
            self.assertEqual("ready_for_review", proposal["readiness"])
            self.assertEqual("resolved", proposal["selector_bindings"][0]["status"])
            self.assertEqual(0.4, proposal["parameters"]["stepover"])
            self.assertEqual(proposal, service.get_proposal(proposal["proposal_id"]))

    def test_ambiguous_binding_and_stale_context_block_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = CaseLibraryService(Path(directory))
            case = service.add_case(CASE)
            proposal = service.create_proposal(SNAPSHOT, case["case_id"])
            ambiguous = dict(SNAPSHOT, objects=SNAPSHOT["objects"] + [dict(SNAPSHOT["objects"][0], object_id="cavity-2")])
            assessment = service.assess_proposal(proposal["proposal_id"], ambiguous)
            self.assertFalse(assessment["ready"])
            self.assertTrue(any("stale" in reason for reason in assessment["blocking_reasons"]))
            self.assertTrue(any("ambiguous" in reason for reason in assessment["blocking_reasons"]))

    def test_evidence_hashes_and_export_are_traceable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = CaseLibraryService(Path(directory))
            case = service.add_case(CASE)
            proposal = service.create_proposal(SNAPSHOT, case["case_id"])
            with self.assertRaisesRegex(ValueError, "proposal_hash"):
                service.record_evidence(
                    proposal["proposal_id"],
                    {
                        "gate": "review",
                        "status": "passed",
                        "actor": "qa",
                        "note": "reviewed",
                        "proposal_hash": "sha256:wrong",
                        "snapshot_hash": proposal["snapshot_hash"],
                    },
                )
            for gate in ("review", "simulation", "collision", "shop_approval"):
                service.record_evidence(
                    proposal["proposal_id"],
                    {
                        "gate": gate,
                        "status": "passed",
                        "actor": "qa",
                        "note": "fixture evidence",
                        "proposal_hash": proposal["proposal_hash"],
                        "snapshot_hash": proposal["snapshot_hash"],
                    },
                )
            assessment = service.assess_proposal(proposal["proposal_id"], SNAPSHOT)
            self.assertTrue(assessment["ready"])
            bundle = service.export_bundle(proposal["proposal_id"], SNAPSHOT)
            self.assertEqual("dry_run", bundle["delivery_mode"])
            self.assertFalse(bundle["nc_output_available"])
            self.assertTrue(any("fixture" in item for item in bundle["limitations"]))

    def test_persistence_survives_new_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = CaseLibraryService(root)
            saved = first.add_case(CASE)
            second = CaseLibraryService(root)
            self.assertEqual(saved, second.list_cases()[0])

    def test_material_mismatch_and_opaque_action_block_but_do_not_execute(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = CaseLibraryService(Path(directory))
            case = dict(CASE, material="AL6061", steps=[{"action": "vendor.custom", "parameters": {}}])
            saved = service.add_case(case)
            proposal = service.create_proposal(SNAPSHOT, saved["case_id"])
            self.assertEqual("blocked", proposal["readiness"])
            self.assertTrue(any("material" in item for item in proposal["blocking_reasons"]))
            self.assertTrue(any("opaque" in item for item in proposal["blocking_reasons"]))
            self.assertTrue(proposal["limitations"])

    def test_product_action_and_nc_output_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = CaseLibraryService(Path(directory))
            with self.assertRaisesRegex(ValueError, "nx"):
                service.add_case(dict(CASE, steps=[{"action": "nx.operation.plan"}]))
            with self.assertRaisesRegex(ValueError, "NC"):
                service.add_case(dict(CASE, steps=[{"action": "cam.output.postprocess"}]))
            with self.assertRaisesRegex(ValueError, "machine-ready"):
                service.add_case(dict(CASE, parameters={"nc_code": "G01 X1"}))
            sequence = service.add_case(dict(CASE, steps=[{"action": "cam.sequence.reference"}]))
            proposal = service.create_proposal(SNAPSHOT, sequence["case_id"])
            self.assertEqual("blocked", proposal["readiness"])
            self.assertTrue(any("opaque" in item for item in proposal["blocking_reasons"]))

    def test_empty_and_duplicate_steps_block_or_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = CaseLibraryService(Path(directory))
            empty = service.add_case(dict(CASE, steps=[]))
            proposal = service.create_proposal(SNAPSHOT, empty["case_id"])
            self.assertEqual("blocked", proposal["readiness"])
            with self.assertRaisesRegex(ValueError, "duplicate step_id"):
                service.add_case(
                    dict(
                        CASE,
                        steps=[
                            {"step_id": "same", "action": "cam.operation.plan"},
                            {"step_id": "same", "action": "cam.operation.plan"},
                        ],
                    )
                )

    def test_evidence_server_timestamp_is_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = CaseLibraryService(Path(directory), clock=lambda: "2026-09-29T00:00:00Z")
            case = service.add_case(CASE)
            proposal = service.create_proposal(SNAPSHOT, case["case_id"])
            evidence = service.record_evidence(
                proposal["proposal_id"],
                {
                    "gate": "review",
                    "status": "passed",
                    "actor": "qa",
                    "note": "checked",
                    "proposal_hash": proposal["proposal_hash"],
                    "snapshot_hash": proposal["snapshot_hash"],
                    "recorded_at": "2020-01-01T00:00:00Z",
                },
            )
            self.assertEqual("2020-01-01T00:00:00Z", evidence["observed_at"])
            self.assertEqual("2026-09-29T00:00:00Z", evidence["recorded_at"])
            with self.assertRaisesRegex(ValueError, "ISO-8601"):
                service.record_evidence(
                    proposal["proposal_id"],
                    {
                        "gate": "review",
                        "status": "passed",
                        "actor": "qa",
                        "note": "checked",
                        "proposal_hash": proposal["proposal_hash"],
                        "snapshot_hash": proposal["snapshot_hash"],
                        "recorded_at": 42,
                    },
                )


if __name__ == "__main__":
    unittest.main()
