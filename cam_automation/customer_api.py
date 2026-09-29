"""Local customer workflow facade. It never executes CAM or machine commands."""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any


class CustomerApi:
    """Lazy domain composition keeps the default plugin core lightweight."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.RLock()
        self._context: Any = None
        self._cases: Any = None

    def _services(self) -> tuple[Any, Any]:
        with self._lock:
            if self._context is None:
                from .manufacturing_context import ManufacturingContextService
                from .case_library import CaseLibraryService

                context = ManufacturingContextService(self.root / "contexts")
                cases = CaseLibraryService(self.root / "cases")
                self._context, self._cases = context, cases
            return self._context, self._cases

    @staticmethod
    def _identifier(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValueError(f"{field} must be a non-empty identifier (max 256 characters).")
        if any(char in value for char in ("/", "\\", "\x00", "\r", "\n")):
            raise ValueError(f"{field} contains invalid characters.")
        return value

    @staticmethod
    def _items(items: list[Any]) -> dict[str, Any]:
        return {"schema_version": 1, "items": items}

    def dispatch(
        self, method: str, path: str, body: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        """Return JSON and an optional fixed export filename; unknown routes raise KeyError."""
        if body is not None:
            if not isinstance(body, dict):
                raise ValueError("Request body must be a JSON object.")
            version = body.get("schema_version", 1)
            if type(version) is not int or version != 1:
                raise ValueError("schema_version must be the integer 1.")
        # Serial composition provides one consistent context/case view per request.
        with self._lock:
            return self._dispatch(method, path, body or {})

    def _dispatch(
        self, method: str, path: str, body: dict[str, Any],
    ) -> tuple[dict[str, Any], str | None]:
        route = path.removeprefix("/api/delivery")
        if method == "GET" and route == "/status":
            context, _ = self._services()
            return {
                "schema_version": 1,
                "mode": "offline_review",
                "dry_run": True,
                "machine_output_enabled": False,
                "live_host_verified": False,
                "capabilities": context.capabilities(),
                "required_gates": ["review", "simulation", "collision", "shop_approval"],
                "evidence_policy": "Locally recorded evidence is not independent certification.",
            }, None
        known_get = route in {"/snapshots", "/cases"} or re.fullmatch(
            r"/(snapshots|proposals)/[^/]+", route,
        )
        known_post = route in {
            "/snapshots", "/cases", "/cases/search", "/selectors/resolve", "/proposals",
        } or re.fullmatch(r"/proposals/[^/]+/(evidence|assess|export)", route)
        if not ((method == "GET" and known_get) or (method == "POST" and known_post)):
            raise KeyError("Unknown customer workflow route.")
        context, cases = self._services()
        if method == "GET":
            if route == "/snapshots":
                return self._items(context.list_snapshots()), None
            if route == "/cases":
                return self._items(cases.list_cases()), None
            collection, identifier = route.strip("/").split("/")
            identifier = self._identifier(identifier, "identifier")
            if collection == "snapshots":
                return context.get_snapshot(identifier), None
            return cases.get_proposal(identifier), None
        if route == "/snapshots":
            return context.import_snapshot(body), None
        if route == "/cases":
            return cases.add_case(body), None
        if route in {"/selectors/resolve", "/cases/search", "/proposals"}:
            snapshot_id = self._identifier(body.get("snapshot_id"), "snapshot_id")
            snapshot = context.get_snapshot(snapshot_id)
            if route == "/selectors/resolve":
                selector = body.get("selector")
                if not isinstance(selector, dict):
                    raise ValueError("selector must be an object.")
                return context.resolve_selector(snapshot_id, selector), None
            if route == "/cases/search":
                query = body.get("query", "")
                if not isinstance(query, str) or len(query) > 2000:
                    raise ValueError("query must be text of at most 2000 characters.")
                return self._items(cases.search_cases(snapshot, query)), None
            case_id = self._identifier(body.get("case_id"), "case_id")
            parameters = body.get("parameters")
            if parameters is not None and not isinstance(parameters, dict):
                raise ValueError("parameters must be an object.")
            return cases.create_proposal(snapshot, case_id, parameters), None
        _, proposal_id, operation = route.strip("/").split("/")
        proposal_id = self._identifier(proposal_id, "proposal_id")
        if operation == "evidence":
            return cases.record_evidence(proposal_id, body), None
        snapshot_id = self._identifier(body.get("snapshot_id"), "snapshot_id")
        snapshot = context.get_snapshot(snapshot_id)
        if operation == "assess":
            return cases.assess_proposal(proposal_id, snapshot), None
        return cases.export_bundle(proposal_id, snapshot), "cam-review-bundle.json"

    def close(self) -> None:
        with self._lock:
            for service in (self._context, self._cases):
                close = getattr(service, "close", None)
                if callable(close):
                    close()
            self._context = self._cases = None
