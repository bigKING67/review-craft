from __future__ import annotations

import json
import unittest

from jsonschema import Draft202012Validator

from tests.support import ROOT


class EvalContractTests(unittest.TestCase):
    def test_host_output_literals_have_explicit_types_for_structured_output(self) -> None:
        schema = json.loads(
            (ROOT / "evals/schemas/eval-host-output.schema.json").read_text(
                encoding="utf-8"
            )
        )

        def visit(value: object) -> None:
            if isinstance(value, dict):
                self.assertNotIn("uniqueItems", value)
                if "const" in value or "enum" in value:
                    self.assertIn("type", value)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(schema)

    def test_eval_suite_has_six_positive_and_six_negative_cases(self) -> None:
        payload = json.loads((ROOT / "evals/specs/cases.json").read_text(encoding="utf-8"))
        cases = payload["cases"]
        self.assertEqual(len(cases), 12)
        self.assertEqual(sum(case["class"] == "positive" for case in cases), 6)
        self.assertEqual(sum(case["class"] == "negative" for case in cases), 6)
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        for case in cases:
            self.assertTrue((ROOT / case["fixture"]).is_dir(), case["id"])
            self.assertTrue(case["expectedDecisions"], case["id"])
            self.assertTrue(case["expectedLocations"], case["id"])
            self.assertTrue(case["evidenceRequirement"], case["id"])

    def test_eval_cases_match_the_public_schema(self) -> None:
        payload = json.loads((ROOT / "evals/specs/cases.json").read_text(encoding="utf-8"))
        schema = json.loads(
            (ROOT / "evals/schemas/eval-cases.schema.json").read_text(encoding="utf-8")
        )
        errors = list(Draft202012Validator(schema).iter_errors(payload))
        self.assertEqual(errors, [])

    def test_negative_suite_contains_rewrite_traps(self) -> None:
        cases = json.loads((ROOT / "evals/specs/cases.json").read_text(encoding="utf-8"))[
            "cases"
        ]
        traps = [
            case
            for case in cases
            if case["class"] == "negative" and "REWRITE" in case["prohibitedDecisions"]
        ]
        self.assertGreaterEqual(len(traps), 2)

    def test_long_cohesive_fixture_is_a_real_length_trap(self) -> None:
        parser = ROOT / "evals/fixtures/long-cohesive-file/parser.py"
        tests = ROOT / "evals/fixtures/long-cohesive-file/test_parser.py"
        self.assertGreaterEqual(len(parser.read_text(encoding="utf-8").splitlines()), 120)
        self.assertGreaterEqual(tests.read_text(encoding="utf-8").count("def test_"), 6)


if __name__ == "__main__":
    unittest.main()
