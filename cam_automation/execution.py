from __future__ import annotations

import base64
import binascii
import hashlib
import html
import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Protocol
from urllib.parse import unquote


READ_ONLY_OPERATIONS: Mapping[str, frozenset[str]] = {
    "nx": frozenset(
        {
            "nx.cam.operations.list",
            "nx.cam.setup.describe",
            "nx.cam.tools.list",
            "nx.part.describe",
            "nx.project.describe",
            "nx.selection.preview",
            "nx.session.describe",
        }
    ),
    "powermill": frozenset(
        {
            "cam.model.bounds.query",
            "cam.toolpath.bounds.query",
            "powermill.entity.inspect",
            "powermill.project.info",
            "powermill.project.list_entities",
            "powermill.version.query",
        }
    ),
}
PREVIEW_OPERATIONS = frozenset({"cam.recipe.preview"})

_NC_ACTION_MARKERS = (
    "nc_program.write",
    "output.postprocess",
    "postprocess",
    "machine_code",
    "machine.control",
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
    r"(?:\bPOST\s*PROCESS(?:ING)?\b|\bN\s*C\s*PROGRAM\s+(?:WRITE|POST)\b"
    r"|\bG\s*[-_ ]?\s*CODE\b|\bMACHINE\s*[_ -]?\s*CODE\b"
    r"|\bMACHINE\s*[-_ ]?\s*READY\b)",
    re.IGNORECASE,
)
_MACHINE_CODE_LINE = re.compile(
    r"(?im)(?:^|[;\r\n])\s*(?:N\s*\d+\s+)?(?:G\s*0?\s*\d|M\s*0?\s*\d)\b"
)
_LIVE_WRITE = re.compile(
    r"\b(?:CREATE|EDIT|CALCULATE|ACTIVATE|IMPORT|EXPORT|SAVE|WRITE|DELETE|"
    r"REMOVE|ERASE|EXECUTE|RUN)\b",
    re.IGNORECASE,
)
_BASE64_TOKEN = re.compile(r"^[A-Za-z0-9+/_-]{8,}={0,2}$")
_COMPACT_DANGER_MARKERS = (
    "gcode",
    "machinecode",
    "machineready",
    "ncprogram",
    "nccode",
    "ncoutput",
    "postprocess",
    "postprocessor",
    "runjournal",
    "journalsource",
    "machinecommand",
    "machinecontrol",
    "dnc",
    "mdi",
)
_DANGEROUS_ARGUMENT_KEYS = frozenset(
    {
        "gcode",
        "journalcode",
        "journalsource",
        "live",
        "livecommand",
        "machinecommand",
        "machinecontrol",
        "machinecode",
        "machineready",
        "nc",
        "nccode",
        "ncoutput",
        "ncprogram",
        "postprocess",
        "postprocessor",
        "productionoutput",
    }
)
_SENSITIVE_AUDIT_KEYS = frozenset(
    {
        "accesstoken",
        "apikey",
        "authorization",
        "credential",
        "credentials",
        "password",
        "privatekey",
        "secret",
        "token",
    }
)
_PATH_AUDIT_KEYS = frozenset(
    {
        "directory",
        "filepath",
        "localpath",
        "path",
        "sourcefile",
        "targetfile",
    }
)
_LOCAL_PATH = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\|/(?:home|Users|var|tmp|opt|mnt)/)"
)
_INLINE_CREDENTIAL = re.compile(
    r"\b(?:api[_-]?key|authorization|password|secret|token)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


def _normalized_key(value: Any) -> str:
    decoded = unquote(html.unescape(str(value)))
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKC", decoded).lower())


def _security_variants(value: str) -> tuple[str, ...]:
    """Return bounded decoded variants used only for fail-closed inspection."""

    pending = [unicodedata.normalize("NFKC", value)]
    variants: list[str] = []
    seen: set[str] = set()
    while pending and len(variants) < 12:
        current = pending.pop(0)
        if current in seen:
            continue
        seen.add(current)
        variants.append(current)
        decoded_html = html.unescape(current)
        decoded_url = unquote(current)
        if decoded_html != current:
            pending.append(decoded_html)
        if decoded_url != current:
            pending.append(decoded_url)
        if re.search(r"\\(?:u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2})", current):
            decoded_escapes = re.sub(
                r"\\(?:u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2}))",
                lambda match: chr(int(match.group(1) or match.group(2), 16)),
                current,
            )
            if decoded_escapes != current:
                pending.append(unicodedata.normalize("NFKC", decoded_escapes))
        compact = re.sub(r"\s+", "", current)
        if len(compact) >= 8 and len(compact) % 2 == 0 and re.fullmatch(
            r"[0-9a-fA-F]+",
            compact,
        ):
            try:
                decoded_hex = bytes.fromhex(compact).decode("utf-8")
            except (UnicodeDecodeError, ValueError):
                pass
            else:
                pending.append(unicodedata.normalize("NFKC", decoded_hex))
        if _BASE64_TOKEN.fullmatch(compact):
            padded = compact + "=" * (-len(compact) % 4)
            for decoder in (base64.b64decode, base64.urlsafe_b64decode):
                try:
                    decoded = decoder(padded).decode("utf-8")
                except (binascii.Error, UnicodeDecodeError, ValueError):
                    continue
                if decoded and decoded != current:
                    pending.append(unicodedata.normalize("NFKC", decoded))
    return tuple(variants)


def _dangerous_text(value: str, *, machine_output_only: bool = False) -> bool:
    for variant in _security_variants(value):
        lowered = variant.casefold()
        compact = re.sub(r"[^a-z0-9]+", "", lowered)
        if _MACHINE_CODE_LINE.search(variant):
            return True
        if any(marker in compact for marker in _COMPACT_DANGER_MARKERS):
            return True
        if _NC_COMMAND.search(variant):
            return True
        if machine_output_only:
            continue
        if _BLOCKED_COMMAND.search(variant):
            return True
        if _LIVE_WRITE.search(variant):
            return True
        if re.search(
            r"\b(?:machine)\s+(?:run|start|jog|home|move|control)\b",
            lowered,
        ):
            return True
    return False


def contains_forbidden_payload(value: Any, *, key: str = "") -> bool:
    """Inspect nested input without interpreting product-specific syntax."""

    if key and _normalized_key(key) in _DANGEROUS_ARGUMENT_KEYS:
        return value not in (None, False, "", [], {})
    if isinstance(value, Mapping):
        return any(
            contains_forbidden_payload(item, key=str(item_key))
            for item_key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(contains_forbidden_payload(item) for item in value)
    return isinstance(value, str) and _dangerous_text(value)


def contains_machine_ready_output(value: Any, *, key: str = "") -> bool:
    """Second-pass response inspection; callers must discard a matching payload."""

    normalized = _normalized_key(key)
    if normalized in _DANGEROUS_ARGUMENT_KEYS:
        return value not in (None, False, "", [], {})
    if isinstance(value, Mapping):
        return any(
            contains_machine_ready_output(item, key=str(item_key))
            for item_key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(contains_machine_ready_output(item) for item in value)
    return isinstance(value, str) and _dangerous_text(
        value,
        machine_output_only=True,
    )


def classify_command(
    *,
    product: str,
    task_type: str,
    execution_mode: str,
    operation: str,
    arguments: Mapping[str, Any],
) -> tuple[str, ...]:
    """Layer one: classify the requested behavior independently of a transport."""

    reasons: list[str] = []
    normalized_product = product.strip().lower()
    normalized_task_type = task_type.strip().lower()
    normalized_mode = execution_mode.strip().lower().replace("-", "_")
    normalized_operation = operation.strip().casefold()
    if normalized_product not in READ_ONLY_OPERATIONS:
        reasons.append("unsupported product")
    if normalized_task_type == "query":
        if normalized_mode != "read_only":
            reasons.append("query tasks require read_only execution")
    elif normalized_task_type == "recipe_preview":
        if normalized_mode != "dry_run":
            reasons.append("recipe preview tasks require dry_run execution")
    else:
        reasons.append("unsupported task type")
    if not normalized_operation:
        reasons.append("missing operation")
    if _dangerous_text(operation):
        reasons.append("operation is classified as unsafe or machine-ready")
    if contains_forbidden_payload(arguments):
        reasons.append("arguments contain an unsafe, live-write, or machine-ready payload")
    return tuple(dict.fromkeys(reasons))


def transport_operation_allowed(
    *,
    product: str,
    task_type: str,
    operation: str,
) -> bool:
    """Layer two: exact transport-facing allowlist after classification."""

    normalized_product = product.strip().lower()
    normalized_type = task_type.strip().lower()
    normalized_operation = operation.strip().casefold()
    if normalized_type == "query":
        allowed = READ_ONLY_OPERATIONS.get(normalized_product, frozenset())
    elif normalized_type == "recipe_preview":
        allowed = PREVIEW_OPERATIONS
    else:
        return False
    return normalized_operation in {item.casefold() for item in allowed}


def redact_for_audit(value: Any, *, key: str = "") -> Any:
    """Return a JSON-safe audit value without credentials or sensitive local paths."""

    normalized = _normalized_key(key)
    if normalized in _SENSITIVE_AUDIT_KEYS:
        return "<redacted>"
    if (
        normalized in _PATH_AUDIT_KEYS
        and value not in (None, "")
        and not (isinstance(value, str) and value.startswith("local:"))
    ):
        return "<redacted-local-path>"
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_for_audit(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact_for_audit(item) for item in value]
    if isinstance(value, str):
        if _INLINE_CREDENTIAL.search(value):
            return "<redacted>"
        if _LOCAL_PATH.search(value.strip()):
            return "<redacted-local-path>"
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


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
    target_instance_id: str = ""
    project_id: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ExecutionRequest:
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
            target_instance_id=str(value.get("target_instance_id", "")).strip(),
            project_id=str(value.get("project_id", "")).strip(),
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
    """Backward-compatible synchronous dry-run gate."""

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
        if request.mode != "dry-run" and not request.target_instance_id:
            reasons.append("live execution requires an explicit target CAM instance")
        if (
            request.mode != "dry-run"
            and request.target_instance_id
            and not request.target_instance_id.startswith(f"{request.product}:")
        ):
            reasons.append("target CAM instance does not match the requested product")
        if request.mode != "dry-run":
            reasons.append("live execution is outside the v1 execution contract")
        if (request.product, request.mode) not in self.transports:
            reasons.append(
                f"{request.product} transport '{request.mode}' is not configured for this version"
            )
        return list(dict.fromkeys(reasons))

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
        if contains_machine_ready_output(result):
            result = {
                "status": "rejected",
                "reasons": ["transport response contained forbidden machine-ready output"],
            }
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
        return json.loads(json.dumps(redact_for_audit(value)))
