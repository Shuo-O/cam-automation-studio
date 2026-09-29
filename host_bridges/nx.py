"""Siemens NX read-only exporter boundary.

NXOpen Python is host-process oriented; an external Python process cannot be
treated as an attached GUI session.  A vendor-authorized in-process exporter
must therefore provide the snapshot explicitly.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import uuid
from typing import Any

from ._base import HostBridgeUnavailable, ReadOnlyHostBridge, has_verified_evidence, utc_now


class NxReadOnlyBridge(ReadOnlyHostBridge):
    product = "nx"
    vendor_api = "Siemens NXOpen"

    def capabilities(self) -> dict[str, Any]:
        verified = has_verified_evidence(self.evidence)
        return {
            "schema_version": 1,
            "product": self.product,
            "bridge": "nx.read_only",
            "api_surface": self.vendor_api,
            "api_source": "Siemens NXOpen (target installation and release must be verified)",
            "snapshot_export": any(
                callable(getattr(self.api, name, None))
                for name in ("read_only_snapshot", "export_read_only_snapshot")
            )
            or isinstance(self.api, dict) and isinstance(self.api.get("snapshot"), dict),
            "status": "verified" if verified else "unverified",
            "live_connection": False,
            "external_python_attach": False,
            "execution": False,
            "machine_output": False,
            "limitations": [
                "NX installation, release, stubs, and in-process exporter are not verified here",
                "an injected read_only_snapshot provider is required",
            ],
        }


class NxOpenSnapshotExporter:
    """Read-only exporter intended to run inside the installed NXOpen Python.

    NXOpen's supported access path is ``NXOpen.Session.GetSession()`` and the
    work part's ``Bodies`` collection.  The exporter never commits builders,
    changes a part, queues commands, or emits NC.  Material and machine data
    are deliberately operator-supplied because they are not inferred from a
    body enumeration.
    """

    product = "nx"
    api_surface = "Siemens NXOpen"

    def __init__(self, nxopen_module: Any = None) -> None:
        self._nxopen_module = nxopen_module

    def export_snapshot(
        self,
        *,
        instance_id: str,
        project_id: str,
        material: str,
        machine: dict[str, Any],
        units: str | None = None,
        target_version: str | None = None,
        service: Any = None,
    ) -> dict[str, Any]:
        if not all(isinstance(value, str) and value.strip() for value in (instance_id, project_id, material)):
            raise ValueError("instance_id, project_id, and material are required")
        if not isinstance(machine, dict):
            raise ValueError("machine must be an object supplied by the operator")
        if type(machine.get("axes")) is not int or machine.get("axes") != 3:
            raise ValueError("machine.axes must be the integer 3")
        module = self._nxopen_module
        injected_fixture = module is not None
        if module is None:
            try:
                module = importlib.import_module("NXOpen")
            except ImportError as error:
                raise HostBridgeUnavailable(
                    "NXOpen is unavailable; run this exporter from the target NX installation"
                ) from error
        try:
            session = module.Session.GetSession()
            work_part = session.Parts.Work
            bodies = work_part.Bodies
        except (AttributeError, RuntimeError) as error:
            raise HostBridgeUnavailable(
                "NXOpen session/work part could not be read without modifying the host"
            ) from error

        objects: list[dict[str, Any]] = []
        for body in bodies:
            try:
                tag = int(body.Tag)
            except (AttributeError, TypeError, ValueError) as error:
                raise HostBridgeUnavailable("NXOpen body did not expose a stable Tag") from error
            name = str(getattr(body, "Name", "") or "")
            objects.append(
                {
                    "object_id": f"nx-session-body:{tag}",
                    "kind": "body",
                    "name": name or f"Body {tag}",
                    "attributes": {"tag": tag, "tag_scope": "session"},
                }
            )
        release = target_version or str(getattr(session, "ReleaseNumber", "") or "")
        if not release:
            raise ValueError("target_version is required when NXOpen.ReleaseNumber is unavailable")
        object_inventory_digest = "sha256:" + hashlib.sha256(
            json.dumps(objects, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if units is None:
            raw_units = getattr(work_part, "PartUnits", None)
            units = self._normalize_units(raw_units)
            units_source = "NXOpen.Part.PartUnits"
        else:
            units = self._normalize_units(units)
            units_source = "operator"
        capture_nonce = uuid.uuid4().hex
        verification_status = "fixture" if injected_fixture else "runtime_observed"
        payload: dict[str, Any] = {
            "schema_version": 1,
            "product": "nx",
            "instance_id": instance_id,
            "project_id": project_id,
            "target_version": release,
            "units": units,
            "material": material,
            "machine": dict(machine),
            "objects": objects,
            "object_inventory_digest": object_inventory_digest,
            "geometry_complete": False,
            "source": "host_read_only",
            "host_evidence": {
                "verified": False,
                "verification_status": verification_status,
                "evidence_type": "official_api_runtime",
                "api": self.api_surface,
                "instance_id": instance_id,
                "project_id": project_id,
                "target_version": release,
                "captured_at": utc_now(),
                "capture_nonce": capture_nonce,
                "captured_in_host": True,
                "units_source": units_source,
            },
            "warnings": [
                "material and machine context were supplied by the operator",
                "NXOpen body enumeration is read-only; no production action or NC output is enabled",
                "body Tag is a session-level identifier and is not stable across NX reopen",
                "body inventory is not a complete geometry digest; later same-file shape edits may be undetected",
            ],
            "captured_in_host": True,
            "connection_status": "captured",
            "live_connection": False,
            "dry_run": True,
        }
        if service is not None:
            return service.import_snapshot(payload)
        return payload

    @staticmethod
    def _normalize_units(value: Any) -> str:
        text = str(value or "").casefold()
        if (
            "millimeter" in text
            or "millimetre" in text
            or text in {"mm", "metric"}
            or text.endswith(".mm")
        ):
            return "mm"
        if "inch" in text or text in {"in", "imperial"} or text.endswith(".inches"):
            return "inch"
        raise ValueError(
            "units must be supplied explicitly or exposed by NXOpen.Part.PartUnits"
        )


__all__ = ["NxReadOnlyBridge", "NxOpenSnapshotExporter"]
