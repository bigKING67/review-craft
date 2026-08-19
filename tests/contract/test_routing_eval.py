from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from routing_eval import (  # noqa: E402
    DEFAULT_SKILL,
    DEFAULT_SUITE,
    routing_policy_errors,
    run_routing,
    validate_routing_result,
)

FAKE_ADAPTER = ROOT / "tests/fixtures/fake_eval_adapter.py"


class RoutingEvalTests(unittest.TestCase):
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
