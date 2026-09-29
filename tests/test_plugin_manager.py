from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cam_automation.plugin_manager import PluginManager, PluginNotInstalled


CATALOG_ROOT = Path(__file__).resolve().parents[1] / "plugins"


class PluginManagerTests(unittest.TestCase):
    def test_registry_starts_empty_and_persists_explicit_installs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = PluginManager(directory, CATALOG_ROOT)
            status = manager.status()

            self.assertEqual(0, status["installed_count"])
            self.assertEqual(6, status["available_count"])
            with self.assertRaises(PluginNotInstalled):
                manager.require("ug-cam-copilot")

            result = manager.install("ug-cam-copilot")
            self.assertEqual(["ug-cam-copilot"], result["changed"])
            self.assertTrue(manager.is_installed("ug-cam-copilot"))

            restored = PluginManager(directory, CATALOG_ROOT)
            self.assertTrue(restored.is_installed("ug-cam-copilot"))

    def test_cimatron_install_exposes_static_analysis_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = PluginManager(directory, CATALOG_ROOT)
            result = manager.install("cimatron-cam-copilot")
            self.assertEqual(["cimatron-cam-copilot"], result["changed"])
            self.assertIn("analysis:cimatron", result["features"])
            self.assertNotIn("flow:cimatron", result["features"])
            self.assertNotIn("execute:cimatron", result["features"])

    def test_execution_install_adds_capture_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = PluginManager(directory, CATALOG_ROOT)
            result = manager.install("cam-execution-gateway")

            self.assertEqual(
                ["cam-local-capture", "cam-execution-gateway"],
                result["changed"],
            )
            with self.assertRaisesRegex(ValueError, "dependent plugins"):
                manager.uninstall("cam-local-capture")

            manager.uninstall("cam-execution-gateway")
            manager.uninstall("cam-local-capture")
            self.assertEqual(0, manager.status()["installed_count"])


if __name__ == "__main__":
    unittest.main()
