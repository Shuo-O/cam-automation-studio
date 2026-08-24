from __future__ import annotations

import hashlib
import json
import math
import ntpath
import posixpath
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4


PROTOCOL = "cam.codex.bridge.v1"
SCHEMA_VERSION = 1
REQUEST_TYPE = "workflow_review"
EXECUTION_MODE = "dry-run"

MAX_JSON_BYTES = 1_000_000
MAX_SOURCE_EVENTS = 200
MAX_QUESTIONS = 50
MAX_FINDINGS = 500
MAX_PARAMETERS = 250
MAX_JSON_DEPTH = 20
MAX_JSON_ITEMS = 20_000
MAX_STRING_CHARS = 32_768

REQUIRED_PRODUCTION_GATES = (
    "recipe_review",
    "target_version_validation",
    "cam_simulation",
    "collision_check",
    "shop_approval",
)
GATE_CONTEXT = {
    "human_review": "required",
    "execution_mode": EXECUTION_MODE,
    "target_version_validation": "required",
    "cam_simulation": "required",
    "collision_check": "required",
    "shop_approval": "required",
    "machine_ready_nc": "prohibited",
}

_PRODUCTS = {"nx", "powermill"}
_REVIEW_STATUSES = {
    "needs_review",
    "needs_changes",
    "approved_for_simulation",
    "rejected",
}
_FINDING_SEVERITIES = {"info", "warning", "error", "blocker"}
_RECIPE_STATUSES = {
    "draft",
    "review_required",
    "approved_for_simulation",
    "rejected",
    "retired",
}
_PARAMETER_TYPES = {
    "string",
    "integer",
    "number",
    "boolean",
    "enum",
    "path",
    "object_selector",
}
_STEP_RISKS = {"safe", "review", "blocked"}
_STEP_REVIEW_STATUSES = {"unreviewed", "needs_review", "accepted", "rejected"}
_SOURCE_MODES = {"manual", "automation", "system", "execution_audit"}
_VIEW_LEVELS = {"L0", "L1", "L2", "L3", "L4"}
_EXPERTISE_LABELS = {"unlabeled", "routine", "expert"}
_EVENT_REVIEW_STATUSES = {
    "unreviewed",
    "needs_review",
    "needs_changes",
    "approved_for_simulation",
    "rejected",
}
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_PARAMETER_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_SENSITIVE_KEYS = {
    "accesstoken",
    "apikey",
    "apitoken",
    "authorization",
    "bearertoken",
    "clientsecret",
    "credential",
    "credentials",
    "password",
    "privatekey",
    "refreshtoken",
    "secret",
    "token",
}
_PATH_KEYS = {
    "directory",
    "filepath",
    "folder",
    "outputpath",
    "path",
    "projectpath",
    "sourcefile",
    "sourcepath",
}
_REMOTE_KEYS = {
    "externaldestination",
    "remoteendpoint",
    "upload",
    "uploaddestination",
    "uploadurl",
    "webhook",
}
_MACHINE_OUTPUT_KEYS = {
    "gcode",
    "machinecode",
    "machinereadync",
    "machinereadyncincluded",
    "nccode",
    "ncoutput",
    "ncprogram",
    "postprocess",
    "postprocessor",
}
_DANGEROUS_ACTION_PARTS = {
    "axisjog",
    "axismove",
    "executelive",
    "executejournal",
    "gcode",
    "journalexecute",
    "journalexecutelive",
    "journallive",
    "machinecontrol",
    "machinejog",
    "machinemove",
    "machineoutput",
    "machinerun",
    "machinesend",
    "machinestart",
    "machinestop",
    "machinewrite",
    "ncoutput",
    "ncprogram",
    "ncwrite",
    "postprocess",
    "postprocessor",
    "spindlestart",
    "toolchange",
}
_DANGEROUS_PROMPT_PARTS = (
    "execute journal live",
    "generate g-code",
    "generate machine code",
    "generate nc code",
    "ignore dry-run",
    "ignore dry run",
    "postprocess this",
    "run journal live",
    "send to machine",
    "switch to live",
)

__all__ = [
    "CodexReviewRequest",
    "CodexReviewResult",
    "ReviewFinding",
    "ReviewQuestion",
    "SuggestedParameter",
    "parse_review_request_json",
    "parse_review_result_json",
    "review_request",
    "validate_review",
    "write_exchange",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalized_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _is_sensitive_key(value: str) -> bool:
    normalized = _normalized_key(value)
    return normalized in _SENSITIVE_KEYS or normalized.endswith(
        (
            "accesskey",
            "accesstoken",
            "apikey",
            "apitoken",
            "authorization",
            "clientsecret",
            "credential",
            "credentials",
            "password",
            "privatekey",
            "refreshtoken",
            "secret",
            "token",
        )
    )


def _is_remote_key(value: str) -> bool:
    normalized = _normalized_key(value)
    return normalized in _REMOTE_KEYS or normalized.endswith(
        (
            "externaldestination",
            "remoteendpoint",
            "uploaddestination",
            "uploadendpoint",
            "uploaduri",
            "uploadurl",
            "webhook",
        )
    )


def _is_path_key(value: str) -> bool:
    normalized = _normalized_key(value)
    return normalized in _PATH_KEYS or normalized.endswith(
        (
            "directory",
            "filepath",
            "folder",
            "inputfile",
            "outputfile",
            "path",
            "sourcefile",
        )
    )


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object.")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field_name} keys must be strings.")
    return value


def _array(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a JSON array.")
    return value


def _string(
    value: Any,
    field_name: str,
    *,
    allow_empty: bool = False,
    max_chars: int = MAX_STRING_CHARS,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    if len(value) > max_chars:
        raise ValueError(f"{field_name} exceeds the {max_chars} character limit.")
    if not allow_empty and not value.strip():
        raise ValueError(f"{field_name} must not be empty.")
    return value


def _integer(value: Any, field_name: str, *, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise ValueError(f"{field_name} must be an integer.")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field_name} must be at least {minimum}.")
    return value


def _boolean(value: Any, field_name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field_name} must be a boolean.")
    return value


def _timestamp(value: Any, field_name: str) -> str:
    text = _string(value, field_name)
    if "T" not in text:
        raise ValueError(f"{field_name} must be an RFC 3339 timestamp.")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field_name} must be an RFC 3339 timestamp.") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone.")
    return text


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError("Value must contain only finite JSON data.") from error


def _check_json_limits(value: Any) -> None:
    pending: list[tuple[Any, int]] = [(value, 0)]
    item_count = 0
    while pending:
        current, depth = pending.pop()
        item_count += 1
        if item_count > MAX_JSON_ITEMS:
            raise ValueError(f"JSON input exceeds the {MAX_JSON_ITEMS} item limit.")
        if depth > MAX_JSON_DEPTH:
            raise ValueError(f"JSON input exceeds the {MAX_JSON_DEPTH} level depth limit.")
        if isinstance(current, str):
            if len(current) > MAX_STRING_CHARS:
                raise ValueError(
                    f"JSON string exceeds the {MAX_STRING_CHARS} character limit."
                )
        elif current is None or type(current) in {bool, int}:
            continue
        elif type(current) is float:
            if not math.isfinite(current):
                raise ValueError("JSON numbers must be finite.")
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
        elif isinstance(current, Mapping):
            if any(not isinstance(key, str) for key in current):
                raise ValueError("JSON object keys must be strings.")
            pending.extend((item, depth + 1) for item in current.values())
        else:
            raise ValueError(f"Unsupported JSON value type: {type(current).__name__}.")


