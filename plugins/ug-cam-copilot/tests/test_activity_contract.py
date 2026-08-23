from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "src"))

from ugcam_ai.models import ActivityEvent, OPTIONAL_ACTIVITY_FIELDS
from ugcam_ai.store import ActivityStore


class ActivityContractCompatibilityTest(unittest.TestCase):
    def test_frozen_activity_event_fixture_round_trips_exactly(self) -> None:
        fixture = json.loads(
            (REPOSITORY_ROOT / "examples" / "contracts" / "activity-event.json").read_text(
                encoding="utf-8"
            )
        )

        event = ActivityEvent.from_dict(fixture)

        self.assertEqual(event.to_dict(), fixture)

    def test_legacy_event_does_not_gain_new_optional_fields(self) -> None:
        legacy = {
            "schema_version": 1,
            "session_id": "legacy-nx",
            "seq": 0,
            "product": "nx",
            "action": "part.open",
            "category": "data",
            "mode": "manual",
            "params": {},
            "source_file": "legacy.jsonl",
            "source_line": 1,
            "duration_ms": None,
            "timestamp": None,
        }

        round_trip = ActivityEvent.from_dict(legacy).to_dict()

        self.assertEqual(round_trip, legacy)
        self.assertTrue(all(name not in round_trip for name in OPTIONAL_ACTIVITY_FIELDS))

    def test_execution_audit_mirrors_legacy_mode_to_automation(self) -> None:
        value = {
            "session_id": "audit",
            "seq": 0,
            "product": "nx",
            "action": "nx.query.completed",
            "category": "execution",
            "mode": "manual",
            "source_mode": "execution_audit",
        }

        serialized = ActivityEvent.from_dict(value).to_dict()

        self.assertEqual(serialized["mode"], "automation")
        self.assertEqual(serialized["source_mode"], "execution_audit")

    def test_store_preserves_optional_fields_and_explicit_nulls(self) -> None:
        value = {
            "session_id": "nx:new-contract",
            "seq": 0,
            "product": "nx",
            "action": "cam.operation.create",
            "category": "operation",
            "mode": "manual",
            "source_mode": "manual",
            "view_level": "L1",
            "expertise_label": "expert",
            "instance_id": "nx:1:A",
            "project_id": None,
            "target_version": "NX 2406",
            "command_response": None,
            "review_status": "needs_review",
            "recipe_hash": None,
        }
        event = ActivityEvent.from_dict(value)

        with tempfile.TemporaryDirectory() as directory:
            with ActivityStore(Path(directory) / "events.db") as store:
                store.replace_session([event])
                restored = store.load_sessions(product="nx")[event.session_id][0]

        restored_value = restored.to_dict()
        for name in OPTIONAL_ACTIVITY_FIELDS:
            self.assertIn(name, restored_value)
            self.assertEqual(restored_value[name], value[name])


if __name__ == "__main__":
    unittest.main()
