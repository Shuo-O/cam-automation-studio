from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "src"))

from ugcam_ai.adapters.nx_journal import (
    NxJournalAdapter,
    NxJournalSyntaxError,
    NxUnsupportedSourceError,
)


class NxJournalAdapterTest(unittest.TestCase):
    def test_preserves_legacy_canonical_actions_and_adds_nx_evidence(self) -> None:
        source = PLUGIN_ROOT / "examples" / "nx_journals" / "session_a.py"
        events = list(NxJournalAdapter().parse(source))
        actions = [event.action for event in events]

        self.assertEqual(
            [action for action in actions if action.startswith("cam.")],
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
        self.assertIn("nx.session.access", actions)
        self.assertIn("nx.object.access", actions)
        self.assertIn("nx.undo_mark.rename", actions)
        self.assertEqual([event.seq for event in events], list(range(len(events))))
        self.assertTrue(all(event.product == "nx" for event in events))
        self.assertTrue(all(event.source_mode == "manual" for event in events))
        self.assertTrue(all(event.view_level == "L1" for event in events))
        self.assertTrue(all(event.params["journal_executed"] is False for event in events))

    def test_tracks_complete_builder_and_error_boundary_lifecycle(self) -> None:
        source = (
            PLUGIN_ROOT
            / "examples"
            / "nx_journals"
            / "recorded_builder_session.py"
        )
        events = list(NxJournalAdapter().parse(source))
        actions = [event.action for event in events]

        self.assertIn("cam.builder.create", actions)
        self.assertIn("cam.builder.commit", actions)
        self.assertIn("cam.builder.destroy", actions)
        self.assertIn("nx.undo_mark.set", actions)
        self.assertIn("nx.undo_mark.rename", actions)
        self.assertIn("nx.error_boundary.enter", actions)
        self.assertIn("nx.error_boundary.handler", actions)
        self.assertIn("nx.error_boundary.finally", actions)
        self.assertNotIn("nx.builder.lifecycle_incomplete", actions)

        destroy = next(event for event in events if event.action == "cam.builder.destroy")
        lifecycle = destroy.params["lifecycle"]
        self.assertTrue(lifecycle["committed"])
        self.assertTrue(lifecycle["destroyed"])
        self.assertTrue(lifecycle["complete"])
        self.assertEqual(lifecycle["parameters"]["Tolerance"], 0.025)
        self.assertEqual(lifecycle["parameters"]["CutParameters.Stock"], 0.2)

    def test_incomplete_builder_lifecycle_is_explicit_and_blocked(self) -> None:
        source = (
            PLUGIN_ROOT
            / "examples"
            / "nx_journals"
            / "incomplete_builder_session.py"
        )
        events = list(NxJournalAdapter().parse(source))
        incomplete = [
            event for event in events if event.action == "nx.builder.lifecycle_incomplete"
        ]

        self.assertEqual(len(incomplete), 1)
        self.assertEqual(incomplete[0].params["risk"], "blocked")
        self.assertEqual(incomplete[0].review_status, "needs_changes")
        self.assertIn("builder_not_committed", incomplete[0].params["issues"])
        self.assertIn("builder_not_destroyed", incomplete[0].params["issues"])
        self.assertEqual(
            incomplete[0].params["lifecycle"]["issues"],
            incomplete[0].params["issues"],
        )

    def test_builder_alias_and_setter_call_share_one_lifecycle(self) -> None:
        source = """
import NXOpen
import NXOpen.CAM

work_part = NXOpen.Session.GetSession().Parts.Work
builder = work_part.CAMSetup.CAMOperationCollection.CreatePlanarMillingBuilder(None)
parameters = builder.CutParameters
parameters.SetStock(0.25)
builder_alias = builder
builder_alias.Commit()
builder_alias.Destroy()
"""
        events = NxJournalAdapter().parse_source(source)
        setter = next(
            event for event in events if event.action == "cam.parameter.set.stock"
        )
        destroyed = next(
            event for event in events if event.action == "cam.builder.destroy"
        )

        self.assertEqual(setter.params["lifecycle"]["builder_id"], "builder")
        self.assertTrue(destroyed.params["lifecycle"]["complete"])
        self.assertEqual(
            destroyed.params["lifecycle"]["parameters"][
                "CutParameters.SetStock"
            ],
            0.25,
        )

    def test_find_object_reports_stable_selector_options_without_uniqueness_claim(self) -> None:
        source = """
import NXOpen

setup = NXOpen.Session.GetSession().Parts.Work.CAMSetup
recorded = setup.CAMGroupCollection.FindObject("ENTITY 32 2 1")
"""
        events = NxJournalAdapter().parse_source(source)
        selected = next(event for event in events if event.action == "nx.object.find")
        selector = selected.params["selector"]

        self.assertTrue(selector["fragile"])
        self.assertTrue(selector["recorded_id_pattern"])
        self.assertEqual(selector["ambiguity_risk"], "high")
        self.assertFalse(selector["unique_match_claimed"])
        self.assertFalse(selector["resolved"])
        self.assertEqual(
            {item["strategy"] for item in selector["suggestions"]},
            {"name", "attribute", "pmi", "geometry"},
        )
        self.assertTrue(
            all(item["requires_unique_match"] for item in selector["suggestions"])
        )

    def test_dynamic_and_malicious_ast_is_never_executed(self) -> None:
        source = """
import NXOpen

raise SystemExit("Journal execution would reach this")
__import__("os").system("echo should-not-run")
method_name = input()
if method_name:
    getattr(NXOpen.Session.GetSession(), method_name)()
"""
        events = NxJournalAdapter().parse_source(source)
        actions = [event.action for event in events]

        self.assertIn("nx.error_boundary.raise", actions)
        self.assertIn("nx.journal.untrusted_call", actions)
        self.assertIn("nx.journal.dynamic_control_flow", actions)
        blocked = [
            event
            for event in events
            if event.action == "nx.journal.untrusted_call"
        ]
        self.assertTrue(blocked)
        self.assertTrue(all(event.review_status == "rejected" for event in blocked))
        self.assertTrue(all(event.params["journal_executed"] is False for event in events))

    def test_power_mill_and_non_nx_sources_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            macro = Path(directory) / "recording.mac"
            macro.write_text('CALCULATE TOOLPATH "ROUGH_A"\n', encoding="utf-8")
            with self.assertRaises(NxUnsupportedSourceError):
                list(NxJournalAdapter().parse(macro))

        with self.assertRaises(NxJournalSyntaxError):
            NxJournalAdapter().parse_source('EDIT PAR TOLERANCE "0.05"')
        with self.assertRaises(NxUnsupportedSourceError):
            NxJournalAdapter().parse_source(
                "event = {'product': 'powermill', 'action': 'CALCULATE'}"
            )

    def test_dangerous_nx_calls_are_blocked_and_payloads_are_redacted(self) -> None:
        source = """
import NXOpen

session = NXOpen.Session.GetSession()
session.ExecuteJournal("unsafe.py")
session.CAMSetup.Postprocess("operation", "output.nc")
session.CAMSetup.GenerateGCode("output.nc")
session.MachineControl.SendToMachine("payload")
"""
        events = NxJournalAdapter().parse_source(source)
        blocked_actions = {
            "nx.journal.execute",
            "cam.output.postprocess",
            "cam.output.machine_ready",
            "nx.machine.control",
        }
        blocked = [event for event in events if event.action in blocked_actions]

        self.assertEqual({event.action for event in blocked}, blocked_actions)
        self.assertTrue(all(event.review_status == "rejected" for event in blocked))
        self.assertTrue(all(event.params["args"] == [] for event in blocked))
        self.assertTrue(all(event.params["kwargs"] == {} for event in blocked))
        self.assertTrue(all(event.params["payload_redacted"] for event in blocked))

    def test_syntax_error_reports_static_source_location(self) -> None:
        with self.assertRaises(NxJournalSyntaxError) as captured:
            NxJournalAdapter().parse_source(
                "import NXOpen\nif True print('bad')\n",
                source_file="broken-journal.py",
            )

        self.assertEqual(captured.exception.source_file, "broken-journal.py")
        self.assertEqual(captured.exception.line, 2)


if __name__ == "__main__":
    unittest.main()
