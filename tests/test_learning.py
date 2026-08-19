import unittest

from cam_automation.learning import command_shape, learn_workflow
from cam_automation.sample import SAMPLE_LOG


class LearningTests(unittest.TestCase):
    def test_command_shape_abstracts_literals(self) -> None:
        self.assertEqual(
            "EDIT TOOL <STRING> DIAMETER <NUMBER>",
            command_shape("EDIT TOOL 'T10' DIAMETER 10.0"),
        )

    def test_learns_parameters_across_matching_sessions(self) -> None:
        parsed, recipe = learn_workflow(SAMPLE_LOG, name="demo")

        self.assertEqual(2, len(parsed.sessions))
        self.assertEqual(8, len(recipe.steps))
        self.assertEqual(["cavity-a", "cavity-b"], recipe.sessions_matched)
        parameter_names = {parameter.name for parameter in recipe.parameters}
        self.assertIn("model_path", parameter_names)
        self.assertIn("model_name", parameter_names)
        self.assertEqual(1, sum(step.risk == "review" for step in recipe.steps))
        self.assertEqual(1, sum(step.risk == "blocked" for step in recipe.steps))

    def test_selects_dominant_structural_sequence(self) -> None:
        text = """
# session: a
SIZE MODEL 'a'
PRINT 'a'
# session: b
SIZE MODEL 'b'
PRINT 'b'
# session: outlier
SIZE TOOLPATH 'x'
"""
        _, recipe = learn_workflow(text)

        self.assertEqual(["a", "b"], recipe.sessions_matched)
        self.assertEqual(2, len(recipe.steps))
        self.assertTrue(any("Used 2 of 3" in item for item in recipe.diagnostics))

    def test_empty_log_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "No PowerMill commands"):
            learn_workflow("# comments only")


if __name__ == "__main__":
    unittest.main()

