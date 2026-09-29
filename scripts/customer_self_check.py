"""Offline installation self-check for the customer distribution."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
from pathlib import Path
from typing import Any


def default_data_dir() -> Path:
    configured = os.environ.get("CAM_APP_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "CAM Automation Studio" / "app-data"
    return Path.home() / ".cam-automation-studio" / "app-data"


def check_installation(
    package_root: str | os.PathLike[str] | None = None,
    data_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = Path(package_root or Path(__file__).resolve().parents[1]).resolve()
    data = Path(data_dir or default_data_dir()).expanduser().resolve()
    checks: list[dict[str, Any]] = []

    python_ok = sys.version_info >= (3, 10)
    checks.append(
        {
            "name": "python_version",
            "passed": python_ok,
            "detail": platform.python_version(),
            "required": ">=3.10",
        }
    )
    required = (
        root / "cam_automation" / "__init__.py",
        root / "cam_automation" / "cli.py",
        root / "cam_automation" / "web" / "index.html",
        root / "cam_automation" / "web" / "delivery" / "index.html",
        root / "host_bridges" / "__init__.py",
        root / "plugins" / "powermill-cam-copilot" / "app-plugin.json",
        root / "plugins" / "ug-cam-copilot" / "app-plugin.json",
    )
    for path in required:
        checks.append(
            {
                "name": f"file:{path.relative_to(root).as_posix()}",
                "passed": path.is_file(),
                "detail": "present" if path.is_file() else "missing",
            }
        )
    try:
        data.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=".self-check-", dir=data, delete=False
        ) as marker:
            marker.write("ok\n")
            marker_path = Path(marker.name)
        marker_path.unlink()
        data_ok = True
        data_detail = str(data)
    except OSError as error:
        data_ok = False
        data_detail = str(error)
    checks.append({"name": "data_directory", "passed": data_ok, "detail": data_detail})
    passed = all(bool(item["passed"]) for item in checks)
    return {
        "schema_version": 1,
        "status": "passed" if passed else "blocked",
        "package_root": str(root),
        "data_dir": str(data),
        "checks": checks,
        "dry_run_only": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查客户安装环境（需要 Python >= 3.10）。")
    parser.add_argument("--package-root", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    result = check_installation(args.package_root, args.data_dir)
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"自检: {result['status']}")
        for check in result["checks"]:
            label = "通过" if check["passed"] else "失败"
            print(f"- {label}: {check['name']} ({check['detail']})")
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
