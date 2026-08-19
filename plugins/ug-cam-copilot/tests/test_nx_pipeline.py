from __future__ import annotations

import json
import py_compile
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "src"))

from ugcam_ai.adapters.nx_journal import NxJournalAdapter
from ugcam_ai.mining import SequenceMiner
from ugcam_ai.scaffold import scaffold_nx_candidate
from ugcam_ai.store import ActivityStore


class NxPipelineTest(unittest.TestCase):
    def test_end_to_end_offline_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "activity.db"
            examples = PLUGIN_ROOT / "examples" / "nx_journals"
            with ActivityStore(database) as store:
                for source in sorted(examples.glob("*.py")):
                    store.replace_session(NxJournalAdapter().parse(source))
                sessions = store.load_sessions(product="nx")

            patterns = SequenceMiner(
                min_support=2, min_length=3, max_length=8
            ).mine(sessions, product="nx")

            self.assertTrue(patterns)
            self.assertEqual(patterns[0].support, 3)
            self.assertIn("cam.operation.create", patterns[0].steps)
            self.assertIn("cam.toolpath.generate", patterns[0].steps)

            output = root / "generated"
            paths = scaffold_nx_candidate(patterns[0], output)
            self.assertEqual(len(paths), 3)
            recipe = json.loads((output / "recipe.json").read_text(encoding="utf-8"))
            self.assertEqual(recipe["execution_mode"], "dry-run")
            self.assertEqual(recipe["status"], "draft")

            journal = output / "nx_preview_journal.py"
            py_compile.compile(str(journal), doraise=True)
            result = subprocess.run(
                [sys.executable, str(journal)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("No NX object was modified.", result.stdout)


if __name__ == "__main__":
    unittest.main()

