"""PowerMill read-only exporter boundary.

The official Autodesk PowerShapeAndPowerMillAPI exposes mutable automation
surfaces and version-specific COM/.NET details.  This module intentionally
accepts an already-installed/vendor-owned exporter object; it never imports
COM, starts PowerMill, sends a macro, or invokes an arbitrary command.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from ._base import HostBridgeUnavailable, ReadOnlyHostBridge, has_verified_evidence


class PowerMillReadOnlyBridge(ReadOnlyHostBridge):
    product = "powermill"
    vendor_api = "Autodesk.PowerShapeAndPowerMillAPI"

    def capabilities(self) -> dict[str, Any]:
        verified = has_verified_evidence(self.evidence)
        return {
            "schema_version": 1,
            "product": self.product,
            "bridge": "powermill.read_only",
            "api_surface": self.vendor_api,
            "api_source": "Autodesk official API repository (version must be verified on host)",
            "snapshot_export": any(
                callable(getattr(self.api, name, None))
                for name in ("read_only_snapshot", "export_read_only_snapshot")
            )
            or isinstance(self.api, dict) and isinstance(self.api.get("snapshot"), dict),
            "status": "verified" if verified else "unverified",
            "live_connection": False,
            "execution": False,
            "macro_or_command_send": False,
            "machine_output": False,
            "limitations": [
                "PMAutomation must be constructed from an already selected COM object",
                "entity enumeration is unavailable without a reviewed, version-bound API",
            ],
        }


class PowerMillComReadOnlyExporter:
    """Export a snapshot from an already selected PowerMill COM object.

    The caller supplies the ``PMAutomation`` .NET type (normally through
    pythonnet) and a COM object returned by the official static
    ``GetListOfPmComObjects`` method. This class never starts PowerMill.
    """

    product = "powermill"
    api_surface = "Autodesk.ProductInterface.PowerMILL.PMAutomation"

    def __init__(self, com_object: Any, pm_automation_type: Any) -> None:
        if com_object is None or pm_automation_type is None:
            raise ValueError("an existing COM object and PMAutomation type are required")
        try:
            self.automation = pm_automation_type(com_object)
        except Exception as error:
            raise HostBridgeUnavailable(
                "PMAutomation could not attach to the supplied existing COM object"
            ) from error

    def export_snapshot(
        self,
        *,
        instance_id: str,
        project_id: str,
        material: str,
        machine: Mapping[str, Any],
        object_inventory: list[Mapping[str, Any]] | None = None,
        service: Any = None,
    ) -> dict[str, Any]:
        if not all(isinstance(value, str) and value.strip() for value in (instance_id, project_id, material)):
            raise ValueError("instance_id, project_id, and material are required")
        if (
            not isinstance(machine, Mapping)
            or type(machine.get("axes")) is not int
            or machine.get("axes") != 3
        ):
            raise ValueError("machine.axes must be the integer 3")
        try:
            version = str(self.automation.Version)
            process_id = int(self.automation.ProcessId)
            units = self._units(self.automation.Units)
        except (AttributeError, TypeError, ValueError, RuntimeError) as error:
            raise HostBridgeUnavailable(
                "PowerMill read-only Version/ProcessId/Units properties were unavailable"
            ) from error
        objects: list[dict[str, Any]] = []
        inventory_status = "unavailable"
        if object_inventory is not None:
            for index, item in enumerate(object_inventory):
                if not isinstance(item, Mapping):
                    raise ValueError(f"object_inventory[{index}] must be an object")
                required = ("object_id", "kind", "name")
                if any(not isinstance(item.get(key), str) or not item[key] for key in required):
                    raise ValueError("object inventory entries require object_id, kind, and name")
                objects.append(
                    {
                        "object_id": item["object_id"],
                        "kind": item["kind"],
                        "name": item["name"],
                        "attributes": dict(item.get("attributes") or {}),
                    }
                )
            inventory_status = "operator_or_vendor_supplied"
        inventory_digest = "sha256:" + hashlib.sha256(
            json.dumps(objects, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        captured_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload: dict[str, Any] = {
            "schema_version": 1,
            "product": self.product,
            "instance_id": instance_id,
            "project_id": project_id,
            "target_version": version,
            "units": units,
            "material": material,
            "machine": dict(machine),
            "objects": objects,
            "object_inventory_digest": inventory_digest,
            "object_inventory_status": inventory_status,
            "geometry_complete": False,
            "source": "host_read_only",
            "host_evidence": {
                "verified": False,
                "verification_status": "runtime_observed",
                "evidence_type": "official_api_runtime",
                "api": self.api_surface,
                "instance_id": instance_id,
                "project_id": project_id,
                "target_version": version,
                "process_id": process_id,
                "captured_at": captured_at,
                "capture_nonce": uuid.uuid4().hex,
                "captured_in_host": True,
            },
            "captured_in_host": True,
            "connection_status": "captured",
            "live_connection": False,
            "dry_run": True,
            "warnings": [
                "runtime_observed evidence is a capture record, not independent verification",
                "PowerMill entity enumeration is unavailable unless supplied by a reviewed provider",
                "no command, macro, project mutation, or machine output was performed",
            ],
        }
        if service is not None:
            return service.import_snapshot(payload)
        return payload

    @staticmethod
    def _units(value: Any) -> str:
        text = str(value or "").casefold()
        if "inch" in text or text in {"in", "imperial"} or text.endswith(".inches"):
            return "inch"
        if (
            "millimeter" in text
            or "millimetre" in text
            or text in {"mm", "metric"}
            or text.endswith(".mm")
        ):
            return "mm"
        raise ValueError("PowerMill Units returned an unknown value")


PowerMillSnapshotExporter = PowerMillComReadOnlyExporter


__all__ = [
    "PowerMillReadOnlyBridge",
    "PowerMillComReadOnlyExporter",
    "PowerMillSnapshotExporter",
]
