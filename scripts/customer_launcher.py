"""Clear Windows entry point for the offline customer workbench."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="启动 CAM Automation Studio 客户工作台。")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--skip-self-check", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from customer_self_check import check_installation

    configured_data_dir = os.environ.get("CAM_APP_DATA_DIR")
    data_dir = (args.data_dir or (Path(configured_data_dir) if configured_data_dir else None))
    if data_dir is None:
        from customer_self_check import default_data_dir

        data_dir = default_data_dir()
    if not args.skip_self_check:
        check = check_installation(root, data_dir)
        if check["status"] != "passed":
            print("自检未通过；请先修复环境后再启动。", file=sys.stderr)
            return 2
    os.environ["CAM_APP_DATA_DIR"] = str(data_dir.resolve())
    print(f"客户工作台: http://{args.host}:{args.port}/delivery/", flush=True)
    from cam_automation.cli import main as cam_main

    return int(cam_main(["serve", "--host", args.host, "--port", str(args.port)]) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
