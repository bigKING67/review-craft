from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from tests.support import ROOT

sys.path.insert(0, str(ROOT / "scripts"))

import remediation_safety as remediation
from eval_contracts import ADAPTER_SCHEMA, EvalError, schema_errors

FAKE_ADAPTER = ROOT / "tests/fixtures/fake_eval_adapter.py"
SUITE = ROOT / "evals/specs/remediation-safety-cases.json"
SKILL = ROOT / "skills/review-craft"
PAIR = ["bounded-saturating-add-positive", "bounded-saturating-add-negative"]


def fake_description(*, mode: str = "usage") -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, str(FAKE_ADAPTER), "--mode", mode, "--describe"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def run_synthetic(
    output_root: Path,
    *,
    cases: list[str],
    rounds: int = 1,
    timeout_seconds: int = 30,
    mode: str = "usage",
) -> tuple[Path, dict[str, object]]:
    return remediation.run_remediation_safety(
        suite_path=SUITE,
        skill_root=SKILL,
        output_root=output_root,
        requested_cases=cases,
        rounds=rounds,
        timeout_seconds=timeout_seconds,
        adapter_command=[sys.executable, str(FAKE_ADAPTER), "--mode", mode],
        adapter=fake_description(mode=mode),
    )


def arm(case: dict[str, object], name: str) -> dict[str, object]:
    return next(row for row in case["arms"] if row["arm"] == name)


class RemediationSafetyContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._core_temp = tempfile.TemporaryDirectory()
        cls.core_run_dir, cls.core_payload = run_synthetic(
            Path(cls._core_temp.name),
            cases=PAIR,
            rounds=2,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._core_temp.cleanup()

    def test_eight_case_suite_schema_pairs_and_live_baselines(self) -> None:
        suite = json.loads(SUITE.read_text(encoding="utf-8"))
        schema = json.loads(remediation.SUITE_SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(suite)), [])
        self.assertEqual(remediation.validate_remediation_suite(suite), [])
        self.assertEqual(len(suite["cases"]), 8)

        pairs: dict[str, set[str]] = {}
        for case in suite["cases"]:
            pairs.setdefault(case["pairId"], set()).add(case["class"])
            completed = subprocess.run(
                [
                    sys.executable,
                    str(remediation.DEFAULT_VERIFIER),
                    "--case",
                    case["id"],
                    "--target",
                    str(ROOT / case["fixture"]),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            oracle = json.loads(completed.stdout)
            expected = {
                row["id"]: row["expectedBaseline"] for row in case["claims"]
            }
            actual = {row["id"]: row["status"] for row in oracle["claims"]}
            self.assertEqual(actual, expected, case["id"])
        self.assertEqual(len(pairs), 4)
        self.assertTrue(all(classes == {"positive", "negative"} for classes in pairs.values()))

    def test_adapter_artifacts_redact_common_credential_echoes(self) -> None:
        raw = (
            b"Incorrect API key provided: secret-value. "
            b"api_key=second-secret Authorization: Bearer third-secret\n"
        )
        redacted = remediation._redact_adapter_output(raw)
        self.assertNotIn(b"secret-value", redacted)
        self.assertNotIn(b"second-secret", redacted)
        self.assertNotIn(b"third-secret", redacted)
        self.assertEqual(redacted.count(b"[REDACTED]"), 3)

    def test_every_baseline_closes_before_the_first_adapter_invocation(self) -> None:
        suite = json.loads(SUITE.read_text(encoding="utf-8"))
        suite["cases"][1]["claims"][0]["id"] = "unknown-baseline-claim"
        self.assertEqual(remediation.validate_remediation_suite(suite), [])
        with tempfile.TemporaryDirectory() as directory:
            suite_path = Path(directory) / "suite.json"
            suite_path.write_text(json.dumps(suite) + "\n", encoding="utf-8")
            with (
                patch.object(remediation, "_invoke_adapter") as invoke,
                self.assertRaisesRegex(EvalError, "oracle claim coverage mismatch"),
            ):
                remediation.run_remediation_safety(
                    suite_path=suite_path,
                    skill_root=SKILL,
                    output_root=Path(directory) / "runs",
                    requested_cases=None,
                    rounds=1,
                    timeout_seconds=30,
                    adapter_command=[sys.executable, str(FAKE_ADAPTER)],
                    adapter=fake_description(),
                )
            invoke.assert_not_called()

    def test_three_arm_pair_run_applies_the_evidence_gate_and_stop_rules(self) -> None:
        self.assertEqual(self.core_payload["status"], "COMPLETED")
        self.assertEqual(remediation.validate_remediation_run(self.core_run_dir), [])
        positive, negative = self.core_payload["cases"]

        gated_positive = arm(positive, remediation.GATED_ARM)
        self.assertEqual(gated_positive["repairInvocations"], 1)
        self.assertEqual(gated_positive["stopReason"], "CLAIMS_SATISFIED")
        self.assertEqual(len(gated_positive["rounds"]), 1)
        self.assertTrue(all(row["status"] == "PASS" for row in gated_positive["finalClaims"]))

        for name in remediation.ARMS[:2]:
            ungated = arm(positive, name)
            self.assertEqual(ungated["repairInvocations"], 2)
            self.assertEqual(len(ungated["rounds"]), 2)
            self.assertEqual(ungated["stopReason"], "NO_CHANGE")

        gated_negative = arm(negative, remediation.GATED_ARM)
        self.assertEqual(gated_negative["repairInvocations"], 0)
        self.assertEqual(gated_negative["sourceMutationRounds"], 0)
        self.assertEqual(gated_negative["stopReason"], "REVIEW_ABSTAINED")

        metrics = self.core_payload["metrics"]["arms"]
        self.assertEqual(metrics[remediation.GATED_ARM]["repairSuccessRate"]["percent"], 100.0)
        self.assertEqual(metrics[remediation.ARMS[0]]["repairSuccessRate"]["percent"], 50.0)
        self.assertEqual(metrics[remediation.ARMS[1]]["repairSuccessRate"]["percent"], 50.0)

    def test_bound_source_diff_oracle_and_result_tampering_fails_validation(self) -> None:
        relative_paths = {
            "source snapshot": (
                "cases/001-bounded-saturating-add-positive/"
                "baseline-source/bounded_add.py"
            ),
            "source diff": (
                "cases/001-bounded-saturating-add-positive/arms/ordinary-naive-loop/"
                "rounds/001/source-diff.json"
            ),
            "oracle": (
                "cases/001-bounded-saturating-add-positive/baseline-oracle.json"
            ),
        }
        for label, relative in relative_paths.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                copied = Path(directory) / "run"
                shutil.copytree(self.core_run_dir, copied)
                path = copied / relative
                path.write_bytes(path.read_bytes() + b"\n")
                self.assertTrue(remediation.validate_remediation_run(copied))

        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "run"
            shutil.copytree(self.core_run_dir, copied)
            result_path = copied / "result.json"
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            payload["status"] = "FAILED"
            result_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            errors = remediation.validate_remediation_run(copied)
            self.assertIn("result contentSha256 mismatch", errors)

    def test_suite_and_selection_validation_rejects_unsafe_or_ambiguous_inputs(self) -> None:
        suite = json.loads(SUITE.read_text(encoding="utf-8"))
        unpaired = copy.deepcopy(suite)
        unpaired["cases"].pop()
        unpaired_errors = remediation.validate_remediation_suite(unpaired)
        self.assertTrue(
            any("expected one positive and one negative" in error for error in unpaired_errors)
        )

        unsafe = copy.deepcopy(suite)
        unsafe["cases"][0]["allowedMutationPaths"] = ["../escape.py"]
        unsafe_errors = remediation.validate_remediation_suite(unsafe)
        self.assertTrue(
            any("unsafe mutation path" in error for error in unsafe_errors)
        )

        with tempfile.TemporaryDirectory() as directory:
            for selection, message in (
                ([PAIR[0], PAIR[0]], "must be unique"),
                (["unknown-case"], "unknown remediation cases"),
            ):
                with self.subTest(selection=selection), self.assertRaisesRegex(EvalError, message):
                    run_synthetic(Path(directory), cases=selection)

    def test_scope_violation_uses_actual_diff_and_remains_a_completed_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir, payload = run_synthetic(
                Path(directory),
                cases=[PAIR[0]],
                mode="remediation-scope-violation",
            )
            self.assertEqual(payload["status"], "COMPLETED")
            self.assertEqual(remediation.validate_remediation_run(run_dir), [])
            for result in payload["cases"][0]["arms"]:
                self.assertTrue(result["scopeViolation"])
                self.assertEqual(result["sourceMutationRounds"], 1)
                self.assertIn("unexpected.py", result["cumulativeChangedPaths"])
                self.assertFalse(result["rounds"][0]["repairSucceeded"])
                self.assertEqual(result["stopReason"], "SCOPE_VIOLATION")

    def test_claimed_paths_mismatch_does_not_override_the_actual_diff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir, payload = run_synthetic(
                Path(directory),
                cases=[PAIR[0]],
                mode="remediation-claimed-mismatch",
            )
            self.assertEqual(remediation.validate_remediation_run(run_dir), [])
            for result in payload["cases"][0]["arms"]:
                round_record = result["rounds"][0]
                self.assertTrue(round_record["claimedPathsMismatch"])
                actual_paths = [row["path"] for row in round_record["changes"]]
                self.assertEqual(actual_paths, ["bounded_add.py"])
                self.assertEqual(result["sourceMutationRounds"], 1)

    def test_source_regression_is_measured_without_becoming_infrastructure_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir, payload = run_synthetic(
                Path(directory),
                cases=[PAIR[1]],
                mode="remediation-regression",
            )
            self.assertEqual(payload["status"], "COMPLETED")
            self.assertEqual(remediation.validate_remediation_run(run_dir), [])
            for name in remediation.ARMS[:2]:
                result = arm(payload["cases"][0], name)
                self.assertTrue(result["everRegressed"])
                self.assertEqual(result["sourceMutationRounds"], 1)
                self.assertTrue(
                    any(
                        transition["transition"] == "PASS_TO_FAIL"
                        for transition in result["rounds"][0]["claimTransitions"]
                    )
                )
            gated = arm(payload["cases"][0], remediation.GATED_ARM)
            self.assertEqual(gated["stopReason"], "EVIDENCE_REJECTED")
            self.assertEqual(gated["repairInvocations"], 0)

    def test_invalid_timeout_unavailable_and_reviewer_mutation_runs_are_preserved(self) -> None:
        scenarios = (
            ("invalid", 30, "FAILED", "REVIEW_FAILED"),
            ("timeout", 1, "FAILED", "REVIEW_FAILED"),
            ("mutate-source", 30, "FAILED", "REVIEW_MUTATED_WORKSPACE"),
        )
        for mode, timeout_seconds, expected_status, stop_reason in scenarios:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                run_dir, payload = run_synthetic(
                    Path(directory),
                    cases=[PAIR[0]],
                    timeout_seconds=timeout_seconds,
                    mode=mode,
                )
                self.assertEqual(payload["status"], expected_status)
                self.assertEqual(remediation.validate_remediation_run(run_dir), [])
                results = payload["cases"][0]["arms"]
                self.assertTrue(
                    all(result["stopReason"] == stop_reason for result in results)
                )
                if mode == "mutate-source":
                    self.assertTrue(
                        all(result["sandboxBreach"] for result in payload["cases"][0]["arms"])
                    )

        with tempfile.TemporaryDirectory() as directory:
            run_dir, payload = remediation.run_remediation_safety(
                suite_path=SUITE,
                skill_root=SKILL,
                output_root=Path(directory),
                requested_cases=[PAIR[0]],
                rounds=1,
                timeout_seconds=30,
                adapter_command=["definitely-unavailable-review-craft-adapter"],
                adapter=fake_description(),
            )
            self.assertEqual(payload["status"], "UNAVAILABLE")
            self.assertEqual(remediation.validate_remediation_run(run_dir), [])

    def test_remediation_requires_v5_while_v2_through_v4_schema_remain_valid(self) -> None:
        current = fake_description()
        legacy_descriptions = []
        for version in (2, 3, 4):
            description = copy.deepcopy(current)
            description["schema"] = f"review-craft.eval-adapter.v{version}"
            description.pop("capabilities")
            if version < 4:
                description.pop("toolTrace")
                description["isolation"].pop("homeMatchesCodexHome")
            if version < 3:
                description.pop("usage")
            legacy_descriptions.append(description)
            self.assertEqual(schema_errors(description, ADAPTER_SCHEMA), [])

        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(EvalError, "requires an adapter.v5"),
        ):
            remediation.run_remediation_safety(
                suite_path=SUITE,
                skill_root=SKILL,
                output_root=Path(directory),
                requested_cases=[PAIR[0]],
                rounds=1,
                timeout_seconds=30,
                adapter_command=[sys.executable, str(FAKE_ADAPTER)],
                adapter=legacy_descriptions[-1],
            )


if __name__ == "__main__":
    unittest.main()
