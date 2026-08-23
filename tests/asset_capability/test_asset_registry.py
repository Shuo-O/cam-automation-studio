from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone

from cam_automation.asset_registry import (
    AssetAccessDenied,
    AssetRegistry,
    AssetRegistryError,
)


class AssetRegistryTests(unittest.TestCase):
    def test_content_addressed_revisions_are_idempotent_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = AssetRegistry(
                directory,
                clock=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
            )
            first = registry.register_asset(
                b"ONE 'fixture'\r\nTWO\r\n",
                asset_id="asset:pm:fixture",
                product="powermill",
                asset_type="powermill_macro",
                display_name=r"C:\customer\secret\fixture.mac",
                source_locator=r"C:\customer\secret\fixture.mac",
                source_origin="user_authored",
                target_versions=("PowerMill 2025",),
                runtime_modes=("offline",),
                dependencies=(
                    {
                        "dependency_id": "dep:fixture:1",
                        "kind": "fixture",
                        "content_hash": "sha256:" + ("1" * 64),
                        "required": True,
                        "status": "available",
                    },
                ),
            )
            duplicate = registry.register_asset(
                b"ONE 'fixture'\r\nTWO\r\n",
                asset_id="asset:pm:fixture",
                product="powermill",
                asset_type="powermill_macro",
                display_name="renamed-by-caller.mac",
                source_locator=r"D:\another\private\location.mac",
            )

            self.assertEqual(first, duplicate)
            self.assertEqual("fixture.mac", first["display_name"])
            self.assertNotIn("customer", first["source_locator"])
            self.assertTrue(first["source_locator"].startswith("local-content:sha256:"))
            self.assertEqual("utf-8", first["encoding"])
            self.assertEqual("none", first["bom"])
            self.assertEqual("crlf", first["newline_profile"])
            self.assertEqual(1, len(registry.list_revisions("asset:pm:fixture")))

            second = registry.register_asset(
                b"ONE 'fixture'\nTWO\nTHREE\n",
                asset_id="asset:pm:fixture",
                product="powermill",
                asset_type="powermill_macro",
                display_name="fixture.mac",
                source_locator="selected-file",
            )
            self.assertNotEqual(
                first["asset_revision_id"],
                second["asset_revision_id"],
            )
            self.assertEqual(
                b"ONE 'fixture'\r\nTWO\r\n",
                registry.read_bytes(first["asset_revision_id"]),
            )
            self.assertEqual(2, len(registry.list_revisions("asset:pm:fixture")))

            first["rights"]["status"] = "legal_reviewed"
            stored = registry.get_revision(first["asset_revision_id"])
            self.assertEqual("unreviewed", stored["rights"]["status"])

            restored = AssetRegistry(directory)
            self.assertEqual(2, len(restored.list_revisions("asset:pm:fixture")))
            self.assertEqual(
                b"ONE 'fixture'\r\nTWO\r\n",
                restored.read_bytes(stored["asset_revision_id"]),
            )

    def test_unknown_and_restricted_rights_fail_closed_except_local_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = AssetRegistry(directory)
            unknown = registry.register_asset(
                b"# offline fixture\n",
                asset_id="asset:nx:unknown",
                product="nx",
                asset_type="nx_journal",
                display_name="fixture.py",
            )

            self.assertTrue(
                registry.access_decision(
                    unknown["asset_revision_id"],
                    "read_local",
                )["allowed"]
            )
            self.assertEqual(
                b"# offline fixture\n",
                registry.read_bytes(unknown["asset_revision_id"]),
            )
            for operation in ("share", "network_egress", "preview"):
                with self.subTest(operation=operation):
                    with self.assertRaises(AssetAccessDenied) as caught:
                        registry.require_operation(
                            unknown["asset_revision_id"],
                            operation,
                        )
                    self.assertEqual("ASSET_RIGHTS_UNKNOWN", caught.exception.code)

            allowed = registry.register_asset(
                b"# reviewed fixture\n",
                asset_id="asset:nx:reviewed",
                product="nx",
                asset_type="nx_journal",
                display_name="reviewed.py",
                rights={
                    "status": "legal_reviewed",
                    "evidence_ref": "rights:fixture:1",
                    "sharing_scope": "team",
                    "redistribution_allowed": None,
                    "network_egress_allowed": False,
                    "binary_inspection": False,
                },
            )
            self.assertTrue(
                registry.require_operation(
                    allowed["asset_revision_id"],
                    "share",
                )["allowed"]
            )
            self.assertTrue(
                registry.require_operation(
                    allowed["asset_revision_id"],
                    "preview",
                )["allowed"]
            )
            with self.assertRaises(AssetAccessDenied) as redistribution:
                registry.require_operation(
                    allowed["asset_revision_id"],
                    "redistribute",
                )
            self.assertEqual(
                "ASSET_REDISTRIBUTION_FORBIDDEN",
                redistribution.exception.code,
            )
            with self.assertRaises(AssetAccessDenied) as network:
                registry.require_operation(
                    allowed["asset_revision_id"],
                    "network",
                )
            self.assertEqual("ASSET_NETWORK_EGRESS_FORBIDDEN", network.exception.code)

            restricted = registry.register_asset(
                b"# restricted fixture\n",
                asset_id="asset:nx:restricted",
                product="nx",
                asset_type="nx_journal",
                display_name="restricted.py",
                rights={
                    "status": "restricted",
                    "evidence_ref": "rights:fixture:restricted",
                    "sharing_scope": "restricted",
                    "redistribution_allowed": False,
                    "network_egress_allowed": False,
                    "binary_inspection": False,
                },
            )
            self.assertEqual(
                b"# restricted fixture\n",
                registry.read_bytes(restricted["asset_revision_id"]),
            )
            with self.assertRaises(AssetAccessDenied) as preview:
                registry.require_operation(
                    restricted["asset_revision_id"],
                    "preview",
                )
            self.assertEqual("ASSET_RIGHTS_RESTRICTED", preview.exception.code)

    def test_binary_inspection_is_permanently_blocked_without_persisting_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = AssetRegistry(directory)
            with self.assertRaises(AssetRegistryError) as caught:
                registry.register_asset(
                    b"MZ commercial payload",
                    asset_id="asset:vendor:binary",
                    product="powermill",
                    asset_type="vendor_export",
                    display_name="vendor-addon.dll",
                    source_locator=r"C:\Program Files\Vendor\vendor-addon.dll",
                    rights={
                        "status": "vendor_authorized",
                        "evidence_ref": "vendor:claim",
                        "sharing_scope": "private",
                        "redistribution_allowed": False,
                        "network_egress_allowed": False,
                        "binary_inspection": False,
                    },
                )
            self.assertEqual(
                "ASSET_BINARY_INSPECTION_FORBIDDEN",
                caught.exception.code,
            )
            self.assertEqual([], registry.list_revisions())

            text = registry.register_asset(
                b"# authorized text export\n",
                asset_id="asset:vendor:text",
                product="powermill",
                asset_type="vendor_export",
                display_name="vendor-export.txt",
            )
            with self.assertRaises(AssetAccessDenied) as inspection:
                registry.require_operation(
                    text["asset_revision_id"],
                    "binary_inspection",
                )
            self.assertEqual(
                "ASSET_BINARY_INSPECTION_FORBIDDEN",
                inspection.exception.code,
            )


if __name__ == "__main__":
    unittest.main()
