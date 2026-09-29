from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from cam_automation.case_library import CaseLibraryService
from scripts.build_customer_delivery import build_customer_zip
from scripts.customer_backup import create_backup
from scripts.customer_restore import restore_backup
from scripts.customer_self_check import check_installation, default_data_dir
from tests.test_case_library import CASE as CASE_FIXTURE
from tests.test_case_library import SNAPSHOT as SNAPSHOT_FIXTURE


ROOT = Path(__file__).resolve().parents[1]


class CustomerDeliveryTests(unittest.TestCase):
    def test_default_data_dir_is_server_root_not_customer_child(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"LOCALAPPDATA": r"C:\Customer\AppData", "CAM_APP_DATA_DIR": ""},
            clear=False,
        ):
            self.assertEqual(
                default_data_dir(),
                Path(r"C:\Customer\AppData") / "CAM Automation Studio" / "app-data",
            )

    def test_builder_is_reproducible_and_filters_development_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "one.zip"
            second = Path(directory) / "two.zip"
            build_customer_zip(first, repo_root=ROOT)
            build_customer_zip(second, repo_root=ROOT)
            self.assertEqual(
                hashlib.sha256(first.read_bytes()).digest(),
                hashlib.sha256(second.read_bytes()).digest(),
            )
            with zipfile.ZipFile(first) as archive:
                names = set(archive.namelist())
            self.assertIn("cam_automation/cli.py", names)
            self.assertIn("host_bridges/powermill.py", names)
            self.assertIn("plugins/powermill-cam-copilot/app-plugin.json", names)
            self.assertIn("delivery/examples/snapshot-fixture.json", names)
            self.assertIn("delivery/examples/case-fixture.json", names)
            self.assertIn("docs/customer/验收手册.zh-CN.md", names)
            self.assertIn("delivery/BUILD-MANIFEST.json", names)
            self.assertFalse(any(name.startswith(("build/", ".git/", "tests/")) for name in names))
            self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))

    def test_unzip_smoke_and_manifest_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "customer.zip"
            extract = Path(directory) / "extract"
            build_customer_zip(package, repo_root=ROOT)
            with zipfile.ZipFile(package) as archive:
                archive.extractall(extract)
            manifest = json.loads(
                (extract / "delivery" / "BUILD-MANIFEST.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["application_version"], "0.7.0")
            snapshot_example = json.loads(
                (extract / "delivery" / "examples" / "snapshot-fixture.json").read_text(
                    encoding="utf-8"
                )
            )
            case_example = json.loads(
                (extract / "delivery" / "examples" / "case-fixture.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(snapshot_example["source"], "fixture")
            self.assertEqual(case_example["provenance"]["source"], "fixture")
            self.assertEqual(manifest["requires_python"], ">=3.10")
            for item in manifest["files"]:
                payload = extract / Path(item["path"])
                self.assertTrue(payload.is_file(), item["path"])
                self.assertEqual(
                    hashlib.sha256(payload.read_bytes()).hexdigest(), item["sha256"]
                )
            result = check_installation(extract, Path(directory) / "customer-data")
            self.assertEqual(result["status"], "passed")

    def test_backup_restore_and_default_overwrite_protection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "case.json").write_text('{"schema_version":1}\n', encoding="utf-8")
            backup = root / "backup.zip"
            create_backup(source, backup)
            target = root / "target"
            restored = restore_backup(backup, target)
            self.assertEqual(restored["files"], 1)
            self.assertEqual((target / "case.json").read_text(encoding="utf-8"), '{"schema_version":1}\n')
            with self.assertRaises(FileExistsError):
                restore_backup(backup, target)
            restore_backup(backup, target, overwrite=True)

    def test_sqlite_backup_uses_consistent_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            database = source / "customer.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute("create table cases (name text)")
                connection.execute("insert into cases values ('before')")
                connection.commit()
            finally:
                connection.close()
            backup = root / "backup.zip"
            create_backup(source, backup)
            target = root / "target"
            restore_backup(backup, target)
            connection = sqlite3.connect(target / "customer.db")
            try:
                self.assertEqual(connection.execute("select name from cases").fetchone()[0], "before")
            finally:
                connection.close()

    def test_real_case_library_sqlite3_survives_app_data_backup_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_data = root / "app-data"
            service = CaseLibraryService(app_data / "customer")
            saved = service.add_case(CASE_FIXTURE)
            backup = root / "app-data.zip"
            create_backup(app_data, backup)
            with zipfile.ZipFile(backup) as archive:
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
                self.assertTrue(manifest["sqlite_backup_api"])
                self.assertIn("data/customer/case_library.sqlite3", archive.namelist())
                self.assertNotIn("data/customer/case_library.sqlite3-wal", archive.namelist())
                self.assertNotIn("data/customer/case_library.sqlite3-shm", archive.namelist())
            restored_root = root / "restored"
            restore_backup(backup, restored_root)
            restored = CaseLibraryService(restored_root / "customer")
            matches = restored.search_cases(SNAPSHOT_FIXTURE, "cavity")
            self.assertEqual([saved["case_id"]], [item["case_id"] for item in matches])

    def test_restore_preflights_all_conflicts_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "a.txt").write_text("new-a", encoding="utf-8")
            (source / "b.txt").write_text("new-b", encoding="utf-8")
            backup = root / "backup.zip"
            create_backup(source, backup)
            target = root / "target"
            target.mkdir()
            (target / "b.txt").write_text("old-b", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                restore_backup(backup, target)
            self.assertFalse((target / "a.txt").exists())
            self.assertEqual((target / "b.txt").read_text(encoding="utf-8"), "old-b")

    def test_restore_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            malicious = root / "malicious.zip"
            with zipfile.ZipFile(malicious, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"format": "cam-customer-backup-v1", "files": []}))
                archive.writestr("data/../escape.txt", "bad")
            with self.assertRaises(ValueError):
                restore_backup(malicious, root / "target")

    def test_restore_rejects_windows_unsafe_components(self) -> None:
        for member in ("data/foo:bar.txt", "data/CON/file.txt", "data/name. /x.txt"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                malicious = root / "malicious.zip"
                with zipfile.ZipFile(malicious, "w") as archive:
                    archive.writestr("manifest.json", json.dumps({"format": "cam-customer-backup-v1", "files": []}))
                    archive.writestr(member, "bad")
                with self.assertRaises(ValueError):
                    restore_backup(malicious, root / "target")


if __name__ == "__main__":
    unittest.main()
