from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "cam.codex.bridge.v1"


def review_request(context: Mapping[str, Any]) -> dict[str, Any]:
    """Wrap an analysis as a stable, portable Codex review request."""

    return {
        "protocol": PROTOCOL,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "request_type": "workflow_review",
        "execution_mode": "dry-run",
        "context": dict(context),
    }


def validate_review(value: Mapping[str, Any]) -> dict[str, Any]:
    status = value.get("review_status", value.get("status", "needs_review"))
    if status not in {"approved_for_simulation", "needs_changes", "rejected", "needs_review"}:
        raise ValueError(
            "review_status must be approved_for_simulation, needs_changes, rejected, or needs_review."
        )
    findings = value.get("findings", [])
    gates = value.get("required_gates", [])
    if not isinstance(findings, list) or not all(isinstance(item, str) for item in findings):
        raise ValueError("findings must be a list of strings.")
    if not isinstance(gates, list) or not all(isinstance(item, str) for item in gates):
        raise ValueError("required_gates must be a list of strings.")
    return {
        "protocol": value.get("protocol", PROTOCOL),
        "review_status": status,
        "findings": findings,
        "required_gates": gates,
        "reviewer": str(value.get("reviewer", "Codex")),
        "notes": str(value.get("notes", "")),
        "received_at": datetime.now(timezone.utc).isoformat(),
    }


def write_exchange(
    directory: str | Path,
    *,
    context: Mapping[str, Any],
    review: Mapping[str, Any] | None = None,
) -> dict[str, Path]:
    """Persist an explicit exchange bundle for Codex or a shop review record."""

    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    paths = {"request": destination / "codex-review-request.json"}
    paths["request"].write_text(
        json.dumps(review_request(context), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if review is not None:
        paths["review"] = destination / "codex-review.json"
        paths["review"].write_text(
            json.dumps(validate_review(review), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return paths
