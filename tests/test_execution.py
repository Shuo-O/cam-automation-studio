from __future__ import annotations

import unittest

from cam_automation.execution import ExecutionGateway, ExecutionRequest


def request(**overrides: object) -> ExecutionRequest:
    value = {
        "product": "powermill",
        "action": "cam.model.import",
        "command": "IMPORT MODEL 'part.dmt'",
        "risk": "safe",
        "recipe_hash": "reviewed-recipe-hash",
        "target_version": "PowerMill 2026",
        "test_project": True,
        "mode": "dry-run",
    }
    value.update(overrides)
    return ExecutionRequest.from_dict(value)


class ExecutionGatewayTests(unittest.TestCase):
    def test_safe_reviewed_test_command_remains_dry_run(self) -> None:
        result = ExecutionGateway().execute(request())

        self.assertEqual("dry_run", result["status"])
        self.assertEqual("<redacted>", result["request"]["command"])
        self.assertTrue(result["request"]["command_hash"])

    def test_blocked_nc_and_unconfigured_live_transports_fail_closed(self) -> None:
        gateway = ExecutionGateway()

        blocked = gateway.execute(request(risk="blocked"))
        nc_output = gateway.execute(request(action="cam.nc_program.write"))
        live = gateway.execute(request(mode="live"))

        self.assertEqual("rejected", blocked["status"])
        self.assertIn("blocked actions are never executable", blocked["reasons"])
        self.assertEqual("rejected", nc_output["status"])
        self.assertTrue(any("NC" in reason for reason in nc_output["reasons"]))
        self.assertEqual("rejected", live["status"])
        self.assertTrue(any("not configured" in reason for reason in live["reasons"]))

    def test_command_text_is_checked_independently_from_claimed_risk(self) -> None:
        gateway = ExecutionGateway()

        destructive = gateway.execute(request(command="PROJECT RESET", risk="safe"))
        nc_output = gateway.execute(
            request(command="NCPROGRAM WRITE 'out.tap'", risk="safe")
        )

        self.assertEqual("rejected", destructive["status"])
        self.assertTrue(any("independently blocked" in reason for reason in destructive["reasons"]))
        self.assertEqual("rejected", nc_output["status"])
        self.assertTrue(any("machine-ready NC" in reason for reason in nc_output["reasons"]))

    def test_review_action_requires_identified_approval(self) -> None:
        gateway = ExecutionGateway()

        missing = gateway.execute(request(risk="review"))
        approved = gateway.execute(
            request(risk="review", reviewed=True, approver="shop-reviewer")
        )

        self.assertEqual("rejected", missing["status"])
        self.assertEqual("dry_run", approved["status"])


if __name__ == "__main__":
    unittest.main()
