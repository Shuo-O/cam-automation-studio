from __future__ import annotations

import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "src"))

from ugcam_ai.adapters.nx_journal import NxJournalAdapter


class NxJournalAdapterTest(unittest.TestCase):
    def test_normalizes_recorded_aliases_and_filters_recorder_noise(self) -> None:
        source = PLUGIN_ROOT / "examples" / "nx_journals" / "session_a.py"
        events = list(NxJournalAdapter().parse(source))
        actions = [event.action for event in events]

        self.assertEqual(
            actions,
            [
                "cam.group.select.program",
                "cam.group.select.method",
                "cam.group.select.tool",
                "cam.group.select.geometry",
                "cam.operation.create",
                "cam.parameter.set.tolerance",
                "cam.builder.commit",
                "cam.toolpath.generate",
            ],
        )
        self.assertEqual([event.seq for event in events], list(range(len(events))))
        self.assertTrue(all(event.product == "nx" for event in events))


if __name__ == "__main__":
    unittest.main()

