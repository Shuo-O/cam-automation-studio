from __future__ import annotations

import copy
import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from cam_automation.web_server import _WorkflowHandler, _WorkflowServer


SNAPSHOT = {
    "source": "fixture", "product": "powermill", "instance_id": "pm-fixture",
    "project_id": "demo-cavity", "target_version": "PowerMill 2026", "units": "mm",
    "material": "P20", "machine": {"axes": 3},
    "objects": [{"object_id": "cavity-1", "kind": "pocket", "name": "型腔",
                 "attributes": {"role": "cavity"}}],
}
CASE = {
    "name": "三轴型腔示例", "product": "powermill", "material": "P20", "units": "mm",
    "machine": {"axes": 3}, "tags": ["cavity"], "parameters": {"stepover": 0.5},
    "steps": [{"action": "cam.operation.plan", "selector": {"kind": "pocket",
               "attributes": {"role": "cavity"}}, "parameters": {}}],
    "provenance": {"source": "fixture", "note": "演示案例，未验证加工"},
}


class CustomerHttpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="cam-customer-api-")
        self.environment = patch.dict(os.environ, {
            "CAM_APP_DATA_DIR": self.directory.name,
            "CAM_CAPTURE_DIR": str(Path(self.directory.name) / "capture"),
        })
        self.environment.start()
        self.server = _WorkflowServer(("127.0.0.1", 0), _WorkflowHandler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=5)
        self.environment.stop()
        self.directory.cleanup()

    def request(self, method, route, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        connection.request(method, route, None if body is None else json.dumps(body), request_headers)
        response = connection.getresponse()
        raw = response.read()
        response_headers = dict(response.getheaders())
        connection.close()
        return response.status, json.loads(raw), response_headers

    def post(self, route, body):
        status, data, _ = self.request("POST", "/api/delivery" + route, body)
        self.assertEqual(200, status, data)
        return data

    def create_proposal(self):
        snapshot = self.post("/snapshots", SNAPSHOT)
        case = self.post("/cases", CASE)
        proposal = self.post("/proposals", {
            "snapshot_id": snapshot["snapshot_id"], "case_id": case["case_id"],
        })
        return snapshot, case, proposal

    def test_customer_round_trip_preserves_binding_and_exports_review_only(self):
        status, delivery, _ = self.request("GET", "/api/delivery/status")
        self.assertEqual(200, status)
        self.assertEqual("offline_review", delivery["mode"])
        self.assertFalse(delivery["machine_output_enabled"])
        snapshot, case, proposal = self.create_proposal()
        self.assertTrue(proposal["dry_run"])
        self.assertEqual(snapshot["content_hash"], proposal["snapshot_hash"])
        match = self.post("/selectors/resolve", {
            "snapshot_id": snapshot["snapshot_id"], "selector": {"object_id": "cavity-1"},
        })
        self.assertEqual("resolved", match["status"])
        search = self.post("/cases/search", {"snapshot_id": snapshot["snapshot_id"], "query": "cavity"})
        self.assertTrue(search["items"])
        for gate in ("review", "simulation", "collision", "shop_approval"):
            self.post(f"/proposals/{proposal['proposal_id']}/evidence", {
                "gate": gate, "status": "passed", "actor": "API integration test",
                "note": "Fixture evidence only; not a real manufacturing result.",
                "proposal_hash": proposal["proposal_hash"],
                "snapshot_hash": snapshot["content_hash"],
            })
        status, bundle, headers = self.request(
            "POST", f"/api/delivery/proposals/{proposal['proposal_id']}/export",
            {"snapshot_id": snapshot["snapshot_id"]},
        )
        self.assertEqual(200, status, bundle)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertTrue(bundle["dry_run"])
        self.assertIn(proposal["proposal_hash"], json.dumps(bundle))
        status, plugins, _ = self.request("GET", "/api/plugins")
        self.assertEqual(200, status)
        self.assertEqual(0, plugins["installed_count"])

    def test_records_survive_domain_service_recreation(self):
        snapshot, case, proposal = self.create_proposal()
        self.server.customer_api.close()
        from cam_automation.customer_api import CustomerApi
        self.server.customer_api = CustomerApi(Path(self.directory.name) / "customer")
        status, values, _ = self.request("GET", "/api/delivery/snapshots")
        self.assertEqual(200, status)
        self.assertIn(snapshot["snapshot_id"], [item["snapshot_id"] for item in values["items"]])
        status, fetched, _ = self.request("GET", f"/api/delivery/proposals/{proposal['proposal_id']}")
        self.assertEqual(200, status)
        self.assertEqual(proposal["proposal_hash"], fetched["proposal_hash"])

    def test_wrong_product_and_invalid_shapes_fail_closed(self):
        status, data, _ = self.request("POST", "/api/delivery/snapshots", {
            **SNAPSHOT, "product": "unknown-product",
        })
        self.assertEqual(400, status, data)
        status, data, _ = self.request("POST", "/api/delivery/proposals", {
            "snapshot_id": {"path": "C:/secret.json"}, "case_id": "arbitrary",
        })
        self.assertEqual(400, status, data)
        status, data, _ = self.request("GET", "/api/delivery/snapshots?path=C:/secret.json")
        self.assertEqual(400, status, data)
        status, data, _ = self.request("POST", "/api/delivery/execute", {"code": "NC"})
        self.assertEqual(404, status, data)

    def test_evidence_cannot_be_attached_to_a_different_proposal_hash(self):
        snapshot, _, proposal = self.create_proposal()
        status, result, _ = self.request(
            "POST", f"/api/delivery/proposals/{proposal['proposal_id']}/evidence", {
                "gate": "simulation", "status": "passed", "actor": "engineer", "note": "review",
                "proposal_hash": "sha256:" + "0" * 64, "snapshot_hash": snapshot["content_hash"],
            },
        )
        self.assertEqual(400, status, result)

    def test_parameter_change_creates_new_proposal_without_inheriting_evidence(self):
        snapshot, case, proposal = self.create_proposal()
        self.post(f"/proposals/{proposal['proposal_id']}/evidence", {
            "gate": "review", "status": "passed", "actor": "engineer",
            "note": "Reviewed the original fixture parameters only.",
            "proposal_hash": proposal["proposal_hash"], "snapshot_hash": snapshot["content_hash"],
        })
        changed = self.post("/proposals", {
            "snapshot_id": snapshot["snapshot_id"], "case_id": case["case_id"],
            "parameters": {"stepover": 0.25},
        })
        self.assertNotEqual(proposal["proposal_hash"], changed["proposal_hash"])
        assessment = self.post(f"/proposals/{changed['proposal_id']}/assess", {
            "snapshot_id": snapshot["snapshot_id"],
        })
        self.assertFalse(assessment["ready"])
        self.assertEqual([], assessment["evidence"])
        self.assertTrue(assessment["dry_run"])

    def test_ambiguous_selector_does_not_choose_first_object(self):
        payload = copy.deepcopy(SNAPSHOT)
        payload["objects"].append({**payload["objects"][0], "object_id": "cavity-2"})
        snapshot = self.post("/snapshots", payload)
        result = self.post("/selectors/resolve", {
            "snapshot_id": snapshot["snapshot_id"], "selector": {"name": "型腔"},
        })
        self.assertEqual("ambiguous", result["status"])
        self.assertEqual(2, len(result["matches"]))

    def test_browser_origin_and_dns_rebinding_are_rejected_before_mutation(self):
        for headers in (
            {"Origin": "https://example.com"}, {"Origin": "null"},
            {"Host": f"attacker.example:{self.server.server_port}"},
            {"Origin": "http://localhost:1"},
        ):
            status, data, _ = self.request("POST", "/api/delivery/snapshots", SNAPSHOT, headers)
            self.assertEqual(403, status, data)
        self.assertIsNone(self.server.customer_api._context)


if __name__ == "__main__":
    unittest.main()
