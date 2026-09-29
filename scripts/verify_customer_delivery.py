"""Exercise the extracted distribution using its actual launcher and HTTP API."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path


def verify(package: Path) -> dict:
    checks: list[str] = []
    package_hash = hashlib.sha256(package.read_bytes()).hexdigest()
    sidecar = Path(str(package) + ".sha256").read_text(encoding="utf-8").split()[0]
    if package_hash != sidecar:
        raise ValueError("Package SHA-256 does not match sidecar.")
    checks.append("package_sha256")
    with tempfile.TemporaryDirectory(prefix="cam-release-acceptance-") as temporary:
        root = Path(temporary)
        extract, data = root / "application", root / "data"
        with zipfile.ZipFile(package) as archive:
            for name in archive.namelist():
                target = (extract / name).resolve()
                if not target.is_relative_to(extract.resolve()):
                    raise ValueError("Invalid distribution member path.")
            archive.extractall(extract)
        manifest = json.loads((extract / "delivery/BUILD-MANIFEST.json").read_text("utf-8"))
        for item in manifest["files"]:
            digest = hashlib.sha256((extract / item["path"]).read_bytes()).hexdigest()
            if digest != item["sha256"]:
                raise ValueError("Distribution file hash mismatch: " + item["path"])
        checks.append("all_manifest_file_hashes")
        environment = dict(os.environ, PYTHONUTF8="1", PYTHONNOUSERSITE="1")
        # Acceptance never consumes a developer/customer capture override.
        environment.pop("CAM_CAPTURE_DIR", None)
        environment["CAM_APP_DATA_DIR"] = str(data)

        def script(name: str, *args: str) -> str:
            return subprocess.check_output(
                [sys.executable, "-s", str(extract / "scripts" / name), *args],
                cwd=extract, env=environment, text=True, encoding="utf-8", timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

        check = json.loads(script("customer_self_check.py", "--json", "--data-dir", str(data)))
        if check["status"] != "passed":
            raise ValueError("Extracted self-check failed.")
        checks.append("extracted_self_check")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base = f"http://127.0.0.1:{port}"

        def request(path: str, payload: dict | None = None):
            encoded = None if payload is None else json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(base + path, data=encoded, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.load(response)

        with (root / "server.log").open("w", encoding="utf-8") as log:
            server = subprocess.Popen(
                [sys.executable, "-s", str(extract / "scripts/customer_launcher.py"),
                 "--port", str(port), "--data-dir", str(data)],
                cwd=extract, env=environment, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                deadline = time.monotonic() + 15
                while True:
                    if server.poll() is not None:
                        raise RuntimeError("Extracted launcher exited: " + (root / "server.log").read_text("utf-8"))
                    try:
                        health = request("/api/health")
                        break
                    except (OSError, ValueError):
                        if time.monotonic() > deadline:
                            raise RuntimeError("Extracted launcher did not become ready.")
                        time.sleep(0.1)
                assert health["version"] == manifest["application_version"]
                with urllib.request.urlopen(base + "/delivery/", timeout=5) as response:
                    assert "客户交付工作台" in response.read().decode("utf-8")
                status = request("/api/delivery/status")
                assert status["dry_run"] and not status["machine_output_enabled"]
                assert request("/api/plugins")["installed_count"] == 0
                checks.append("launcher_health_customer_page_and_default_plugin_state")
                examples = extract / "delivery/examples"
                snapshot = request("/api/delivery/snapshots", json.loads((examples / "snapshot-fixture.json").read_text("utf-8")))
                case = request("/api/delivery/cases", json.loads((examples / "case-fixture.json").read_text("utf-8")))
                context = {"snapshot_id": snapshot["snapshot_id"]}
                assert request("/api/delivery/cases/search", context)["items"]
                proposal = request("/api/delivery/proposals", {**context, "case_id": case["case_id"]})
                proposal_path = "/api/delivery/proposals/" + proposal["proposal_id"]
                for gate in ("review", "simulation", "collision", "shop_approval"):
                    request(proposal_path + "/evidence", {
                        "gate": gate, "status": "passed", "actor": "Distribution acceptance fixture",
                        "note": "Automated synthetic evidence, not a CAM simulation or shop approval.",
                        "proposal_hash": proposal["proposal_hash"], "snapshot_hash": snapshot["content_hash"],
                    })
                bundle = request(proposal_path + "/export", context)
                assert bundle["dry_run"] and len(bundle["evidence"]) == 4
                assert bundle["snapshot"]["source"] == "fixture"
                assert not bundle["execution_available"] and not bundle["nc_output_available"]
                changed = request("/api/delivery/proposals", {
                    **context, "case_id": case["case_id"], "parameters": {"stepover": 0.25},
                })
                assert changed["proposal_hash"] != proposal["proposal_hash"]
                assessed = request("/api/delivery/proposals/" + changed["proposal_id"] + "/assess", context)
                assert not assessed["ready"] and not assessed["evidence"]
                checks.append("packaged_examples_search_proposal_evidence_export_and_invalidation")
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
        backup, restore = root / "backup.zip", root / "restored"
        script("customer_backup.py", str(data), str(backup))
        script("customer_restore.py", str(backup), str(restore))
        restore_check = subprocess.check_output(
            [sys.executable, "-s", "-c",
             "from pathlib import Path; from cam_automation.customer_api import CustomerApi; "
             "import json,sys; a=CustomerApi(Path(sys.argv[1])/'customer'); "
             "print(json.dumps(a.dispatch('GET','/api/delivery/cases')[0])); a.close()", str(restore)],
            cwd=extract, env=environment, text=True, encoding="utf-8", timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert json.loads(restore_check)["items"][0]["case_id"] == case["case_id"]
        checks.append("packaged_backup_restore_preserves_case_library")
    return {"schema_version": 1, "status": "passed", "version": manifest["application_version"],
            "package": str(package.resolve()), "sha256": package_hash,
            "checks": checks, "real_cam_validation": "not_run", "dry_run": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = json.dumps(verify(args.package), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    print(report)
