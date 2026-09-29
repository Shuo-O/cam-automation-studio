from __future__ import annotations

import csv
import hashlib
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from cam_automation.profiles.powermill import PowerMillProfile
from cam_automation.product_catalog import PRODUCT_CATALOG


_CAM_PROCESS_NAMES = {
    product.key: product.process_names for product in PRODUCT_CATALOG.values()
}
_CACHE_LOCK = threading.RLock()
_CACHE_AT = 0.0
_CACHE: dict[str, list[dict[str, Any]]] = {
    product: [] for product in _CAM_PROCESS_NAMES
}
_NX_VERSION = re.compile(r"\bNX\s+(\d{4}(?:\.\d+)?)\b", re.IGNORECASE)
_CIMATRON_VERSION = re.compile(
    r"\bCimatron(?:\s+E)?\s+(20\d{2}(?:\.\d+)?)\b",
    re.IGNORECASE,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class InstanceDescriptor:
    """Frozen v1 runtime descriptor with optional connection response state."""

    instance_id: str
    product: str
    pid: int
    process_name: str
    window_handle: str | None
    window_title: str
    is_foreground: bool
    window_state: str
    target_version: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    connection_status: str = "detected"
    capabilities: tuple[str, ...] = ()
    discovered_at: str = ""
    last_seen_at: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    last_response: Mapping[str, Any] | None = None
    transport_kind: str | None = None
    live_connected: bool = False
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "instance_id": self.instance_id,
            "product": self.product,
            "pid": self.pid,
            "process_name": self.process_name,
            "window_handle": self.window_handle,
            "window_title": self.window_title,
            "is_foreground": self.is_foreground,
            "window_state": self.window_state,
            "target_version": self.target_version,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "connection_status": self.connection_status,
            "capabilities": list(self.capabilities),
            "discovered_at": self.discovered_at,
            "last_seen_at": self.last_seen_at,
            "metadata": dict(self.metadata),
            "last_response": (
                dict(self.last_response) if self.last_response is not None else None
            ),
            "transport_kind": self.transport_kind,
            "live_connected": self.live_connected,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "InstanceDescriptor":
        return cls(
            schema_version=int(value.get("schema_version", 1)),
            instance_id=str(value["instance_id"]),
            product=str(value["product"]),
            pid=int(value["pid"]),
            process_name=str(value["process_name"]),
            window_handle=(
                str(value["window_handle"])
                if value.get("window_handle") is not None
                else None
            ),
            window_title=str(value.get("window_title", "")),
            is_foreground=bool(value.get("is_foreground", False)),
            window_state=str(value.get("window_state", "background")),
            target_version=(
                str(value["target_version"])
                if value.get("target_version") is not None
                else None
            ),
            project_id=(
                str(value["project_id"])
                if value.get("project_id") is not None
                else None
            ),
            project_name=(
                str(value["project_name"])
                if value.get("project_name") is not None
                else None
            ),
            connection_status=str(value.get("connection_status", "detected")),
            capabilities=tuple(str(item) for item in value.get("capabilities", ())),
            discovered_at=str(value.get("discovered_at", "")),
            last_seen_at=str(value.get("last_seen_at", "")),
            metadata=dict(value.get("metadata") or {}),
            last_response=(
                dict(value["last_response"])
                if isinstance(value.get("last_response"), Mapping)
                else None
            ),
            transport_kind=(
                str(value["transport_kind"])
                if value.get("transport_kind") is not None
                else None
            ),
            live_connected=bool(value.get("live_connected", False)),
        )


def _empty_snapshot() -> dict[str, list[dict[str, Any]]]:
    return {product: [] for product in _CAM_PROCESS_NAMES}


def _copy_snapshot(
    value: Mapping[str, Iterable[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    return {
        product: [dict(instance) for instance in value.get(product, [])]
        for product in _CAM_PROCESS_NAMES
    }


def _coerce_process(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {
            "process_name": str(
                value.get("process_name") or value.get("name") or value.get("executable") or ""
            ),
            "pid": int(value.get("pid", 0) or 0),
            "start_token": value.get("start_token", value.get("create_time")),
            "target_version": value.get("target_version"),
            "source_kind": str(value.get("source_kind") or "local_os"),
            "metadata": dict(value.get("metadata") or {}),
        }
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        return {
            "process_name": str(value[0]),
            "pid": int(value[1]),
            "start_token": value[2] if len(value) >= 3 else None,
            "target_version": value[3] if len(value) >= 4 else None,
            "source_kind": "local_os",
            "metadata": {},
        }
    raise TypeError("process observations must be mappings or tuples")


def _lifetime_segment(product: str, pid: int, start_token: Any) -> str | None:
    if start_token in (None, ""):
        return None
    evidence = f"{product}|{pid}|{start_token}".encode("utf-8")
    return hashlib.sha256(evidence).hexdigest()[:12]


def _runtime_id(
    *,
    product: str,
    pid: int,
    handle: int | None,
    lifetime: str | None,
) -> str:
    segments = [product, str(pid)]
    if lifetime:
        segments.append(lifetime)
    segments.append(f"{handle:X}" if handle else "process")
    return ":".join(segments)


def _window_metadata(product: str, window: Mapping[str, Any], title: str) -> dict[str, Any]:
    metadata = {
        "target_version": window.get("target_version"),
        "project_id": window.get("project_id"),
        "project_name": window.get("project_name"),
    }
    if product == "powermill":
        inferred = PowerMillProfile().window_metadata(title)
        metadata["target_version"] = metadata["target_version"] or inferred["target_version"]
        metadata["project_name"] = metadata["project_name"] or inferred["project_name"]
    elif product == "nx" and metadata["target_version"] is None:
        match = _NX_VERSION.search(title)
        if match:
            metadata["target_version"] = f"NX {match.group(1)}"
    elif product == "cimatron" and metadata["target_version"] is None:
        match = _CIMATRON_VERSION.search(title)
        if match:
            metadata["target_version"] = f"Cimatron {match.group(1)}"
    return metadata


def _build_instances(
    processes: Iterable[Any],
    windows: Iterable[Mapping[str, Any]],
    *,
    foreground_handle: int = 0,
    observed_at: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Build a deterministic v1 snapshot from process and top-level window evidence."""

    seen_at = observed_at or _utc_now()
    process_products: dict[int, tuple[str, dict[str, Any]]] = {}
    for raw_process in processes:
        process = _coerce_process(raw_process)
        process_name = process["process_name"]
        pid = process["pid"]
        if pid <= 0:
            continue
        normalized = process_name.casefold()
        for product, names in _CAM_PROCESS_NAMES.items():
            if normalized in names:
                process_products[pid] = (product, process)
                break

    windows_by_pid: dict[int, list[dict[str, Any]]] = {}
    for window in windows:
        pid = int(window.get("pid", 0) or 0)
        handle = int(window.get("handle", 0) or 0)
        title = str(window.get("title", "")).strip()[:512]
        if pid not in process_products or not handle or not title:
            continue
        windows_by_pid.setdefault(pid, []).append(
            {**dict(window), "handle": handle, "title": title}
        )

    snapshot = _empty_snapshot()
    for pid, (product, process) in process_products.items():
        process_name = process["process_name"]
        lifetime = _lifetime_segment(product, pid, process.get("start_token"))
        source_kind = process.get("source_kind", "local_os")
        process_metadata = dict(process.get("metadata") or {})
        process_metadata.update(
            {
                "source_kind": source_kind,
                "local_source": source_kind in {"local_os", "fixture_local"},
            }
        )
        if lifetime:
            process_metadata["process_lifetime"] = lifetime
        product_windows = windows_by_pid.get(pid, [])
        if not product_windows:
            metadata = {**process_metadata, "headless": True}
            snapshot[product].append(
                InstanceDescriptor(
                    instance_id=_runtime_id(
                        product=product,
                        pid=pid,
                        handle=None,
                        lifetime=lifetime,
                    ),
                    product=product,
                    pid=pid,
                    process_name=process_name,
                    window_handle=None,
                    window_title="",
                    is_foreground=False,
                    window_state="background",
                    target_version=(
                        str(process["target_version"])
                        if process.get("target_version") is not None
                        else None
                    ),
                    connection_status="detected",
                    capabilities=PRODUCT_CATALOG[product].process_capabilities,
                    discovered_at=seen_at,
                    last_seen_at=seen_at,
                    metadata=metadata,
                ).to_dict()
            )
            continue

        seen_handles: set[int] = set()
        for window in product_windows:
            handle = int(window["handle"])
            if handle in seen_handles:
                continue
            seen_handles.add(handle)
            title = str(window["title"])
            display = _window_metadata(product, window, title)
            target_version = display["target_version"] or process.get("target_version")
            snapshot[product].append(
                InstanceDescriptor(
                    instance_id=_runtime_id(
                        product=product,
                        pid=pid,
                        handle=handle,
                        lifetime=lifetime,
                    ),
                    product=product,
                    pid=pid,
                    process_name=process_name,
                    window_handle=f"0x{handle:X}",
                    window_title=title,
                    is_foreground=handle == foreground_handle,
                    window_state=(
                        "foreground" if handle == foreground_handle else "visible"
                    ),
                    target_version=(
                        str(target_version) if target_version is not None else None
                    ),
                    project_id=(
                        str(display["project_id"])
                        if display["project_id"] is not None
                        else None
                    ),
                    project_name=(
                        str(display["project_name"])
                        if display["project_name"] is not None
                        else None
                    ),
                    connection_status="detected",
                    capabilities=PRODUCT_CATALOG[product].process_capabilities,
                    discovered_at=seen_at,
                    last_seen_at=seen_at,
                    metadata={**process_metadata, "headless": False},
                ).to_dict()
            )

    for instances in snapshot.values():
        instances.sort(
            key=lambda item: (
                not bool(item["is_foreground"]),
                int(item["pid"]),
                str(item["window_handle"] or ""),
            )
        )
    return snapshot


def _windows_process_start_token(pid: int) -> int | None:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(
        process_query_limited_information,
        False,
        pid,
    )
    if not handle:
        return None
    try:
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        return (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
    finally:
        kernel32.CloseHandle(handle)


def _windows_processes() -> list[tuple[str, int, int | None]]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"],
        check=False,
        capture_output=True,
        text=True,
        timeout=3,
        creationflags=flags,
    )
    processes: list[tuple[str, int, int | None]] = []
    known_names = frozenset().union(*_CAM_PROCESS_NAMES.values())
    for row in csv.reader(result.stdout.splitlines()):
        if len(row) < 2 or row[0].strip().casefold() not in known_names:
            continue
        try:
            pid = int(row[1].replace(",", "").strip())
        except ValueError:
            continue
        processes.append((row[0].strip(), pid, _windows_process_start_token(pid)))
    return processes


def _windows_top_level_windows() -> tuple[list[dict[str, Any]], int]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    foreground_handle = int(user32.GetForegroundWindow() or 0)
    discovered: list[dict[str, Any]] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def collect(handle: int, _parameter: int) -> bool:
        if not user32.IsWindowVisible(handle):
            return True
        if user32.GetWindow(handle, 4):  # GW_OWNER
            return True
        length = int(user32.GetWindowTextLengthW(handle))
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        if not user32.GetWindowTextW(handle, buffer, length + 1):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        discovered.append(
            {
                "handle": int(handle),
                "pid": int(pid.value),
                "title": buffer.value,
            }
        )
        return True

    user32.EnumWindows(collect, 0)
    return discovered, foreground_handle


class ConnectionMonitor:
    """Stateful multi-instance discovery with explicit local authorization."""

    def __init__(
        self,
        *,
        process_provider: Callable[[], Iterable[Any]] | None = None,
        window_provider: Callable[
            [], tuple[Iterable[Mapping[str, Any]], int] | Iterable[Mapping[str, Any]]
        ]
        | None = None,
        transports: Mapping[str, Any] | None = None,
        auto_connect: bool = True,
        authorized_local_sources: bool = False,
    ) -> None:
        self._process_provider = process_provider or _windows_processes
        self._window_provider = window_provider or _windows_top_level_windows
        self._transports: dict[str, Any] = {
            str(product).casefold(): transport
            for product, transport in (transports or {}).items()
        }
        self.auto_connect = auto_connect
        self._authorize_all_local = authorized_local_sources
        self._authorized_instance_ids: set[str] = set()
        self._manual_disconnects: set[str] = set()
        self._instances: dict[str, InstanceDescriptor] = {}
        self._instance_locks: dict[str, threading.RLock] = {}
        self._lock = threading.RLock()

    def authorize_local_sources(
        self,
        authorized: bool,
        *,
        instance_ids: Iterable[str] | None = None,
    ) -> None:
        with self._lock:
            if instance_ids is None:
                self._authorize_all_local = authorized
                targets = list(self._instances)
            else:
                targets = [str(item) for item in instance_ids]
                if authorized:
                    self._authorized_instance_ids.update(targets)
                else:
                    self._authorized_instance_ids.difference_update(targets)
            if not authorized:
                for instance_id in targets:
                    descriptor = self._instances.get(instance_id)
                    if descriptor and descriptor.connection_status == "connected":
                        self._disconnect_transport(descriptor)
                        self._instances[instance_id] = replace(
                            descriptor,
                            connection_status="disconnected",
                            last_response=None,
                            transport_kind=None,
                            live_connected=False,
                        )

    def register_transport(self, product: str, transport: Any) -> None:
        with self._lock:
            self._transports[product.casefold()] = transport

    def list_instances(self, *, refresh: bool = True) -> list[InstanceDescriptor]:
        with self._lock:
            if refresh:
                self._refresh_locked()
            return sorted(
                self._instances.values(),
                key=lambda item: (
                    not item.is_foreground,
                    item.product,
                    item.pid,
                    item.window_handle or "",
                ),
            )

    def list_instance_dicts(self, *, refresh: bool = True) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.list_instances(refresh=refresh)]

    def refresh(self) -> list[InstanceDescriptor]:
        return self.list_instances(refresh=True)

    def connect(self, instance_id: str) -> InstanceDescriptor:
        with self._lock:
            descriptor = self._require_instance(instance_id)
            if not self._is_authorized(descriptor):
                raise PermissionError("local source authorization is required before connection")
            connected = self._connect_transport(descriptor)
            self._manual_disconnects.discard(instance_id)
            self._instances[instance_id] = connected
            return connected

    def disconnect(self, instance_id: str) -> InstanceDescriptor:
        with self._lock:
            descriptor = self._require_instance(instance_id)
            self._disconnect_transport(descriptor)
            self._manual_disconnects.add(instance_id)
            disconnected = replace(
                descriptor,
                connection_status="disconnected",
                last_response=None,
                transport_kind=None,
                live_connected=False,
            )
            self._instances[instance_id] = disconnected
            return disconnected

    def query(
        self,
        instance_id: str,
        command: str | Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            descriptor = self._require_instance(instance_id)
            transport = self._transports.get(descriptor.product)
            if descriptor.connection_status != "connected" or transport is None:
                return self._local_error_response(
                    descriptor,
                    command,
                    "disconnected",
                    "instance_disconnected",
                    "The target instance has no configured connection.",
                )
            instance_lock = self._instance_locks.setdefault(
                instance_id,
                threading.RLock(),
            )

        with instance_lock:
            try:
                response = dict(transport.query(instance_id, command))
            except TimeoutError:
                response = self._local_error_response(
                    descriptor,
                    command,
                    "timed_out",
                    "transport_timeout",
                    "The instance query exceeded its transport timeout.",
                )
            except (ConnectionError, OSError):
                response = self._local_error_response(
                    descriptor,
                    command,
                    "disconnected",
                    "transport_disconnected",
                    "The instance transport disconnected during the query.",
                )
            except (RuntimeError, TypeError, ValueError) as error:
                response = self._local_error_response(
                    descriptor,
                    command,
                    "failed",
                    "transport_error",
                    str(error) or "The instance transport failed.",
                )
            with self._lock:
                current = self._require_instance(instance_id)
                mismatch = self._response_mismatch(current, response)
                if mismatch:
                    response = self._local_error_response(
                        current,
                        command,
                        "failed",
                        "response_attribution_mismatch",
                        mismatch,
                    )
                response_status = str(response.get("status", "failed"))
                connection_status = current.connection_status
                if response_status == "disconnected":
                    connection_status = "disconnected"
                elif response_status == "failed":
                    connection_status = "error"
                summary = {
                    "response_id": response.get("response_id"),
                    "task_id": response.get("task_id"),
                    "status": response_status,
                    "duration_ms": response.get("duration_ms"),
                    "completed_at": response.get("completed_at"),
                    "transport": current.transport_kind,
                }
                self._instances[instance_id] = replace(
                    current,
                    connection_status=connection_status,
                    last_response=summary,
                )
                return response

    def snapshot(self, instance_id: str) -> dict[str, Any]:
        with self._lock:
            descriptor = self._require_instance(instance_id)
            transport = self._transports.get(descriptor.product)
            if descriptor.connection_status != "connected" or transport is None:
                return {
                    "schema_version": 1,
                    "snapshot_id": None,
                    "product": descriptor.product,
                    "target_instance_id": instance_id,
                    "project_id": descriptor.project_id,
                    "target_version": descriptor.target_version,
                    "captured_at": _utc_now(),
                    "content_hash": None,
                    "status": "disconnected",
                }
            instance_lock = self._instance_locks.setdefault(
                instance_id,
                threading.RLock(),
            )
        with instance_lock:
            try:
                snapshot = dict(transport.snapshot(instance_id))
            except TimeoutError:
                snapshot = {
                    "schema_version": 1,
                    "snapshot_id": None,
                    "product": descriptor.product,
                    "target_instance_id": instance_id,
                    "project_id": descriptor.project_id,
                    "target_version": descriptor.target_version,
                    "captured_at": _utc_now(),
                    "content_hash": None,
                    "status": "timed_out",
                    "error": {
                        "code": "transport_timeout",
                        "message": "The instance snapshot exceeded its transport timeout.",
                    },
                }
            except (ConnectionError, OSError, RuntimeError, TypeError, ValueError) as error:
                snapshot = {
                    "schema_version": 1,
                    "snapshot_id": None,
                    "product": descriptor.product,
                    "target_instance_id": instance_id,
                    "project_id": descriptor.project_id,
                    "target_version": descriptor.target_version,
                    "captured_at": _utc_now(),
                    "content_hash": None,
                    "status": "failed",
                    "error": {
                        "code": "transport_error",
                        "message": str(error) or "The instance snapshot failed.",
                    },
                }
        if str(snapshot.get("target_instance_id", "")) != instance_id:
            return {
                "schema_version": 1,
                "snapshot_id": None,
                "product": descriptor.product,
                "target_instance_id": instance_id,
                "project_id": descriptor.project_id,
                "target_version": descriptor.target_version,
                "captured_at": _utc_now(),
                "content_hash": None,
                "status": "failed",
                "error": {
                    "code": "snapshot_attribution_mismatch",
                    "message": "Transport returned a snapshot for another instance.",
                },
            }
        return snapshot

    def _refresh_locked(self) -> None:
        processes = list(self._process_provider())
        window_result = self._window_provider()
        if (
            isinstance(window_result, tuple)
            and len(window_result) == 2
            and isinstance(window_result[1], int)
        ):
            windows, foreground = window_result
        else:
            windows = window_result
            foreground = 0
        raw = _build_instances(
            processes,
            windows,
            foreground_handle=int(foreground),
        )
        discovered = {
            value["instance_id"]: InstanceDescriptor.from_mapping(value)
            for product_instances in raw.values()
            for value in product_instances
        }
        next_instances: dict[str, InstanceDescriptor] = {}
        for instance_id, fresh in discovered.items():
            previous = self._instances.get(instance_id)
            if previous is not None:
                binding_changed = (
                    previous.project_id != fresh.project_id
                    or previous.project_name != fresh.project_name
                    or previous.target_version != fresh.target_version
                )
                if binding_changed and previous.connection_status == "connected":
                    self._disconnect_transport(previous)
                fresh = replace(
                    fresh,
                    discovered_at=previous.discovered_at,
                    connection_status=(
                        "detected" if binding_changed else previous.connection_status
                    ),
                    capabilities=(
                        fresh.capabilities
                        if binding_changed
                        else previous.capabilities
                    ),
                    last_response=None if binding_changed else previous.last_response,
                    transport_kind=None if binding_changed else previous.transport_kind,
                    live_connected=False if binding_changed else previous.live_connected,
                )
            if (
                self.auto_connect
                and fresh.connection_status != "connected"
                and instance_id not in self._manual_disconnects
                and self._is_authorized(fresh)
                and fresh.product in self._transports
            ):
                fresh = self._connect_transport(fresh)
            next_instances[instance_id] = fresh

        for instance_id, stale in self._instances.items():
            if instance_id not in next_instances:
                self._disconnect_transport(stale)
                self._instance_locks.pop(instance_id, None)
                self._authorized_instance_ids.discard(instance_id)
                self._manual_disconnects.discard(instance_id)
        self._instances = next_instances

    def _is_authorized(self, descriptor: InstanceDescriptor) -> bool:
        return bool(descriptor.metadata.get("local_source")) and (
            self._authorize_all_local
            or descriptor.instance_id in self._authorized_instance_ids
        )

    def _connect_transport(self, descriptor: InstanceDescriptor) -> InstanceDescriptor:
        transport = self._transports.get(descriptor.product)
        if transport is None:
            return descriptor
        try:
            connect = getattr(transport, "connect", None)
            if callable(connect):
                connect(descriptor.to_dict())
            is_connected = getattr(transport, "is_connected", None)
            if callable(is_connected) and not bool(is_connected(descriptor.instance_id)):
                raise ConnectionError("transport did not confirm the instance connection")
            capabilities = set(descriptor.capabilities)
            get_capabilities = getattr(transport, "capabilities", None)
            if callable(get_capabilities):
                capabilities.update(get_capabilities(descriptor.instance_id))
            offline = bool(getattr(transport, "offline", False))
            return replace(
                descriptor,
                connection_status="connected",
                capabilities=tuple(sorted(capabilities)),
                transport_kind=str(getattr(transport, "name", "configured")),
                live_connected=not offline,
                metadata={
                    **dict(descriptor.metadata),
                    "connection_mode": "fixture" if offline else "live",
                },
            )
        except (ConnectionError, OSError, RuntimeError, ValueError):
            return replace(
                descriptor,
                connection_status="error",
                transport_kind=str(getattr(transport, "name", "configured")),
                live_connected=False,
            )

    def _disconnect_transport(self, descriptor: InstanceDescriptor) -> None:
        transport = self._transports.get(descriptor.product)
        disconnect = getattr(transport, "disconnect", None)
        if callable(disconnect):
            disconnect(descriptor.instance_id)

    def _require_instance(self, instance_id: str) -> InstanceDescriptor:
        try:
            return self._instances[instance_id]
        except KeyError as error:
            raise KeyError(f"unknown CAM instance: {instance_id}") from error

    @staticmethod
    def _response_mismatch(
        descriptor: InstanceDescriptor,
        response: Mapping[str, Any],
    ) -> str | None:
        if response.get("target_instance_id") != descriptor.instance_id:
            return "Transport returned a response for another target instance."
        if response.get("product") != descriptor.product:
            return "Transport returned a response for another CAM product."
        response_project = response.get("project_id")
        if (
            descriptor.project_id
            and response_project
            and response_project != descriptor.project_id
        ):
            return "Transport response project no longer matches the discovered instance."
        response_version = response.get("target_version")
        if (
            descriptor.target_version
            and response_version
            and response_version != descriptor.target_version
        ):
            return "Transport response version no longer matches the discovered instance."
        return None

    @staticmethod
    def _local_error_response(
        descriptor: InstanceDescriptor,
        command: str | Mapping[str, Any],
        status: str,
        code: str,
        message: str,
    ) -> dict[str, Any]:
        if isinstance(command, Mapping):
            task_id = str(command.get("task_id") or "")
        else:
            task_id = ""
        now = _utc_now()
        digest = hashlib.sha256(descriptor.instance_id.encode("utf-8")).hexdigest()[:10]
        return {
            "schema_version": 1,
            "response_id": f"monitor-response:{digest}:{time.monotonic_ns()}",
            "task_id": task_id or f"monitor-query:{digest}",
            "product": descriptor.product,
            "target_version": descriptor.target_version or "",
            "target_instance_id": descriptor.instance_id,
            "project_id": descriptor.project_id or "",
            "status": status,
            "started_at": now,
            "completed_at": now,
            "duration_ms": 0,
            "raw_response": None,
            "structured_response": None,
            "diff_report": None,
            "error": {"code": code, "message": message},
        }


def detect_cam_instances(
    *,
    force: bool = False,
    max_age_seconds: float = 2.0,
) -> dict[str, list[dict[str, Any]]]:
    """Return local CAM descriptors without attaching to or automating any product."""

    global _CACHE_AT, _CACHE
    if os.name != "nt":
        return _empty_snapshot()
    now = time.monotonic()
    with _CACHE_LOCK:
        if not force and now - _CACHE_AT < max_age_seconds:
            return _copy_snapshot(_CACHE)
        try:
            processes = _windows_processes()
            windows, foreground_handle = _windows_top_level_windows()
            snapshot = _build_instances(
                processes,
                windows,
                foreground_handle=foreground_handle,
            )
        except (OSError, UnicodeError, subprocess.SubprocessError, AttributeError):
            snapshot = _empty_snapshot()
        _CACHE = snapshot
        _CACHE_AT = now
        return _copy_snapshot(snapshot)
