from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import benchmark_cam_flow


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "cam-flow" / "performance"
BENCHMARK = ROOT / "scripts" / "benchmark_cam_flow.py"


class PerformanceFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = benchmark_cam_flow.load_fixture_set(FIXTURE_ROOT)

    def test_frozen_fixture_dimensions_and_static_safety(self) -> None:
        graph = self.fixtures["bundle"]["graph"]
        self.assertEqual(benchmark_cam_flow.NODE_COUNT, 500)
        self.assertEqual(benchmark_cam_flow.EDGE_COUNT, 800)
        self.assertEqual(500, len(graph["flows"][0]["nodes"]))
        self.assertEqual(800, len(graph["flows"][0]["edges"]))
        self.assertEqual(500, len(graph["source_mappings"]))
        self.assertEqual(
            10_000,
            len(self.fixtures["nx_source"].splitlines()),
        )
        self.assertEqual(
            10_000,
            len(self.fixtures["powermill_source"].decode("utf-8").splitlines()),
        )
        self.assertFalse(
            benchmark_cam_flow.FORBIDDEN_SOURCE.search(
                self.fixtures["source"].decode("utf-8")
            )
        )
        self.assertFalse(
            benchmark_cam_flow.FORBIDDEN_SOURCE.search(self.fixtures["nx_source"])
        )
        self.assertFalse(
            benchmark_cam_flow.FORBIDDEN_SOURCE.search(
                self.fixtures["powermill_source"].decode("utf-8")
            )
        )

    def test_fixture_generation_is_byte_reproducible(self) -> None:
        committed_manifest = json.loads(
            (FIXTURE_ROOT / "fixture-manifest.json").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory(prefix="cam-flow-fixture-repro-") as directory:
            generated_root = Path(directory)
            benchmark_cam_flow.generate_fixtures(generated_root)
            generated_manifest = json.loads(
                (generated_root / "fixture-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(committed_manifest, generated_manifest)
            for name in committed_manifest["fixtures"]:
                self.assertEqual(
                    (FIXTURE_ROOT / name).read_bytes(),
                    (generated_root / name).read_bytes(),
                    name,
                )


class ReleasePerformanceGateTests(unittest.TestCase):
    def test_all_hard_performance_and_determinism_gates_pass(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(BENCHMARK),
                "--samples",
                "20",
                "--determinism-runs",
                "20",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=240,
        )
        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            self.fail(
                "Benchmark did not emit a JSON report.\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}\n"
                f"decode error: {error}"
            )
        self.assertEqual(
            0,
            result.returncode,
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        )
        self.assertEqual("PASS", report["status"])
        self.assertEqual([], report["failures"])
        self.assertTrue(report["determinism"]["passed"])
        self.assertTrue(
            all(
                gate["passed"] is True
                for gate in report["benchmarks"].values()
            )
        )
        self.assertEqual("none", report["safety"]["transport"])
        self.assertEqual(0, report["safety"]["commands_sent"])
        self.assertFalse(report["safety"]["journal_executed"])
        self.assertFalse(report["safety"]["macro_executed"])
        self.assertEqual(0, report["safety"]["machine_output_count"])


if __name__ == "__main__":
    unittest.main()
