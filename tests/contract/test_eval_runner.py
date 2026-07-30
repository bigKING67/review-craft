from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import ROOT

sys.path.insert(0, str(ROOT / "scripts"))

import eval_contracts  # noqa: E402
import run_evals as eval_runner  # noqa: E402

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

    def _adjudication_payload(
        self,
        run_dir: Path,
        outcomes: dict[str, str] | None = None,
    ) -> dict:
        run = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        default_outcomes = {
            "swallowed-error": "SEEDED_ISSUE_MATCH",
            "long-cohesive-file": "NO_FINDING_CORRECT",
        }
        default_outcomes.update(outcomes or {})
        return {
            "schema": "review-craft.eval-adjudication.v1",
            "runId": run["runId"],
            "runContentSha256": run["contentSha256"],
            "adjudicator": {
                "kind": "AGENT_ASSISTED",
                "protocol": "test-semantic-v1",
            },
            "cases": [
                {
                    "id": record["id"],
                    "normalizedOutputSha256": record["normalizedOutputSha256"],
                    "outcome": default_outcomes[record["id"]],
                    "decisionDisposition": "APPROPRIATE",
                    "rationale": f"Contract adjudication for {record['id']}.",
                }
                for record in run["cases"]
            ],
        }

    def test_synthetic_adapter_exercises_runner_without_claiming_golden_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = self._synthetic_run(Path(directory))
            result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["schema"], "review-craft.eval-run.v3")
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
            self.assertEqual(result["metrics"]["usage"]["availability"], "UNAVAILABLE")
            self.assertEqual(result["metrics"]["usage"]["reportedCases"], 0)
            self.assertIsNone(result["metrics"]["usage"]["reportedUsage"])
            self.assertEqual(
                result["metrics"]["usage"]["unavailableReasons"],
                {"ADAPTER_DID_NOT_REPORT_USAGE": 2},
            )
            self.assertTrue(
                all(
                    record["usage"]["availability"] == "UNAVAILABLE"
                    for record in result["cases"]
                )
            )
            validated = run_eval("validate", "--run-dir", str(run_dir))
            self.assertEqual(validated.returncode, 0, validated.stderr)

    def test_adapter_usage_is_structured_aggregated_and_content_bound(self) -> None:
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
                "usage",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            run_dir = Path(json.loads(completed.stdout)["runDir"])
            result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
            usage = result["cases"][0]["usage"]
            self.assertEqual(usage["availability"], "AVAILABLE")
            self.assertEqual(usage["inputTokens"], 100)
            self.assertEqual(usage["cachedInputTokens"], 25)
            self.assertEqual(usage["outputTokens"], 20)
            self.assertEqual(usage["totalTokens"], 120)
            self.assertEqual(usage["toolCalls"]["total"], 2)
            aggregate = result["metrics"]["usage"]
            self.assertEqual(aggregate["availability"], "COMPLETE")
            self.assertEqual(aggregate["reportedCases"], 1)
            self.assertEqual(aggregate["unavailableCases"], 0)
            self.assertEqual(aggregate["reportedUsage"]["totalTokens"], 120)
            self.assertEqual(aggregate["reportedUsage"]["toolCalls"]["total"], 2)
            partial = eval_contracts.aggregate_usage(
                [
                    {"usage": usage},
                    {
                        "usage": eval_contracts.unavailable_usage(
                            "ADAPTER_DID_NOT_REPORT_USAGE"
                        )
                    },
                ]
            )
            self.assertEqual(partial["availability"], "PARTIAL")
            self.assertEqual(partial["reportedCases"], 1)
            self.assertEqual(partial["unavailableCases"], 1)
            self.assertEqual(partial["reportedUsage"]["totalTokens"], 120)
            self.assertEqual(
                partial["unavailableReasons"],
                {"ADAPTER_DID_NOT_REPORT_USAGE": 1},
            )
            validated = run_eval("validate", "--run-dir", str(run_dir))
            self.assertEqual(validated.returncode, 0, validated.stderr)

            usage_path = run_dir / result["cases"][0]["usageArtifact"]
            usage["inputTokens"] = 101
            usage_path.write_text(json.dumps(usage), encoding="utf-8")
            tampered = run_eval("validate", "--run-dir", str(run_dir))
            self.assertEqual(tampered.returncode, 2)
            self.assertIn("usage: sha256 mismatch", tampered.stderr)

    def test_invalid_adapter_usage_is_unavailable_not_zero(self) -> None:
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
                "invalid-usage",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            run_dir = Path(json.loads(completed.stdout)["runDir"])
            result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
            usage = result["cases"][0]["usage"]
            self.assertEqual(usage["availability"], "UNAVAILABLE")
            self.assertEqual(usage["unavailableReason"], "ADAPTER_USAGE_INVALID")
            self.assertIsNone(usage["totalTokens"])
            self.assertIsNone(usage["toolCalls"])
            self.assertEqual(
                result["metrics"]["usage"]["unavailableReasons"],
                {"ADAPTER_USAGE_INVALID": 1},
            )

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
            self.assertEqual(payload["schema"], "review-craft.eval-comparison.v1")
            self.assertIsNone(payload["semantic"])
            self.assertRegex(payload["contentSha256"], r"^[a-f0-9]{64}$")
            self.assertEqual(payload["metricDeltaPercentagePoints"]["candidateRecallPercent"], 0.0)

    def test_matched_comparison_binds_semantic_adjudications(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            review = self._synthetic_run(root)
            baseline = self._synthetic_run(root, treatment="ORDINARY_PROMPT")
            results = {}
            for label, run_dir in (("review", review), ("baseline", baseline)):
                adjudication = self._adjudication_payload(run_dir)
                if label == "baseline":
                    adjudication["cases"][0]["decisionDisposition"] = "INAPPROPRIATE"
                input_path = root / f"{label}-adjudication-input.json"
                result_path = root / f"{label}-adjudication-result.json"
                input_path.write_text(json.dumps(adjudication), encoding="utf-8")
                adjudicated = run_eval(
                    "adjudicate",
                    "--run-dir",
                    str(run_dir),
                    "--adjudication",
                    str(input_path),
                    "--output",
                    str(result_path),
                )
                self.assertEqual(adjudicated.returncode, 0, adjudicated.stderr)
                results[label] = result_path

            output_path = root / "comparison.json"
            compared = run_eval(
                "compare",
                "--review-craft-run",
                str(review),
                "--baseline-run",
                str(baseline),
                "--review-craft-adjudication",
                str(results["review"]),
                "--baseline-adjudication",
                str(results["baseline"]),
                "--output",
                str(output_path),
            )
            self.assertEqual(compared.returncode, 0, compared.stderr)
            payload = json.loads(compared.stdout)
            self.assertEqual(
                payload["semantic"]["metricDeltaPercentagePoints"][
                    "semanticDecisionAccuracyPercent"
                ],
                50.0,
            )
            self.assertEqual(
                payload["semantic"]["reviewCraft"]["metrics"][
                    "semanticEvidenceValidation"
                ],
                "ADJUDICATED",
            )
            self.assertFalse(payload["semantic"]["comparativeEligible"])
            self.assertEqual(payload, json.loads(output_path.read_text(encoding="utf-8")))

            tampered_result = json.loads(
                results["review"].read_text(encoding="utf-8")
            )
            tampered_result["cases"][0]["rationale"] = "Tampered after adjudication."
            tampered_path = root / "tampered-adjudication-result.json"
            tampered_path.write_text(json.dumps(tampered_result), encoding="utf-8")
            tampered_compare = run_eval(
                "compare",
                "--review-craft-run",
                str(review),
                "--baseline-run",
                str(baseline),
                "--review-craft-adjudication",
                str(tampered_path),
                "--baseline-adjudication",
                str(results["baseline"]),
            )
            self.assertEqual(tampered_compare.returncode, 2)
            self.assertIn("does not match the bound run", tampered_compare.stderr)

            missing_pair = run_eval(
                "compare",
                "--review-craft-run",
                str(review),
                "--baseline-run",
                str(baseline),
                "--review-craft-adjudication",
                str(results["review"]),
            )
            self.assertEqual(missing_pair.returncode, 2)
            self.assertIn("requires both adjudication results", missing_pair.stderr)

            export = run_eval(
                "export-golden",
                "--review-craft-run",
                str(review),
                "--baseline-run",
                str(baseline),
                "--review-craft-adjudication",
                str(results["review"]),
                "--baseline-adjudication",
                str(results["baseline"]),
                "--output",
                str(root / "golden.json"),
            )
            self.assertEqual(export.returncode, 2)
            self.assertIn("comparative-eligible", export.stderr)

            review_payload = json.loads((review / "result.json").read_text(encoding="utf-8"))
            baseline_payload = json.loads((baseline / "result.json").read_text(encoding="utf-8"))
            payload["comparativeEligible"] = True
            payload["contentSha256"] = eval_runner.sha256_json(
                {key: value for key, value in payload.items() if key != "contentSha256"}
            )
            with self.assertRaisesRegex(
                eval_runner.EvalError,
                "complete matched semantic adjudications",
            ):
                eval_runner.build_golden_snapshot(
                    payload,
                    review_payload,
                    baseline_payload,
                )

            payload["semantic"]["comparativeEligible"] = True
            with self.assertRaisesRegex(
                eval_runner.EvalError,
                "contentSha256",
            ):
                eval_runner.build_golden_snapshot(
                    payload,
                    review_payload,
                    baseline_payload,
                )

    def test_golden_builder_is_deterministic_and_sanitized(self) -> None:
        fixture = json.loads(
            (
                ROOT
                / "evals/golden-results/705dbac-gpt-5.6-sol/snapshot.json"
            ).read_text(encoding="utf-8")
        )

        def run_payload(result_key: str) -> dict:
            result = fixture["results"][result_key]
            source = fixture["source"]
            description = {
                "name": fixture["host"]["name"],
                "version": fixture["host"]["version"],
                "model": fixture["host"]["model"],
                "reasoning": fixture["host"]["reasoning"],
                "adapterVersion": fixture["host"]["adapterVersion"],
                "evidenceKind": fixture["host"]["evidenceKind"],
                "provider": {
                    **fixture["host"]["provider"],
                    "baseUrl": "https://provider.invalid/v1",
                },
                "isolation": fixture["host"]["isolation"],
            }
            return {
                "runId": result["runId"],
                "contentSha256": result["contentSha256"],
                "treatment": result["treatment"],
                "goldenEligible": result["goldenEligible"],
                "metrics": result["metrics"],
                "source": {
                    **source,
                    "dirtyFingerprint": "1" * 64,
                    "completedRevision": source["revision"],
                    "completedDirty": False,
                    "completedDirtyFingerprint": "1" * 64,
                    "completedTreeSha256": source["treeSha256"],
                },
                "suite": fixture["suite"],
                "skill": fixture["skill"],
                "adapter": {
                    "description": description,
                    "command": ["/private/tmp/private-adapter", "--internal-flag"],
                },
                "caseTimeoutSeconds": 900,
            }

        review = run_payload("reviewCraft")
        baseline = run_payload("baseline")
        semantic = fixture["semantic"]
        comparison = {
            "schema": "review-craft.eval-comparison.v1",
            "matched": True,
            "comparativeEligible": True,
            "reviewCraft": {
                "runId": review["runId"],
                "contentSha256": review["contentSha256"],
                "metrics": review["metrics"],
            },
            "baseline": {
                "runId": baseline["runId"],
                "contentSha256": baseline["contentSha256"],
                "metrics": baseline["metrics"],
            },
            "metricDeltaPercentagePoints": eval_runner._metric_deltas(
                review["metrics"], baseline["metrics"]
            ),
            "matchedFields": eval_runner._comparison_fields(review),
            "semantic": {
                "comparativeEligible": True,
                "adjudicator": semantic["adjudicator"],
                "reviewCraft": {
                    "runId": review["runId"],
                    "runContentSha256": review["contentSha256"],
                    "resultContentSha256": semantic["reviewCraft"]["contentSha256"],
                    "metrics": semantic["reviewCraft"]["metrics"],
                },
                "baseline": {
                    "runId": baseline["runId"],
                    "runContentSha256": baseline["contentSha256"],
                    "resultContentSha256": semantic["baseline"]["contentSha256"],
                    "metrics": semantic["baseline"]["metrics"],
                },
                "metricDeltaPercentagePoints": semantic[
                    "metricDeltaPercentagePoints"
                ],
            },
            "contentSha256": "0" * 64,
        }
        comparison["contentSha256"] = eval_runner.sha256_json(
            {
                key: value
                for key, value in comparison.items()
                if key != "contentSha256"
            }
        )

        first = eval_runner.build_golden_snapshot(comparison, review, baseline)
        second = eval_runner.build_golden_snapshot(comparison, review, baseline)
        self.assertEqual(first, second)
        self.assertEqual(eval_runner.validate_golden_snapshot(first), [])
        rendered = json.dumps(first, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("baseUrl", rendered)
        self.assertNotIn("private-adapter", rendered)
        self.assertNotIn("adapterCommand", rendered)

        with tempfile.TemporaryDirectory() as directory:
            first_path = Path(directory) / "first.json"
            second_path = Path(directory) / "second.json"
            eval_runner.write_json(first_path, first)
            eval_runner.write_json(second_path, second)
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())

    def test_semantic_adjudication_is_content_bound_and_revalidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = self._synthetic_run(root)
            template_path = root / "adjudication-template.json"
            prepared = run_eval(
                "prepare-adjudication",
                "--run-dir",
                str(run_dir),
                "--kind",
                "AGENT_ASSISTED",
                "--protocol",
                "test-semantic-v1",
                "--output",
                str(template_path),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            template = json.loads(template_path.read_text(encoding="utf-8"))
            self.assertTrue(all(case["outcome"] == "UNRESOLVED" for case in template["cases"]))
            self.assertTrue(
                all(case["normalizedOutputSha256"] != "0" * 64 for case in template["cases"])
            )
            adjudication_path = root / "adjudication-input.json"
            result_path = root / "adjudication-result.json"
            adjudication = self._adjudication_payload(run_dir)
            adjudication["cases"].reverse()
            adjudication_path.write_text(json.dumps(adjudication), encoding="utf-8")
            completed = run_eval(
                "adjudicate",
                "--run-dir",
                str(run_dir),
                "--adjudication",
                str(adjudication_path),
                "--output",
                str(result_path),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [case["id"] for case in result["cases"]],
                ["swallowed-error", "long-cohesive-file"],
            )
            self.assertEqual(result["metrics"]["semanticSeededRecallPercent"], 100.0)
            self.assertEqual(result["metrics"]["semanticFindingPrecisionPercent"], 100.0)
            self.assertEqual(result["metrics"]["semanticFalsePositiveRatePercent"], 0.0)
            self.assertEqual(result["metrics"]["semanticDecisionAccuracyPercent"], 100.0)
            self.assertEqual(result["metrics"]["semanticEvidenceValidation"], "ADJUDICATED")

            validated = run_eval(
                "validate-adjudication",
                "--run-dir",
                str(run_dir),
                "--result",
                str(result_path),
            )
            self.assertEqual(validated.returncode, 0, validated.stderr)

            result["cases"][0]["rationale"] = "Tampered rationale."
            result_path.write_text(json.dumps(result), encoding="utf-8")
            tampered = run_eval(
                "validate-adjudication",
                "--run-dir",
                str(run_dir),
                "--result",
                str(result_path),
            )
            self.assertEqual(tampered.returncode, 2)
            self.assertIn("does not match the bound run", tampered.stderr)

    def test_semantic_adjudication_rejects_output_hash_and_outcome_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = self._synthetic_run(root)
            adjudication_path = root / "adjudication-input.json"
            payload = self._adjudication_payload(run_dir)
            payload["cases"][0]["normalizedOutputSha256"] = "0" * 64
            adjudication_path.write_text(json.dumps(payload), encoding="utf-8")
            hash_mismatch = run_eval(
                "adjudicate",
                "--run-dir",
                str(run_dir),
                "--adjudication",
                str(adjudication_path),
            )
            self.assertEqual(hash_mismatch.returncode, 2)
            self.assertIn("normalized output sha256 does not match", hash_mismatch.stderr)

            payload = self._adjudication_payload(
                run_dir,
                outcomes={"swallowed-error": "NO_FINDING_CORRECT"},
            )
            adjudication_path.write_text(json.dumps(payload), encoding="utf-8")
            outcome_mismatch = run_eval(
                "adjudicate",
                "--run-dir",
                str(run_dir),
                "--adjudication",
                str(adjudication_path),
            )
            self.assertEqual(outcome_mismatch.returncode, 2)
            self.assertIn("conflicts with findingDetected=true", outcome_mismatch.stderr)

    def test_semantic_adjudication_excludes_contaminated_negative_from_fpr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            completed = run_eval(
                "run",
                "--output-root",
                str(root),
                "--case",
                "long-cohesive-file",
                "--case",
                "reasonable-monolith",
                "--adapter-command",
                sys.executable,
                str(FAKE_ADAPTER),
                "--mode",
                "negative-finding",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            run_dir = Path(json.loads(completed.stdout)["runDir"])
            adjudication = self._adjudication_payload(
                run_dir,
                outcomes={
                    "long-cohesive-file": "OTHER_VALID_FINDING",
                    "reasonable-monolith": "NO_FINDING_CORRECT",
                },
            )
            adjudication_path = root / "adjudication-input.json"
            adjudication_path.write_text(json.dumps(adjudication), encoding="utf-8")
            adjudicated = run_eval(
                "adjudicate",
                "--run-dir",
                str(run_dir),
                "--adjudication",
                str(adjudication_path),
            )
            self.assertEqual(adjudicated.returncode, 0, adjudicated.stderr)
            metrics = json.loads(adjudicated.stdout)["metrics"]
            self.assertEqual(metrics["contaminatedNegativeCases"], 1)
            self.assertEqual(metrics["validFindings"], 1)
            self.assertEqual(metrics["semanticFindingPrecisionPercent"], 100.0)
            self.assertEqual(metrics["semanticFalsePositiveRatePercent"], 0.0)

    def test_semantic_fpr_does_not_count_false_positives_from_positive_cases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = self._synthetic_run(root)
            adjudication = self._adjudication_payload(
                run_dir,
                outcomes={"swallowed-error": "FALSE_POSITIVE"},
            )
            adjudication["cases"][0]["decisionDisposition"] = "INAPPROPRIATE"
            adjudication_path = root / "adjudication-input.json"
            adjudication_path.write_text(json.dumps(adjudication), encoding="utf-8")
            adjudicated = run_eval(
                "adjudicate",
                "--run-dir",
                str(run_dir),
                "--adjudication",
                str(adjudication_path),
            )
            self.assertEqual(adjudicated.returncode, 0, adjudicated.stderr)
            metrics = json.loads(adjudicated.stdout)["metrics"]
            self.assertEqual(metrics["seededIssueMatches"], 0)
            self.assertEqual(metrics["missedSeededIssues"], 1)
            self.assertEqual(metrics["falsePositiveFindings"], 1)
            self.assertEqual(metrics["semanticSeededRecallPercent"], 0.0)
            self.assertEqual(metrics["semanticFindingPrecisionPercent"], 0.0)
            self.assertEqual(metrics["semanticFalsePositiveRatePercent"], 0.0)

    def test_semantic_adjudication_keeps_unresolved_metrics_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = self._synthetic_run(root)
            adjudication = self._adjudication_payload(
                run_dir,
                outcomes={"swallowed-error": "UNRESOLVED"},
            )
            adjudication["cases"][0]["decisionDisposition"] = "UNRESOLVED"
            adjudication_path = root / "adjudication-input.json"
            adjudication_path.write_text(json.dumps(adjudication), encoding="utf-8")
            adjudicated = run_eval(
                "adjudicate",
                "--run-dir",
                str(run_dir),
                "--adjudication",
                str(adjudication_path),
            )
            self.assertEqual(adjudicated.returncode, 0, adjudicated.stderr)
            metrics = json.loads(adjudicated.stdout)["metrics"]
            self.assertEqual(metrics["unresolvedCases"], 1)
            self.assertIsNone(metrics["semanticSeededRecallPercent"])
            self.assertIsNone(metrics["semanticFindingPrecisionPercent"])
            self.assertEqual(metrics["semanticFalsePositiveRatePercent"], 0.0)
            self.assertIsNone(metrics["semanticDecisionAccuracyPercent"])
            self.assertEqual(metrics["semanticEvidenceValidation"], "PARTIAL")

    def test_prepare_adjudication_rejects_whitespace_only_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = self._synthetic_run(root)
            prepared = run_eval(
                "prepare-adjudication",
                "--run-dir",
                str(run_dir),
                "--kind",
                "HUMAN",
                "--protocol",
                "   ",
            )
            self.assertEqual(prepared.returncode, 2)
            self.assertIn("does not match", prepared.stderr)

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
