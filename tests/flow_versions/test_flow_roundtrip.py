from __future__ import annotations

import copy
import json
import unittest

from cam_automation.flow_roundtrip import (
    SourcePatch,
    build_minimal_candidate,
    verify_round_trip,
)
from cam_automation.flow_validation import validate_contract

from .helpers import FIXED_TIME, FIXTURE_ROOT, fixture, graph, target_graph


class FixtureRoundTripAdapter:
    def __init__(
        self,
        reparsed: dict,
        *,
        fail: bool = False,
        safety_issues: list[dict] | None = None,
    ) -> None:
        self.reparsed = reparsed
        self.fail = fail
        self.safety_issues = safety_issues or []
        self.candidates: list[bytes] = []

    def validate_candidate(self, candidate: bytes, *, target_graph: dict):
        self.candidates.append(candidate)
        return copy.deepcopy(self.safety_issues)

    def reparse_candidate(self, candidate: bytes, *, target_graph: dict):
        if self.fail:
            raise ValueError("fixture reparse failed")
        return copy.deepcopy(self.reparsed)


class FlowRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = fixture()["source_text"].encode("utf-8")
        self.graph = graph()

    def test_f0_f1_f2_f3_and_fb_golden_cases(self) -> None:
        golden = json.loads(
            (FIXTURE_ROOT / "roundtrip-golden.json").read_text(encoding="utf-8")
        )
        for case in golden["cases"]:
            with self.subTest(case=case["name"]):
                mapping_id = case.get("mapping_id", "mapping:value")
                patches = (
                    []
                    if case["replacement"] is None
                    else [
                        SourcePatch(
                            mapping_id,
                            case["replacement"].encode("utf-8"),
                        )
                    ]
                )
                target = self.graph if not patches else target_graph()
                result = verify_round_trip(
                    self.graph,
                    target,
                    self.source,
                    patches,
                    FixtureRoundTripAdapter(target),
                    required_fidelity=case["required_fidelity"],
                    checked_at=FIXED_TIME,
                )
                report = result.report.to_dict()
                self.assertTrue(
                    validate_contract(
                        report,
                        "round_trip_report",
                    ).valid
                )
                self.assertEqual(case["expected_status"], report["status"])
                self.assertEqual(case["expected_fidelity"], report["fidelity"])
                if report["status"] == "passed":
                    self.assertIsNotNone(result.candidate)
                    self.assertTrue(report["candidate_reparsed"])
                    self.assertTrue(report["untouched_spans_exact"])
                    self.assertTrue(report["opaque_spans_preserved"])
                else:
                    self.assertIsNone(result.candidate)
                    self.assertIsNone(report["candidate_artifact_ref"])
                    self.assertIsNone(report["candidate_content_hash"])

    def test_minimal_patch_preserves_all_unaffected_bytes_and_original(self) -> None:
        original = bytes(self.source)
        candidate = build_minimal_candidate(
            self.source,
            self.graph,
            [SourcePatch("mapping:value", b"VALUE=200\n")],
        )
        self.assertEqual(b"BEGIN\nVALUE=200\nOPAQUE\nEND\n", candidate.content)
        self.assertEqual(original, self.source)
        self.assertEqual(("mapping:value",), candidate.changed_mapping_ids)

    def test_source_drift_fails_closed_without_candidate(self) -> None:
        result = verify_round_trip(
            self.graph,
            target_graph(),
            self.source + b"drift",
            [SourcePatch("mapping:value", b"VALUE=2\n")],
            FixtureRoundTripAdapter(target_graph()),
            checked_at=FIXED_TIME,
        )
        report = result.report.to_dict()
        self.assertEqual("FB", report["fidelity"])
        self.assertEqual(
            "SOURCE_DIGEST_MISMATCH",
            report["issues"][0]["code"],
        )
        self.assertIsNone(result.candidate)

    def test_mapping_digest_and_reviewed_excerpt_cannot_be_overridden(self) -> None:
        wrong_digest = copy.deepcopy(self.graph)
        wrong_digest["source_mappings"][1]["source_digest"] = "sha256:" + ("0" * 64)
        result = verify_round_trip(
            wrong_digest,
            target_graph(),
            self.source,
            [SourcePatch("mapping:value", b"VALUE=2\n")],
            FixtureRoundTripAdapter(target_graph()),
            checked_at=FIXED_TIME,
        )
        self.assertEqual(
            "SOURCE_DIGEST_MISMATCH",
            result.report.to_dict()["issues"][0]["code"],
        )
        self.assertIsNone(result.candidate)

        wrong_excerpt = copy.deepcopy(self.graph)
        wrong_excerpt["source_mappings"][1]["excerpt_hash"] = "sha256:" + ("0" * 64)
        result = verify_round_trip(
            wrong_excerpt,
            target_graph(),
            self.source,
            [
                SourcePatch(
                    "mapping:value",
                    b"VALUE=2\n",
                    expected_excerpt_hash=(
                        "sha256:"
                        "ddced955472eca8cf0eea19edf798c43514e97feae458369b17732a4a6453e4b"
                    ),
                )
            ],
            FixtureRoundTripAdapter(target_graph()),
            checked_at=FIXED_TIME,
        )
        self.assertEqual(
            "SOURCE_DIGEST_MISMATCH",
            result.report.to_dict()["issues"][0]["code"],
        )
        self.assertIsNone(result.candidate)

    def test_reparse_failure_discards_complete_candidate(self) -> None:
        result = verify_round_trip(
            self.graph,
            target_graph(),
            self.source,
            [SourcePatch("mapping:value", b"VALUE=2\n")],
            FixtureRoundTripAdapter(target_graph(), fail=True),
            checked_at=FIXED_TIME,
        )
        report = result.report.to_dict()
        self.assertEqual("failed", report["status"])
        self.assertEqual("ROUNDTRIP_REPARSE_FAILED", report["issues"][0]["code"])
        self.assertIsNone(result.candidate)
        self.assertIsNone(report["candidate_content_hash"])

    def test_semantic_mismatch_discards_complete_candidate(self) -> None:
        result = verify_round_trip(
            self.graph,
            target_graph(),
            self.source,
            [SourcePatch("mapping:value", b"VALUE=2\n")],
            FixtureRoundTripAdapter(self.graph),
            checked_at=FIXED_TIME,
        )
        report = result.report.to_dict()
        self.assertEqual("ROUNDTRIP_SEMANTIC_MISMATCH", report["issues"][0]["code"])
        self.assertIsNone(result.candidate)
        self.assertIsNone(report["candidate_artifact_ref"])

    def test_adapter_safety_blocker_discards_candidate_without_payload(self) -> None:
        issue = {
            "code": "SAFETY_MACHINE_OUTPUT_FORBIDDEN",
            "message": "Blocked category.",
        }
        result = verify_round_trip(
            self.graph,
            target_graph(),
            self.source,
            [SourcePatch("mapping:value", b"VALUE=2\n")],
            FixtureRoundTripAdapter(target_graph(), safety_issues=[issue]),
            checked_at=FIXED_TIME,
        )
        report = result.report.to_dict()
        self.assertEqual("blocked", report["status"])
        self.assertEqual("FB", report["fidelity"])
        self.assertIsNone(result.candidate)
        self.assertNotIn("VALUE=2", json.dumps(report))

    def test_round_trip_report_is_byte_deterministic_for_twenty_runs(self) -> None:
        reports = [
            verify_round_trip(
                self.graph,
                target_graph(),
                self.source,
                [SourcePatch("mapping:value", b"VALUE=2\n")],
                FixtureRoundTripAdapter(target_graph()),
                required_fidelity="F3",
                checked_at=FIXED_TIME,
            ).report.canonical_bytes()
            for _ in range(20)
        ]
        self.assertEqual(1, len(set(reports)))


if __name__ == "__main__":
    unittest.main()
