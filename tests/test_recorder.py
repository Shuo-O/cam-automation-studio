from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cam_automation.models import EventQuery
from cam_automation.recorder import CaptureService, RecorderService


def _event(
    seq: int,
    *,
    product: str = "powermill",
    source_mode: str = "manual",
    view_level: str = "L1",
    action: str = "cam.model.import",
    instance_id: str = "powermill:1:A",
    project_id: str | None = "project-a",
    timestamp: str = "2026-08-23T09:00:00Z",
    note: str = "",
) -> dict[str, object]:
    mode = "automation" if source_mode == "execution_audit" else source_mode
    return {
        "schema_version": 1,
        "session_id": "source-session",
        "seq": seq,
        "product": product,
        "action": action,
        "category": "operation",
        "mode": mode,
        "params": {"command": f"PRINT 'event-{seq}'", "note": note},
        "source_file": "fixture.jsonl",
        "source_line": seq + 1,
        "duration_ms": None,
        "timestamp": timestamp,
        "source_mode": source_mode,
        "view_level": view_level,
        "expertise_label": "expert",
        "instance_id": instance_id,
        "project_id": project_id,
        "target_version": "PowerMill 2026" if product == "powermill" else "NX 2406",
        "review_status": "unreviewed",
        "future_contract_field": {"ordinal": seq},
    }


def _jsonl(events: list[dict[str, object]], *, trailing_newline: bool = True) -> str:
    payload = "\n".join(json.dumps(event) for event in events)
    return payload + ("\n" if trailing_newline else "")


