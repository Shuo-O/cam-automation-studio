"""Optional stdio MCP transport over the existing offline workflow services."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .integrations import analyze, capability_manifest
from .mcp_catalog import integration_catalog, mcp_config

mcp = FastMCP(
    "CAM Automation Studio",
    instructions="Analyze recorded workflows as dry-run previews. CAD integrations are opt-in "
    "configuration references, not connected hosts. Never execute machine-ready NC code.",
)
_READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


@mcp.tool(annotations=_READ_ONLY)
def get_capabilities() -> dict[str, Any]:
    """List supported CAM products, input formats, and required review gates."""
    return capability_manifest()


@mcp.tool(annotations=_READ_ONLY)
def analyze_workflow(
    product: Literal["nx", "powermill"],
    source: str,
    source_format: str | None = None,
    name: str | None = None,
    source_name: str = "mcp-input",
) -> dict[str, Any]:
    """Statically analyze recorded text into recipe, events, preview, and review context.

    Journal code is never executed. Review-classified macro steps remain commented out.
    No artifacts are written and no CAD/CAM host is attached.
    """
    return analyze(
        product=product,
        source=source,
        source_format=source_format,
        name=name,
        source_name=source_name,
        allow_review_steps=False,
    )


@mcp.tool(annotations=_READ_ONLY)
def list_cad_integrations() -> dict[str, Any]:
    """List reviewed upstream CAD MCP servers, prerequisites, sources, and limitations."""
    return integration_catalog()


@mcp.tool(annotations=_READ_ONLY)
def get_mcp_config(server_ids: list[str]) -> dict[str, Any]:
    """Export selected client config templates; never install or start external servers.

    Replace REPLACE_WITH paths and install the prerequisites listed in the catalog first.
    """
    return mcp_config(server_ids)


def run() -> None:
    mcp.run(transport="stdio")
