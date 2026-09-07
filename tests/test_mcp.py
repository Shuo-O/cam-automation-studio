from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from cam_automation.cli import main
from cam_automation.sample import SAMPLE_LOG
from cam_automation.web_server import _WorkflowHandler, _WorkflowServer


class CatalogTests(unittest.TestCase):
    def cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            try:
                status = main(list(args))
            except SystemExit as error:
                status = error.code
        return status, output.getvalue()

    def test_catalog_and_config_export_are_usable_without_starting_servers(self):
        status, output = self.cli("mcp-catalog")
        self.assertEqual(0, status, "Catalog command must work without optional MCP dependencies")
        catalog = json.loads(output)
        self.assertIn("freecad", {item["id"] for item in catalog["servers"]})
        status, output = self.cli("mcp-config", "freecad", "cam-studio")
        self.assertEqual(0, status)
        config = json.loads(output)["mcpServers"]
        self.assertEqual({"freecad", "cam-studio"}, set(config))
        self.assertEqual(["freecad-mcp"], config["freecad"]["args"])
        self.assertEqual(["-m", "cam_automation", "mcp"], config["cam-studio"]["args"])
        self.assertEqual(2, self.cli("mcp-config", "unknown-server")[0])

    def test_http_catalog_and_selected_config(self):
        server = _WorkflowServer(("127.0.0.1", 0), _WorkflowHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(base + "/api/mcp/catalog") as response:
                    catalog = json.load(response)
            except HTTPError as error:
                self.fail(f"Catalog route is unavailable: {error.code}")
            self.assertTrue(catalog["servers"])
            with urlopen(base + "/api/mcp/config?server=fusion") as response:
                config = json.load(response)["mcpServers"]
            self.assertEqual({"fusion"}, set(config))
            self.assertEqual(["--server_type", "stdio"], config["fusion"]["args"][-2:])
            for query in ("", "?server=unknown-server"):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(base + "/api/mcp/config" + query)
                self.assertEqual(400, caught.exception.code)
                caught.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Install .[mcp] for real stdio checks")
class McpTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_client_can_analyze_both_products_and_reject_bad_input(self):
        from anyio import fail_after
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "cam_automation", "mcp"],
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        with fail_after(30):
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    names = {tool.name for tool in listed.tools}
                    self.assertIn("analyze_workflow", names)
                    self.assertTrue(all(tool.annotations.readOnlyHint for tool in listed.tools))
                    for product, source in [
                        ("powermill", SAMPLE_LOG),
                        (
                            "nx",
                            (
                                "import NXOpen\ns = NXOpen.Session.GetSession()\n"
                                'p = s.Parts.Work\np.CAMSetup.CAMOperationCollection.Create("mill_planar")'
                            ),
                        ),
                    ]:
                        result = await session.call_tool(
                            "analyze_workflow",
                            {
                                "product": product,
                                "source": source,
                            },
                        )
                        self.assertFalse(result.isError, result.content)
                        payload = json.loads(result.content[0].text)
                        self.assertEqual(product, payload["product"])
                        self.assertEqual("dry-run", payload["adapter"]["execution_mode"])
                        self.assertTrue(payload["activity_events"])
                        self.assertIn("cam.codex.bridge.v1", json.dumps(payload["codex_context"]))
                    round_trip = await session.call_tool(
                        "analyze_workflow",
                        {
                            "product": "nx",
                            "source_format": "jsonl",
                            "source": "\n".join(
                                json.dumps(event) for event in payload["activity_events"]
                            ),
                        },
                    )
                    self.assertFalse(round_trip.isError, round_trip.content)
                    bad = await session.call_tool(
                        "analyze_workflow", {"product": "nx", "source": ""}
                    )
                    self.assertTrue(bad.isError)
                    catalog = await session.call_tool("list_cad_integrations", {})
                    self.assertIn("freecad", catalog.content[0].text)
                    config = await session.call_tool("get_mcp_config", {"server_ids": ["freecad"]})
                    self.assertEqual(
                        {"freecad"}, set(json.loads(config.content[0].text)["mcpServers"])
                    )
                    capabilities = await session.call_tool("get_capabilities", {})
                    self.assertEqual(
                        "dry-run", json.loads(capabilities.content[0].text)["execution_mode"]
                    )
