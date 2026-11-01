import copy
import json
import unittest
from pathlib import Path

from evals_lab.engine import InvalidPolicy, grade_trajectory, validate_policy

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def load(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


class TestTrajectoryGrading(unittest.TestCase):
    def setUp(self):
        self.policy = load("policy.json")
        self.trace = load("valid.json")

    def test_happy_path(self):
        result = grade_trajectory(self.policy, self.trace)
        self.assertTrue(result.passed)
        self.assertEqual(result.score, 100.0)
        self.assertEqual(result.required_coverage, 1.0)
        self.assertTrue(all(step.passed for step in result.steps))

    def test_missing_safety_check(self):
        result = grade_trajectory(self.policy, load("missing_safety.json"))
        self.assertFalse(result.passed)
        codes = {v.code for v in result.violations}
        self.assertIn("DEPENDENCY_NOT_SATISFIED", codes)
        self.assertIn("REQUIRED_STEP_FAILED", codes)

    def test_hallucinated_api_parameter(self):
        result = grade_trajectory(self.policy, load("hallucinated_parameter.json"))
        self.assertFalse(result.passed)
        self.assertIn("UNKNOWN_ARGUMENT", [v.code for v in result.violations])

    def test_tool_mismatch(self):
        self.trace["calls"][0]["tool"] = "orders.delete"
        self.assertFalse(grade_trajectory(self.policy, self.trace).passed)

    def test_wrong_order(self):
        self.trace["calls"].reverse()
        result = grade_trajectory(self.policy, self.trace)
        self.assertIn("DEPENDENCY_NOT_SATISFIED", [v.code for v in result.violations])

    def test_duplicate_step(self):
        self.trace["calls"].insert(1, copy.deepcopy(self.trace["calls"][0]))
        self.assertFalse(grade_trajectory(self.policy, self.trace).passed)

    def test_unknown_tool_step(self):
        self.trace["calls"].append({"step_id": "hack", "tool": "shell", "arguments": {}})
        self.assertFalse(grade_trajectory(self.policy, self.trace).passed)

    def test_wrong_type(self):
        self.trace["calls"][2]["arguments"]["amount"] = "25"
        self.assertFalse(grade_trajectory(self.policy, self.trace).passed)

    def test_missing_required_argument(self):
        self.trace["calls"][0]["arguments"] = {}
        self.assertFalse(grade_trajectory(self.policy, self.trace).passed)

    def test_reject_cycle(self):
        self.policy["nodes"][0]["depends_on"] = ["issue_refund"]
        with self.assertRaises(InvalidPolicy):
            validate_policy(self.policy)

    def test_reject_invalid_dependency(self):
        self.policy["nodes"][1]["depends_on"] = ["does_not_exist"]
        with self.assertRaises(InvalidPolicy):
            validate_policy(self.policy)

    def test_bad_trajectory_structure(self):
        with self.assertRaises(ValueError):
            grade_trajectory(self.policy, {})

    def test_optional_step_not_required(self):
        self.policy["nodes"].append({"id": "send_receipt", "tool": "email.send", "required": False})
        self.assertTrue(grade_trajectory(self.policy, self.trace).passed)

    def test_optional_step_must_follow_dependencies_when_called(self):
        self.policy["nodes"].append({"id": "send_receipt", "tool": "email.send", "required": False,
                                     "depends_on": ["issue_refund"]})
        self.trace["calls"].insert(0, {"step_id": "send_receipt", "tool": "email.send", "arguments": {}})
        self.assertFalse(grade_trajectory(self.policy, self.trace).passed)

    def test_invalid_json_types(self):
        self.trace["calls"] = ["string is not a call"]
        self.assertFalse(grade_trajectory(self.policy, self.trace).passed)


if __name__ == "__main__":
    unittest.main()
