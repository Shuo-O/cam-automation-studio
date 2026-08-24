from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
MARKETPLACE = ROOT / ".agents" / "plugins" / "marketplace.json"

PRODUCT_SKILLS = {
    "ug-cam-copilot": {
        "nx-workflow-learning",
        "nx-session-comparison",
        "nx-recipe-review",
        "nx-activity-query-preview",
        "nx-flow-studio",
    },
    "powermill-cam-copilot": {
        "powermill-cam-workflow-learning",
        "powermill-session-comparison",
        "powermill-recipe-review",
        "powermill-activity-query-preview",
        "powermill-flow-studio",
    },
}


class CodexPluginContractTests(unittest.TestCase):
    def test_product_manifests_expose_exact_skill_directories(self) -> None:
        for plugin_name, expected_skills in PRODUCT_SKILLS.items():
            with self.subTest(plugin=plugin_name):
                plugin_root = PLUGINS / plugin_name
                manifest = json.loads(
                    (plugin_root / ".codex-plugin" / "plugin.json").read_text(
                        encoding="utf-8"
                    )
                )
                skill_names = {
                    path.parent.name
                    for path in (plugin_root / "skills").glob("*/SKILL.md")
                }
                self.assertEqual(plugin_name, manifest["name"])
                self.assertEqual("./skills/", manifest["skills"])
                self.assertEqual(expected_skills, skill_names)
                self.assertNotIn("apps", manifest)
                self.assertNotIn("mcpServers", manifest)

    def test_marketplace_is_available_on_install_and_never_default_installed(self) -> None:
        marketplace = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
        self.assertEqual(5, len(marketplace["plugins"]))
        for entry in marketplace["plugins"]:
            with self.subTest(plugin=entry["name"]):
                self.assertEqual("AVAILABLE", entry["policy"]["installation"])
                self.assertEqual("ON_INSTALL", entry["policy"]["authentication"])
                self.assertEqual("Developer Tools", entry["category"])
                self.assertEqual(
                    f"./plugins/{entry['name']}",
                    entry["source"]["path"],
                )

    def test_skill_references_and_declared_scripts_exist(self) -> None:
        for plugin_name, expected_skills in PRODUCT_SKILLS.items():
            for skill_name in expected_skills:
                with self.subTest(skill=skill_name):
                    skill_root = PLUGINS / plugin_name / "skills" / skill_name
                    skill_file = skill_root / "SKILL.md"
                    text = skill_file.read_text(encoding="utf-8")
                    self.assertTrue((skill_root / "agents" / "openai.yaml").is_file())
                    self.assertNotIn("[TODO:", text)
                    for relative in re.findall(r"\]\((references/[^)]+)\)", text):
                        self.assertTrue(
                            (skill_root / relative).is_file(),
                            f"Missing {skill_name}/{relative}",
                        )
                    for relative in re.findall(
                        r"`(plugins/[^`]+|cam_automation/[^`]+)`",
                        text,
                    ):
                        if relative.endswith((".py", ".pyi")):
                            self.assertTrue((ROOT / relative).is_file(), relative)

        wrapper = (
            PLUGINS
            / "powermill-cam-copilot"
            / "skills"
            / "powermill-cam-workflow-learning"
            / "scripts"
            / "learn_powermill_log.py"
        )
        self.assertTrue(wrapper.is_file())

    def test_product_skill_trees_do_not_cross_adapter_or_script_boundaries(self) -> None:
        nx_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (PLUGINS / "ug-cam-copilot" / "skills").rglob("*")
            if path.is_file() and path.suffix in {".md", ".yaml", ".py"}
        ).casefold()
        pm_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (PLUGINS / "powermill-cam-copilot" / "skills").rglob("*")
            if path.is_file() and path.suffix in {".md", ".yaml", ".py"}
        ).casefold()

        for forbidden in ("powermill", "powermill_macro", ".mac"):
            self.assertNotIn(forbidden, nx_text)
        for forbidden in ("nx_journal", "nxopen", "ugopen", "siemens"):
            self.assertNotIn(forbidden, pm_text)
        self.assertIn("src/ugcam_ai/adapters/nx_journal.py", nx_text)
        self.assertIn("cam_automation/adapters/powermill_macro.py", pm_text)

    def test_every_skill_states_review_and_production_safety_gates(self) -> None:
        for plugin_name, expected_skills in PRODUCT_SKILLS.items():
            for skill_name in expected_skills:
                with self.subTest(skill=skill_name):
                    skill_root = PLUGINS / plugin_name / "skills" / skill_name
                    text = "\n".join(
                        path.read_text(encoding="utf-8")
                        for path in skill_root.rglob("*.md")
                    ).casefold()
                    self.assertIn("review", text)
                    self.assertIn("human", text)
                    self.assertIn("simulation", text)
                    self.assertIn("collision", text)
                    self.assertIn("shop approval", text)
                    self.assertIn("nc", text)
                    self.assertIn("g-code", text)
                    self.assertTrue(
                        "dry-run" in text
                        or "dry run" in text
                        or "fixture_dry_run" in text
                    )

    def test_manifest_versions_and_cachebuster_base_are_consistent(self) -> None:
        expected = {
            "ug-cam-copilot": "0.3.0",
            "powermill-cam-copilot": "0.2.0",
            "cam-local-capture": "0.2.0",
            "cam-execution-gateway": "0.2.0",
            "cam-codex-review": "0.2.0",
        }
        for plugin_name, base_version in expected.items():
            manifest = json.loads(
                (
                    PLUGINS
                    / plugin_name
                    / ".codex-plugin"
                    / "plugin.json"
                ).read_text(encoding="utf-8")
            )
            self.assertRegex(
                manifest["version"],
                rf"^{re.escape(base_version)}\+codex\.\d{{14}}$",
            )


if __name__ == "__main__":
    unittest.main()
