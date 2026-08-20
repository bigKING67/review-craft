from __future__ import annotations

import copy
import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
contracts = importlib.import_module("real_repository_contracts")
runner = importlib.import_module("real_repository_benchmark")


SUITE_PATH = ROOT / "evals/specs/real-repositories.json"
RUNNER = ROOT / "scripts/real_repository_benchmark.py"
CURRENT_ROOT = ROOT / "evals/real-repositories/current"


class RealRepositoryBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.suite = json.loads(SUITE_PATH.read_text(encoding="utf-8"))

    def _output(
        self,
        repository: dict,
        *,
        score: float = 82,
        additional_findings: list[dict] | None = None,
    ) -> dict:
        dispositions = {
            "REAL_FINDING": ("VALIDATED", "CLEAN_UP", "P2", "known-root"),
            "KEEP": ("VALIDATED", "KEEP", None, "preserved-design"),
            "DECOY": ("FALSIFIED", "KEEP", None, None),
            "MEASUREMENT": ("BLOCKED", "MEASURE", None, None),
            "EVIDENCE_GAP": ("BLOCKED", "DEFER", None, None),
        }
        probes = []
        for probe in repository["probes"]:
            disposition, decision, severity, root_cause = dispositions[probe["kind"]]
            probes.append(
                {
                    "probeId": probe["id"],
                    "disposition": disposition,
                    "decision": decision,
                    "severity": severity,
                    "rootCauseKey": root_cause,
                    "locations": [],
                    "evidence": [],
                    "confidence": "HIGH",
                    "rationale": "Synthetic deterministic contract fixture.",
                }
            )
        return {
            "schema": "review-craft.eval-real-repository-output.v1",
            "repositoryId": repository["id"],
            "score": {"status": "FINAL", "value": score},
            "probes": probes,
            "additionalFindings": additional_findings or [],
            "summary": "Synthetic deterministic contract fixture.",
        }

    def _campaign(self, *, additional_finding: bool = False) -> tuple[dict, dict]:
        blind = contracts.blind_suite(self.suite)
        repository = self.suite["repositories"][0]
        samples = []
        for model_index in range(2):
            for treatment in contracts.TREATMENTS:
                for repetition in range(1, 4):
                    finding = []
                    if additional_finding and model_index == 0 and repetition == 1:
                        finding = [
                            {
                                "findingId": "extra-bug",
                                "title": "Extra fixture finding",
                                "decision": "CLEAN_UP",
                                "severity": "P3",
                                "rootCauseKey": "extra-root",
                                "locations": [
                                    {
                                        "path": repository["scope"][0],
                                        "lineStart": 1,
                                        "lineEnd": 1,
                                    }
                                ],
                                "evidence": [
                                    {
                                        "claim": "Synthetic evidence.",
                                        "locations": [
                                            {
                                                "path": repository["scope"][0],
                                                "lineStart": 1,
                                                "lineEnd": 1,
                                            }
                                        ],
                                    }
                                ],
                                "confidence": "MEDIUM",
                            }
                        ]
                    sample_id = f"sample-{model_index}-{treatment.lower()}-{repetition}"
                    samples.append(
                        {
                            "sampleId": sample_id,
                            "repositoryId": repository["id"],
                            "treatment": treatment,
                            "modelConfiguration": {
                                "id": f"model-{model_index}",
                                "model": "fixture-model",
                                "reasoning": f"level-{model_index}",
                                "adapterName": "fixture-adapter",
                                "adapterVersion": "fixture-v1",
                                "hostVersion": "fixture-host-v1",
                                "evidenceKind": "REAL_HOST",
                                "providerName": "fixture-provider",
                                "isolationSha256": "1" * 64,
                            },
                            "repetition": repetition,
                            "status": "COMPLETED",
                            "durationSeconds": float(repetition),
                            "usage": {
                                "inputTokens": 100,
                                "outputTokens": 20,
                                "totalTokens": 120,
                                "toolCalls": 2,
                            },
                            "sourceMutationDetected": False,
                            "output": self._output(
                                repository,
                                score=80 + (2 * repetition),
                                additional_findings=finding,
                            ),
                            "failureReason": None,
                            "artifacts": {
                                "promptSha256": "2" * 64,
                                "stdoutSha256": "3" * 64,
                                "stderrSha256": "4" * 64,
                                "usageSha256": "5" * 64,
                                "outputSha256": "0" * 64,
                            },
                        }
                    )
                    samples[-1]["artifacts"]["outputSha256"] = contracts.sha256_json(
                        samples[-1]["output"]
                    )
        campaign = {
            "schema": "review-craft.eval-real-repository-campaign.v1",
            "campaignId": "synthetic-partial",
            "status": "PARTIAL",
            "suiteSha256": contracts.sha256_json(self.suite),
            "blindSuiteSha256": blind["contentSha256"],
            "samples": samples,
            "contentSha256": "0" * 64,
        }
        campaign["contentSha256"] = contracts.sha256_json(
            {key: value for key, value in campaign.items() if key != "contentSha256"}
        )
        return campaign, blind

    def test_public_suite_is_valid_and_has_required_real_repository_mix(self) -> None:
        self.assertEqual(contracts.validate_suite(self.suite), [])
        repositories = self.suite["repositories"]
        self.assertEqual(len(repositories), 8)
        self.assertGreaterEqual(
            sum(row["projectType"] == "legacy-compatibility" for row in repositories),
            1,
        )
        for ecosystem, minimum in contracts.ECOSYSTEM_MINIMUMS.items():
            self.assertGreaterEqual(
                sum(row["ecosystem"] == ecosystem for row in repositories),
                minimum,
                ecosystem,
            )
        for repository in repositories:
            self.assertEqual(
                [probe["kind"] for probe in repository["probes"]],
                list(contracts.PROBE_KINDS),
            )
            real_finding = repository["probes"][0]
            self.assertIn("upstreamFix", real_finding)
            self.assertNotEqual(repository["revision"], real_finding["upstreamFix"]["revision"])

    def test_blind_suite_excludes_oracles_and_expected_answers(self) -> None:
        payload = contracts.blind_suite(self.suite)
        text = json.dumps(payload, sort_keys=True)
        for forbidden in (
            "upstreamFix",
            "expectedDispositions",
            "expectedDecisions",
            "forbiddenDecisions",
            "rationale",
        ):
            self.assertNotIn(forbidden, text)
        for repository in self.suite["repositories"]:
            for probe in repository["probes"]:
                self.assertNotIn(probe["rationale"], text)
                if "upstreamFix" in probe:
                    self.assertNotIn(probe["upstreamFix"]["revision"], text)
                    self.assertNotIn(probe["upstreamFix"]["title"], text)
        expected_hash = contracts.sha256_json(
            {key: value for key, value in payload.items() if key != "contentSha256"}
        )
        self.assertEqual(payload["contentSha256"], expected_hash)

    def test_published_blind_suite_and_materialization_receipt_are_bound(self) -> None:
        blind = json.loads((CURRENT_ROOT / "blind-suite.json").read_text(encoding="utf-8"))
        receipt = json.loads((CURRENT_ROOT / "materialization.json").read_text(encoding="utf-8"))
        self.assertEqual(contracts.validate_blind_suite(blind, self.suite), [])
        self.assertEqual(contracts.validate_materialization_receipt(receipt, self.suite), [])
        self.assertTrue(receipt["suite"]["fullSuite"])
        self.assertTrue(
            all(repository["fixParentVerified"] for repository in receipt["repositories"])
        )

        tampered = copy.deepcopy(receipt)
        tampered["repositories"][0]["revision"] = "0" * 40
        errors = contracts.validate_materialization_receipt(tampered, self.suite)
        self.assertTrue(any("contentSha256 mismatch" in error for error in errors), errors)
        self.assertTrue(any("revision does not match suite" in error for error in errors), errors)

    def test_suite_rejects_missing_ecosystem_and_oracle_outside_scope(self) -> None:
        missing = copy.deepcopy(self.suite)
        rust_repository = next(
            repository
            for repository in missing["repositories"]
            if repository["ecosystem"] == "rust"
        )
        rust_repository["ecosystem"] = "node"
        errors = contracts.validate_suite(missing)
        self.assertTrue(any("ecosystem rust" in error for error in errors), errors)

        escaped = copy.deepcopy(self.suite)
        escaped["repositories"][0]["probes"][0]["upstreamFix"]["locations"] = [
            {"path": "../secret"}
        ]
        errors = contracts.validate_suite(escaped)
        self.assertTrue(any("unsafe fix location" in error for error in errors), errors)

    def test_host_output_requires_complete_probe_coverage_and_honest_score(self) -> None:
        repository = self.suite["repositories"][0]
        payload = {
            "schema": "review-craft.eval-real-repository-output.v1",
            "repositoryId": repository["id"],
            "score": {"status": "NOT_PRODUCED", "value": None},
            "probes": [
                {
                    "probeId": probe["id"],
                    "disposition": "BLOCKED",
                    "decision": "DEFER",
                    "severity": None,
                    "rootCauseKey": None,
                    "locations": [],
                    "evidence": [],
                    "confidence": "LOW",
                    "rationale": "Evidence is intentionally unresolved in this synthetic result.",
                }
                for probe in repository["probes"]
            ],
            "additionalFindings": [],
            "summary": "Synthetic contract fixture.",
        }
        self.assertEqual(contracts.validate_host_output(payload, repository), [])
        payload["score"] = {"status": "NOT_PRODUCED", "value": 90}
        self.assertIn(
            "score.value must be null when score.status is NOT_PRODUCED",
            contracts.validate_host_output(payload, repository),
        )
        payload["score"] = {"status": "NOT_PRODUCED", "value": None}
        payload["probes"][0], payload["probes"][1] = (
            payload["probes"][1],
            payload["probes"][0],
        )
        self.assertIn(
            "probes: must cover repository probes in canonical order",
            contracts.validate_host_output(payload, repository),
        )

    def test_cli_validates_and_writes_content_bound_blind_suite(self) -> None:
        validated = subprocess.run(
            [sys.executable, str(RUNNER), "validate-suite", "--suite", str(SUITE_PATH)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(validated.returncode, 0, validated.stderr)
        summary = json.loads(validated.stdout)
        self.assertTrue(summary["valid"])
        self.assertEqual(summary["repositories"], 8)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "blind-suite.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "blind-suite",
                    "--suite",
                    str(SUITE_PATH),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            expected = contracts.sha256_json(
                {key: value for key, value in payload.items() if key != "contentSha256"}
            )
            self.assertEqual(payload["contentSha256"], expected)

    def test_campaign_validation_is_content_bound_and_fail_closed(self) -> None:
        campaign, blind = self._campaign()
        self.assertEqual(contracts.validate_campaign(campaign, self.suite, blind), [])

        mutated = copy.deepcopy(campaign)
        mutated["samples"][0]["sourceMutationDetected"] = True
        mutated["contentSha256"] = contracts.sha256_json(
            {key: value for key, value in mutated.items() if key != "contentSha256"}
        )
        errors = contracts.validate_campaign(mutated, self.suite, blind)
        self.assertTrue(any("completed after source mutation" in row for row in errors))

        inconsistent = copy.deepcopy(campaign)
        inconsistent["status"] = "COMPLETED"
        inconsistent["contentSha256"] = contracts.sha256_json(
            {key: value for key, value in inconsistent.items() if key != "contentSha256"}
        )
        errors = contracts.validate_campaign(inconsistent, self.suite, blind)
        self.assertIn("campaign status COMPLETED requires the full successful matrix", errors)

    def test_adapter_configuration_requires_unique_ids(self) -> None:
        payload = {
            "schema": "review-craft.eval-real-repository-adapters.v1",
            "adapters": [
                {"id": "model-a", "command": ["adapter", "--model", "a"]},
                {"id": "model-a", "command": ["adapter", "--model", "b"]},
            ],
        }
        self.assertIn(
            "adapter configuration contains duplicate ids",
            contracts.validate_adapter_config(payload),
        )

    def test_only_evidence_loop_receives_oracle_free_verifier_boundary(self) -> None:
        evidence_root = ROOT / "evals/real-repositories/verifiers"
        self.assertEqual(
            runner._adapter_evidence_args("REVIEW_CRAFT_EVIDENCE_LOOP", evidence_root),
            ["--evidence-root", str(evidence_root)],
        )
        self.assertEqual(runner._adapter_evidence_args("ORDINARY_PROMPT", evidence_root), [])
        self.assertNotIn(
            "upstreamFix",
            (evidence_root / "README.md").read_text(encoding="utf-8"),
        )

    def test_stability_report_computes_repeated_metrics_without_overclaiming(self) -> None:
        campaign, _blind = self._campaign()
        report = contracts.build_stability_report(self.suite, campaign)

        self.assertEqual(report["status"], "PARTIAL")
        self.assertEqual(report["coverage"]["repositories"], 1)
        self.assertEqual(report["coverage"]["modelConfigurations"], 2)
        self.assertEqual(report["coverage"]["minimumRepetitions"], 3)
        self.assertEqual(report["metrics"]["findingOverlap"]["value"], 1)
        self.assertEqual(report["metrics"]["rootCauseOverlap"]["value"], 1)
        self.assertEqual(report["metrics"]["decisionStability"]["value"], 1)
        self.assertEqual(report["metrics"]["severityAgreement"]["value"], 1)
        self.assertEqual(report["metrics"]["scoreVariance"]["medianAbsoluteDeviation"], 2)
        self.assertEqual(report["metrics"]["scoreVariance"]["maximumRange"], 4)
        self.assertEqual(report["metrics"]["falsePositiveRate"]["value"], 0)
        self.assertEqual(report["metrics"]["falsificationAccuracy"]["value"], 1)
        self.assertEqual(report["metrics"]["completionRate"]["value"], 1 / 8)
        self.assertEqual(report["metrics"]["wallTime"]["p50"], 2)
        self.assertEqual(report["metrics"]["wallTime"]["p95"], 3)
        self.assertEqual(report["metrics"]["tokenCost"]["totalTokens"], 2160)
        self.assertIsNone(report["metrics"]["humanAgreement"]["value"])
        self.assertEqual(
            contracts.validate_stability_report(report, self.suite, campaign, None),
            [],
        )

    def test_independent_adjudication_covers_every_additional_finding(self) -> None:
        campaign, _blind = self._campaign(additional_finding=True)
        samples = [row for row in campaign["samples"] if row["output"]["additionalFindings"]]
        adjudication = {
            "schema": "review-craft.eval-real-repository-adjudication.v1",
            "campaignContentSha256": campaign["contentSha256"],
            "adjudicators": [
                {"id": "human-a", "kind": "HUMAN", "independent": True},
                {"id": "human-b", "kind": "HUMAN", "independent": True},
            ],
            "labels": [
                {
                    "adjudicatorId": adjudicator,
                    "sampleId": sample["sampleId"],
                    "findingKey": "extra-bug",
                    "label": "FALSE_POSITIVE",
                    "rootCauseKey": None,
                    "rationale": "The synthetic finding is intentionally unsupported.",
                }
                for sample in samples
                for adjudicator in ("human-a", "human-b")
            ],
            "contentSha256": "0" * 64,
        }
        adjudication["contentSha256"] = contracts.sha256_json(
            {key: value for key, value in adjudication.items() if key != "contentSha256"}
        )
        self.assertEqual(contracts.validate_adjudication(adjudication, campaign), [])
        report = contracts.build_stability_report(self.suite, campaign, adjudication)
        self.assertEqual(report["metrics"]["humanAgreement"]["value"], 1)
        self.assertGreater(report["metrics"]["falsePositiveRate"]["value"], 0)

        missing = copy.deepcopy(adjudication)
        missing["labels"].pop()
        missing["contentSha256"] = contracts.sha256_json(
            {key: value for key, value in missing.items() if key != "contentSha256"}
        )
        errors = contracts.validate_adjudication(missing, campaign)
        self.assertTrue(any("missing 1 independent labels" in row for row in errors))


if __name__ == "__main__":
    unittest.main()
