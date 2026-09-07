"""Reviewed CAD integrations and opt-in client configuration; no process launching."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def integration_catalog() -> dict[str, Any]:
    catalog = json.loads(Path(__file__).with_name("cad_integrations.json").read_text("utf-8"))
    catalog["servers"].insert(
        0,
        {
            "id": "cam-studio",
            "name": "CAM Automation Studio",
            "category": "工作流审阅",
            "summary": "通过 MCP 分析 NX Journal / PowerMill 日志，返回配方、事件和 dry-run 预览。",
            "repository": "https://github.com/Shuo-O/cam-automation-studio",
            "license": "MIT",
            "capabilities": [
                "get_capabilities",
                "analyze_workflow",
                "list_cad_integrations",
                "get_mcp_config",
            ],
            "prerequisites": ["Python 3.10+；在启动工作台的 Python 环境安装本项目的 mcp extra"],
            "setup": ["python -m pip install -e '.[mcp]'"],
            "caveats": "仅分析与配置导出；不运行 Journal、不连接 CAD 进程、不生成机床 NC。",
            "integration_status": "builtin",
            "config": {"command": sys.executable, "args": ["-m", "cam_automation", "mcp"]},
        },
    )
    return catalog


def mcp_config(server_ids: list[str]) -> dict[str, Any]:
    if not isinstance(server_ids, list) or not server_ids:
        raise ValueError("Select at least one MCP server ID.")
    servers = {item["id"]: item for item in integration_catalog()["servers"]}
    selected = {}
    for server_id in server_ids:
        if not isinstance(server_id, str) or server_id not in servers:
            raise ValueError(f"Unknown MCP server ID: {server_id!r}")
        selected[server_id] = servers[server_id]["config"]
    return {"mcpServers": selected}
