from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "src"))

from ugcam_ai import NxVersionContract
from ugcam_ai.versioning import (
    NxStubVersionMismatchError,
    NxStubVersionMissingError,
    NxStubsNotFoundError,
)


class NxVersionContractTest(unittest.TestCase):
    def test_loads_target_install_stubs_without_importing_them(self) -> None:
        install = PLUGIN_ROOT / "examples" / "nx_stubs" / "NX2406"

        contract = NxVersionContract.load_stubs(
            install, expected_version="NX 2406"
        )

        self.assertEqual(contract.target_version, "NX 2406")
        self.assertEqual(contract.python_minor, "3.10")
        self.assertIn("NXOpen", contract.modules)
        self.assertIn("NXOpen.CAM", contract.modules)
        self.assertTrue(
            contract.supports(
                "NXOpen.CAM.CAMOperationCollection.CreatePlanarMillingBuilder"
            )
        )
        member = contract.modules["NXOpen.CAM"].classes[
            "CAMOperationCollection"
        ].members["CreatePlanarMillingBuilder"]
        self.assertIn("operation: object | None", member.signatures[0])

    def test_missing_and_version_mismatch_are_explicit(self) -> None:
        install = PLUGIN_ROOT / "examples" / "nx_stubs" / "NX2406"
        with self.assertRaises(NxStubVersionMismatchError) as captured:
            NxVersionContract.load_stubs(
                install, expected_version="NX 2312"
            )
        self.assertEqual(captured.exception.expected, "NX 2312")
        self.assertEqual(captured.exception.detected, "NX 2406")

        with self.assertRaises(NxStubsNotFoundError):
            NxVersionContract.load_stubs(install / "missing")

    def test_version_is_not_silently_assumed_for_generic_stub_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stubs = Path(directory) / "pythonStubs"
            stubs.mkdir()
            (stubs / "NXOpen.pyi").write_text(
                "class Session:\n    pass\n", encoding="utf-8"
            )

            with self.assertRaises(NxStubVersionMissingError):
                NxVersionContract.load_stubs(
                    stubs, expected_version="NX 2406"
                )


if __name__ == "__main__":
    unittest.main()
