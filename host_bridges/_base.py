from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Mapping


class HostBridgeUnavailable(RuntimeError):
    """The requested vendor API surface was not supplied or is not verified."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def copy_json(value: Any) -> Any:
    return copy.deepcopy(value)


def has_verified_evidence(evidence: Mapping[str, Any]) -> bool:
    """Conservative proof check; caller cannot mark a bridge connected by name alone."""

    required = ("evidence_type", "instance_id", "project_id", "target_version")
    return (
        bool(evidence.get("verified"))
        and evidence.get("evidence_type") in {"official_api_runtime", "vendor_export"}
        and evidence.get("verification_status") == "independently_verified"
        and all(isinstance(evidence.get(key), str) and evidence[key].strip() for key in required)
    )


class ReadOnlyHostBridge:
    product = ""
    vendor_api = ""

    def __init__(self, api: Any = None, *, evidence: Mapping[str, Any] | None = None) -> None:
        self.api = api
        self.evidence = dict(evidence or {})

    def capabilities(self) -> dict[str, Any]:
        raise NotImplementedError

    def _read_payload(self) -> Mapping[str, Any]:
        api = self.api
        if isinstance(api, Mapping):
            candidate = api.get("snapshot")
        else:
            candidate = None
            for name in ("read_only_snapshot", "export_read_only_snapshot"):
                method = getattr(api, name, None)
                if callable(method):
                    candidate = method()
                    break
        if not isinstance(candidate, Mapping):
            raise HostBridgeUnavailable(
                f"{self.product} read-only provider is unavailable; no vendor snapshot was supplied"
            )
        return candidate

    def _decorate(self, payload: Mapping[str, Any], *, source: str = "host_read_only") -> dict[str, Any]:
        evidence = copy_json(self.evidence)
        evidence.setdefault("captured_at", utc_now())
        verified = has_verified_evidence(evidence)
        output = copy_json(payload)
        output.update(
            {
                "schema_version": 1,
                "product": self.product,
                "source": source,
                "host_evidence": evidence,
                "connection_status": "connected" if verified else "unverified",
                "live_connected": verified,
                "dry_run": True,
            }
        )
        if not verified:
            output.setdefault("warnings", []).append(
                "host export evidence is not verified; this is not a live connection claim"
            )
        return output

    def export_snapshot(self, *, service: Any = None) -> dict[str, Any]:
        payload = self._decorate(self._read_payload())
        if service is None:
            return payload
        return service.import_snapshot(payload)
