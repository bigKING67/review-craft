from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
import unittest

from jsonschema import Draft202012Validator

from tests.support import ROOT

sys.path.insert(0, str(ROOT / "scripts"))

import eval_contracts  # noqa: E402


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

    def test_skill_keeps_fix_authorization_and_verification_outcomes_explicit(self) -> None:
        skill = (ROOT / "skills/review-craft/SKILL.md").read_text(encoding="utf-8")
        remediation = skill.split("## Remediation workflow", 1)[1].split(
            "## Delivery", 1
        )[0]

        self.assertIn("user explicitly asks to implement", remediation)
        self.assertIn("runtime must never mutate target source", remediation)
        self.assertIn("[remediation.md](references/remediation.md)", remediation)
        for status in ("`VERIFIED`", "`PARTIAL`", "`FAILED`", "`NO_CHANGES`"):
            self.assertIn(status, remediation)
        self.assertIn("Do not infer implementation authorization", remediation)

    def test_host_bound_schemas_are_compatible_with_structured_output(self) -> None:
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

        for schema_name in (
            "eval-host-output.schema.json",
            "eval-routing-output.schema.json",
            "eval-ablation-adjudication.schema.json",
            "eval-ablation-adjudication-v2.schema.json",
        ):
            with self.subTest(schema=schema_name):
                schema = json.loads(
                    (ROOT / "evals/schemas" / schema_name).read_text(encoding="utf-8")
                )
                visit(schema)

    def test_active_ablation_excludes_adversarial_only_treatments(self) -> None:
        prompt_root = ROOT / "evals/prompts"
        risk_lens_prompt = (prompt_root / "risk-lens-review.md").read_text(
            encoding="utf-8"
        )

        self.assertFalse((prompt_root / "adversarial-review.md").exists())
        self.assertFalse((prompt_root / "risk-lens-adversarial.md").exists())
        self.assertNotIn("adversarial", risk_lens_prompt.lower())
        self.assertNotIn("challenge the implementation", risk_lens_prompt.lower())
        self.assertNotIn("falsify", risk_lens_prompt.lower())
        self.assertNotIn("strongest credible failure path", risk_lens_prompt.lower())
        self.assertEqual(
            list(eval_contracts.ABLATION_TREATMENTS),
            [
                "ORDINARY_PROMPT",
                "RISK_LENS_REVIEW",
                "REVIEW_CRAFT_EVIDENCE_LOOP",
            ],
        )

    def test_three_arm_docs_do_not_claim_external_evidence_isolation(self) -> None:
        result_readme = (
            ROOT / "evals/ablation-results/README.md"
        ).read_text(encoding="utf-8")
        normalized = " ".join(result_readme.split())
        self.assertNotIn(
            "isolates project-specific attention guidance from external evidence",
            normalized,
        )
        self.assertIn(
            "does not isolate the independent effect of external evidence",
            normalized,
        )

    def test_historical_four_arm_snapshot_remains_validation_only_evidence(self) -> None:
        snapshot = json.loads(
            (
                ROOT
                / "evals/ablation-results/13ad6f2-gpt-5.6-sol/snapshot.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(snapshot["schema"], "review-craft.eval-ablation-snapshot.v1")
        self.assertEqual(snapshot["host"]["adapterVersion"], "0.6.0")
        self.assertEqual(eval_contracts.validate_ablation_snapshot(snapshot), [])

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
        schemas = {
            "review-craft.eval-cases": json.loads(
                (ROOT / "evals/schemas/eval-cases.schema.json").read_text(
                    encoding="utf-8"
                )
            ),
            "review-craft.eval-remediation-cases": json.loads(
                (
                    ROOT / "evals/schemas/eval-remediation-cases.schema.json"
                ).read_text(encoding="utf-8")
            ),
            "review-craft.eval-routing-cases": json.loads(
                (ROOT / "evals/schemas/eval-routing-cases.schema.json").read_text(
                    encoding="utf-8"
                )
            ),
            "review-craft.eval-real-repository-suite": json.loads(
                (
                    ROOT
                    / "evals/schemas/eval-real-repository-suite.schema.json"
                ).read_text(encoding="utf-8")
            ),
        }
        for path in sorted((ROOT / "evals/specs").glob("*.json")):
            with self.subTest(path=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                schema_key = payload["schema"].rsplit(".", 1)[0]
                schema = schemas[schema_key]
                errors = list(Draft202012Validator(schema).iter_errors(payload))
                self.assertEqual(errors, [])

    def test_self_correction_suite_has_six_matched_positive_negative_pairs(self) -> None:
        payload = json.loads(
            (ROOT / "evals/specs/self-correction-cases.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(payload["schema"], "review-craft.eval-cases.v2")
        pairs: dict[str, list[dict]] = {}
        for case in payload["cases"]:
            pairs.setdefault(case["pairId"], []).append(case)
            self.assertIn(case["id"], case["verification"]["argv"])
            self.assertTrue((ROOT / case["fixture"]).is_dir())
        self.assertEqual(len(pairs), 6)
        for pair_id, cases in pairs.items():
            with self.subTest(pair=pair_id):
                self.assertEqual(
                    sorted(case["class"] for case in cases),
                    ["negative", "positive"],
                )
                self.assertEqual(cases[0]["riskLens"], cases[1]["riskLens"])

    def test_self_correction_verifier_observes_all_twelve_fixture_behaviors(self) -> None:
        suite = json.loads(
            (ROOT / "evals/specs/self-correction-cases.json").read_text(
                encoding="utf-8"
            )
        )
        expected = {
            "failure-truthfulness-positive": {
                "returned": {"ok": True, "order_id": None},
                "durableRecordCount": 0,
            },
            "failure-truthfulness-negative": {
                "returned": {"ok": True, "order_id": "order-1"},
                "durableRecordCount": 1,
            },
            "retry-idempotency-positive": {
                "returned": {"charged": 25},
                "attemptCount": 2,
                "distinctKeyCount": 2,
                "chargeCount": 2,
            },
            "retry-idempotency-negative": {
                "returned": {"charged": 25},
                "attemptCount": 2,
                "distinctKeyCount": 1,
                "chargeCount": 1,
            },
            "ack-order-positive": {
                "events": ["acknowledge", "save-attempt"],
                "error": "OSError",
            },
            "ack-order-negative": {
                "events": ["save-attempt"],
                "error": "OSError",
            },
            "cache-lifetime-positive": {"cacheSize": 100, "inputKeyCount": 100},
            "cache-lifetime-negative": {"cacheSize": 16, "inputKeyCount": 100},
            "compatibility-path-positive": {
                "supportedVersions": [2, 3],
                "publicExports": ["load_record"],
                "version1Result": None,
                "version1Error": "unsupported record version",
            },
            "compatibility-path-negative": {
                "supportedVersions": [1, 2, 3],
                "publicExports": ["load_record"],
                "version1Result": {"name": "Ada"},
                "version1Error": None,
            },
            "io-multiplicity-positive": {
                "resultCount": 50,
                "singleReadCount": 50,
                "batchReadCount": 0,
            },
            "io-multiplicity-negative": {
                "resultCount": 50,
                "singleReadCount": 0,
                "batchReadCount": 1,
            },
        }
        verifier = ROOT / "evals/verifiers/verify_case.py"
        for case in suite["cases"]:
            with self.subTest(case=case["id"]):
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(verifier),
                        "--case",
                        case["id"],
                        "--target",
                        str(ROOT / case["fixture"]),
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                observation = json.loads(completed.stdout)
                self.assertEqual(
                    observation["schema"], "review-craft.eval-observation.v1"
                )
                self.assertEqual(observation["caseId"], case["id"])
                self.assertEqual(observation["observation"], expected[case["id"]])

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