def _check_serialized_size(value: Any) -> None:
    _check_json_limits(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("Value must be valid JSON.") from error
    if len(encoded) > MAX_JSON_BYTES:
        raise ValueError(f"Codex exchange exceeds the {MAX_JSON_BYTES} byte limit.")


def _strict_json_loads(payload: str | bytes | bytearray) -> Mapping[str, Any]:
    if isinstance(payload, str):
        encoded = payload.encode("utf-8")
        text = payload
    elif isinstance(payload, (bytes, bytearray)):
        encoded = bytes(payload)
        try:
            text = encoded.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("Codex exchange JSON must be UTF-8.") from error
    else:
        raise TypeError("JSON payload must be str, bytes, or bytearray.")
    if len(encoded) > MAX_JSON_BYTES:
        raise ValueError(f"Codex exchange exceeds the {MAX_JSON_BYTES} byte limit.")

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON object key: {key}")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise ValueError(f"Invalid JSON number: {value}")

    try:
        value = json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_constant=invalid_constant,
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid Codex exchange JSON: {error.msg}.") from error
    except RecursionError as error:
        raise ValueError(
            f"JSON input exceeds the {MAX_JSON_DEPTH} level depth limit."
        ) from error
    result = _mapping(value, "Codex exchange")
    _check_json_limits(result)
    return result


def _looks_absolute_path(value: str) -> bool:
    return (
        ntpath.isabs(value)
        or posixpath.isabs(value)
        or value.startswith("\\\\")
        or value.startswith("//")
        or value.startswith(("~/", "~\\"))
    )


def _looks_path_like(value: str) -> bool:
    return _looks_absolute_path(value) or "/" in value or "\\" in value


def _redact_embedded_paths(value: str) -> str:
    characters = list(value)
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(value):
        windows_start = (
            index + 2 < len(value)
            and value[index].isalpha()
            and value[index + 1] == ":"
            and value[index + 2] in {"\\", "/"}
        )
        unc_start = value.startswith("\\\\", index)
        previous = value[index - 1] if index > 0 else ""
        home_start = (
            value.startswith(("~/", "~\\"), index)
            and (index == 0 or previous.isspace() or previous in {"'", '"', "=", "("})
        )
        relative_start = (
            value.startswith(("../", "..\\", "./", ".\\"), index)
            and (index == 0 or previous.isspace() or previous in {"'", '"', "=", "("})
        )
        posix_start = (
            value[index] == "/"
            and not value.startswith("//", index)
            and index + 1 < len(value)
            and (index == 0 or previous.isspace() or previous in {"'", '"', "=", "("})
            and (value[index + 1].isalnum() or value[index + 1] in {".", "_", "~"})
        )
        if not any(
            (windows_start, unc_start, home_start, relative_start, posix_start)
        ):
            index += 1
            continue
        quote = value[index - 1] if index > 0 and value[index - 1] in {"'", '"'} else None
        if windows_start or value.startswith(("../", "..\\"), index):
            end = index + 3
        elif unc_start or home_start or relative_start:
            end = index + 2
        else:
            end = index + 1
        stop_characters = {quote} if quote is not None else {"'", '"', "\r", "\n", "\t", " "}
        while end < len(value) and value[end] not in stop_characters:
            end += 1
        spans.append((index, end))
        index = end
    for start, end in reversed(spans):
        characters[start:end] = list("<redacted-path>")
    return "".join(characters)


def _sanitize_string(value: str, key: str) -> str:
    lowered = value.casefold().strip()
    compact = "".join(lowered.split())
    credential_markers = (
        "apikey=",
        "apikey:",
        "authorization:basic",
        "authorization:bearer",
        "authorization=basic",
        "authorization=bearer",
        "clientsecret=",
        "credential=",
        "credentials=",
        "pass=",
        "pass:",
        "password=",
        "password:",
        "privatekey=",
        "pwd=",
        "pwd:",
        "refreshtoken=",
        "secret=",
        "token=",
        "token:",
    )
    if lowered.startswith("bearer ") or any(
        marker in compact for marker in credential_markers
    ):
        return "<redacted>"
    if _is_path_key(key) and _looks_path_like(value):
        filename = ntpath.basename(value.replace("/", "\\"))
        return f"<redacted-path>/{filename}" if filename else "<redacted-path>"
    return _redact_embedded_paths(value)


def _sanitize_json(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if depth > MAX_JSON_DEPTH:
        raise ValueError(f"JSON input exceeds the {MAX_JSON_DEPTH} level depth limit.")
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise ValueError("JSON object keys must be strings.")
            normalized = _normalized_key(raw_key)
            if _is_sensitive_key(raw_key):
                result[raw_key] = "<redacted>"
            elif _is_remote_key(raw_key) and item not in (None, "", [], {}):
                raise ValueError("Undeclared external uploads or remote destinations are blocked.")
            else:
                result[raw_key] = _sanitize_json(item, key=raw_key, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [_sanitize_json(item, key=key, depth=depth + 1) for item in value]
    if isinstance(value, str):
        return _sanitize_string(value, key)
    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError(f"Unsupported JSON value type: {type(value).__name__}.")


def _looks_like_machine_code(value: str) -> bool:
    for line in value.splitlines() or [value]:
        tokens = line.strip().upper().split()
        for raw_token in tokens:
            token = raw_token.strip("()[]{}<>,;:'\"").lstrip("/")
            if token.startswith("%"):
                return True
            if token.startswith("N"):
                index = 1
                while index < len(token) and token[index].isdigit():
                    index += 1
                token = token[index:]
            if len(token) > 1 and token[0] in {"G", "M"} and token[1].isdigit():
                return True
    return False


def _action_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    current: list[str] = []
    for character in value.casefold():
        if character.isalnum():
            current.append(character)
        elif current:
            tokens.add("".join(current))
            current = []
    if current:
        tokens.add("".join(current))
    return tokens


def _is_dangerous_action(value: str) -> bool:
    normalized = _normalized_key(value)
    if any(part in normalized for part in _DANGEROUS_ACTION_PARTS):
        return True
    tokens = _action_tokens(value)
    if "live" in tokens:
        return True
    if tokens.intersection({"gcode", "nc", "nccode", "ncprogram", "postprocess"}):
        return True
    namespaces = {"cam", "nx", "powermill"}
    read_only = {
        "analyze",
        "compare",
        "describe",
        "evidence",
        "inspect",
        "list",
        "observed",
        "parse",
        "preview",
        "query",
        "read",
        "review",
        "source",
        "static",
        "status",
    }
    if "journal" in tokens:
        allowed = namespaces | read_only | {
            "event",
            "journal",
            "line",
            "metadata",
            "recorded",
            "replay",
            "state",
        }
        return not tokens.issubset(allowed) or not tokens.intersection(read_only)
    if tokens.intersection({"axis", "machine", "spindle"}):
        allowed = namespaces | read_only | {
            "axis",
            "configuration",
            "machine",
            "metadata",
            "position",
            "spindle",
            "state",
        }
        return not tokens.issubset(allowed) or not tokens.intersection(read_only)
    if "tool" in tokens and tokens.intersection({"change", "load", "unload"}):
        return True
    return False


def _requests_dangerous_operation(value: str) -> bool:
    lowered = " ".join(value.casefold().split())
    legacy_safe_instruction = (
        "review the recipe for correctness, missing preconditions, unsafe actions, "
        "and target-version assumptions. return review_status, findings, and "
        "required_gates. do not produce or execute machine-ready nc code."
    )
    if lowered == legacy_safe_instruction:
        return False
    if any(phrase in lowered for phrase in _DANGEROUS_PROMPT_PARTS):
        return True
    tokens = _action_tokens(value)
    domains = {"axis", "gcode", "journal", "machine", "nc", "postprocess", "spindle"}
    if not tokens.intersection(domains):
        return False
    if tokens.intersection({"live", "production"}):
        return True
    mutations = {
        "advance",
        "dispatch",
        "emit",
        "execute",
        "jog",
        "launch",
        "move",
        "off",
        "on",
        "produce",
        "rotate",
        "run",
        "save",
        "send",
        "start",
        "stop",
        "write",
    }
    if tokens.intersection(mutations):
        return True
    read_only = {
        "analyze",
        "compare",
        "describe",
        "inspect",
        "list",
        "preview",
        "query",
        "read",
        "review",
        "status",
    }
    return not tokens.intersection(read_only)


def _assert_safe(value: Any, *, path: str = "request") -> None:
    pending: list[tuple[str, Any]] = [(path, value)]
    while pending:
        current_path, current = pending.pop()
        if isinstance(current, Mapping):
            for key, item in current.items():
                normalized = _normalized_key(str(key))
                child = f"{current_path}.{key}"
                if normalized in _MACHINE_OUTPUT_KEYS and item not in (None, "", False, [], {}):
                    raise ValueError("Machine-ready NC, G-code, and postprocessing are prohibited.")
                if _is_remote_key(str(key)) and item not in (None, "", [], {}):
                    raise ValueError(
                        "Undeclared external uploads or remote destinations are blocked."
                    )
                if normalized in {"executionmode", "runmode"} and isinstance(item, str):
                    if item.casefold().replace("_", "-") not in {"dry-run", "read-only"}:
                        raise ValueError("CAM execution must remain read-only or dry-run.")
                if normalized in {"action", "operation", "tasktype"} and isinstance(item, str):
                    if _is_dangerous_action(item):
                        raise ValueError(
                            "Machine-ready NC, postprocessing, and machine control are prohibited."
                        )
                if isinstance(item, str):
                    lowered = item.casefold().strip()
                    if _looks_like_machine_code(item):
                        raise ValueError(
                            "Machine-ready NC, G-code, and postprocessing are prohibited."
                        )
                    if normalized in {
                        "command",
                        "macro",
                        "script",
                        "proposedvalue",
                    } and ("postprocess" in lowered or "write nc" in lowered):
                        raise ValueError(
                            "Machine-ready NC, G-code, and postprocessing are prohibited."
                        )
                pending.append((child, item))
        elif isinstance(current, list):
            pending.extend(
                (f"{current_path}[{index}]", item)
                for index, item in enumerate(current)
            )
        elif isinstance(current, str):
            if _looks_like_machine_code(current):
                raise ValueError(
                    "Machine-ready NC, G-code, and postprocessing are prohibited."
                )
            free_text_field = (
                ".questions" in current_path
                or current_path.endswith(
                    (
                        ".command",
                        ".description",
                        ".instruction",
                        ".macro",
                        ".message",
                        ".notes",
                        ".question",
                        ".rationale",
                        ".script",
                        ".summary",
                        ".template",
                        ".text",
                    )
                )
            )
            prompt_field = free_text_field
            if prompt_field and _requests_dangerous_operation(current):
                raise ValueError(
                    "Prompt content cannot request live Journal execution, machine control, "
                    "postprocessing, NC, or G-code."
                )


def _event_ref(value: Any, field_name: str) -> dict[str, Any]:
    item = _mapping(value, field_name)
    return {
        "event_session_id": _string(
            item.get("event_session_id"),
            f"{field_name}.event_session_id",
        ),
        "seq": _integer(item.get("seq"), f"{field_name}.seq", minimum=0),
    }


def _string_array(
    value: Any,
    field_name: str,
    *,
    maximum: int | None = None,
) -> list[str]:
    items = _array(value, field_name)
    if maximum is not None and len(items) > maximum:
        raise ValueError(f"{field_name} exceeds the {maximum} item limit.")
    return [
        _string(item, f"{field_name}[{index}]")
        for index, item in enumerate(items)
    ]


def _validate_parameter_value(
    value_type: str,
    value: Any,
    field_name: str,
    enum_values: Sequence[Any],
) -> None:
    if value is None:
        return
    valid = True
    if value_type in {"string", "path", "object_selector"}:
        valid = isinstance(value, str)
    elif value_type == "integer":
        valid = type(value) is int
    elif value_type == "number":
        valid = type(value) in {int, float} and (
            type(value) is not float or math.isfinite(value)
        )
    elif value_type == "boolean":
        valid = type(value) is bool
    elif value_type == "enum":
        valid = value in enum_values
    if not valid:
        raise ValueError(f"{field_name} does not match value_type {value_type}.")


def _assert_product_action(
    product: str,
    action: str,
    field_name: str,
    *,
    allow_legacy_nx: bool = False,
) -> None:
    if action.startswith(("part.", "object.")):
        if product == "nx" and allow_legacy_nx:
            return
        raise ValueError(f"{field_name} uses a legacy NX-only action outside NX event evidence.")
    if not action.startswith(("cam.", "nx.", "powermill.")):
        raise ValueError(f"{field_name} must use cam.*, nx.*, or powermill.*.")
    if product == "nx" and action.startswith("powermill."):
        raise ValueError(f"{field_name} crosses the NX product boundary.")
    if product == "powermill" and action.startswith("nx."):
        raise ValueError(f"{field_name} crosses the PowerMill product boundary.")


def _normalize_recipe(value: Any, expected_product: str) -> dict[str, Any]:
    item = _mapping(value, "recipe")
    _assert_safe(item, path="request.recipe")
    if _integer(item.get("schema_version"), "recipe.schema_version") != SCHEMA_VERSION:
        raise ValueError("recipe.schema_version must be 1.")
    product = _string(item.get("product"), "recipe.product").casefold()
    if product != expected_product:
        raise ValueError("recipe.product must match the review request product.")
    recipe_hash = _string(item.get("recipe_hash"), "recipe.recipe_hash")
    if not _HASH.fullmatch(recipe_hash):
        raise ValueError("recipe.recipe_hash must be sha256:<64 lowercase hex>.")
    status = _string(item.get("status"), "recipe.status")
    if status not in _RECIPE_STATUSES:
        raise ValueError("recipe.status is invalid.")

    target_versions = _string_array(item.get("target_versions"), "recipe.target_versions")
    source_session_ids = _string_array(
        item.get("source_session_ids"),
        "recipe.source_session_ids",
    )
    support = _mapping(item.get("support"), "recipe.support")
    matched_sessions = _integer(
        support.get("matched_sessions"),
        "recipe.support.matched_sessions",
        minimum=0,
    )
    total_sessions = _integer(
        support.get("total_sessions"),
        "recipe.support.total_sessions",
        minimum=0,
    )
    ratio = support.get("ratio")
    if type(ratio) not in {int, float} or not math.isfinite(float(ratio)):
        raise ValueError("recipe.support.ratio must be a finite number.")
    if not 0 <= float(ratio) <= 1 or matched_sessions > total_sessions:
        raise ValueError("recipe.support values are inconsistent.")

    raw_parameters = _array(item.get("parameters"), "recipe.parameters")
    parameters: list[dict[str, Any]] = []
    seen_parameters: set[str] = set()
    for index, raw_parameter in enumerate(raw_parameters):
        prefix = f"recipe.parameters[{index}]"
        parameter = _mapping(raw_parameter, prefix)
        if "default" not in parameter:
            raise ValueError(f"{prefix}.default is required.")
        name = _string(parameter.get("name"), f"{prefix}.name")
        if not _PARAMETER_NAME.fullmatch(name) or name in seen_parameters:
            raise ValueError(f"{prefix}.name is invalid or duplicated.")
        seen_parameters.add(name)
        value_type = _string(parameter.get("value_type"), f"{prefix}.value_type")
        if value_type not in _PARAMETER_TYPES:
            raise ValueError(f"{prefix}.value_type is invalid.")
        required = _boolean(parameter.get("required"), f"{prefix}.required")
        samples = _array(parameter.get("samples"), f"{prefix}.samples")
        description = _sanitize_string(
            _string(
                parameter.get("description"),
                f"{prefix}.description",
                allow_empty=True,
            ),
            "description",
        )
        constraints = _mapping(
            parameter.get("constraints", {}),
            f"{prefix}.constraints",
        )
        enum_values = _array(
            parameter.get("enum_values", []),
            f"{prefix}.enum_values",
        )
        if value_type == "enum" and not enum_values:
            raise ValueError(f"{prefix}.enum_values must not be empty for enum parameters.")
        default = _sanitize_json(parameter.get("default"), key="default")
        sanitized_samples = [_sanitize_json(sample, key="sample") for sample in samples]
        sanitized_enum_values = [
            _sanitize_json(enum_value, key="enum_value") for enum_value in enum_values
        ]
        _validate_parameter_value(
            value_type,
            default,
            f"{prefix}.default",
            sanitized_enum_values,
        )
        for sample_index, sample in enumerate(sanitized_samples):
            _validate_parameter_value(
                value_type,
                sample,
                f"{prefix}.samples[{sample_index}]",
                sanitized_enum_values,
            )
        normalized: dict[str, Any] = {
            "name": name,
            "value_type": value_type,
            "required": required,
            "default": default,
            "samples": sanitized_samples,
            "description": description,
        }
        if constraints:
            normalized["constraints"] = _sanitize_json(constraints, key="constraints")
        if sanitized_enum_values:
            normalized["enum_values"] = sanitized_enum_values
        if "source_event_refs" in parameter:
            refs = _array(
                parameter["source_event_refs"],
                f"{prefix}.source_event_refs",
            )
            normalized["source_event_refs"] = [
                _event_ref(ref, f"{prefix}.source_event_refs[{ref_index}]")
                for ref_index, ref in enumerate(refs)
            ]
        parameters.append(normalized)

    raw_steps = _array(item.get("steps"), "recipe.steps")
    steps: list[dict[str, Any]] = []
    seen_step_ids: set[str] = set()
    orders: list[int] = []
    for index, raw_step in enumerate(raw_steps):
        prefix = f"recipe.steps[{index}]"
        step = _mapping(raw_step, prefix)
        if "condition" not in step:
            raise ValueError(f"{prefix}.condition is required.")
        step_id = _string(step.get("step_id"), f"{prefix}.step_id")
        if step_id in seen_step_ids:
            raise ValueError(f"{prefix}.step_id is duplicated.")
        seen_step_ids.add(step_id)
        order = _integer(step.get("order"), f"{prefix}.order", minimum=1)
        orders.append(order)
        action = _string(step.get("action"), f"{prefix}.action")
        _assert_product_action(product, action, f"{prefix}.action")
        enabled = _boolean(step.get("enabled"), f"{prefix}.enabled")
        risk = _string(step.get("risk"), f"{prefix}.risk")
        if risk not in _STEP_RISKS:
            raise ValueError(f"{prefix}.risk is invalid.")
        review_status = _string(
            step.get("review_status"),
            f"{prefix}.review_status",
        )
        if review_status not in _STEP_REVIEW_STATUSES:
            raise ValueError(f"{prefix}.review_status is invalid.")
        arguments = _mapping(step.get("arguments"), f"{prefix}.arguments")
        condition = step.get("condition")
        if condition is not None:
            _mapping(condition, f"{prefix}.condition")
        raw_refs = _array(
            step.get("source_event_refs"),
            f"{prefix}.source_event_refs",
        )
        normalized_step: dict[str, Any] = {
            "step_id": step_id,
            "order": order,
            "action": action,
            "enabled": enabled,
            "risk": risk,
            "review_status": review_status,
            "arguments": _sanitize_json(arguments, key="arguments"),
            "condition": _sanitize_json(condition, key="condition"),
            "source_event_refs": [
                _event_ref(ref, f"{prefix}.source_event_refs[{ref_index}]")
                for ref_index, ref in enumerate(raw_refs)
            ],
        }
        if "notes" in step:
            normalized_step["notes"] = _sanitize_string(
                _string(
                    step["notes"],
                    f"{prefix}.notes",
                    allow_empty=True,
                ),
                "notes",
            )
        if "adapter_payload" in step:
            normalized_step["adapter_payload"] = _sanitize_json(
                _mapping(step["adapter_payload"], f"{prefix}.adapter_payload"),
                key="adapter_payload",
            )
        steps.append(normalized_step)
    if sorted(orders) != list(range(1, len(orders) + 1)):
        raise ValueError("recipe step order must be contiguous and start at 1.")

    required_gates = _string_array(
        item.get("required_gates"),
        "recipe.required_gates",
    )
    if not set(REQUIRED_PRODUCTION_GATES).issubset(required_gates):
        raise ValueError("recipe.required_gates is missing mandatory CAM safety gates.")
    normalized_recipe: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "recipe_id": _string(item.get("recipe_id"), "recipe.recipe_id"),
        "recipe_hash": recipe_hash,
        "name": _sanitize_string(_string(item.get("name"), "recipe.name"), "name"),
        "product": product,
        "status": status,
        "target_versions": target_versions,
        "source_session_ids": source_session_ids,
        "support": {
            "matched_sessions": matched_sessions,
            "total_sessions": total_sessions,
            "ratio": float(ratio),
        },
        "parameters": parameters,
        "steps": sorted(steps, key=lambda step: step["order"]),
        "required_gates": required_gates,
        "created_at": _timestamp(item.get("created_at"), "recipe.created_at"),
        "updated_at": _timestamp(item.get("updated_at"), "recipe.updated_at"),
    }
    if "description" in item:
        normalized_recipe["description"] = _sanitize_string(
            _string(
                item["description"],
                "recipe.description",
                allow_empty=True,
            ),
            "description",
        )
    if "project_conditions" in item:
        normalized_recipe["project_conditions"] = _sanitize_json(
            _mapping(item["project_conditions"], "recipe.project_conditions"),
            key="project_conditions",
        )
    if "branches" in item:
        raw_branches = _array(item["branches"], "recipe.branches")
        normalized_recipe["branches"] = [
            _sanitize_json(
                _mapping(branch, f"recipe.branches[{index}]"),
                key="branch",
            )
            for index, branch in enumerate(raw_branches)
        ]
    _assert_safe(normalized_recipe, path="request.recipe")
    return normalized_recipe


def _normalize_event(value: Any, expected_product: str, index: int) -> dict[str, Any]:
    prefix = f"source_events[{index}]"
    item = _mapping(value, prefix)
    if _integer(item.get("schema_version"), f"{prefix}.schema_version") != 1:
        raise ValueError(f"{prefix}.schema_version must be 1.")
    product = _string(item.get("product"), f"{prefix}.product").casefold()
    if product != expected_product:
        raise ValueError(f"{prefix}.product must match the review request product.")
    action = _string(item.get("action"), f"{prefix}.action")
    _assert_product_action(
        product,
        action,
        f"{prefix}.action",
        allow_legacy_nx=True,
    )
    mode = _string(item.get("mode", "manual"), f"{prefix}.mode")
    if mode not in {"manual", "automation", "system"}:
        raise ValueError(f"{prefix}.mode is invalid.")
    normalized: dict[str, Any] = {
        "schema_version": 1,
        "session_id": _string(item.get("session_id"), f"{prefix}.session_id"),
        "seq": _integer(item.get("seq"), f"{prefix}.seq", minimum=0),
        "product": product,
        "action": action,
        "category": _string(item.get("category"), f"{prefix}.category"),
        "mode": mode,
        "params": _sanitize_json(
            _mapping(item.get("params", {}), f"{prefix}.params"),
            key="params",
        ),
        "source_file": _sanitize_string(
            _string(
                item.get("source_file", ""),
                f"{prefix}.source_file",
                allow_empty=True,
            ),
            "source_file",
        ),
        "source_line": _integer(
            item.get("source_line", 0),
            f"{prefix}.source_line",
            minimum=0,
        ),
        "duration_ms": None,
        "timestamp": None,
    }
    if item.get("duration_ms") is not None:
        normalized["duration_ms"] = _integer(
            item["duration_ms"],
            f"{prefix}.duration_ms",
            minimum=0,
        )
    if item.get("timestamp") is not None:
        normalized["timestamp"] = _timestamp(
            item["timestamp"],
            f"{prefix}.timestamp",
        )
    if "source_mode" in item:
        source_mode = _string(item["source_mode"], f"{prefix}.source_mode")
        if source_mode not in _SOURCE_MODES:
            raise ValueError(f"{prefix}.source_mode is invalid.")
        if source_mode == "execution_audit" and mode != "automation":
            raise ValueError(
                f"{prefix}.mode must be automation for execution_audit compatibility."
            )
        normalized["source_mode"] = source_mode
    if "view_level" in item:
        view_level = _string(item["view_level"], f"{prefix}.view_level")
        if view_level not in _VIEW_LEVELS:
            raise ValueError(f"{prefix}.view_level is invalid.")
        normalized["view_level"] = view_level
    if "expertise_label" in item:
        expertise = _string(item["expertise_label"], f"{prefix}.expertise_label")
        if expertise not in _EXPERTISE_LABELS:
            raise ValueError(f"{prefix}.expertise_label is invalid.")
        normalized["expertise_label"] = expertise
    for optional_string in ("instance_id", "target_version"):
        if optional_string in item:
            normalized[optional_string] = _string(
                item[optional_string],
                f"{prefix}.{optional_string}",
            )
    if "project_id" in item:
        normalized["project_id"] = (
            None
            if item["project_id"] is None
            else _string(item["project_id"], f"{prefix}.project_id")
        )
    if "command_response" in item:
        normalized["command_response"] = (
            None
            if item["command_response"] is None
            else _sanitize_json(
                _mapping(item["command_response"], f"{prefix}.command_response"),
                key="command_response",
            )
        )
    if "review_status" in item:
        review_status = _string(item["review_status"], f"{prefix}.review_status")
        if review_status not in _EVENT_REVIEW_STATUSES:
            raise ValueError(f"{prefix}.review_status is invalid.")
        normalized["review_status"] = review_status
    if "recipe_hash" in item:
        recipe_hash = item["recipe_hash"]
        if recipe_hash is not None:
            recipe_hash = _string(recipe_hash, f"{prefix}.recipe_hash")
            if not _HASH.fullmatch(recipe_hash):
                raise ValueError(f"{prefix}.recipe_hash is invalid.")
        normalized["recipe_hash"] = recipe_hash
    _assert_safe(normalized, path=f"request.{prefix}")
    return normalized


def _normalize_session_diff(
    value: Any,
    expected_product: str,
) -> dict[str, Any] | None:
    if value is None:
        return None
    item = _mapping(value, "session_diff")
    if _integer(item.get("schema_version"), "session_diff.schema_version") != 1:
        raise ValueError("session_diff.schema_version must be 1.")
    session_ids = _string_array(item.get("session_ids"), "session_diff.session_ids")
    if not 2 <= len(session_ids) <= 5 or len(set(session_ids)) != len(session_ids):
        raise ValueError("session_diff.session_ids must contain 2 to 5 unique IDs.")
    baseline = _string(
        item.get("baseline_session_id"),
        "session_diff.baseline_session_id",
    )
    if baseline not in session_ids:
        raise ValueError("session_diff.baseline_session_id must belong to session_ids.")
    common_steps = _array(item.get("common_steps"), "session_diff.common_steps")
    session_deltas = _array(
        item.get("session_deltas"),
        "session_diff.session_deltas",
    )
    parameter_differences = _array(
        item.get("parameter_differences"),
        "session_diff.parameter_differences",
    )
    normalized_common_steps: list[dict[str, Any]] = []
    for index, raw_step in enumerate(common_steps):
        prefix = f"session_diff.common_steps[{index}]"
        step = _mapping(raw_step, prefix)
        action = _string(step.get("action"), f"{prefix}.action")
        _assert_product_action(
            expected_product,
            action,
            f"{prefix}.action",
            allow_legacy_nx=True,
        )
        normalized_step = _sanitize_json(step, key="common_step")
        normalized_step.update(
            {
                "step_key": _string(step.get("step_key"), f"{prefix}.step_key"),
                "order": _integer(step.get("order"), f"{prefix}.order", minimum=1),
                "action": action,
            }
        )
        normalized_common_steps.append(normalized_step)

    normalized_deltas: list[dict[str, Any]] = []
    delta_session_ids: list[str] = []
    for index, raw_delta in enumerate(session_deltas):
        prefix = f"session_diff.session_deltas[{index}]"
        delta = _mapping(raw_delta, prefix)
        session_id = _string(delta.get("session_id"), f"{prefix}.session_id")
        if session_id not in session_ids:
            raise ValueError(f"{prefix}.session_id is not in session_ids.")
        delta_session_ids.append(session_id)
        extra_steps = _array(delta.get("extra_steps"), f"{prefix}.extra_steps")
        normalized_extra_steps: list[dict[str, Any]] = []
        for extra_index, raw_extra_step in enumerate(extra_steps):
            extra_prefix = f"{prefix}.extra_steps[{extra_index}]"
            extra_step = _mapping(raw_extra_step, extra_prefix)
            normalized_extra_step = _sanitize_json(extra_step, key="extra_step")
            if "action" in extra_step:
                action = _string(extra_step["action"], f"{extra_prefix}.action")
                _assert_product_action(
                    expected_product,
                    action,
                    f"{extra_prefix}.action",
                    allow_legacy_nx=True,
                )
                normalized_extra_step["action"] = action
            normalized_extra_steps.append(normalized_extra_step)
        normalized_deltas.append(
            {
                "session_id": session_id,
                "missing_step_keys": _string_array(
                    delta.get("missing_step_keys"),
                    f"{prefix}.missing_step_keys",
                ),
                "extra_steps": normalized_extra_steps,
            }
        )
    if set(delta_session_ids) != set(session_ids) or len(delta_session_ids) != len(
        set(delta_session_ids)
    ):
        raise ValueError(
            "session_diff.session_deltas must contain each session exactly once."
        )

    normalized_differences: list[dict[str, Any]] = []
    for index, raw_difference in enumerate(parameter_differences):
        prefix = f"session_diff.parameter_differences[{index}]"
        difference = _mapping(raw_difference, prefix)
        values = _mapping(
            difference.get("values_by_session"),
            f"{prefix}.values_by_session",
        )
        if set(values) != set(session_ids):
            raise ValueError(f"{prefix}.values_by_session must cover every session.")
        normalized_differences.append(
            {
                "step_key": _string(
                    difference.get("step_key"),
                    f"{prefix}.step_key",
                ),
                "parameter": _string(
                    difference.get("parameter"),
                    f"{prefix}.parameter",
                ),
                "values_by_session": _sanitize_json(
                    values,
                    key="values_by_session",
                ),
            }
        )
    durations = _mapping(
        item.get("duration_ms_by_session"),
        "session_diff.duration_ms_by_session",
    )
    normalized_durations: dict[str, int] = {}
    for session_id, duration in durations.items():
        if session_id not in session_ids:
            raise ValueError("session_diff duration contains an unknown session ID.")
        normalized_durations[session_id] = _integer(
            duration,
            f"session_diff.duration_ms_by_session.{session_id}",
            minimum=0,
        )
    if set(normalized_durations) != set(session_ids):
        raise ValueError("session_diff must include duration for every session.")
    normalized = {
        "schema_version": 1,
        "diff_id": _string(item.get("diff_id"), "session_diff.diff_id"),
        "session_ids": session_ids,
        "baseline_session_id": baseline,
        "common_steps": normalized_common_steps,
        "session_deltas": normalized_deltas,
        "parameter_differences": normalized_differences,
        "duration_ms_by_session": normalized_durations,
        "created_at": _timestamp(item.get("created_at"), "session_diff.created_at"),
    }
    _assert_safe(normalized, path="request.session_diff")
    return normalized


def _legacy_recipe(
    value: Mapping[str, Any],
    *,
    product: str,
    source_events: Sequence[Mapping[str, Any]],
    target_version: str | None,
    now: str,
) -> dict[str, Any]:
    name = _string(value.get("name", f"{product}-workflow"), "recipe.name")
    source = _mapping(value.get("source", {}), "recipe.source")
    matched_session_ids = [
        item
        for item in source.get("sessions_matched", [])
        if isinstance(item, str) and item.strip()
    ]
    source_session_ids = matched_session_ids or sorted(
        {str(event["session_id"]) for event in source_events}
    )
    matched = len(source_session_ids)
    total = source.get("sessions_analyzed", matched)
    if type(total) is not int or total < matched:
        total = matched

    parameters: list[dict[str, Any]] = []
    raw_parameters = _array(value.get("parameters", []), "recipe.parameters")
    for index, raw in enumerate(raw_parameters):
        item = _mapping(raw, f"recipe.parameters[{index}]")
        value_type = _string(
            item.get("value_type", "string"),
            f"recipe.parameters[{index}].value_type",
        )
        if value_type not in _PARAMETER_TYPES:
            raise ValueError(f"recipe.parameters[{index}].value_type is invalid.")
        samples = _array(
            item.get("samples", []),
            f"recipe.parameters[{index}].samples",
        )
        parameters.append(
            {
                "name": _string(item.get("name"), f"recipe.parameters[{index}].name"),
                "value_type": value_type,
                "required": False,
                "default": _sanitize_json(
                    item.get("default"),
                    key="path" if value_type == "path" else "default",
                ),
                "samples": [
                    _sanitize_json(
                        sample,
                        key="path" if value_type == "path" else "sample",
                    )
                    for sample in samples
                ],
                "description": _string(
                    item.get("description", ""),
                    f"recipe.parameters[{index}].description",
                    allow_empty=True,
                ),
            }
        )

    refs_by_line: dict[int, list[dict[str, Any]]] = {}
    for event in source_events:
        refs_by_line.setdefault(int(event.get("source_line", 0)), []).append(
            {
                "event_session_id": str(event["session_id"]),
                "seq": int(event["seq"]),
            }
        )
    raw_steps = _array(value.get("steps", []), "recipe.steps")
    steps: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_steps):
        item = _mapping(raw, f"recipe.steps[{index}]")
        action = _string(item.get("action"), f"recipe.steps[{index}].action")
        _assert_product_action(product, action, f"recipe.steps[{index}].action")
        risk = _string(item.get("risk", "review"), f"recipe.steps[{index}].risk")
        if risk not in _STEP_RISKS:
            risk = "review"
        refs: list[dict[str, Any]] = []
        for line in item.get("source_lines", []):
            if type(line) is int:
                refs.extend(refs_by_line.get(line, []))
        notes = "; ".join(
            reason for reason in item.get("reasons", []) if isinstance(reason, str)
        )
        steps.append(
            {
                "step_id": _string(
                    item.get("step_id", f"step-{index + 1:03d}"),
                    f"recipe.steps[{index}].step_id",
                ),
                "order": index + 1,
                "action": action,
                "enabled": risk != "blocked",
                "risk": risk,
                "review_status": "rejected" if risk == "blocked" else "needs_review",
                "arguments": {
                    "template": _sanitize_string(
                        _string(
                            item.get("template", ""),
                            f"recipe.steps[{index}].template",
                            allow_empty=True,
                        ),
                        "template",
                    )
                },
                "condition": None,
                "source_event_refs": refs,
                "notes": notes,
            }
        )

    identity = hashlib.sha256(f"{product}\0{name}".encode("utf-8")).hexdigest()[:24]
    recipe: dict[str, Any] = {
        "schema_version": 1,
        "recipe_id": f"recipe:{product}:{identity}",
        "recipe_hash": "sha256:" + ("0" * 64),
        "name": name,
        "product": product,
        "status": "review_required",
        "target_versions": [target_version] if target_version else [],
        "source_session_ids": source_session_ids,
        "support": {
            "matched_sessions": matched,
            "total_sessions": total,
            "ratio": (matched / total) if total else 0.0,
        },
        "parameters": parameters,
        "steps": steps,
        "required_gates": list(REQUIRED_PRODUCTION_GATES),
        "created_at": now,
        "updated_at": now,
        "description": "Migrated from the legacy local review context.",
        "project_conditions": {
            "legacy_context": True,
            "test_copy_required": True,
        },
        "branches": [],
    }
    from .recipes import compute_recipe_hash

    recipe["recipe_hash"] = compute_recipe_hash(recipe)
    return _normalize_recipe(recipe, product)


def _normalize_project(
    raw: Any,
    source_events: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], str]:
    if raw is not None:
        if isinstance(raw, str):
            return {"project_id": _string(raw, "project")}, "explicit"
        item = _mapping(raw, "project")
        project_id = item.get("project_id", item.get("id"))
        result: dict[str, Any] = {
            "project_id": _string(project_id, "project.project_id")
        }
        for name in ("project_name", "snapshot_id", "units"):
            if name in item:
                result[name] = _string(item[name], f"project.{name}")
        return _sanitize_json(result, key="project"), "explicit"
    project_ids = {
        event["project_id"]
        for event in source_events
        if event.get("project_id") not in (None, "")
    }
    if len(project_ids) == 1:
        return {"project_id": str(next(iter(project_ids)))}, "derived"
    return {"project_id": "unresolved", "state": "offline_review_only"}, "unresolved"


