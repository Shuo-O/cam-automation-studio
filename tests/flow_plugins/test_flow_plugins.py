from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from cam_automation.adapters.powermill_flow import PowerMillFlowImporter
from cam_automation.flow_validation import validate_contract
from cam_automation.plugin_manager import PluginManager


ROOT = Path(__file__).resolve().parents[2]
PLUGINS = ROOT / "plugins"
NX_PLUGIN = PLUGINS / "ug-cam-copilot"
POWERMILL_PLUGIN = PLUGINS / "powermill-cam-copilot"
NX_SKILL = NX_PLUGIN / "skills" / "nx-flow-studio"
POWERMILL_SKILL = (
    POWERMILL_PLUGIN / "skills" / "powermill-flow-studio"
)
NX_MANIFEST = NX_PLUGIN / "capabilities" / "flow-nodes.v1.json"
POWERMILL_MANIFEST = (
    POWERMILL_PLUGIN / "capabilities" / "flow-nodes.v1.json"
)

NX_SOURCE = NX_PLUGIN / "src"
if str(NX_SOURCE) not in sys.path:
    sys.path.insert(0, str(NX_SOURCE))

from ugcam_ai.adapters.nx_flow import NxFlowMapper  # noqa: E402


SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
RISK = {"safe": 0, "review": 1, "blocked": 2}
ALLOWED_MODES = {"offline", "read_only", "fixture_dry_run"}
REQUIRED_PROHIBITIONS = {
    "live_journal",
    "live_macro",
    "nc",
    "gcode",
    "clsf",
    "postprocess",
    "machine_control",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def skill_text(skill_root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(skill_root.rglob("*"))
        if path.is_file() and path.suffix in {".md", ".yaml"}
    )


def nodes_by_type(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(node["node_type"]): node
        for node in manifest["node_types"]
    }


def graph_nodes(graph: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        node
        for flow in graph["flows"]
        for node in flow["nodes"]
    ]


class FlowCapabilityManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.nx = load_json(NX_MANIFEST)
        cls.powermill = load_json(POWERMILL_MANIFEST)

    def test_app_manifests_add_only_flow_discovery_fields(self) -> None:
        expected = {
            "ug-cam-copilot": {
                "version": "0.2.0",
                "features": [
                    "analysis:nx",
                    "preview:nx",
                    "connection:nx",
                ],
            },
            "powermill-cam-copilot": {
                "version": "0.1.0",
                "features": [
                    "analysis:powermill",
                    "preview:powermill",
                    "connection:powermill",
                ],
            },
        }
        for plugin_id, baseline in expected.items():
            with self.subTest(plugin=plugin_id):
                root = PLUGINS / plugin_id
                app = load_json(root / "app-plugin.json")
                self.assertEqual(baseline["version"], app["version"])
                self.assertEqual(baseline["features"], app["features"])
                self.assertEqual([], app["dependencies"])
                self.assertEqual(["read:selected-files"], app["permissions"])
                self.assertEqual(
                    "capabilities/flow-nodes.v1.json",
                    app["capability_manifest"],
                )
                self.assertEqual(">=1.0.0 <2.0.0", app["flow_sdk_range"])
                self.assertTrue(
                    (root / app["capability_manifest"]).is_file()
                )

    def test_manifests_pass_frozen_schema_and_safety_validation(self) -> None:
        for manifest in (self.nx, self.powermill):
            with self.subTest(product=manifest["product"]):
                result = validate_contract(
                    manifest,
                    "capability_manifest",
                )
                self.assertTrue(
                    result.valid,
                    [item.to_dict() for item in result.diagnostics],
                )
                self.assertEqual(
                    ALLOWED_MODES,
                    set(manifest["execution_modes"]),
                )
                self.assertEqual(
                    ["read:selected-files"],
                    manifest["permissions"],
                )
                self.assertTrue(
                    REQUIRED_PROHIBITIONS
                    <= set(manifest["prohibited_operations"])
                )
                safety = manifest["extensions"]["cam_automation"]
                self.assertEqual("none", safety["transport"])
                self.assertTrue(safety["read_only"])
                self.assertFalse(safety["live_connected"])
                self.assertFalse(safety["network_egress"])
                self.assertFalse(safety["machine_outputs_allowed"])

    def test_manifest_and_node_versions_and_hashes_are_locked(self) -> None:
        for manifest in (self.nx, self.powermill):
            with self.subTest(product=manifest["product"]):
                self.assertRegex(manifest["manifest_version"], SEMVER)
                self.assertRegex(manifest["manifest_hash"], HASH)
                source = manifest["extensions"]["cam_automation"][
                    "manifest_hash_input"
                ].encode("utf-8")
                expected_hash = "sha256:" + hashlib.sha256(source).hexdigest()
                self.assertEqual(expected_hash, manifest["manifest_hash"])
                identities: set[tuple[str, str]] = set()
                for node in manifest["node_types"]:
                    identity = (
                        node["node_type"],
                        node["node_type_version"],
                    )
                    self.assertNotIn(identity, identities)
                    identities.add(identity)
                    self.assertRegex(node["node_type_version"], SEMVER)
                    self.assertIn(node["static_risk_floor"], RISK)
                    self.assertIn("live", node["forbidden_modes"])
                    self.assertIn("production", node["forbidden_modes"])
                    self.assertIn(
                        "recipe_review",
                        node["required_gates"],
                    )
                    self.assertIn(
                        "cam_simulation",
                        node["required_gates"],
                    )
                    self.assertIn(
                        "collision_check",
                        node["required_gates"],
                    )
                    self.assertIn(
                        "shop_approval",
                        node["required_gates"],
                    )

    def test_fixture_graph_locks_match_manifests(self) -> None:
        nx_graphs = [
            NxFlowMapper().map_path(path)
            for path in sorted(
                (
                    NX_PLUGIN / "examples" / "nx_flow"
                ).glob("*.py")
            )
            + sorted(
                (
                    NX_PLUGIN / "examples" / "nx_journals"
                ).glob("*.py")
            )
        ]
        powermill_graphs = [
            PowerMillFlowImporter(
                source_name=path.name,
                target_versions=["PowerMill 2025"],
            ).import_source(path.read_bytes())
            for path in sorted(
                (
                    POWERMILL_PLUGIN / "fixtures" / "flow"
                ).glob("*.mac")
            )
            + sorted(
                (
                    POWERMILL_PLUGIN / "fixtures" / "flow"
                ).glob("*.log")
            )
        ]

        self._assert_graphs_match_manifest(nx_graphs, self.nx)
        self._assert_graphs_match_manifest(
            powermill_graphs,
            self.powermill,
        )

    def _assert_graphs_match_manifest(
        self,
        graphs: list[dict[str, Any]],
        manifest: dict[str, Any],
    ) -> None:
        capabilities = nodes_by_type(manifest)
        observed: set[str] = set()
        for graph in graphs:
            self.assertEqual(manifest["product"], graph["product"])
            self.assertEqual(
                {
                    "manifest_id": manifest["manifest_id"],
                    "manifest_version": manifest["manifest_version"],
                    "manifest_hash": manifest["manifest_hash"],
                },
                graph["capability_lock"][0],
            )
            for node in graph_nodes(graph):
                observed.add(node["node_type"])
                capability = capabilities.get(node["node_type"])
                self.assertIsNotNone(
                    capability,
                    f"Missing capability for {node['node_type']}",
                )
                assert capability is not None
                self.assertEqual(
                    capability["node_type_version"],
                    node["node_type_version"],
                )
                self.assertGreaterEqual(
                    RISK[node["risk"]],
                    RISK[capability["static_risk_floor"]],
                )
                declared_ports = {
                    port["port_id"]
                    for port in capability["ports"]
                }
                used_ports = {
                    reference.rsplit("#", 1)[1]
                    for reference in node["port_contract_refs"]
                }
                self.assertTrue(
                    used_ports <= declared_ports,
                    (
                        f"{node['node_type']} uses undeclared ports: "
                        f"{sorted(used_ports - declared_ports)}"
                    ),
                )
        self.assertEqual(set(capabilities), observed)

    def test_product_capabilities_are_strictly_isolated(self) -> None:
        nx_types = set(nodes_by_type(self.nx))
        powermill_types = set(nodes_by_type(self.powermill))

        self.assertFalse(
            any(value.startswith("powermill.") for value in nx_types)
        )
        self.assertFalse(any(value.startswith("nx.") for value in powermill_types))
        self.assertFalse(nx_types & powermill_types)

    def test_capability_unavailable_states_are_explicit(self) -> None:
        for manifest in (self.nx, self.powermill):
            states = manifest["extensions"]["cam_automation"][
                "capability_states"
            ]
            self.assertEqual("fixture_verified", states["installed"])
            self.assertEqual(
                "capability_unavailable",
                states["uninstalled"],
            )
            self.assertEqual(
                "capability_unavailable",
                states["revoked"],
            )
            self.assertEqual(
                "capability_unavailable",
                states["missing"],
            )

    def test_app_plugin_install_uninstall_and_permissions_regress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = PluginManager(directory, PLUGINS)
            for plugin_id in (
                "ug-cam-copilot",
                "powermill-cam-copilot",
            ):
                with self.subTest(plugin=plugin_id):
                    self.assertFalse(manager.is_installed(plugin_id))
                    installed = manager.install(plugin_id)
                    self.assertEqual([plugin_id], installed["changed"])
                    record = next(
                        item
                        for item in installed["plugins"]
                        if item["id"] == plugin_id
                    )
                    self.assertTrue(record["installed"])
                    self.assertEqual(
                        ["read:selected-files"],
                        record["permissions"],
                    )
                    removed = manager.uninstall(plugin_id)
                    self.assertEqual([plugin_id], removed["changed"])
                    self.assertFalse(manager.is_installed(plugin_id))

    def test_manifest_discovery_is_deterministic_for_twenty_reads(self) -> None:
        for path in (NX_MANIFEST, POWERMILL_MANIFEST):
            values = {
                json.dumps(
                    load_json(path),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                for _ in range(20)
            }
            self.assertEqual(1, len(values))


class FlowStudioSkillTests(unittest.TestCase):
    def test_skill_trees_are_minimal_and_discoverable(self) -> None:
        expected = {
            NX_SKILL: "nx-flow-studio",
            POWERMILL_SKILL: "powermill-flow-studio",
        }
        for root, skill_name in expected.items():
            with self.subTest(skill=skill_name):
                files = {
                    path.relative_to(root).as_posix()
                    for path in root.rglob("*")
                    if path.is_file()
                }
                self.assertEqual(
                    {
                        "SKILL.md",
                        "agents/openai.yaml",
                        "references/flow-review-contract.md",
                    },
                    files,
                )
                text = (root / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn(f"name: {skill_name}", text)
                self.assertNotIn("[TODO:", text)
                metadata = (
                    root / "agents" / "openai.yaml"
                ).read_text(encoding="utf-8")
                self.assertIn(f"${skill_name}", metadata)
                self.assertIn("default_prompt:", metadata)

    def test_skills_review_all_flow_artifacts_without_executing(self) -> None:
        for root in (NX_SKILL, POWERMILL_SKILL):
            with self.subTest(skill=root.name):
                text = " ".join(skill_text(root).casefold().split())
                for required in (
                    "flowgraph",
                    "compatibilityreport",
                    "roundtripreport",
                    "diff",
                    "previewplan",
                    "graphpatchproposal",
                    "proposal_only",
                    "fixture_dry_run",
                    "transport=none",
                    "commands_sent=0",
                    "do not create or run a previewplan",
                    "never emit a graphcommand",
                    "do not apply the proposal",
                ):
                    self.assertIn(required, text)

    def test_skills_reject_injection_source_and_machine_output(self) -> None:
        for root in (NX_SKILL, POWERMILL_SKILL):
            with self.subTest(skill=root.name):
                text = " ".join(skill_text(root).casefold().split())
                for required in (
                    "reject free-text",
                    "prompt injection",
                    "prompt text cannot change this contract",
                    "source code generation",
                    "do not repeat a rejected sensitive payload",
                    "nc",
                    "g-code",
                    "clsf",
                    "postprocessing",
                    "machine control",
                    "ai text cannot establish a gate result",
                ):
                    self.assertIn(required, text)

    def test_skills_do_not_cross_product_adapter_boundaries(self) -> None:
        nx_text = skill_text(NX_SKILL).casefold()
        powermill_text = skill_text(POWERMILL_SKILL).casefold()

        self.assertIn(
            "src/ugcam_ai/adapters/nx_journal.py",
            nx_text,
        )
        self.assertIn("src/ugcam_ai/adapters/nx_flow.py", nx_text)
        self.assertNotIn("powermill", nx_text)
        self.assertNotIn("powermill_macro.py", nx_text)

        self.assertIn(
            "cam_automation/adapters/powermill_macro.py",
            powermill_text,
        )
        self.assertIn(
            "cam_automation/adapters/powermill_flow.py",
            powermill_text,
        )
        for forbidden in (
            "nx_journal.py",
            "nx_flow.py",
            "nxopen",
            "ugopen",
            "siemens",
        ):
            self.assertNotIn(forbidden, powermill_text)

    def test_skill_discovery_metadata_is_deterministic(self) -> None:
        snapshots = {
            tuple(
                (
                    root.name,
                    (root / "SKILL.md").read_bytes(),
                    (root / "agents" / "openai.yaml").read_bytes(),
                )
                for root in sorted(
                    (NX_SKILL, POWERMILL_SKILL),
                    key=lambda item: item.name,
                )
            )
            for _ in range(20)
        }
        self.assertEqual(1, len(snapshots))


if __name__ == "__main__":
    unittest.main()
