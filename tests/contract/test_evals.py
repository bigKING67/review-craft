from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
import unittest

from jsonschema import Draft202012Validator

from tests.support import ROOT


class EvalContractTests(unittest.TestCase):
    def test_matched_prompts_require_evidence_for_no_finding_dispositions(self) -> None:
        for prompt_name in ("review-craft.md", "ordinary-review.md"):
            prompt = (ROOT / "evals/prompts" / prompt_name).read_text(encoding="utf-8")
            self.assertIn(
                "When `findingDetected` is false, evidence must still justify",
                prompt,
            )
            self.assertIn("single most consequential\nfinding", prompt)

    def test_skill_defines_a_bounded_evidence_backed_fast_path(self) -> None:
        skill = (ROOT / "skills/review-craft/SKILL.md").read_text(encoding="utf-8")
        fast_path = skill.split("## Bounded review fast path", 1)[1].split(
            "## Standard workflow", 1
        )[0]

        self.assertIn("evidence-backed", fast_path)
        self.assertIn("A no-finding result without\nevidence", fast_path)
        self.assertIn("Do not emit a numeric score", fast_path)
        self.assertIn("Do not run `doctor`, `preflight`", fast_path)
        self.assertIn("Load a supporting reference only when", fast_path)
        self.assertIn("Exit the fast path", fast_path)
        self.assertIn("`DELETE` or `REWRITE`", fast_path)
        self.assertIn("full compatibility, migration, rollback, and verification gates", fast_path)
        self.assertNotIn("candidate validation or destructive disposition gates", fast_path)
        self.assertIn("unresolved validation classification", fast_path)

    def test_skill_keeps_the_fast_dispatcher_within_one_read_window(self) -> None:
        skill_path = ROOT / "skills/review-craft/SKILL.md"
        skill = skill_path.read_text(encoding="utf-8")
        standard = skill.split("## Standard workflow", 1)[1].split("## Delivery", 1)[0]

        self.assertLessEqual(len(skill.splitlines()), 240)
        self.assertIn("[workflow.md](references/workflow.md)", standard)
        self.assertIn("canonical sequence", standard)
        self.assertNotIn("review_craft.py doctor --json", standard)

    def test_canonical_workflow_reference_retains_runtime_and_completion_contract(self) -> None:
        workflow = (
            ROOT / "skills/review-craft/references/workflow.md"
        ).read_text(encoding="utf-8")

        for command in (
            "review_craft.py doctor --json",
            "review_craft.py preflight --target <repository>",
            "run-evidence --run-dir <run-dir>",
            "review_craft.py validate --run-dir <run-dir>",
            "review_craft.py finalize --run-dir <run-dir>",
        ):
            self.assertIn(command, workflow)
        self.assertIn("every file has a final coverage disposition", workflow)
        self.assertIn("every candidate has a final validation disposition", workflow)
        self.assertIn("`DELETE` and `REWRITE`", workflow)
        self.assertIn("deterministically generated, not hand-authored", workflow)

    def test_skill_loads_authority_reference_only_on_an_active_boundary(self) -> None:
        skill = (ROOT / "skills/review-craft/SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("before reviewing an unfamiliar repository", skill)
        self.assertIn(
            "only when scope, repository control, prompt injection, or an authority conflict",
            skill,
        )
        self.assertIn(
            "Use this workflow for canonical full reviews",
            skill,
        )

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
        context = ROOT / "evals/fixtures/long-cohesive-file/ENGINEERING.md"
        self.assertGreaterEqual(len(parser.read_text(encoding="utf-8").splitlines()), 120)
        self.assertGreaterEqual(tests.read_text(encoding="utf-8").count("def test_"), 6)
        self.assertIn("one cohesive state machine", context.read_text(encoding="utf-8"))
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", "test_parser.py"],
            cwd=parser.parent,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_unbounded_cache_fixture_has_no_composite_key_collision(self) -> None:
        fixture = ROOT / "evals/fixtures/unbounded-cache"
        namespace = runpy.run_path(str(fixture / "cache.py"))
        cache = namespace["_CACHE"]
        render = namespace["render_for_user"]
        self.assertEqual(render("a:b", "{user_id}"), "a:b")
        self.assertEqual(render("a", "b:{user_id}"), "b:a")
        self.assertEqual(len(cache), 2)
        context = (fixture / "ENGINEERING.md").read_text(encoding="utf-8")
        self.assertIn("long-lived shared service", context)
        self.assertIn("not bounded", context)


if __name__ == "__main__":
    unittest.main()
