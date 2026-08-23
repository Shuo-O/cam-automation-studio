import json
import tempfile
import unittest
from pathlib import Path

from cam_automation.generator import generate_macro, render_command, write_artifacts
from cam_automation.learning import learn_workflow
from cam_automation.sample import SAMPLE_LOG


class GeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parsed, self.recipe = learn_workflow(SAMPLE_LOG, name="demo")

    def test_default_macro_comments_review_and_blocked_steps(self) -> None:
        macro = generate_macro(self.recipe)

        self.assertIn("IMPORT MODEL 'C:\\Jobs\\cavity-a\\part-a.dmt'", macro)
        self.assertIn("// REVIEW_REQUIRED step-006", macro)
        self.assertIn("// PROJECT SAVE AS", macro)
        self.assertIn("// BLOCKED step-008", macro)
        self.assertIn("// PROJECT RESET", macro)

    def test_explicit_review_flag_activates_only_review_steps(self) -> None:
        macro = generate_macro(self.recipe, allow_review_steps=True)

        self.assertIn("// REVIEW_ACCEPTED step-006", macro)
        self.assertIn("PROJECT SAVE AS 'C:\\Jobs\\cavity-a\\project'", macro)
        self.assertIn("// PROJECT RESET", macro)

    def test_parameter_values_are_quoted_and_escaped(self) -> None:
        model_parameter = next(
            parameter for parameter in self.recipe.parameters if parameter.name == "model_name"
        )
        command = render_command(
            "SIZE MODEL {{model_name}}",
            [model_parameter],
            {"model_name": "operator's-part"},
        )

        self.assertEqual("SIZE MODEL 'operator''s-part'", command)

    def test_control_characters_are_rejected(self) -> None:
        parameter = self.recipe.parameters[0]
        with self.assertRaisesRegex(ValueError, "control character"):
            render_command(
                "{{" + parameter.name + "}}",
                [parameter],
                {parameter.name: "bad\ncommand"},
            )

    def test_writes_end_to_end_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = write_artifacts(
                directory,
                parsed_count=self.parsed.event_count,
                recipe=self.recipe,
            )

            self.assertEqual({"recipe", "macro", "report"}, set(paths))
            decoded = json.loads(Path(paths["recipe"]).read_text(encoding="utf-8"))
            self.assertEqual("0.1", decoded["schema_version"])
            self.assertIn("Review every command", Path(paths["macro"]).read_text(encoding="utf-8"))

    def test_writes_shared_activity_contract_when_provided(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = write_artifacts(
                directory,
                parsed_count=self.parsed.event_count,
                recipe=self.recipe,
                activity_events=self.parsed.to_activity_events(),
            )

            event = json.loads(Path(paths["events"]).read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(1, event["schema_version"])
            self.assertEqual("powermill", event["product"])
            self.assertTrue(event["action"].startswith(("cam.", "powermill.")))

    def test_machine_ready_nc_command_is_omitted_even_as_blocked_text(self) -> None:
        unsafe = SAMPLE_LOG.replace(
            "PROJECT RESET",
            "NCPROGRAM WRITE 'unsafe.tap'\nPROJECT RESET",
        )
        _, recipe = learn_workflow(unsafe)

        macro = generate_macro(recipe)

        self.assertNotIn("NCPROGRAM WRITE", macro)
        self.assertNotIn("unsafe.tap", macro)
        self.assertIn("machine-ready NC content omitted", macro)


if __name__ == "__main__":
    unittest.main()
