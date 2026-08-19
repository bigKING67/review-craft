from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import run_evals as eval_runner  # noqa: E402
from routing_eval import (  # noqa: E402
    DEFAULT_SKILL,
    DEFAULT_SUITE,
    routing_policy_errors,
    run_routing,
    validate_routing_result,
)

FAKE_ADAPTER = ROOT / "tests/fixtures/fake_eval_adapter.py"


class RoutingEvalTests(unittest.TestCase):
    def test_ambiguous_bounded_negatives_accept_only_low_cost_routes(self) -> None:
        suite = json.loads(DEFAULT_SUITE.read_text(encoding="utf-8"))
        ambiguous_ids = {
            f"{language}-direct-none-{suffix}"
            for language in ("zh", "en")
            for suffix in ("quickscore", "scorepair", "fixpair", "perfpair")
        }
        cases = {case["id"]: case for case in suite["cases"]}

        self.assertEqual(ambiguous_ids, ambiguous_ids & cases.keys())
        for case_id in sorted(ambiguous_ids):
            with self.subTest(case_id=case_id):
                self.assertEqual(
                    set(cases[case_id]["allowedRoutes"]),
                    {"DIRECT_TASK", "NATIVE_REVIEW"},
                )
                self.assertIn("REVIEW_CRAFT", cases[case_id]["forbiddenRoutes"])

    def test_run_command_fails_closed_when_thresholds_fail(self) -> None:
        with tempfile.TemporaryDirectory(prefix="review-craft-routing-command-") as directory:
            result = Path(directory) / "result.json"
            args = SimpleNamespace(
                adapter_command=[sys.executable],
                suite=str(DEFAULT_SUITE),
                skill_root=str(DEFAULT_SKILL),
                output_root=directory,
                repetitions=2,
                case_timeout=30,
            )
            for passed, expected_status, expected_exit in (
                (True, "PASSED", 0),
                (False, "FAILED", 2),
            ):
                with self.subTest(passed=passed):
                    result.write_text(json.dumps({"passed": passed}), encoding="utf-8")
                    output = io.StringIO()
                    with (
                        patch.object(eval_runner, "run_routing", return_value=result),
                        redirect_stdout(output),
                    ):
                        exit_code = eval_runner.command_run_routing(args)

                    payload = json.loads(output.getvalue())
                    self.assertEqual(exit_code, expected_exit)
                    self.assertEqual(payload["status"], expected_status)
                    self.assertIs(payload["passed"], passed)

    def test_synthetic_routing_run_is_bound_and_recomputable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="review-craft-routing-") as directory:
            result = run_routing(
                suite_path=DEFAULT_SUITE,
                skill_root=DEFAULT_SKILL,
                output_root=Path(directory),
                repetitions=2,
                timeout=30,
                adapter_command=[sys.executable, str(FAKE_ADAPTER)],
            )

            self.assertEqual(validate_routing_result(result, skill_root=DEFAULT_SKILL), [])
            payload = json.loads(result.read_text(encoding="utf-8"))
            self.assertTrue(payload["passed"])
            self.assertEqual(len(payload["cases"]), 120)
            self.assertEqual(
                {row["implicitPrecision"] for row in payload["metrics"]}, {100.0}
            )
            self.assertEqual(
                {row["explicitActivationRate"] for row in payload["metrics"]}, {100.0}
            )

            policy_skill = Path(directory) / "policy-skill"
            (policy_skill / "agents").mkdir(parents=True)
            shutil.copyfile(DEFAULT_SKILL / "SKILL.md", policy_skill / "SKILL.md")
            interface = (DEFAULT_SKILL / "agents/openai.yaml").read_text(encoding="utf-8")
            (policy_skill / "agents/openai.yaml").write_text(
                interface.replace(
                    "allow_implicit_invocation: false", "allow_implicit_invocation: true"
                ),
                encoding="utf-8",
            )
            self.assertIn(
                "implicit invocation requires REAL_HOST routing evidence",
                routing_policy_errors(skill_root=policy_skill, result_path=result),
            )

            payload["metrics"][0]["implicitPrecision"] = 0.0
            result.write_text(json.dumps(payload), encoding="utf-8")
            errors = validate_routing_result(result, skill_root=DEFAULT_SKILL)
            self.assertTrue(any("contentSha256" in error for error in errors))
            self.assertTrue(any("metrics do not match" in error for error in errors))

    def test_implicit_policy_fails_closed_without_current_result(self) -> None:
        with tempfile.TemporaryDirectory(prefix="review-craft-routing-policy-") as directory:
            skill_root = Path(directory)
            agents = skill_root / "agents"
            agents.mkdir()
            policy = agents / "openai.yaml"
            missing = skill_root / "missing-result.json"

            policy.write_text(
                "policy:\n  allow_implicit_invocation: false\n", encoding="utf-8"
            )
            self.assertEqual(
                routing_policy_errors(skill_root=skill_root, result_path=missing), []
            )

            policy.write_text(
                "policy:\n  allow_implicit_invocation: true\n", encoding="utf-8"
            )
            self.assertIn(
                "implicit invocation requires a current, content-bound routing result",
                routing_policy_errors(skill_root=skill_root, result_path=missing),
            )


if __name__ == "__main__":
    unittest.main()