class CaptureServiceTests(unittest.TestCase):
    def _authorized_service(
        self,
        data_dir: Path,
        source: Path,
        *,
        product: str = "powermill",
    ) -> RecorderService:
        service = RecorderService(
            data_dir,
            source_paths={product: [source]},
            start_background=False,
        )
        service.config.auto_connect = False
        service._save_config()
        service.configure(
            consent=True,
            detect_instances=False,
        )
        return service

    def test_consent_gates_redacted_cross_product_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            power_log = root / "operator.mac"
            power_log.write_text(
                "\n".join(
                    [
                        "# session: expert-a",
                        "PowerMill> IMPORT MODEL 'C:\\Users\\Alice\\Client-X\\part.dmt'",
                        "IMPORT MODEL 'D:\\Automation\\template.dmt'",
                        "PowerMill> PRINT 'system'",
                    ]
                ),
                encoding="utf-8",
            )
            nx_journal = root / "recorded.py"
            nx_journal.write_text(
                "\n".join(
                    [
                        "session = NXOpen.Session.GetSession()",
                        "part = session.Parts.Work",
                        "operation = part.CAMSetup.CAMOperationCollection.Create('mill_planar')",
                        "operation.GenerateToolPath()",
                    ]
                ),
                encoding="utf-8",
            )

            service = CaptureService(
                root / "capture",
                source_paths={"powermill": [power_log], "nx": [nx_journal]},
                start_background=False,
            )
            try:
                service.config.auto_connect = False
                service._save_config()
                self.assertEqual("awaiting_consent", service.status()["state"])
                self.assertEqual(0, service.scan_once())

                service.configure(consent=True, operator_label="expert")
                inserted = service.scan_once()
                events = service.recent_events(limit=100)
                serialized = json.dumps(events, ensure_ascii=False)

                self.assertGreaterEqual(inserted, 5)
                self.assertEqual({"nx", "powermill"}, {event["product"] for event in events})
                self.assertNotIn("Alice", serialized)
                self.assertNotIn("Client-X", serialized)
                self.assertNotIn("C:\\\\Users", serialized)
                self.assertIn("<PATH_", serialized)
                self.assertTrue(
                    all(
                        event["params"]["capture"]["operator_label"] == "expert"
                        for event in events
                    )
                )
                self.assertEqual(0, service.scan_once())

                execution_added = service.record_execution(
                    request={
                        "product": "powermill",
                        "action": "cam.model.import",
                        "command": "IMPORT MODEL 'C:\\Users\\Alice\\secret.dmt'",
                        "risk": "safe",
                        "recipe_hash": "abc123",
                    },
                    result={"status": "dry_run", "duration_ms": 1.5},
                )
                self.assertEqual(1, execution_added)
                latest = service.recent_events(limit=1)[0]
                self.assertEqual("execution", latest["category"])
                self.assertEqual("execution_audit", latest["source_mode"])
                self.assertEqual("automation", latest["mode"])
                self.assertNotIn("Alice", json.dumps(latest))

                removed = service.clear()
                self.assertEqual(inserted + 1, removed)
                self.assertEqual(0, service.status()["counts"]["total"])
            finally:
                service.close()

    def test_pause_resume_uses_stable_incremental_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "events.jsonl"
            first = _jsonl([_event(0)], trailing_newline=False)
            source.write_text(first, encoding="utf-8")
            service = self._authorized_service(root / "capture", source)
            try:
                self.assertEqual(1, service.scan_now())
                self.assertEqual(0, service.scan_now())
                initial_bytes = service.status()["bytes_read_total"]

                service.pause()
                appended = "\n" + _jsonl([_event(1)], trailing_newline=False)
                with source.open("a", encoding="utf-8") as stream:
                    stream.write(appended)
                self.assertEqual(0, service.scan_now())
                self.assertEqual("paused", service.status()["state"])

                service.resume()
                self.assertEqual(1, service.scan_now())
                status = service.status()
                self.assertEqual("recording", status["state"])
                appended_bytes = source.stat().st_size - len(first.encode("utf-8"))
                self.assertEqual(appended_bytes, status["last_scan_bytes"])
                self.assertEqual(
                    initial_bytes + appended_bytes,
                    status["bytes_read_total"],
                )
                self.assertEqual(2, status["counts"]["total"])
                self.assertEqual(
                    status["sources"][0]["size_bytes"],
                    status["sources"][0]["cursor_bytes"],
                )
                self.assertEqual(0, service.scan_now())
            finally:
                service.close()

    def test_authorized_background_recorder_starts_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "events.jsonl"
            source.write_text(_jsonl([_event(0)]), encoding="utf-8")
            service = RecorderService(
                root / "capture",
                source_paths={"powermill": [source]},
                start_background=True,
            )
            try:
                service.config.auto_connect = False
                service._save_config()
                service.configure(consent=True, detect_instances=False)
                deadline = time.monotonic() + 2
                while (
                    service.status()["counts"]["total"] == 0
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)

                status = service.status()
                self.assertEqual("recording", status["state"])
                self.assertEqual(1, status["counts"]["total"])
                self.assertTrue(status["local_only"])
                self.assertFalse(status["uploads_enabled"])
            finally:
                service.close()

    def test_crash_recovery_and_concurrent_scans_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "events.jsonl"
            source.write_text(_jsonl([_event(0)]), encoding="utf-8")
            data_dir = root / "capture"
            service = self._authorized_service(data_dir, source)
            self.assertEqual(1, service.scan_now())
            service.close()

            with source.open("a", encoding="utf-8") as stream:
                stream.write(_jsonl([_event(index) for index in range(1, 251)]))
            recovered = self._authorized_service(data_dir, source)
            try:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    inserted = list(pool.map(lambda _: recovered.scan_now(), range(8)))
                self.assertEqual(250, sum(inserted))
                self.assertEqual(251, recovered.status()["counts"]["total"])
                self.assertEqual(0, recovered.scan_now())
            finally:
                recovered.close()

    def test_source_interruption_rotation_and_error_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "events.jsonl"
            source.write_text(_jsonl([_event(0)]), encoding="utf-8")
            service = self._authorized_service(root / "capture", source)
            try:
                self.assertEqual(1, service.scan_now())
                source.rename(root / "events.previous")

                self.assertEqual(0, service.scan_now())
                self.assertEqual("source_interrupted", service.status()["state"])
                self.assertEqual(
                    "source_interrupted",
                    service.status()["sources"][0]["status"],
                )

                source.write_text(_jsonl([_event(1)]), encoding="utf-8")
                self.assertEqual(1, service.scan_now())
                status = service.status()
                self.assertEqual("recording", status["state"])
                self.assertEqual(1, status["sources"][0]["generation"])
                self.assertEqual(
                    "rotated_or_truncated",
                    status["last_source_transition"]["kind"],
                )
                self.assertEqual(2, status["counts"]["total"])

                source.write_text('{"session_id":', encoding="utf-8")
                self.assertEqual(0, service.scan_now())
                self.assertEqual("error", service.status()["state"])

                source.write_text(_jsonl([_event(2)]), encoding="utf-8")
                self.assertEqual(1, service.scan_now())
                self.assertEqual("recording", service.status()["state"])
                self.assertEqual(3, service.status()["counts"]["total"])
            finally:
                service.close()

    def test_catching_up_state_is_visible_and_drains_incrementally(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "events.jsonl"
            source.write_text(
                _jsonl([_event(index) for index in range(40)]),
                encoding="utf-8",
            )
            service = self._authorized_service(root / "capture", source)
            try:
                with mock.patch(
                    "cam_automation.recorder._MAX_INCREMENTAL_BYTES",
                    1024,
                ):
                    first_inserted = service.scan_now()
                    self.assertGreater(first_inserted, 0)
                    self.assertEqual("catching_up", service.status()["state"])
                    while service.status()["state"] == "catching_up":
                        service.scan_now()
                self.assertEqual("recording", service.status()["state"])
                self.assertEqual(40, service.status()["counts"]["total"])
            finally:
                service.close()

    def test_combined_filters_keep_manual_and_view_level_orthogonal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "events.jsonl"
            events = [
                _event(
                    level,
                    product="nx",
                    view_level=f"L{level}",
                    action=f"cam.level.{level}",
                    instance_id="nx:10:A",
                    project_id="nx-project",
                    timestamp=f"2026-08-23T09:00:0{level}Z",
                    note="needle" if level == 3 else "",
                )
                for level in range(5)
            ]
            events.extend(
                [
                    _event(5, source_mode="automation", view_level="L3"),
                    _event(6, source_mode="system", view_level="L0"),
                    _event(7, source_mode="execution_audit", view_level="L4"),
                ]
            )
            source.write_text(_jsonl(events), encoding="utf-8")
            service = self._authorized_service(root / "capture", source)
            try:
                self.assertEqual(8, service.scan_now())
                page = service.query_events(
                    EventQuery(
                        source_modes=("manual",),
                        view_levels=("L3",),
                        products=("nx",),
                        instance_ids=("nx:10:A",),
                        project_ids=("nx-project",),
                        actions=("cam.level.3",),
                        from_time="2026-08-23T09:00:03Z",
                        to_time="2026-08-23T09:00:03Z",
                        text="needle",
                    )
                )
                self.assertEqual(1, page.returned_count)
                self.assertEqual("L3", page.events[0].view_level)
                self.assertEqual("manual", page.events[0].effective_source_mode)
                self.assertEqual(
                    {"L0", "L1", "L2", "L3", "L4"},
                    {
                        event.view_level
                        for event in service.query_events(
                            EventQuery(source_modes=("manual",), limit=100)
                        ).events
                    },
                )
                audit = service.query_events(
                    EventQuery(source_modes=("execution_audit",))
                ).events[0]
                self.assertEqual("automation", audit.mode)
                exported = service.export_jsonl().decode("utf-8")
                self.assertIn("future_contract_field", exported)
            finally:
                service.close()

    def test_stable_cursor_paginates_more_than_one_thousand_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "events.jsonl"
            source.write_text(
                _jsonl([_event(index) for index in range(1005)]),
                encoding="utf-8",
            )
            service = self._authorized_service(root / "capture", source)
            try:
                self.assertEqual(1005, service.scan_now())
                first = service.query_events(EventQuery(limit=127, sort="asc"))
                self.assertEqual(127, first.returned_count)
                self.assertTrue(first.has_more)

                with source.open("a", encoding="utf-8") as stream:
                    stream.write(_jsonl([_event(1005)]))
                self.assertEqual(1, service.scan_now())

                seen = [event.seq for event in first.events]
                cursor = first.next_cursor
                while cursor is not None:
                    page = service.query_events(
                        EventQuery(limit=127, sort="asc", cursor=cursor)
                    )
                    seen.extend(event.seq for event in page.events)
                    cursor = page.next_cursor
                self.assertEqual(list(range(1006)), seen)

                descending = service.query_events(EventQuery(limit=25, sort="desc"))
                with source.open("a", encoding="utf-8") as stream:
                    stream.write(_jsonl([_event(1006)]))
                self.assertEqual(1, service.scan_now())
                second = service.query_events(
                    EventQuery(
                        limit=25,
                        sort="desc",
                        cursor=descending.next_cursor,
                    )
                )
                self.assertTrue(
                    {event.seq for event in descending.events}.isdisjoint(
                        event.seq for event in second.events
                    )
                )
                self.assertNotIn(1006, [event.seq for event in second.events])
                with self.assertRaisesRegex(ValueError, "query-mismatched"):
                    service.query_events(
                        EventQuery(
                            products=("nx",),
                            limit=25,
                            sort="desc",
                            cursor=descending.next_cursor,
                        )
                    )
            finally:
                service.close()
                service.close()

    def test_enable_requires_consent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = CaptureService(directory, start_background=False)
            try:
                with self.assertRaisesRegex(ValueError, "explicit consent"):
                    service.configure(enabled=True)
            finally:
                service.close()

    def test_category_settings_disable_each_capture_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = CaptureService(directory, start_background=False)
            try:
                service.configure(
                    consent=True,
                    capture_logs=False,
                    detect_instances=False,
                    audit_execution=False,
                )
                self.assertEqual(0, service.scan_once())
                self.assertEqual(0, service.status()["counts"]["total"])
                self.assertEqual(
                    {"logs": False, "instances": False, "execution_audit": False},
                    service.status()["categories"],
                )
                self.assertEqual(
                    0,
                    service.record_execution(
                        request={
                            "product": "nx",
                            "action": "cam.model.import",
                            "command": "IMPORT MODEL",
                        },
                        result={"status": "dry_run"},
                    ),
                )
            finally:
                service.close()

    def test_revoking_consent_clears_runtime_window_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = CaptureService(directory, start_background=False)
            try:
                service.config.consent = True
                service.config.consent_source = "auto_install"
                service._instances["nx"] = [
                    {
                        "instance_id": "nx:100:A1",
                        "pid": 100,
                        "window_title": "Sensitive project title",
                    }
                ]
                service._processes["nx"] = True

                status = service.configure(consent=False)

                self.assertFalse(status["consent"])
                self.assertEqual("revoked", status["consent_source"])
                self.assertEqual([], status["instances"]["nx"])
                self.assertFalse(status["processes"]["nx"])
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
