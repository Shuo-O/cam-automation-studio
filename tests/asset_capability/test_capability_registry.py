from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cam_automation.capability_registry import (
    AuthorizationRequired,
    CapabilityRegistry,
    CapabilityUnavailable,
)


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "cam-flow"
    / "registry"
    / "capability-manifest.json"
)


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 24, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def load_manifest() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def binding(manifest: dict, **overrides: object) -> dict:
    value = {
        "user_id": "user:fixture",
        "plugin_id": "powermill-cam-copilot",
        "plugin_version": "1.0.0",
        "manifest_id": manifest["manifest_id"],
        "manifest_hash": manifest["manifest_hash"],
        "category": "read:selected-files",
        "scope": {"asset_ids": ["asset:pm:fixture"], "product": "powermill"},
        "purpose": "offline asset interpretation",
    }
    value.update(overrides)
    return value


class CapabilityRegistryTests(unittest.TestCase):
    def test_registration_lock_risk_floor_and_revocation_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = CapabilityRegistry(directory)
            manifest = load_manifest()
            registered = registry.register_manifest(manifest)
            duplicate = registry.register_manifest(copy.deepcopy(manifest))
            self.assertEqual(registered, duplicate)

            registered["provider"]["display_name"] = "caller mutation"
            self.assertNotEqual(
                "caller mutation",
                registry.get_manifest(manifest["manifest_id"])["provider"][
                    "display_name"
                ],
            )
            lock = registry.create_lock(manifest["manifest_id"])
            self.assertTrue(registry.lock_status(lock)["available"])
            self.assertEqual(
                "review",
                registry.enforce_risk_floor(
                    lock,
                    node_type="powermill.fixture.inspect",
                    node_type_version="1.0.0",
                    requested_risk="review",
                ),
            )
            with self.assertRaises(CapabilityUnavailable) as downgrade:
                registry.enforce_risk_floor(
                    lock,
                    node_type="powermill.fixture.inspect",
                    node_type_version="1.0.0",
                    requested_risk="safe",
                )
            self.assertEqual(
                "CAPABILITY_RISK_DOWNGRADE",
                downgrade.exception.code,
            )

            result = registry.revoke_manifest(
                manifest["manifest_id"],
                reason="fixture capability withdrawn",
                revoked_by="user:fixture",
            )
            self.assertTrue(result["revoked"])
            self.assertFalse(registry.lock_status(lock)["available"])
            self.assertEqual(
                "CAPABILITY_REVOKED",
                registry.lock_status(lock)["code"],
            )
            # Revocation affects availability, not immutable recognition data.
            self.assertEqual(
                manifest["manifest_hash"],
                registry.get_manifest(
                    manifest["manifest_id"],
                    manifest_version=manifest["manifest_version"],
                    manifest_hash=manifest["manifest_hash"],
                )["manifest_hash"],
            )

    def test_manifest_hash_plugin_version_category_and_scope_bind_grants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = CapabilityRegistry(directory)
            first_manifest = load_manifest()
            registry.register_manifest(first_manifest)

            first_grant = registry.grant(**binding(first_manifest))
            duplicate = registry.grant(**binding(first_manifest))
            self.assertEqual(first_grant, duplicate)
            first_handle = registry.open_handle(**binding(first_manifest))
            self.assertFalse(first_handle.closed)

            second_manifest = copy.deepcopy(first_manifest)
            second_manifest["manifest_version"] = "1.1.0"
            second_manifest["manifest_hash"] = "sha256:" + ("b" * 64)
            second_manifest["permissions"].append("write:local-artifacts")
            registry.register_manifest(second_manifest)

            self.assertTrue(first_handle.closed)
            self.assertEqual("manifest_changed", first_handle.close_reason)
            self.assertTrue(
                registry.requires_authorization(**binding(second_manifest))
            )
            with self.assertRaises(CapabilityUnavailable):
                registry.open_handle(**binding(first_manifest))

            second_grant = registry.grant(**binding(second_manifest))
            self.assertNotEqual(first_grant["grant_id"], second_grant["grant_id"])
            self.assertFalse(
                registry.requires_authorization(**binding(second_manifest))
            )
            self.assertTrue(
                registry.requires_authorization(
                    **binding(second_manifest, plugin_version="1.1.0")
                )
            )
            self.assertTrue(
                registry.requires_authorization(
                    **binding(
                        second_manifest,
                        scope={
                            "asset_ids": ["asset:pm:fixture", "asset:pm:expanded"],
                            "product": "powermill",
                        },
                    )
                )
            )
            self.assertTrue(
                registry.requires_authorization(
                    **binding(
                        second_manifest,
                        category="write:local-artifacts",
                        purpose="save a reviewed local artifact",
                    )
                )
            )

    def test_fake_clock_category_revocation_closes_handles_without_deleting_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock()
            registry = CapabilityRegistry(directory, clock=clock)
            manifest = load_manifest()
            registry.register_manifest(manifest)
            first_grant = registry.grant(**binding(manifest))
            first_handle = registry.open_handle(**binding(manifest))
            opened_at = datetime.fromisoformat(
                first_handle.opened_at.replace("Z", "+00:00")
            )

            clock.advance(1.5)
            result = registry.revoke_category(
                user_id="user:fixture",
                plugin_id="powermill-cam-copilot",
                category="read:selected-files",
                reason="operator category revocation",
            )
            closed_at = datetime.fromisoformat(
                str(first_handle.closed_at).replace("Z", "+00:00")
            )

            self.assertLessEqual((closed_at - opened_at).total_seconds(), 2.0)
            self.assertTrue(first_handle.closed)
            self.assertEqual(1, result["closed_handle_count"])
            self.assertFalse(result["data_deleted"])
            self.assertFalse(result["auto_resumed"])
            with self.assertRaises(AuthorizationRequired):
                registry.open_handle(**binding(manifest))
            self.assertEqual(
                manifest["manifest_hash"],
                registry.get_manifest(manifest["manifest_id"])["manifest_hash"],
            )

            second_grant = registry.grant(**binding(manifest))
            self.assertNotEqual(first_grant["grant_id"], second_grant["grant_id"])
            self.assertTrue(first_handle.closed)
            self.assertEqual(0, registry.active_handle_count())
            second_handle = registry.open_handle(**binding(manifest))
            self.assertFalse(second_handle.closed)

            restored = CapabilityRegistry(directory, clock=clock)
            grants = restored.list_grants()
            self.assertEqual(["revoked", "active"], [item["status"] for item in grants])
            self.assertEqual(
                manifest["manifest_hash"],
                restored.get_manifest(manifest["manifest_id"])["manifest_hash"],
            )


if __name__ == "__main__":
    unittest.main()
