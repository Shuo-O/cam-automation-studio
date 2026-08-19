from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "src"))

from ugcam_ai.models import ActivityEvent
from ugcam_ai.store import ActivityStore


class StorePerformanceTest(unittest.TestCase):
    def test_batch_imports_ten_thousand_events(self) -> None:
        events = [
            ActivityEvent(
                session_id="nx:performance",
                seq=index,
                product="nx",
                action=f"cam.parameter.set.p{index % 20}",
                category="parameter",
                mode="automation" if index == 0 else "manual",
            )
            for index in range(10_000)
        ]
        with tempfile.TemporaryDirectory() as directory:
            started = time.perf_counter()
            with ActivityStore(Path(directory) / "events.db") as store:
                count = store.replace_session(events)
                loaded = store.load_sessions(product="nx")
            elapsed = time.perf_counter() - started

        self.assertEqual(count, 10_000)
        self.assertEqual(len(loaded["nx:performance"]), 10_000)
        self.assertEqual(loaded["nx:performance"][0].mode, "automation")
        self.assertLess(elapsed, 10.0)


if __name__ == "__main__":
    unittest.main()
