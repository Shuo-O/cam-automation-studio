from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol


_NC_ACTION_MARKERS = (
    "nc_program.write",
    "output.postprocess",
    "postprocess",
    "machine_code",
)
_BLOCKED_COMMAND = re.compile(
    r"(?:^\s*(?:DELETE|REMOVE|ERASE|QUIT|EXIT|SYSTEM|SHELL|PROCESS)\b"
    r"|^\s*PROJECT\s+(?:RESET|CLOSE|DELETE)\b"
    r"|\bos\.system\s*\("
    r"|\bsubprocess\."
    r"|\.Delete\s*\("
    r"|\.Remove\s*\()",
    re.IGNORECASE,
)
_NC_COMMAND = re.compile(
    r"(?:\bPOSTPROCESS\b|\bNCPROGRAM\s+WRITE\b|\bMACHINE[_ ]?CODE\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExecutionRequest:
    product: str
    action: str
    command: str
    risk: str
    recipe_hash: str
    target_version: str
    test_project: bool
    reviewed: bool = False
    approver: str = ""
    mode: str = "dry-run"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExecutionRequest":
        return cls(
            product=str(value.get("product", "")).lower(),
            action=str(value.get("action", "")),
            command=str(value.get("command", "")),
            risk=str(value.get("risk", "blocked")).lower(),
            recipe_hash=str(value.get("recipe_hash", "")),
            target_version=str(value.get("target_version", "")),
            test_project=bool(value.get("test_project", False)),
            reviewed=bool(value.get("reviewed", False)),
            approver=str(value.get("approver", "")),
            mode=str(value.get("mode", "dry-run")).lower(),
        )


class CommandTransport(Protocol):
    name: str

    def execute(self, request: ExecutionRequest) -> dict[str, Any]:
        ...


class DryRunTransport:
    name = "dry-run"

    def execute(self, request: ExecutionRequest) -> dict[str, Any]:
        return {
            "status": "dry_run",
            "transport": self.name,
            "command_hash": hashlib.sha256(request.command.encode("utf-8")).hexdigest(),
            "message": "Command passed the safety gate but was not sent to a CAM process.",
        }


class ExecutionGateway:
    """Fail-closed command gate; live transports must be registered explicitly."""

    def __init__(self) -> None:
        self.transports: dict[tuple[str, str], CommandTransport] = {
            ("nx", "dry-run"): DryRunTransport(),
            ("powermill", "dry-run"): DryRunTransport(),
        }

    def register(self, product: str, mode: str, transport: CommandTransport) -> None:
        if mode == "dry-run":
            raise ValueError("The built-in dry-run transport cannot be replaced.")
        self.transports[(product.lower(), mode.lower())] = transport

    def evaluate(self, request: ExecutionRequest) -> list[str]:
        reasons: list[str] = []
        if request.product not in {"nx", "powermill"}:
            reasons.append("unsupported product")
        if request.risk not in {"safe", "review", "blocked"}:
            reasons.append("invalid risk classification")
        if request.risk == "blocked":
            reasons.append("blocked actions are never executable")
        if _BLOCKED_COMMAND.search(request.command):
            reasons.append("command text matches an independently blocked operation")
        if any(marker in request.action.lower() for marker in _NC_ACTION_MARKERS):
            reasons.append("machine-ready NC or postprocessing output is outside this gateway")
        if _NC_COMMAND.search(request.command):
            reasons.append("command text requests machine-ready NC or postprocessing output")
        if not request.command.strip():
            reasons.append("empty command")
        if not request.recipe_hash.strip():
            reasons.append("missing reviewed recipe hash")
        if not request.target_version.strip():
            reasons.append("missing target CAM version")
        if not request.test_project:
            reasons.append("execution requires a disposable or snapshotted test project")
        if request.risk == "review" and not request.reviewed:
            reasons.append("review-classified action lacks explicit approval")
        if request.risk == "review" and not request.approver.strip():
            reasons.append("review-classified action lacks an identified approver")
        if request.mode != "dry-run" and not request.reviewed:
            reasons.append("live execution requires an explicitly reviewed recipe")
        if request.mode != "dry-run" and not request.approver.strip():
            reasons.append("live execution requires an identified human approver")
        if (request.product, request.mode) not in self.transports:
            reasons.append(
                f"{request.product} transport '{request.mode}' is not configured for this version"
            )
        return reasons

    def execute(self, request: ExecutionRequest) -> dict[str, Any]:
        started = time.perf_counter()
        reasons = self.evaluate(request)
        if reasons:
            return {
                "status": "rejected",
                "reasons": reasons,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "request": self._audit_request(request),
            }
        transport = self.transports[(request.product, request.mode)]
        result = transport.execute(request)
        result["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        result["request"] = self._audit_request(request)
        return result

    @staticmethod
    def _audit_request(request: ExecutionRequest) -> dict[str, Any]:
        value = asdict(request)
        value["command"] = "<redacted>"
        value["command_hash"] = hashlib.sha256(
            request.command.encode("utf-8")
        ).hexdigest()
        return json.loads(json.dumps(value))
