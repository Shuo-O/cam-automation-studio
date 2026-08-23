from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cam_automation import models
from cam_automation.adapters.powermill_macro import PowerMillAdapter
from cam_automation.learning import WorkflowLearner
from cam_automation.models import ActivityEvent, EventPage, EventQuery
from cam_automation.recipes import PreviewRequest, RecipeService
from cam_automation.recorder import RecorderService
from cam_automation.sessions import Session as LearningSession
from cam_automation.sessions import SessionService


ROOT = Path(__file__).resolve().parents[1]
BARRIER_FIXTURE = ROOT / "tests" / "fixtures" / "barrier1-events.json"
CONTRACT_FIXTURES = ROOT / "examples" / "contracts"


class FixturePreviewAdapter:
    def __init__(
        self,
        product: str,
        target_version: str,
        target_instance_id: str,
        project_id: str,
    ) -> None:
        self.product = product
        self.target_version = target_version
        self.target_instance_id = target_instance_id
        self.project_id = project_id
        self.reviewer = "operator:barrier-reviewer"
        self.calls: list[PreviewRequest] = []

    def preview(self, request: PreviewRequest) -> dict:
        self.calls.append(request)
        return {
            "execution_mode": "dry_run",
            "status": "changes_detected",
            "changes": [
                {
                    "path": "fixture/reviewed-operation",
                    "kind": "proposed_update",
                    "before": None,
                    "after": "preview-only",
                    "severity": "review",
                }
            ],
            "gate_results": [],
            "summary": "Barrier fixture preview only.",
        }


class Barrier1IntegrationTests(unittest.TestCase):
    def _fixture(self) -> dict:
        return json.loads(BARRIER_FIXTURE.read_text(encoding="utf-8"))

    def _recorder_page(self, root: Path) -> tuple[RecorderService, EventPage]:
        fixture = self._fixture()
        source = root / "barrier1-events.jsonl"
        source.write_text(
            "".join(
                json.dumps(event, separators=(",", ":")) + "\n"
                for event in fixture["events"]
            ),
            encoding="utf-8",
        )
        service = RecorderService(
            root / "capture",
            source_paths={"nx": [source]},
            start_background=False,
        )
        service.config.auto_connect = False
        service._save_config()
        service.configure(consent=True, detect_instances=False)
        self.assertEqual(len(fixture["events"]), service.scan_now())
        return service, service.query_events(EventQuery(limit=100, sort="asc"))

    def test_unified_fixture_covers_frozen_event_axes_and_json_contracts(self) -> None:
        fixture = self._fixture()
        events = [ActivityEvent.from_dict(event) for event in fixture["events"]]
        expected = fixture["expected"]

        self.assertEqual(set(expected["products"]), {event.product for event in events})
        self.assertEqual(
            set(expected["instance_ids"]),
            {event.instance_id for event in events},
        )
        self.assertEqual(
            set(expected["source_modes"]),
            {event.effective_source_mode for event in events},
        )
        self.assertEqual(
            set(expected["view_levels"]),
            {event.view_level for event in events},
        )
        self.assertTrue(
            all(
                event.action.startswith(("cam.", "nx.", "powermill."))
                for event in events
            )
        )
        audit = next(
            event for event in events if event.source_mode == "execution_audit"
        )
        self.assertEqual("automation", audit.mode)
        self.assertEqual("dry_run_complete", audit.command_response["status"])
        self.assertEqual("dry_run", audit.params["execution_mode"])

        contract_files = sorted(CONTRACT_FIXTURES.glob("*.json"))
        self.assertEqual(12, len(contract_files))
        for path in contract_files:
            with self.subTest(path=path.name):
                self.assertIsNotNone(json.loads(path.read_text(encoding="utf-8")))

    def test_recorder_event_page_enters_session_and_learning_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, page = self._recorder_page(Path(directory))
            try:
                self.assertIsInstance(page, EventPage)
                session_service = SessionService()
                sessions = session_service.build(page)
                manual_sessions = [
                    session
                    for session in sessions
                    if session.source_modes == ("manual",)
                ]
                self.assertEqual(
                    self._fixture()["expected"]["manual_session_count"],
                    len(manual_sessions),
                )
                self.assertEqual(
                    {"nx:fixture:A", "nx:fixture:B"},
                    {
                        session.instance_id
                        for session in manual_sessions
                        if session.product == "nx"
                    },
                )
                self.assertEqual(
                    {"powermill:fixture:A", "powermill:fixture:B"},
                    {
                        session.instance_id
                        for session in manual_sessions
                        if session.product == "powermill"
                    },
                )

                candidates = WorkflowLearner(session_service).mine()
                self.assertEqual({"nx", "powermill"}, {item.product for item in candidates})
                nx_candidate = next(item for item in candidates if item.product == "nx")
                self.assertEqual(
                    self._fixture()["expected"]["nx_similar_manual_sessions"],
                    nx_candidate.support["matched_sessions"],
                )

                nx_adapter = FixturePreviewAdapter(
                    "nx",
                    "NX 2406",
                    "nx:fixture:A",
                    "nx-project-a",
                )
                recipe_service = RecipeService(adapters={"nx": nx_adapter})
                version = recipe_service.save(nx_candidate)
                self.assertEqual("review_required", version.recipe.status)
                self.assertTrue(
                    {
                        "recipe_review",
                        "cam_simulation",
                        "collision_check",
                        "shop_approval",
                    }.issubset(version.recipe.required_gates)
                )
                with (
                    patch(
                        "cam_automation.parser.parse_log",
                        side_effect=AssertionError("preview must not call a parser"),
                    ),
                    patch(
                        "ugcam_ai.adapters.nx_journal.NxJournalAdapter.parse_source",
                        side_effect=AssertionError("preview must use only its adapter"),
                    ),
                ):
                    report = recipe_service.preview(version.recipe_hash, {})

                self.assertEqual("changes_detected", report.status)
                self.assertEqual(1, len(nx_adapter.calls))
                self.assertEqual("dry_run", nx_adapter.calls[0].execution_mode)
                gates = {item["gate"]: item["status"] for item in report.gate_results}
                self.assertEqual("not_run", gates["cam_simulation"])
                self.assertEqual("required", gates["collision_check"])
                self.assertEqual("required", gates["shop_approval"])
            finally:
                service.close()

    def test_powermill_parser_is_independent_of_nx_parser(self) -> None:
        macro = "\n".join(
            (
                "# session: pm-independent",
                'PowerMill> CREATE TOOL "T1"',
                'PowerMill> EDIT TOOL "T1" DIAMETER 10',
            )
        )
        with patch(
            "ugcam_ai.adapters.nx_journal.NxJournalAdapter.parse_source",
            side_effect=AssertionError("PowerMill must not use the NX parser"),
        ):
            events = PowerMillAdapter().parse(macro)

        self.assertEqual(2, len(events))
        self.assertTrue(
            all(
                event["product"] == "powermill"
                and event["action"].startswith(("cam.", "powermill."))
                for event in events
            )
        )

    def test_legacy_internal_names_are_aliases_not_contract_dtos(self) -> None:
        self.assertIs(models.Session, models.ParsedSession)
        self.assertIs(models.RecipeParameter, models.LegacyRecipeParameter)
        self.assertIs(models.RecipeStep, models.LegacyRecipeStep)
        self.assertIsNot(models.Session, LearningSession)


if __name__ == "__main__":
    unittest.main()