def _derived_string(
    explicit: Any,
    field_name: str,
    values: Sequence[Any],
    fallback: str,
) -> tuple[str, str]:
    if explicit is not None:
        return _string(explicit, field_name), "explicit"
    candidates = {str(value) for value in values if value not in (None, "")}
    if len(candidates) == 1:
        return next(iter(candidates)), "derived"
    return fallback, "unresolved"


@dataclass(frozen=True, slots=True)
class CodexReviewRequest:
    request_id: str
    created_at: str
    product: str
    target_version: str
    target_instance_id: str
    project: Mapping[str, Any]
    recipe: Mapping[str, Any]
    source_events: tuple[Mapping[str, Any], ...]
    session_diff: Mapping[str, Any] | None
    questions: tuple[str, ...]
    context_metadata: Mapping[str, Any]
    gate_context: Mapping[str, str]
    schema_version: int = SCHEMA_VERSION
    protocol: str = PROTOCOL
    request_type: str = REQUEST_TYPE
    execution_mode: str = EXECUTION_MODE

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        allow_legacy: bool = False,
    ) -> "CodexReviewRequest":
        item = _mapping(value, "CodexReviewRequest")
        if (
            allow_legacy
            and set(item) == {"context"}
            and isinstance(item.get("context"), Mapping)
        ):
            item = _mapping(item["context"], "CodexReviewRequest.context")
        _check_serialized_size(item)
        _assert_safe(item)
        if not allow_legacy:
            required_fields = {
                "schema_version",
                "protocol",
                "request_id",
                "request_type",
                "created_at",
                "execution_mode",
                "product",
                "target_version",
                "recipe",
                "source_events",
                "session_diff",
                "questions",
            }
            missing = sorted(required_fields.difference(item))
            if missing:
                raise ValueError(
                    "CodexReviewRequest is missing required fields: "
                    + ", ".join(missing)
                    + "."
                )
        schema_version = item.get("schema_version")
        if allow_legacy and schema_version is None:
            schema_version = SCHEMA_VERSION
        if _integer(schema_version, "schema_version") != 1:
            raise ValueError("schema_version must be 1.")
        protocol = item.get("protocol")
        if allow_legacy and protocol is None:
            protocol = PROTOCOL
        if protocol != PROTOCOL:
            raise ValueError(f"protocol must be {PROTOCOL}.")
        request_type = item.get("request_type")
        if allow_legacy and request_type is None:
            request_type = item.get("purpose", REQUEST_TYPE)
        allowed_request_types = (
            {REQUEST_TYPE, "review_cam_workflow"}
            if allow_legacy
            else {REQUEST_TYPE}
        )
        if request_type not in allowed_request_types:
            raise ValueError(f"request_type must be {REQUEST_TYPE}.")
        execution_mode = item.get("execution_mode")
        if allow_legacy and execution_mode is None:
            execution_mode = EXECUTION_MODE
        if execution_mode != EXECUTION_MODE:
            raise ValueError("execution_mode must remain dry-run.")
        product = _string(item.get("product"), "product").casefold()
        if product not in _PRODUCTS:
            raise ValueError("product must be nx or powermill.")

        raw_events = (
            item.get("source_events", item.get("activity_events", []))
            if allow_legacy
            else item.get("source_events")
        )
        event_items = _array(raw_events, "source_events")
        if len(event_items) > MAX_SOURCE_EVENTS:
            raise ValueError(
                f"source_events exceeds the {MAX_SOURCE_EVENTS} event limit."
            )
        source_events = tuple(
            _normalize_event(event, product, index)
            for index, event in enumerate(event_items)
        )
        target_version, version_binding = _derived_string(
            item.get("target_version"),
            "target_version",
            [event.get("target_version") for event in source_events],
            "unverified",
        )
        target_instance_id, instance_binding = _derived_string(
            item.get("target_instance_id"),
            "target_instance_id",
            [event.get("instance_id") for event in source_events],
            "offline-review",
        )
        project, project_binding = _normalize_project(
            item.get("project", item.get("project_id")),
            source_events,
        )

        raw_recipe = _mapping(item.get("recipe"), "recipe")
        legacy_recipe = allow_legacy and (
            raw_recipe.get("schema_version") != 1 or "profile" in raw_recipe
        )
        now = _utc_now()
        recipe = (
            _legacy_recipe(
                raw_recipe,
                product=product,
                source_events=source_events,
                target_version=None if target_version == "unverified" else target_version,
                now=now,
            )
            if legacy_recipe
            else _normalize_recipe(raw_recipe, product)
        )
        if (
            target_version != "unverified"
            and recipe["target_versions"]
            and target_version not in recipe["target_versions"]
        ):
            raise ValueError("target_version is outside recipe.target_versions.")

        raw_questions = item.get("questions")
        if raw_questions is None and allow_legacy:
            raw_questions = [
                "Confirm the target version, instance, and project before any preview.",
                "Confirm human review, CAM simulation, collision checks, and shop approval.",
            ]
        questions = tuple(
            _sanitize_string(question, "question")
            for question in _string_array(
                raw_questions,
                "questions",
                maximum=MAX_QUESTIONS,
            )
        )
        for question in questions:
            _assert_safe({"questions": [question]})

        raw_gate_context = item.get("gate_context")
        if raw_gate_context is not None:
            supplied = _mapping(raw_gate_context, "gate_context")
            for key, expected in GATE_CONTEXT.items():
                if key in supplied and supplied[key] != expected:
                    raise ValueError(f"gate_context.{key} cannot weaken the fixed safety gate.")

        metadata = _mapping(item.get("context_metadata", {}), "context_metadata")
        selected_metadata: dict[str, Any] = {}
        for key in ("source_format", "redaction", "source_session_count"):
            if key in metadata:
                selected_metadata[key] = _sanitize_json(metadata[key], key=key)
        selected_metadata.update(
            {
                "redaction": "bridge-minimized-v1",
                "source_event_count": len(source_events),
                "machine_ready_nc_included": False,
                "external_uploads_declared": [],
                "legacy_compatibility": legacy_recipe,
                "target_bindings": {
                    "target_version": version_binding,
                    "target_instance_id": instance_binding,
                    "project": project_binding,
                },
            }
        )
        request = cls(
            request_id=_string(
                item.get("request_id")
                if not allow_legacy
                else item.get("request_id", f"codex-review-{uuid4()}"),
                "request_id",
            ),
            created_at=_timestamp(
                item.get("created_at") if not allow_legacy else item.get("created_at", now),
                "created_at",
            ),
            product=product,
            target_version=target_version,
            target_instance_id=target_instance_id,
            project=project,
            recipe=recipe,
            source_events=source_events,
            session_diff=_normalize_session_diff(item.get("session_diff"), product),
            questions=questions,
            context_metadata=selected_metadata,
            gate_context=dict(GATE_CONTEXT),
        )
        _check_serialized_size(request.to_dict())
        return request

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> "CodexReviewRequest":
        return cls.from_mapping(_strict_json_loads(payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "protocol": self.protocol,
            "request_id": self.request_id,
            "request_type": self.request_type,
            "created_at": self.created_at,
            "execution_mode": self.execution_mode,
            "product": self.product,
            "target_version": self.target_version,
            "target_instance_id": self.target_instance_id,
            "project": _json_copy(dict(self.project)),
            "recipe": _json_copy(dict(self.recipe)),
            "source_events": _json_copy(list(self.source_events)),
            "session_diff": _json_copy(self.session_diff),
            "questions": list(self.questions),
            "gate_context": dict(self.gate_context),
            "context_metadata": _json_copy(dict(self.context_metadata)),
        }


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    finding_id: str
    severity: str
    code: str
    message: str
    step_ids: tuple[str, ...]
    evidence_refs: tuple[Mapping[str, Any], ...]

    @classmethod
    def from_mapping(cls, value: Any, index: int) -> "ReviewFinding":
        prefix = f"findings[{index}]"
        item = _mapping(value, prefix)
        severity = _string(item.get("severity"), f"{prefix}.severity")
        if severity not in _FINDING_SEVERITIES:
            raise ValueError(f"{prefix}.severity is invalid.")
        raw_refs = _array(item.get("evidence_refs"), f"{prefix}.evidence_refs")
        return cls(
            finding_id=_string(item.get("finding_id"), f"{prefix}.finding_id"),
            severity=severity,
            code=_string(item.get("code"), f"{prefix}.code"),
            message=_sanitize_string(
                _string(item.get("message"), f"{prefix}.message"),
                "message",
            ),
            step_ids=tuple(_string_array(item.get("step_ids"), f"{prefix}.step_ids")),
            evidence_refs=tuple(
                _event_ref(ref, f"{prefix}.evidence_refs[{ref_index}]")
                for ref_index, ref in enumerate(raw_refs)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "step_ids": list(self.step_ids),
            "evidence_refs": _json_copy(list(self.evidence_refs)),
        }


@dataclass(frozen=True, slots=True)
class SuggestedParameter:
    name: str
    proposed_value: Any
    rationale: str
    confidence: float
    evidence_refs: tuple[Mapping[str, Any], ...]

    @classmethod
    def from_mapping(cls, value: Any, index: int) -> "SuggestedParameter":
        prefix = f"suggested_parameters[{index}]"
        item = _mapping(value, prefix)
        confidence = item.get("confidence")
        if type(confidence) not in {int, float} or not math.isfinite(float(confidence)):
            raise ValueError(f"{prefix}.confidence must be a finite number.")
        if not 0 <= float(confidence) <= 1:
            raise ValueError(f"{prefix}.confidence must be between 0 and 1.")
        raw_refs = _array(item.get("evidence_refs"), f"{prefix}.evidence_refs")
        if "proposed_value" not in item:
            raise ValueError(f"{prefix}.proposed_value is required.")
        proposed_value = _sanitize_json(item["proposed_value"], key="proposed_value")
        _assert_safe({"proposed_value": proposed_value}, path=f"result.{prefix}")
        return cls(
            name=_string(item.get("name"), f"{prefix}.name"),
            proposed_value=proposed_value,
            rationale=_sanitize_string(
                _string(item.get("rationale"), f"{prefix}.rationale"),
                "rationale",
            ),
            confidence=float(confidence),
            evidence_refs=tuple(
                _event_ref(ref, f"{prefix}.evidence_refs[{ref_index}]")
                for ref_index, ref in enumerate(raw_refs)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "proposed_value": _json_copy(self.proposed_value),
            "rationale": self.rationale,
            "confidence": self.confidence,
            "evidence_refs": _json_copy(list(self.evidence_refs)),
        }


@dataclass(frozen=True, slots=True)
class ReviewQuestion:
    question_id: str
    text: str
    required: bool

    @classmethod
    def from_mapping(cls, value: Any, index: int) -> "ReviewQuestion":
        prefix = f"questions[{index}]"
        item = _mapping(value, prefix)
        text = _sanitize_string(
            _string(item.get("text"), f"{prefix}.text"),
            "question",
        )
        _assert_safe({"questions": [text]}, path="result")
        return cls(
            question_id=_string(item.get("question_id"), f"{prefix}.question_id"),
            text=text,
            required=_boolean(item.get("required"), f"{prefix}.required"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "text": self.text,
            "required": self.required,
        }


def _result_gates(value: Any) -> tuple[str, ...]:
    gates = tuple(_string_array(value, "required_gates"))
    required = {
        "target_version_validation",
        "cam_simulation",
        "collision_check",
        "shop_approval",
    }
    if not required.issubset(gates):
        raise ValueError("required_gates is missing mandatory CAM safety gates.")
    if not any(gate == "recipe_review" or gate.endswith("_review") for gate in gates):
        raise ValueError("required_gates must include a human review gate.")
    return gates


@dataclass(frozen=True, slots=True)
class CodexReviewResult:
    request_id: str
    review_status: str
    findings: tuple[ReviewFinding, ...]
    suggested_parameters: tuple[SuggestedParameter, ...]
    questions: tuple[ReviewQuestion, ...]
    summary: str
    required_gates: tuple[str, ...]
    reviewer: str
    reviewed_at: str
    schema_version: int = SCHEMA_VERSION
    protocol: str = PROTOCOL

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        expected_request_id: str | None = None,
    ) -> "CodexReviewResult":
        item = _mapping(value, "CodexReviewResult")
        _check_serialized_size(item)
        _assert_safe(item, path="result")
        if _integer(item.get("schema_version"), "schema_version") != 1:
            raise ValueError("schema_version must be 1.")
        if item.get("protocol") != PROTOCOL:
            raise ValueError(f"protocol must be {PROTOCOL}.")
        request_id = _string(item.get("request_id"), "request_id")
        if expected_request_id is not None and request_id != expected_request_id:
            raise ValueError("Codex review request_id does not match the request.")
        review_status = _string(item.get("review_status"), "review_status")
        if review_status not in _REVIEW_STATUSES:
            raise ValueError("review_status is invalid.")
        raw_findings = _array(item.get("findings"), "findings")
        raw_parameters = _array(
            item.get("suggested_parameters"),
            "suggested_parameters",
        )
        raw_questions = _array(item.get("questions"), "questions")
        if len(raw_findings) > MAX_FINDINGS:
            raise ValueError(f"findings exceeds the {MAX_FINDINGS} item limit.")
        if len(raw_parameters) > MAX_PARAMETERS:
            raise ValueError(
                f"suggested_parameters exceeds the {MAX_PARAMETERS} item limit."
            )
        if len(raw_questions) > MAX_QUESTIONS:
            raise ValueError(f"questions exceeds the {MAX_QUESTIONS} item limit.")
        result = cls(
            request_id=request_id,
            review_status=review_status,
            findings=tuple(
                ReviewFinding.from_mapping(finding, index)
                for index, finding in enumerate(raw_findings)
            ),
            suggested_parameters=tuple(
                SuggestedParameter.from_mapping(parameter, index)
                for index, parameter in enumerate(raw_parameters)
            ),
            questions=tuple(
                ReviewQuestion.from_mapping(question, index)
                for index, question in enumerate(raw_questions)
            ),
            summary=_sanitize_string(_string(item.get("summary"), "summary"), "summary"),
            required_gates=_result_gates(item.get("required_gates")),
            reviewer=_sanitize_string(
                _string(item.get("reviewer"), "reviewer"),
                "reviewer",
            ),
            reviewed_at=_timestamp(item.get("reviewed_at"), "reviewed_at"),
        )
        _check_serialized_size(result.to_dict())
        return result

    @classmethod
    def from_json(
        cls,
        payload: str | bytes | bytearray,
        *,
        expected_request_id: str | None = None,
    ) -> "CodexReviewResult":
        if expected_request_id is None:
            raise ValueError("expected_request_id is required for strict result parsing.")
        return cls.from_mapping(
            _strict_json_loads(payload),
            expected_request_id=expected_request_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "protocol": self.protocol,
            "request_id": self.request_id,
            "review_status": self.review_status,
            "findings": [finding.to_dict() for finding in self.findings],
            "suggested_parameters": [
                parameter.to_dict() for parameter in self.suggested_parameters
            ],
            "questions": [question.to_dict() for question in self.questions],
            "summary": self.summary,
            "required_gates": list(self.required_gates),
            "reviewer": self.reviewer,
            "reviewed_at": self.reviewed_at,
        }


def parse_review_request_json(payload: str | bytes | bytearray) -> CodexReviewRequest:
    return CodexReviewRequest.from_json(payload)


def parse_review_result_json(
    payload: str | bytes | bytearray,
    *,
    expected_request_id: str | None = None,
) -> CodexReviewResult:
    if expected_request_id is None:
        raise ValueError("expected_request_id is required for strict result parsing.")
    return CodexReviewResult.from_json(
        payload,
        expected_request_id=expected_request_id,
    )


def review_request(context: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize new or legacy local analysis context into a v1 review request."""

    return CodexReviewRequest.from_mapping(context, allow_legacy=True).to_dict()


def _legacy_review(
    value: Mapping[str, Any],
    *,
    request_id: str | None,
) -> tuple[CodexReviewResult, str]:
    status = value.get("review_status", value.get("status", "needs_review"))
    if not isinstance(status, str) or status not in _REVIEW_STATUSES:
        raise ValueError("Legacy review_status is invalid.")
    raw_findings = value.get("findings", [])
    if not isinstance(raw_findings, list) or not all(
        isinstance(item, str) and item.strip() for item in raw_findings
    ):
        raise ValueError("Legacy findings must be an array of non-empty strings.")
    if len(raw_findings) > MAX_FINDINGS:
        raise ValueError(f"findings exceeds the {MAX_FINDINGS} item limit.")
    raw_gates = value.get("required_gates", [])
    if not isinstance(raw_gates, list) or not all(
        isinstance(item, str) and item.strip() for item in raw_gates
    ):
        raise ValueError("Legacy required_gates must be an array of non-empty strings.")
    notes = value.get("notes", "")
    if not isinstance(notes, str):
        raise ValueError("Legacy notes must be a string.")
    sanitized_notes = _sanitize_string(notes, "notes")
    reviewer = value.get("reviewer", "Codex")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("Legacy reviewer must be a non-empty string.")
    supplied_request_id = value.get("request_id")
    if supplied_request_id is not None:
        supplied_request_id = _string(supplied_request_id, "request_id")
    if (
        request_id is not None
        and supplied_request_id is not None
        and supplied_request_id != request_id
    ):
        raise ValueError("Codex review request_id does not match the request.")
    resolved_request_id = request_id or supplied_request_id
    if resolved_request_id is None:
        digest = hashlib.sha256(
            json.dumps(
                _json_copy(dict(value)),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:24]
        resolved_request_id = f"legacy-review-{digest}"
    gates = list(dict.fromkeys([*raw_gates, *REQUIRED_PRODUCTION_GATES]))
    reviewed_at = _utc_now()
    result = CodexReviewResult(
        request_id=_string(resolved_request_id, "request_id"),
        review_status=status,
        findings=tuple(
            ReviewFinding(
                finding_id=f"legacy-finding-{index + 1:03d}",
                severity="warning",
                code="legacy.review.finding",
                message=_sanitize_string(message, "message"),
                step_ids=(),
                evidence_refs=(),
            )
            for index, message in enumerate(raw_findings)
        ),
        suggested_parameters=(),
        questions=(),
        summary=(
            sanitized_notes.strip()
            if sanitized_notes.strip()
            else "Legacy structured review imported for human review."
        ),
        required_gates=tuple(gates),
        reviewer=_sanitize_string(reviewer, "reviewer"),
        reviewed_at=reviewed_at,
    )
    _check_serialized_size(result.to_dict())
    return result, sanitized_notes


def validate_review(
    value: Mapping[str, Any],
    *,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Validate structured results, with a bounded migration for legacy findings."""

    item = _mapping(value, "Codex review")
    _check_serialized_size(item)
    _assert_safe(item, path="result")
    structured_keys = {
        "schema_version",
        "suggested_parameters",
        "summary",
        "reviewed_at",
    }
    findings = item.get("findings", [])
    legacy = not any(key in item for key in structured_keys) and (
        not isinstance(findings, list)
        or all(isinstance(finding, str) for finding in findings)
    )
    if legacy:
        result, notes = _legacy_review(item, request_id=request_id)
        normalized = result.to_dict()
        normalized["notes"] = notes
        normalized["received_at"] = result.reviewed_at
        return normalized
    return CodexReviewResult.from_mapping(
        item,
        expected_request_id=request_id,
    ).to_dict()


def _exchange_directory(
    directory: str | Path,
    *,
    base_directory: str | Path | None,
) -> Path:
    raw = Path(directory)
    if base_directory is None:
        return raw.resolve()
    base = Path(base_directory).resolve()
    destination = (raw if raw.is_absolute() else base / raw).resolve()
    if destination != base and base not in destination.parents:
        raise ValueError("Codex exchange directory must stay inside base_directory.")
    return destination


def write_exchange(
    directory: str | Path,
    *,
    context: Mapping[str, Any],
    review: Mapping[str, Any] | None = None,
    base_directory: str | Path | None = None,
) -> dict[str, Path]:
    """Persist a bounded local exchange without enabling CAM or remote upload."""

    request = review_request(context)
    normalized_review = (
        validate_review(review, request_id=request["request_id"])
        if review is not None
        else None
    )
    destination = _exchange_directory(directory, base_directory=base_directory)
    destination.mkdir(parents=True, exist_ok=True)
    paths = {"request": destination / "codex-review-request.json"}
    paths["request"].write_text(
        json.dumps(request, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if normalized_review is not None:
        paths["review"] = destination / "codex-review.json"
        paths["review"].write_text(
            json.dumps(
                normalized_review,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return paths
