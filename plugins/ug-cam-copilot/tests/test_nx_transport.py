from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "src"))

from ugcam_ai import FixtureNxTransport, NxTransport
from ugcam_ai.transport import (
    NxTransportError,
    NxTransportSafetyError,
    NxTransportUnavailableError,
)


class NxTransportTest(unittest.TestCase):
    def setUp(self) -> None:
        fixture = (
            PLUGIN_ROOT / "examples" / "nx_transport" / "two_instances.json"
        )
        self.transport = FixtureNxTransport.from_path(fixture)

    def test_default_transport_has_no_live_connection(self) -> None:
        with self.assertRaises(NxTransportUnavailableError):
            NxTransport().query("nx:3101:A1", "nx.session.describe")
        with self.assertRaises(NxTransportUnavailableError):
            NxTransport().snapshot("nx:3101:A1")

    def test_two_instance_responses_do_not_cross_or_share_payloads(self) -> None:
        first = self.transport.query("nx:3101:A1", "nx.session.describe")
        second = self.transport.query("nx:3102:B2", "nx.session.describe")

        self.assertEqual(first.status, "succeeded")
        self.assertEqual(second.status, "succeeded")
        self.assertEqual(first.target_instance_id, "nx:3101:A1")
        self.assertEqual(second.target_instance_id, "nx:3102:B2")
        self.assertEqual(first.target_version, "NX 2406")
        self.assertEqual(second.target_version, "NX 2312")
        self.assertEqual(
            first.structured_response["result"]["session_name"],
            "fixture-session-a",
        )
        self.assertEqual(
            second.structured_response["result"]["session_name"],
            "fixture-session-b",
        )
        self.assertNotEqual(first.response_id, second.response_id)

        first.structured_response["result"]["session_name"] = "mutated-by-caller"
        repeated = self.transport.query("nx:3101:A1", "nx.session.describe")
        self.assertEqual(
            repeated.structured_response["result"]["session_name"],
            "fixture-session-a",
        )

    def test_snapshots_are_read_only_and_bound_to_each_instance(self) -> None:
        first = self.transport.snapshot("nx:3101:A1")
        second = self.transport.snapshot("nx:3102:B2")

        self.assertTrue(first.read_only)
        self.assertEqual(first.target_instance_id, "nx:3101:A1")
        self.assertEqual(second.target_instance_id, "nx:3102:B2")
        self.assertEqual(first.metadata["result"]["part"]["units"], "mm")
        self.assertEqual(second.metadata["result"]["part"]["units"], "inch")
        self.assertFalse(first.metadata["production_approved"])
        self.assertEqual(first.metadata["commands_sent"], 0)

    def test_concurrent_multi_instance_queries_remain_isolated(self) -> None:
        instance_ids = [
            "nx:3101:A1" if index % 2 == 0 else "nx:3102:B2"
            for index in range(40)
        ]
        with ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(
                executor.map(
                    lambda instance_id: self.transport.query(
                        instance_id, "nx.session.describe"
                    ),
                    instance_ids,
                )
            )

        self.assertEqual(len({response.response_id for response in responses}), 40)
        for instance_id, response in zip(instance_ids, responses):
            expected = (
                "fixture-session-a"
                if instance_id == "nx:3101:A1"
                else "fixture-session-b"
            )
            self.assertEqual(response.target_instance_id, instance_id)
            self.assertEqual(
                response.structured_response["result"]["session_name"], expected
            )

    def test_dangerous_operations_and_product_mismatch_are_rejected(self) -> None:
        dangerous = [
            "nx.journal.execute",
            "nx.nc.generate",
            "cam.output.postprocess",
            "nx.machine.control",
            "nx.part.save",
        ]
        for operation in dangerous:
            with self.subTest(operation=operation):
                response = self.transport.query("nx:3101:A1", operation)
                self.assertEqual(response.status, "rejected")
                self.assertEqual(
                    response.error["code"], "nx.transport.dangerous_operation"
                )
                self.assertIsNone(response.structured_response)

        mismatch = self.transport.query(
            "nx:3101:A1",
            "nx.session.describe",
            {"product": "powermill"},
        )
        self.assertEqual(mismatch.status, "rejected")
        self.assertEqual(
            mismatch.error["code"], "nx.transport.product_mismatch"
        )

    def test_machine_ready_fixture_payload_is_discarded(self) -> None:
        value = {
            "schema_version": 1,
            "instances": [
                {
                    "product": "nx",
                    "instance_id": "nx:unsafe",
                    "target_version": "NX 2406",
                    "project_id": "fixture-unsafe",
                    "queries": {
                        "nx.session.describe": {"program": "G01 X1.0 Y2.0"}
                    },
                    "snapshot": {"nc_code": "G00 X0"},
                }
            ],
        }
        transport = FixtureNxTransport.from_dict(value)

        response = transport.query("nx:unsafe", "nx.session.describe")

        self.assertEqual(response.status, "rejected")
        self.assertEqual(
            response.error["code"],
            "nx.transport.machine_ready_output_blocked",
        )
        self.assertIsNone(response.structured_response)
        with self.assertRaises(NxTransportSafetyError):
            transport.snapshot("nx:unsafe")

    def test_powermill_fixture_is_rejected_at_transport_boundary(self) -> None:
        with self.assertRaises(NxTransportError):
            FixtureNxTransport.from_dict(
                {
                    "schema_version": 1,
                    "instances": [
                        {
                            "product": "powermill",
                            "instance_id": "powermill:1",
                            "target_version": "PowerMill 2026",
                            "project_id": "pm-project",
                            "queries": {},
                            "snapshot": {},
                        }
                    ],
                }
            )


if __name__ == "__main__":
    unittest.main()
