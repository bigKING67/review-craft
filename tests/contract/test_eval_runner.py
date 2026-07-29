from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import ROOT

RUNNER = ROOT / "scripts/run_evals.py"
FAKE_ADAPTER = ROOT / "tests/fixtures/fake_eval_adapter.py"


def run_eval(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


class EvalRunnerTests(unittest.TestCase):
    def _synthetic_run(self, output_root: Path, *, treatment: str = "REVIEW_CRAFT") -> Path:
        completed = run_eval(
            "run",
            "--output-root",
            str(output_root),
            "--treatment",
            treatment,
            "--case",
            "swallowed-error",
            "--case",
            "long-cohesive-file",
            "--adapter-command",
            sys.executable,
            str(FAKE_ADAPTER),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return Path(json.loads(completed.stdout)["runDir"])

    def test_synthetic_adapter_exercises_runner_without_claiming_golden_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = self._synthetic_run(Path(directory))
            result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "COMPLETED")
            self.assertFalse(result["suite"]["fullSuite"])
            self.assertFalse(result["goldenEligible"])
            self.assertEqual(
                result["adapter"]["description"]["evidenceKind"],
                "SYNTHETIC_CONTRACT",
            )
            self.assertEqual(result["metrics"]["candidateRecallPercent"], 100.0)
            self.assertEqual(result["metrics"]["findingPrecisionPercent"], 100.0)
            self.assertEqual(result["metrics"]["falsePositiveRatePercent"], 0.0)
            self.assertEqual(result["metrics"]["decisionAccuracyPercent"], 100.0)
            self.assertEqual(result["metrics"]["rewriteRestraintPercent"], 100.0)
            self.assertEqual(
                result["metrics"]["semanticEvidenceValidation"],
                "NOT_AUTOMATED",
            )
            validated = run_eval("validate", "--run-dir", str(run_dir))
            self.assertEqual(validated.returncode, 0, validated.stderr)

    def test_synthetic_runs_exercise_matched_comparison_without_becoming_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory)
            review = self._synthetic_run(output_root)
            baseline = self._synthetic_run(output_root, treatment="ORDINARY_PROMPT")
            compared = run_eval(
                "compare",
                "--review-craft-run",
                str(review),
                "--baseline-run",
                str(baseline),
            )
            self.assertEqual(compared.returncode, 0, compared.stderr)
            payload = json.loads(compared.stdout)
            self.assertTrue(payload["matched"])
            self.assertFalse(payload["comparativeEligible"])
            self.assertEqual(payload["metricDeltaPercentagePoints"]["candidateRecallPercent"], 0.0)

    def test_single_class_selection_uses_null_for_undefined_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            completed = run_eval(
                "run",
                "--output-root",
                directory,
                "--case",
                "swallowed-error",
                "--adapter-command",
                sys.executable,
                str(FAKE_ADAPTER),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(
                (Path(json.loads(completed.stdout)["runDir"]) / "result.json").read_text()
            )
            self.assertIsNone(result["metrics"]["falsePositiveRatePercent"])
            self.assertIsNone(result["metrics"]["rewriteRestraintPercent"])

    def test_eval_validation_detects_artifact_and_metadata_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = self._synthetic_run(Path(directory))
            result_path = run_dir / "result.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            stdout = run_dir / result["cases"][0]["stdoutArtifact"]
            stdout.write_text("tampered\n", encoding="utf-8")
            artifact_check = run_eval("validate", "--run-dir", str(run_dir))
            self.assertEqual(artifact_check.returncode, 2)
            self.assertIn("stdout: sha256 mismatch", artifact_check.stderr)

            stdout.write_text("synthetic adapter completed\n", encoding="utf-8")
            result["adapter"]["description"]["model"] = "tampered-model"
            result_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            metadata_check = run_eval("validate", "--run-dir", str(run_dir))
            self.assertEqual(metadata_check.returncode, 2)
            self.assertIn("contentSha256 does not match", metadata_check.stderr)

    def test_invalid_adapter_output_is_preserved_as_a_valid_failed_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            completed = run_eval(
                "run",
                "--output-root",
                directory,
                "--case",
                "swallowed-error",
                "--adapter-command",
                sys.executable,
                str(FAKE_ADAPTER),
                "--mode",
                "invalid",
            )
            self.assertEqual(completed.returncode, 2, completed.stderr)
            run_dir = Path(json.loads(completed.stdout)["runDir"])
            result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
            record = result["cases"][0]
            self.assertEqual(record["status"], "FAILED")
            self.assertIsNotNone(record["adapterOutputArtifact"])
            self.assertIsNone(record["normalizedOutputArtifact"])
            self.assertIn("not valid JSON", record["failureReason"])
            validated = run_eval("validate", "--run-dir", str(run_dir))
            self.assertEqual(validated.returncode, 0, validated.stderr)

    def test_duplicate_decisions_are_rejected_after_host_schema_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            completed = run_eval(
                "run",
                "--output-root",
                directory,
                "--case",
                "swallowed-error",
                "--adapter-command",
                sys.executable,
                str(FAKE_ADAPTER),
                "--mode",
                "duplicate-decisions",
            )
            self.assertEqual(completed.returncode, 2, completed.stderr)
            run_dir = Path(json.loads(completed.stdout)["runDir"])
            result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
            record = result["cases"][0]
            self.assertEqual(record["status"], "FAILED")
            self.assertIn("duplicate decisions", record["failureReason"])

    def test_source_mutation_is_recorded_and_blocks_golden_eligibility(self) -> None:
        marker = ROOT / ".eval-source-mutation-test"
        marker.unlink(missing_ok=True)
        try:
            with tempfile.TemporaryDirectory() as directory:
                completed = run_eval(
                    "run",
                    "--output-root",
                    directory,
                    "--case",
                    "swallowed-error",
                    "--adapter-command",
                    sys.executable,
                    str(FAKE_ADAPTER),
                    "--mode",
                    "mutate-source",
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                run_dir = Path(json.loads(completed.stdout)["runDir"])
                result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
                self.assertFalse(result["source"]["stableThroughoutRun"])
                self.assertFalse(result["goldenEligible"])
                validated = run_eval("validate", "--run-dir", str(run_dir))
                self.assertEqual(validated.returncode, 0, validated.stderr)
        finally:
            marker.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
