from __future__ import annotations

import csv
import os
import subprocess
import threading
import time
from typing import Any, Iterable, Mapping


_CAM_PROCESS_NAMES = {
    "nx": frozenset({"ugraf.exe", "nx.exe"}),
    "powermill": frozenset({"powermill.exe", "pmill.exe"}),
}
_CACHE_LOCK = threading.RLock()
_CACHE_AT = 0.0
_CACHE: dict[str, list[dict[str, Any]]] = {"nx": [], "powermill": []}


def _empty_snapshot() -> dict[str, list[dict[str, Any]]]:
    return {product: [] for product in _CAM_PROCESS_NAMES}


def _copy_snapshot(
    value: Mapping[str, Iterable[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    return {
        product: [dict(instance) for instance in value.get(product, [])]
        for product in _CAM_PROCESS_NAMES
    }


def _build_instances(
    processes: Iterable[tuple[str, int]],
    windows: Iterable[Mapping[str, Any]],
    *,
    foreground_handle: int = 0,
) -> dict[str, list[dict[str, Any]]]:
    """Build a deterministic product/window snapshot from OS discovery results."""

    process_products: dict[int, tuple[str, str]] = {}
    for process_name, pid in processes:
        normalized = process_name.casefold()
        for product, names in _CAM_PROCESS_NAMES.items():
            if normalized in names:
                process_products[int(pid)] = (product, process_name)
                break

    windows_by_pid: dict[int, list[dict[str, Any]]] = {}
    for window in windows:
        pid = int(window.get("pid", 0) or 0)
        handle = int(window.get("handle", 0) or 0)
        title = str(window.get("title", "")).strip()[:512]
        if pid not in process_products or not handle or not title:
            continue
        windows_by_pid.setdefault(pid, []).append(
            {"handle": handle, "title": title}
        )

    snapshot = _empty_snapshot()
    for pid, (product, process_name) in process_products.items():
        product_windows = windows_by_pid.get(pid, [])
        if not product_windows:
            snapshot[product].append(
                {
                    "instance_id": f"{product}:{pid}:process",
                    "product": product,
                    "pid": pid,
                    "process_name": process_name,
                    "window_handle": None,
                    "window_title": "",
                    "is_foreground": False,
                    "window_state": "background",
                }
            )
            continue
        seen_handles: set[int] = set()
        for window in product_windows:
            handle = int(window["handle"])
            if handle in seen_handles:
                continue
            seen_handles.add(handle)
            snapshot[product].append(
                {
                    "instance_id": f"{product}:{pid}:{handle:X}",
                    "product": product,
                    "pid": pid,
                    "process_name": process_name,
                    "window_handle": f"0x{handle:X}",
                    "window_title": str(window["title"]),
                    "is_foreground": handle == foreground_handle,
                    "window_state": "foreground"
                    if handle == foreground_handle
                    else "visible",
                }
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


def _windows_processes() -> list[tuple[str, int]]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"],
        check=False,
        capture_output=True,
        text=True,
        timeout=3,
        creationflags=flags,
    )
    processes: list[tuple[str, int]] = []
    for row in csv.reader(result.stdout.splitlines()):
        if len(row) < 2:
            continue
        try:
            pid = int(row[1].replace(",", "").strip())
        except ValueError:
            continue
        processes.append((row[0].strip(), pid))
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
        if user32.GetWindow(handle, 4):  # GW_OWNER: ignore owned tool/dialog windows.
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


def detect_cam_instances(
    *,
    force: bool = False,
    max_age_seconds: float = 2.0,
) -> dict[str, list[dict[str, Any]]]:
    """Return visible NX and PowerMill instances without attaching to either product."""

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
