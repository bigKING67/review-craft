from __future__ import annotations

import copy
import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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

    def _component_adjudication(
        self,
        campaign: dict,
        *,
        incorrect_subjects: set[tuple[str, str]] | None = None,
    ) -> dict:
        incorrect_subjects = incorrect_subjects or set()
        adjudicators = ("human-a", "human-b")
        subject_hashes = contracts.adjudication_subject_content_hashes(campaign)
        labels = []
        for adjudicator_id in adjudicators:
            for sample_id, subject_type, subject_key in sorted(
                contracts.adjudication_subjects(campaign)
            ):
                incorrect = (sample_id, subject_key) in incorrect_subjects
                components = [
                    {
                        "key": key,
                        "label": (
                            "INCORRECT" if incorrect and index == 0 else "CORRECT"
                        ),
                        "rationale": "Synthetic component oracle fixture.",
                    }
                    for index, key in enumerate(
                        contracts.ADJUDICATION_COMPONENT_KEYS[subject_type]
                    )
                ]
                labels.append(
                    {
                        "adjudicatorId": adjudicator_id,
                        "itemId": "item-"
                        + contracts.sha256_json(
                            [adjudicator_id, sample_id, subject_type, subject_key]
                        )[:20],
                        "sampleId": sample_id,
                        "subjectType": subject_type,
                        "subjectKey": subject_key,
                        "subjectContentSha256": subject_hashes[
                            (sample_id, subject_type, subject_key)
                        ],
                        "label": "INCORRECT" if incorrect else "CORRECT",
                        "rationale": "Synthetic response-validity fixture.",
                        "components": components,
                    }
                )
        adjudication = {
            "schema": "review-craft.eval-real-repository-adjudication.v3",
            "campaignContentSha256": campaign["contentSha256"],
            "mappingContentSha256": "6" * 64,
            "rubricVersion": "review-craft.real-repository-component-rubric.v1",
            "adjudicators": [
                {
                    "id": adjudicator_id,
                    "kind": "HUMAN",
                    "independent": True,
                    "packetContentSha256": contracts.sha256_json(
                        [adjudicator_id, "packet"]
                    ),
                    "submissionContentSha256": contracts.sha256_json(
                        [adjudicator_id, "submission"]
                    ),
                }
                for adjudicator_id in adjudicators
            ],
            "labels": labels,
            "subjectResolutions": contracts.build_adjudication_resolutions(labels),
            "contentSha256": "0" * 64,
        }
        adjudication["contentSha256"] = contracts.sha256_json(
            {
                key: value
                for key, value in adjudication.items()
                if key != "contentSha256"
            }
        )
        return adjudication

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
            keep_probe = repository["probes"][1]
            self.assertTrue(
                keep_probe["publicPrompt"].startswith(contracts.KEEP_PROMPT_PREFIX)
            )
            self.assertEqual(keep_probe["expectedDispositions"], ["VALIDATED"])
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
        self.assertEqual(
            receipt["suite"]["bindingKind"], contracts.MATERIALIZATION_BINDING_KIND
        )
        self.assertTrue(receipt["suite"]["fullSuite"])
        self.assertTrue(
            all(repository["fixParentVerified"] for repository in receipt["repositories"])
        )

        tampered = copy.deepcopy(receipt)
        tampered["repositories"][0]["revision"] = "0" * 40
        errors = contracts.validate_materialization_receipt(tampered, self.suite)
        self.assertTrue(any("contentSha256 mismatch" in error for error in errors), errors)
        self.assertTrue(any("revision does not match suite" in error for error in errors), errors)

    def test_materialization_excludes_hidden_fix_from_evaluation_object_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            remote = root / "upstream"
            workspace = root / "materialized"
            remote.mkdir()
            workspace.mkdir()
            runner.run_git("init", "--quiet", str(remote))

            source = remote / "sample.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            runner.run_git("add", "sample.py", cwd=remote)
            runner.run_git(
                "-c",
                "user.name=Review Craft Fixture",
                "-c",
                "user.email=review-craft@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "parent",
                cwd=remote,
            )
            parent_revision = runner.run_git("rev-parse", "HEAD", cwd=remote)

            source.write_text("VALUE = 2\n", encoding="utf-8")
            runner.run_git("add", "sample.py", cwd=remote)
            runner.run_git(
                "-c",
                "user.name=Review Craft Fixture",
                "-c",
                "user.email=review-craft@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "fix",
                cwd=remote,
            )
            fix_revision = runner.run_git("rev-parse", "HEAD", cwd=remote)
            repository = {
                "id": "fixture-repository",
                "remote": remote.as_uri(),
                "revision": parent_revision,
                "scope": ["sample.py"],
                "probes": [
                    {
                        "kind": "REAL_FINDING",
                        "upstreamFix": {"revision": fix_revision},
                    }
                ],
            }

            receipt = runner._materialize_repository(repository, workspace)
            evaluation = workspace / receipt["checkout"]
            materialization_payload = {
                "schema": "review-craft.eval-real-repository-materialization.v1",
                "createdAt": "2026-08-23T00:00:00Z",
                "suite": {
                    "artifact": "suite.json",
                    "bindingKind": contracts.MATERIALIZATION_BINDING_KIND,
                    "sha256": contracts.materialization_suite_sha256(
                        {"repositories": [repository]}
                    ),
                    "fullSuite": True,
                    "selectedRepositoryIds": [repository["id"]],
                },
                "repositories": [receipt],
                "contentSha256": "0" * 64,
            }
            materialization_payload["contentSha256"] = contracts.sha256_json(
                {
                    key: value
                    for key, value in materialization_payload.items()
                    if key != "contentSha256"
                }
            )
            object_probe = subprocess.run(
                ["git", "cat-file", "-e", f"{fix_revision}^{{commit}}"],
                cwd=evaluation,
                capture_output=True,
                text=True,
                check=False,
            )
            fsck = subprocess.run(
                ["git", "fsck", "--no-reflogs", "--unreachable"],
                cwd=evaluation,
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertEqual(receipt["revision"], parent_revision)
            self.assertTrue(receipt["fixParentVerified"])
            self.assertTrue(receipt["fixObjectExcluded"])
            self.assertEqual(
                contracts.validate_materialization_receipt(
                    materialization_payload, {"repositories": [repository]}
                ),
                [],
            )
            self.assertNotEqual(object_probe.returncode, 0)
            self.assertNotIn(fix_revision, fsck.stdout + fsck.stderr)
            self.assertEqual(
                {path.name for path in workspace.iterdir()},
                {"repositories"},
            )

            plan = {"selection": {"repositories": [repository["id"]]}}
            suite = {"repositories": [repository]}
            materialization = {"repositories": [receipt]}
            self.assertEqual(
                runner._verify_plan_workspaces(
                    plan=plan,
                    suite=suite,
                    receipt=materialization,
                    workspace_root=workspace,
                ),
                {repository["id"]: evaluation},
            )

            runner.run_git("fetch", "--quiet", "origin", fix_revision, cwd=evaluation)
            with self.assertRaisesRegex(
                contracts.RealRepositoryError,
                "upstream fix object leaked into evaluation checkout",
            ):
                runner._verify_plan_workspaces(
                    plan=plan,
                    suite=suite,
                    receipt=materialization,
                    workspace_root=workspace,
                )

    def test_campaign_execution_rejects_pre_hardening_materialization_receipt(self) -> None:
        receipt = json.loads((CURRENT_ROOT / "materialization.json").read_text(encoding="utf-8"))
        repository_id = self.suite["repositories"][0]["id"]
        plan = {"selection": {"repositories": [repository_id]}}
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(
            contracts.RealRepositoryError,
            "materialization receipt does not attest fix object exclusion",
        ):
            runner._verify_plan_workspaces(
                plan=plan,
                suite=self.suite,
                receipt=receipt,
                workspace_root=Path(directory).resolve(),
            )

    def test_materialization_source_binding_ignores_prompt_only_changes(self) -> None:
        receipt = json.loads((CURRENT_ROOT / "materialization.json").read_text(encoding="utf-8"))
        prompt_only = copy.deepcopy(self.suite)
        prompt_only["repositories"][0]["probes"][1]["publicPrompt"] += " Clarify evidence."
        self.assertEqual(
            contracts.validate_materialization_receipt(receipt, prompt_only), []
        )

        changed_source = copy.deepcopy(self.suite)
        changed_source["repositories"][0]["revision"] = "0" * 40
        errors = contracts.validate_materialization_receipt(receipt, changed_source)
        self.assertTrue(
            any("source binding hash mismatch" in error for error in errors), errors
        )
        self.assertTrue(any("revision does not match suite" in error for error in errors), errors)

    def test_legacy_materialization_binding_remains_full_suite_bound(self) -> None:
        receipt = json.loads((CURRENT_ROOT / "materialization.json").read_text(encoding="utf-8"))
        legacy = copy.deepcopy(receipt)
        legacy["suite"].pop("bindingKind")
        legacy["suite"]["sha256"] = contracts.sha256_json(self.suite)
        legacy["contentSha256"] = contracts.sha256_json(
            {key: value for key, value in legacy.items() if key != "contentSha256"}
        )
        self.assertEqual(contracts.validate_materialization_receipt(legacy, self.suite), [])

        prompt_only = copy.deepcopy(self.suite)
        prompt_only["repositories"][0]["probes"][1]["publicPrompt"] += " Clarify evidence."
        errors = contracts.validate_materialization_receipt(legacy, prompt_only)
        self.assertIn("materialization suite hash mismatch", errors)

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

        ambiguous_keep = copy.deepcopy(self.suite)
        ambiguous_keep["repositories"][0]["probes"][1]["publicPrompt"] = (
            "Decide whether the implementation should be rewritten."
        )
        errors = contracts.validate_suite(ambiguous_keep)
        self.assertTrue(
            any("anchor the candidate as the preservation decision" in error for error in errors),
            errors,
        )

    def test_all_treatment_prompts_expose_semantic_output_invariants(self) -> None:
        repository = self.suite["repositories"][0]
        for treatment in contracts.TREATMENTS:
            with self.subTest(treatment=treatment):
                prompt = runner._render_benchmark_prompt(
                    treatment, repository
                ).decode("utf-8")
                normalized_prompt = " ".join(prompt.split())
                self.assertIn(
                    "A BLOCKED probe must use severity null.", normalized_prompt
                )
                self.assertIn(
                    "Every probe, evidence, and additional-finding location must be "
                    "inside the declared scope.",
                    normalized_prompt,
                )
                self.assertIn(
                    "keep each command below roughly 200 output lines or 32 KiB",
                    normalized_prompt,
                )
                self.assertIn(
                    "treat the candidate as the preservation decision: use VALIDATED "
                    "when evidence supports KEEP, DEFER, or DOCUMENT",
                    normalized_prompt,
                )
                self.assertIn(
                    "Do not use FALSIFIED merely because the rejected rewrite or "
                    "replacement proposal is unsupported.",
                    normalized_prompt,
                )

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
        payload["probes"][0]["severity"] = "P2"
        self.assertIn(
            "probes[0].severity must be null when disposition is BLOCKED",
            contracts.validate_host_output(payload, repository),
        )
        payload["probes"][0]["severity"] = None
        payload["probes"][0]["locations"] = [
            {"path": "go.mod", "lineStart": 1, "lineEnd": 1}
        ]
        self.assertIn(
            "probes[0].locations[0]: location is outside declared scope: go.mod",
            contracts.validate_host_output(payload, repository),
        )
        payload["probes"][0]["locations"] = []
        payload["probes"][0]["evidence"] = [
            {
                "claim": "Synthetic evidence outside the declared scope.",
                "locations": [
                    {"path": "go.mod", "lineStart": 1, "lineEnd": 1}
                ],
            }
        ]
        self.assertIn(
            "probes[0].evidence[0].locations[0]: location is outside declared "
            "scope: go.mod",
            contracts.validate_host_output(payload, repository),
        )
        payload["probes"][0]["evidence"] = []
        payload["additionalFindings"] = [
            {
                "findingId": "outside-scope-fixture",
                "title": "Synthetic outside-scope finding",
                "decision": "CLEAN_UP",
                "severity": "P3",
                "rootCauseKey": "outside-scope",
                "locations": [
                    {"path": "go.mod", "lineStart": 1, "lineEnd": 1}
                ],
                "evidence": [
                    {
                        "claim": "Synthetic outside-scope evidence.",
                        "locations": [
                            {"path": "go.mod", "lineStart": 1, "lineEnd": 1}
                        ],
                    }
                ],
                "confidence": "MEDIUM",
            }
        ]
        output_errors = contracts.validate_host_output(payload, repository)
        self.assertIn(
            "additionalFindings[0].locations[0]: location is outside declared "
            "scope: go.mod",
            output_errors,
        )
        self.assertIn(
            "additionalFindings[0].evidence[0].locations[0]: location is outside "
            "declared scope: go.mod",
            output_errors,
        )
        payload["additionalFindings"] = []
        payload["probes"][0]["locations"] = [
            {"path": "../go.mod", "lineStart": 1, "lineEnd": 1}
        ]
        self.assertIn(
            "probes[0].locations[0]: unsafe location: ../go.mod",
            contracts.validate_host_output(payload, repository),
        )
        payload["probes"][0]["locations"] = []
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

    def test_repository_shard_usage_does_not_aggregate_all_runner_samples(self) -> None:
        samples = [
            {
                "repositoryId": repository_id,
                "usage": {
                    "inputTokens": 100,
                    "cachedInputTokens": 20,
                    "cacheWriteInputTokens": 5,
                    "outputTokens": 25,
                    "reasoningOutputTokens": 10,
                    "totalTokens": 125,
                },
            }
            for repository_id in ("repository-a", "repository-b")
        ]
        usage = runner._repository_usage_components(samples, "repository-b")
        self.assertEqual(usage["inputTokens"], 100)
        self.assertEqual(usage["totalTokens"], 125)

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

    def test_timed_out_sample_preserves_partial_stdout_and_tool_trace(self) -> None:
        repository = self.suite["repositories"][0]
        description = {
            "name": "fixture-adapter",
            "adapterVersion": "fixture-v1",
            "version": "fixture-host-v1",
            "model": "fixture-model",
            "reasoning": "medium",
            "evidenceKind": "REAL_HOST",
            "provider": {"name": "fixture-provider"},
            "isolation": {"fixture": True},
            "isolationReceipt": {
                "protocol": "review-craft.eval-isolation-receipt.v1"
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository_root = root / "repository"
            repository_root.mkdir()
            runner.run_git("init", "--quiet", cwd=repository_root)
            runner.run_git("config", "user.name", "Review Craft Tests", cwd=repository_root)
            runner.run_git(
                "config", "user.email", "review-craft-tests@example.invalid", cwd=repository_root
            )
            target = repository_root / repository["scope"][0]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("fixture\n", encoding="utf-8")
            runner.run_git("add", repository["scope"][0], cwd=repository_root)
            runner.run_git("commit", "--quiet", "-m", "fixture", cwd=repository_root)

            def timed_out(
                _command: list[str],
                *,
                cwd: Path,
                timeout: int,
                env: dict[str, str],
            ) -> runner.ProcessResult:
                self.assertEqual(cwd, ROOT)
                self.assertEqual(timeout, 7)
                Path(env[runner.USAGE_OUTPUT_ENV]).write_text(
                    json.dumps(
                        {
                            "inputTokens": None,
                            "outputTokens": None,
                            "totalTokens": None,
                            "toolCalls": None,
                        }
                    ),
                    encoding="utf-8",
                )
                trace = {
                    "schema": "review-craft.eval-tool-trace.v1",
                    "items": [
                        {
                            "sequence": 0,
                            "type": "commandExecution",
                            "status": "completed",
                            "command": "rg --files",
                            "exitCode": 0,
                            "outputBytes": 8,
                            "outputSha256": "1" * 64,
                        }
                    ],
                }
                Path(env[runner.TOOL_TRACE_OUTPUT_ENV]).write_text(
                    json.dumps(trace), encoding="utf-8"
                )
                progress = {
                    "schema": "review-craft.eval-progress.v1",
                    "availability": "AVAILABLE",
                    "startedAt": "2026-08-21T00:00:00Z",
                    "threadStartedAt": "2026-08-21T00:00:01Z",
                    "turnStartedAt": "2026-08-21T00:00:02Z",
                    "firstItemAt": None,
                    "lastEventAt": "2026-08-21T00:00:02Z",
                    "lastEventType": "turn.started",
                    "eventCount": 2,
                    "itemEventCount": 0,
                    "timeToFirstItemSeconds": None,
                    "inactivityWarningSeconds": 3,
                    "inactivityDiagnosticSeconds": 6,
                    "inactivityState": "DIAGNOSTIC",
                    "inactivityAgeSeconds": 6.5,
                    "terminationReason": None,
                    "processTreeCleanup": "NOT_VERIFIED",
                    "unavailableReason": None,
                }
                Path(env[runner.PROGRESS_OUTPUT_ENV]).write_text(
                    json.dumps(progress), encoding="utf-8"
                )
                surface = {
                    "capturedAt": "2026-08-21T00:00:00Z",
                    "systemFileCount": 2,
                    "systemTreeSha256": "1" * 64,
                    "userExtensionFileCount": 0,
                    "userExtensionTreeSha256": "2" * 64,
                }
                runner.write_json(
                    Path(env[runner.ISOLATION_OUTPUT_ENV]),
                    {
                        "schema": "review-craft.eval-isolation-receipt.v1",
                        "availability": "UNAVAILABLE",
                        "policy": {
                            "homeMatchesCodexHome": True,
                            "ignoreUserConfig": True,
                            "ignoreRules": True,
                            "allowCodexHomeExtensions": False,
                        },
                        "preRun": surface,
                        "postStart": surface,
                        "postExit": None,
                        "comparison": {
                            "postStartSystemState": "MATCHED",
                            "postStartUserExtensionState": "MATCHED",
                            "postExitSystemState": "NOT_CAPTURED",
                            "postExitUserExtensionState": "NOT_CAPTURED",
                            "overall": "CAPTURE_UNAVAILABLE",
                        },
                        "unavailableReason": "POST_EXIT_NOT_CAPTURED",
                    },
                )
                return runner.ProcessResult(124, b'{"type":"item.completed"}\n', b"", True)

            with patch.object(runner, "run_process", side_effect=timed_out):
                sample = runner._run_sample(
                    run_dir=root / "run",
                    sample_ordinal=1,
                    repository=repository,
                    repository_root=repository_root,
                    treatment="ORDINARY_PROMPT",
                    repetition=1,
                    adapter={"id": "fixture", "command": ["fixture-adapter"]},
                    description=description,
                    timeout_seconds=7,
                    skill_root=ROOT / "skills/review-craft",
                    evidence_root=ROOT / "evals/real-repositories/verifiers",
                )

            self.assertEqual(sample["status"], "TIMED_OUT")
            self.assertEqual(sample["failureClass"], "TIMEOUT")
            self.assertEqual(sample["usage"]["toolCalls"], 1)
            self.assertIsNotNone(sample["artifacts"]["toolTraceSha256"])
            self.assertIsNotNone(sample["artifacts"]["progressSha256"])
            self.assertIsNotNone(sample["artifacts"]["isolationReceiptSha256"])
            self.assertEqual(sample["lifecycle"]["lastEventType"], "turn.started")
            self.assertEqual(sample["lifecycle"]["terminationReason"], "TIMEOUT")
            self.assertEqual(sample["lifecycle"]["processTreeCleanup"], "COMPLETED")
            self.assertEqual(
                sample["lifecycle"]["inactivityState"],
                "TIMED_OUT_BEFORE_FIRST_ITEM",
            )
            self.assertEqual(
                sample["lifecycle"]["maximumPreItemInactivitySeconds"], 6.5
            )
            sample_dir = next((root / "run/samples").iterdir())
            self.assertEqual(
                (sample_dir / "stdout.txt").read_text(encoding="utf-8"),
                '{"type":"item.completed"}\n',
            )
            self.assertTrue((sample_dir / "tool-trace.json").is_file())
            self.assertTrue((sample_dir / "progress.json").is_file())
            self.assertFalse(sample["sourceMutationDetected"])
            with self.assertRaisesRegex(
                contracts.RealRepositoryError,
                "will not be overwritten",
            ):
                runner._run_sample(
                    run_dir=root / "run",
                    sample_ordinal=1,
                    repository=repository,
                    repository_root=repository_root,
                    treatment="ORDINARY_PROMPT",
                    repetition=1,
                    adapter={"id": "fixture", "command": ["fixture-adapter"]},
                    description=description,
                    timeout_seconds=7,
                    skill_root=ROOT / "skills/review-craft",
                    evidence_root=ROOT / "evals/real-repositories/verifiers",
                )

    def test_adapter_managed_timeout_gets_finalization_grace_and_stays_timeout(
        self,
    ) -> None:
        repository = self.suite["repositories"][0]
        description = {
            "name": "fixture-adapter",
            "adapterVersion": "fixture-v1",
            "version": "fixture-host-v1",
            "model": "fixture-model",
            "reasoning": "medium",
            "evidenceKind": "REAL_HOST",
            "provider": {"name": "fixture-provider"},
            "isolation": {"fixture": True},
            "isolationReceipt": {
                "protocol": "review-craft.eval-isolation-receipt.v1"
            },
            "timeoutControl": {
                "protocol": "review-craft.eval-timeout-control.v1",
                "transport": "ENV_VALUE",
                "environmentVariable": runner.SAMPLE_TIMEOUT_ENV,
                "timeoutExitCode": 124,
                "finalizationGraceSeconds": 30,
            },
        }
        surface = {
            "capturedAt": "2026-08-22T00:00:00Z",
            "systemFileCount": 2,
            "systemTreeSha256": "1" * 64,
            "userExtensionFileCount": 0,
            "userExtensionTreeSha256": "2" * 64,
        }
        receipt = {
            "schema": "review-craft.eval-isolation-receipt.v1",
            "availability": "AVAILABLE",
            "policy": {
                "homeMatchesCodexHome": True,
                "ignoreUserConfig": True,
                "ignoreRules": True,
                "allowCodexHomeExtensions": False,
            },
            "preRun": surface,
            "postStart": surface,
            "postExit": surface,
            "comparison": {
                "postStartSystemState": "MATCHED",
                "postStartUserExtensionState": "MATCHED",
                "postExitSystemState": "MATCHED",
                "postExitUserExtensionState": "MATCHED",
                "overall": "MATCHED",
            },
            "unavailableReason": None,
        }
        progress = {
            "schema": "review-craft.eval-progress.v1",
            "availability": "AVAILABLE",
            "startedAt": "2026-08-22T00:00:00Z",
            "threadStartedAt": "2026-08-22T00:00:01Z",
            "turnStartedAt": "2026-08-22T00:00:01Z",
            "firstItemAt": None,
            "lastEventAt": "2026-08-22T00:00:01Z",
            "lastEventType": "turn.started",
            "eventCount": 2,
            "itemEventCount": 0,
            "timeToFirstItemSeconds": None,
            "timeToThreadStartedSeconds": 0.1,
            "timeToTurnStartedSeconds": 0.2,
            "firstToolCallAt": None,
            "timeToFirstToolCallSeconds": None,
            "lastSemanticProgressAt": None,
            "lastSemanticProgressType": None,
            "semanticProgressEventCount": 0,
            "inactivityWarningSeconds": 1,
            "inactivityDiagnosticSeconds": 2,
            "inactivityState": "TIMED_OUT_BEFORE_FIRST_ITEM",
            "inactivityAgeSeconds": 7.0,
            "maximumPreItemInactivitySeconds": 7.0,
            "diagnosticCapturedAt": "2026-08-22T00:00:03Z",
            "processAliveWhenDiagnosticCaptured": True,
            "terminationReason": "TIMEOUT",
            "processTreeCleanup": "COMPLETED",
            "unavailableReason": None,
        }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository_root = root / "repository"
            repository_root.mkdir()
            runner.run_git("init", "--quiet", cwd=repository_root)
            runner.run_git("config", "user.name", "Review Craft Tests", cwd=repository_root)
            runner.run_git(
                "config", "user.email", "review-craft-tests@example.invalid", cwd=repository_root
            )
            target = repository_root / repository["scope"][0]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("fixture\n", encoding="utf-8")
            runner.run_git("add", repository["scope"][0], cwd=repository_root)
            runner.run_git("commit", "--quiet", "-m", "fixture", cwd=repository_root)

            def managed_timeout(
                _command: list[str],
                *,
                cwd: Path,
                timeout: int,
                env: dict[str, str],
            ) -> runner.ProcessResult:
                self.assertEqual(cwd, ROOT)
                self.assertEqual(timeout, 37)
                self.assertEqual(env[runner.SAMPLE_TIMEOUT_ENV], "7")
                runner.write_json(
                    Path(env[runner.USAGE_OUTPUT_ENV]),
                    {
                        "inputTokens": None,
                        "outputTokens": None,
                        "totalTokens": None,
                        "toolCalls": None,
                    },
                )
                runner.write_json(Path(env[runner.PROGRESS_OUTPUT_ENV]), progress)
                runner.write_json(Path(env[runner.ISOLATION_OUTPUT_ENV]), receipt)
                return runner.ProcessResult(124, b"", b"", False)

            with patch.object(runner, "run_process", side_effect=managed_timeout):
                sample = runner._run_sample(
                    run_dir=root / "run",
                    sample_ordinal=1,
                    repository=repository,
                    repository_root=repository_root,
                    treatment="ORDINARY_PROMPT",
                    repetition=1,
                    adapter={"id": "fixture", "command": ["fixture-adapter"]},
                    description=description,
                    timeout_seconds=7,
                    skill_root=ROOT / "skills/review-craft",
                    evidence_root=ROOT / "evals/real-repositories/verifiers",
                    inactivity_warning_seconds=1,
                    inactivity_diagnostic_seconds=2,
                )

        self.assertEqual(sample["status"], "TIMED_OUT")
        self.assertEqual(sample["failureClass"], "TIMEOUT")
        self.assertEqual(sample["lifecycle"]["terminationReason"], "TIMEOUT")
        self.assertEqual(sample["lifecycle"]["processTreeCleanup"], "COMPLETED")
        self.assertIsNotNone(sample["artifacts"]["isolationReceiptSha256"])
        self.assertEqual(
            runner._model_configuration("fixture", description)[
                "timeoutControlSha256"
            ],
            runner.sha256_json(description["timeoutControl"]),
        )

    def test_runner_requires_matching_per_invocation_isolation_receipt(self) -> None:
        repository = self.suite["repositories"][0]
        description = {
            "name": "fixture-adapter",
            "adapterVersion": "fixture-v1",
            "version": "fixture-host-v1",
            "model": "fixture-model",
            "reasoning": "medium",
            "evidenceKind": "REAL_HOST",
            "provider": {"name": "fixture-provider"},
            "isolation": {"fixture": True},
            "isolationReceipt": {
                "protocol": "review-craft.eval-isolation-receipt.v1"
            },
        }
        surface = {
            "capturedAt": "2026-08-22T00:00:00Z",
            "systemFileCount": 2,
            "systemTreeSha256": "1" * 64,
            "userExtensionFileCount": 0,
            "userExtensionTreeSha256": "2" * 64,
        }

        def isolation_receipt(overall: str) -> dict:
            user_state = "DRIFTED" if overall == "USER_EXTENSION_DRIFT" else "MATCHED"
            return {
                "schema": "review-craft.eval-isolation-receipt.v1",
                "availability": "AVAILABLE",
                "policy": {
                    "homeMatchesCodexHome": True,
                    "ignoreUserConfig": True,
                    "ignoreRules": True,
                    "allowCodexHomeExtensions": False,
                },
                "preRun": surface,
                "postStart": surface,
                "postExit": surface,
                "comparison": {
                    "postStartSystemState": "MATCHED",
                    "postStartUserExtensionState": user_state,
                    "postExitSystemState": "MATCHED",
                    "postExitUserExtensionState": user_state,
                    "overall": overall,
                },
                "unavailableReason": None,
            }

        def managed_system_bootstrap_receipt() -> dict:
            receipt = isolation_receipt("SYSTEM_STATE_DRIFT")
            empty_tree = runner.sha256_json([])
            empty_surface = {
                **surface,
                "systemFileCount": 0,
                "systemTreeSha256": empty_tree,
                "userExtensionFileCount": 0,
                "userExtensionTreeSha256": empty_tree,
            }
            receipt["preRun"] = empty_surface
            receipt["postStart"] = empty_surface
            receipt["postExit"] = {
                **empty_surface,
                "systemFileCount": 60,
                "systemTreeSha256": "3" * 64,
            }
            receipt["comparison"]["postExitSystemState"] = "DRIFTED"
            return receipt

        self.assertFalse(
            runner._isolation_receipt_reports_drift(isolation_receipt("MATCHED"))
        )
        self.assertTrue(
            runner._isolation_receipt_reports_drift(
                isolation_receipt("USER_EXTENSION_DRIFT")
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository_root = root / "repository"
            repository_root.mkdir()
            runner.run_git("init", "--quiet", cwd=repository_root)
            runner.run_git("config", "user.name", "Review Craft Tests", cwd=repository_root)
            runner.run_git(
                "config", "user.email", "review-craft-tests@example.invalid", cwd=repository_root
            )
            target = repository_root / repository["scope"][0]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("fixture\n", encoding="utf-8")
            runner.run_git("add", repository["scope"][0], cwd=repository_root)
            runner.run_git("commit", "--quiet", "-m", "fixture", cwd=repository_root)

            for mode in (
                "matched",
                "managed-bootstrap",
                "system-drift",
                "drifted",
                "contradictory",
                "missing",
            ):
                with self.subTest(mode=mode):

                    def completed(
                        _command: list[str],
                        *,
                        cwd: Path,
                        timeout: int,
                        env: dict[str, str],
                        _mode: str = mode,
                    ) -> runner.ProcessResult:
                        self.assertEqual(cwd, ROOT)
                        self.assertEqual(timeout, 7)
                        output_path = Path(_command[_command.index("--output-file") + 1])
                        runner.write_json(output_path, self._output(repository))
                        runner.write_json(
                            Path(env[runner.USAGE_OUTPUT_ENV]),
                            {
                                "inputTokens": 10,
                                "outputTokens": 2,
                                "totalTokens": 12,
                                "toolCalls": 0,
                            },
                        )
                        if _mode != "missing":
                            if _mode == "managed-bootstrap":
                                receipt = managed_system_bootstrap_receipt()
                            else:
                                overall = (
                                    "MATCHED"
                                    if _mode in {"matched", "contradictory"}
                                    else "SYSTEM_STATE_DRIFT"
                                    if _mode == "system-drift"
                                    else "USER_EXTENSION_DRIFT"
                                )
                                receipt = isolation_receipt(overall)
                                if _mode == "system-drift":
                                    receipt["postExit"] = {
                                        **surface,
                                        "systemFileCount": 3,
                                        "systemTreeSha256": "3" * 64,
                                    }
                                    receipt["comparison"][
                                        "postExitSystemState"
                                    ] = "DRIFTED"
                            if _mode == "contradictory":
                                receipt["comparison"][
                                    "postExitUserExtensionState"
                                ] = "DRIFTED"
                            runner.write_json(
                                Path(env[runner.ISOLATION_OUTPUT_ENV]),
                                receipt,
                            )
                        return runner.ProcessResult(0, b"", b"", False)

                    with patch.object(runner, "run_process", side_effect=completed):
                        sample = runner._run_sample(
                            run_dir=root / f"run-{mode}",
                            sample_ordinal=1,
                            repository=repository,
                            repository_root=repository_root,
                            treatment="ORDINARY_PROMPT",
                            repetition=1,
                            adapter={"id": "fixture", "command": ["fixture-adapter"]},
                            description=description,
                            timeout_seconds=7,
                            skill_root=ROOT / "skills/review-craft",
                            evidence_root=ROOT / "evals/real-repositories/verifiers",
                        )

                    if mode == "matched":
                        self.assertEqual(sample["status"], "COMPLETED")
                        self.assertIsNotNone(
                            sample["artifacts"]["isolationReceiptSha256"]
                        )
                    else:
                        self.assertEqual(sample["status"], "FAILED")
                        self.assertEqual(sample["failureClass"], "ARTIFACT_INVALID")
                        self.assertIn("isolation receipt", sample["failureReason"])

    def test_campaign_plan_requires_prepared_managed_system_state(self) -> None:
        empty_tree = runner.sha256_json([])
        description = {
            "isolationPreparation": {
                "protocol": "review-craft.eval-isolation-preparation.v1"
            },
            "isolation": {
                "codexHomeSystemFileCount": 0,
                "codexHomeSystemTreeSha256": empty_tree,
            },
        }
        with self.assertRaisesRegex(
            contracts.RealRepositoryError,
            "prepare-campaign-isolation",
        ):
            runner._require_prepared_adapter_isolation({"model-a": description})
        description["isolation"] = {
            "codexHomeSystemFileCount": 60,
            "codexHomeSystemTreeSha256": "1" * 64,
        }
        runner._require_prepared_adapter_isolation({"model-a": description})

    def test_prepare_campaign_isolation_seals_final_adapter_state(self) -> None:
        adapter_config = {
            "schema": "review-craft.eval-real-repository-adapters.v1",
            "adapters": [{"id": "model-a", "command": ["fixture-adapter"]}],
        }
        isolation = {
            "homeMatchesCodexHome": True,
            "ignoreUserConfig": True,
            "ignoreRules": True,
            "allowCodexHomeExtensions": False,
            "codexHomeSystemFileCount": 60,
            "codexHomeSystemTreeSha256": "1" * 64,
            "codexHomeExtensionFileCount": 0,
            "codexHomeExtensionTreeSha256": runner.sha256_json([]),
        }
        description = {
            "schema": "review-craft.eval-adapter.v6",
            "name": "codex-cli",
            "version": "codex-cli test",
            "model": "gpt-test",
            "reasoning": "high",
            "adapterVersion": "0.6.4",
            "evidenceKind": "REAL_HOST",
            "provider": {
                "name": "fixture",
                "baseUrl": "http://127.0.0.1:8317/v1",
                "wireApi": "responses",
                "requiresOpenAIAuth": True,
                "supportsWebsockets": False,
            },
            "isolation": isolation,
            "isolationReceipt": {
                "protocol": "review-craft.eval-isolation-receipt.v1",
                "transport": "ENV_PATH",
                "environmentVariable": runner.ISOLATION_OUTPUT_ENV,
            },
            "isolationPreparation": {
                "protocol": "review-craft.eval-isolation-preparation.v1",
                "invocation": "APPEND_FLAG",
                "flag": "--prepare-isolation",
                "requiredWhenSystemTreeEmpty": True,
                "networkBoundary": "OWNED_LOOPBACK_BLACKHOLE",
            },
        }
        receipt = {
            "schema": "review-craft.eval-isolation-preparation.v1",
            "status": "MATERIALIZED",
            "hostVersion": description["version"],
            "startedAt": "2026-08-22T00:00:00Z",
            "completedAt": "2026-08-22T00:00:01Z",
            "before": {
                "systemFileCount": 0,
                "systemTreeSha256": runner.sha256_json([]),
                "userExtensionFileCount": 0,
                "userExtensionTreeSha256": runner.sha256_json([]),
            },
            "after": {
                "systemFileCount": 60,
                "systemTreeSha256": "1" * 64,
                "userExtensionFileCount": 0,
                "userExtensionTreeSha256": runner.sha256_json([]),
            },
            "networkBoundary": "OWNED_LOOPBACK_BLACKHOLE",
            "processTermination": "TERMINATED_AFTER_MATERIALIZATION",
            "durationSeconds": 0.5,
            "contentSha256": "0" * 64,
        }
        receipt["contentSha256"] = runner.sha256_json(
            {
                key: value
                for key, value in receipt.items()
                if key != "contentSha256"
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "adapters.json"
            output_path = root / "preparation.json"
            runner.write_json(config_path, adapter_config)
            args = SimpleNamespace(
                adapter_config=str(config_path),
                output=str(output_path),
                timeout_seconds=30,
            )
            with patch.object(
                runner,
                "_describe_adapter",
                return_value=description,
            ), patch.object(
                runner,
                "_prepare_adapter_isolation",
                return_value=receipt,
            ), patch("builtins.print"):
                status = runner.command_prepare_campaign_isolation(args)
            self.assertEqual(status, 0)
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["preparations"][0]["receipt"], receipt)
            self.assertEqual(
                payload["modelConfigurations"][0]["isolationSha256"],
                runner.sha256_json(isolation),
            )
            self.assertEqual(
                payload["contentSha256"],
                runner.sha256_json(
                    {
                        key: value
                        for key, value in payload.items()
                        if key != "contentSha256"
                    }
                ),
            )
            self.assertEqual(
                runner._validate_isolation_preparation_set(
                    payload, adapter_config
                ),
                [],
            )
            drifted = copy.deepcopy(payload)
            drifted["preparations"][0]["receipt"]["after"][
                "systemTreeSha256"
            ] = "9" * 64
            self.assertIn(
                "isolation preparation set contentSha256 mismatch",
                runner._validate_isolation_preparation_set(
                    drifted, adapter_config
                ),
            )

    def test_blinded_adjudication_packets_cover_probes_and_additional_findings(self) -> None:
        campaign, blind = self._campaign(additional_finding=True)
        expected_subjects = contracts.adjudication_subjects(campaign)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite_path = root / "suite.json"
            blind_path = root / "blind.json"
            campaign_path = root / "campaign.json"
            output_dir = root / "adjudication"
            runner.write_json(suite_path, self.suite)
            runner.write_json(blind_path, blind)
            runner.write_json(campaign_path, campaign)
            with patch("builtins.print"):
                status = runner.command_prepare_adjudication(
                    SimpleNamespace(
                        suite=str(suite_path),
                        blind_suite=str(blind_path),
                        campaign=str(campaign_path),
                        output_dir=str(output_dir),
                        adjudicator=["human-a", "human-b", "human-c"],
                    )
                )
            self.assertEqual(status, 0)
            packets = {
                adjudicator: json.loads(
                    (output_dir / f"packet-{adjudicator}.json").read_text(
                        encoding="utf-8"
                    )
                )
                for adjudicator in ("human-a", "human-b", "human-c")
            }
            for packet in packets.values():
                self.assertEqual(len(packet["items"]), len(expected_subjects))
                rendered = json.dumps(packet, sort_keys=True)
                self.assertNotIn("sampleId", rendered)
                self.assertNotIn("modelConfiguration", rendered)
                for treatment in contracts.TREATMENTS:
                    self.assertNotIn(treatment, rendered)
            self.assertTrue(
                {row["itemId"] for row in packets["human-a"]["items"]}.isdisjoint(
                    {row["itemId"] for row in packets["human-b"]["items"]}
                )
            )

            submission_paths = []
            for adjudicator in packets:
                submission_path = output_dir / f"submission-{adjudicator}.json"
                submission = json.loads(submission_path.read_text(encoding="utf-8"))
                for label in submission["labels"]:
                    label["label"] = "CORRECT"
                    label["rationale"] = "Independently verified synthetic fixture."
                    for component in label["components"]:
                        component["label"] = "CORRECT"
                        component["rationale"] = (
                            "This component is supported by the synthetic fixture."
                        )
                if adjudicator == "human-a":
                    inconsistent = copy.deepcopy(submission)
                    inconsistent["labels"][0]["components"][0]["label"] = "INCORRECT"
                    inconsistent["contentSha256"] = contracts.sha256_json(
                        {
                            key: value
                            for key, value in inconsistent.items()
                            if key != "contentSha256"
                        }
                    )
                    errors = runner._validate_adjudication_submission(
                        inconsistent,
                        packet=packets[adjudicator],
                        require_complete=True,
                    )
                    self.assertTrue(
                        any("label differs from component verdicts" in row for row in errors)
                    )
                runner.write_json(submission_path, submission)
                with patch("builtins.print"):
                    status = runner.command_finalize_adjudication_submission(
                        SimpleNamespace(
                            packet=str(output_dir / f"packet-{adjudicator}.json"),
                            submission=str(submission_path),
                        )
                    )
                self.assertEqual(status, 0)
                submission_paths.append(str(submission_path))

            adjudication_path = root / "adjudication.json"
            with patch("builtins.print"):
                status = runner.command_assemble_adjudication(
                    SimpleNamespace(
                        campaign=str(campaign_path),
                        mapping=str(output_dir / "coordinator-mapping.json"),
                        submission=submission_paths,
                        kind="HUMAN",
                        output=str(adjudication_path),
                    )
                )
            self.assertEqual(status, 0)
            adjudication = json.loads(adjudication_path.read_text(encoding="utf-8"))
            self.assertEqual(
                adjudication["schema"],
                "review-craft.eval-real-repository-adjudication.v3",
            )
            self.assertEqual(
                len(adjudication["labels"]), 3 * len(expected_subjects)
            )
            self.assertTrue(
                all(
                    row["status"] == "UNANIMOUS"
                    for row in adjudication["subjectResolutions"]
                )
            )
            self.assertEqual(
                contracts.validate_adjudication(adjudication, campaign), []
            )
            report = contracts.build_stability_report(
                self.suite, campaign, adjudication
            )
            self.assertEqual(report["metrics"]["humanAgreement"]["value"], 1)
            self.assertEqual(
                report["metrics"]["adjudicatorAgreement"]["value"], 1
            )

            agent_adjudication_path = root / "agent-adjudication.json"
            with patch("builtins.print"):
                status = runner.command_assemble_adjudication(
                    SimpleNamespace(
                        campaign=str(campaign_path),
                        mapping=str(output_dir / "coordinator-mapping.json"),
                        submission=submission_paths,
                        kind="AGENT_ASSISTED",
                        output=str(agent_adjudication_path),
                    )
                )
            self.assertEqual(status, 0)
            agent_adjudication = json.loads(
                agent_adjudication_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                {row["kind"] for row in agent_adjudication["adjudicators"]},
                {"AGENT_ASSISTED"},
            )
            agent_report = contracts.build_stability_report(
                self.suite, campaign, agent_adjudication
            )
            self.assertIsNone(agent_report["metrics"]["humanAgreement"]["value"])
            self.assertEqual(
                agent_report["metrics"]["adjudicatorAgreement"]["value"], 1
            )
            self.assertIn(
                "adjudication is agent-assisted, not independent human adjudication",
                agent_report["limitations"],
            )

            split = copy.deepcopy(adjudication)
            split_label = split["labels"][0]
            split_label["components"][0]["label"] = "INCORRECT"
            split_label["components"][0]["rationale"] = (
                "Synthetic disagreement for split-resolution coverage."
            )
            split_label["label"] = "INCORRECT"
            split_label["rationale"] = "One material component is incorrect."
            split["subjectResolutions"] = contracts.build_adjudication_resolutions(
                split["labels"]
            )
            split["contentSha256"] = contracts.sha256_json(
                {
                    key: value
                    for key, value in split.items()
                    if key != "contentSha256"
                }
            )
            self.assertEqual(contracts.validate_adjudication(split, campaign), [])
            split_resolution = next(
                row
                for row in split["subjectResolutions"]
                if row["sampleId"] == split_label["sampleId"]
                and row["subjectType"] == split_label["subjectType"]
                and row["subjectKey"] == split_label["subjectKey"]
            )
            self.assertEqual(split_resolution["status"], "SPLIT")
            self.assertIsNone(split_resolution["resolvedLabel"])

            unresolved_split = copy.deepcopy(adjudication)
            additional_label = next(
                row
                for row in unresolved_split["labels"]
                if row["subjectType"] == "ADDITIONAL_FINDING"
            )
            additional_label["components"][0]["label"] = "UNRESOLVED"
            additional_label["components"][0]["rationale"] = (
                "Synthetic unresolved component for strict-resolution coverage."
            )
            additional_label["label"] = "UNRESOLVED"
            additional_label["rationale"] = "One material component remains unresolved."
            unresolved_split["subjectResolutions"] = (
                contracts.build_adjudication_resolutions(unresolved_split["labels"])
            )
            unresolved_split["contentSha256"] = contracts.sha256_json(
                {
                    key: value
                    for key, value in unresolved_split.items()
                    if key != "contentSha256"
                }
            )
            self.assertEqual(
                contracts.validate_adjudication(unresolved_split, campaign), []
            )
            original_false_positives = contracts._adjudication_metrics(adjudication)[1]
            unresolved_false_positives = contracts._adjudication_metrics(
                unresolved_split
            )[1]
            self.assertEqual(unresolved_false_positives[0], 0)
            self.assertEqual(
                unresolved_false_positives[1],
                original_false_positives[1] - 1,
            )

            mixed = copy.deepcopy(agent_adjudication)
            mixed["adjudicators"][0]["kind"] = "HUMAN"
            mixed["contentSha256"] = contracts.sha256_json(
                {
                    key: value
                    for key, value in mixed.items()
                    if key != "contentSha256"
                }
            )
            errors = contracts.validate_adjudication(mixed, campaign)
            self.assertIn("adjudication mixes adjudicator kinds", errors)

    def test_stability_report_computes_repeated_metrics_without_overclaiming(self) -> None:
        campaign, _blind = self._campaign()
        report = contracts.build_stability_report(self.suite, campaign)

        self.assertEqual(report["status"], "PARTIAL")
        self.assertEqual(report["coverage"]["repositories"], 1)
        self.assertEqual(report["coverage"]["modelConfigurations"], 2)
        self.assertEqual(report["coverage"]["minimumRepetitions"], 3)
        self.assertEqual(report["metrics"]["findingOverlap"]["value"], 1)
        self.assertEqual(report["metrics"]["rootCauseOverlap"]["value"], 1)
        self.assertEqual(report["metrics"]["rootCauseIdentityOverlap"]["value"], 1)
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

    def test_oracle_assessment_separates_response_validity_from_exact_recall(self) -> None:
        campaign, _blind = self._campaign()
        campaign["samples"] = campaign["samples"][:3]
        real_probe_id = self.suite["repositories"][0]["probes"][0]["id"]
        for index, sample in enumerate(campaign["samples"]):
            response = next(
                row
                for row in sample["output"]["probes"]
                if row["probeId"] == real_probe_id
            )
            if index == 0:
                response.update(
                    {
                        "disposition": "NOT_RAISED",
                        "decision": None,
                        "severity": None,
                        "rootCauseKey": None,
                    }
                )
            else:
                response["rootCauseKey"] = f"alternative-root-{index}"
            sample["artifacts"]["outputSha256"] = contracts.sha256_json(
                sample["output"]
            )
        campaign["contentSha256"] = contracts.sha256_json(
            {key: value for key, value in campaign.items() if key != "contentSha256"}
        )
        first_sample_id = campaign["samples"][0]["sampleId"]
        adjudication = self._component_adjudication(
            campaign,
            incorrect_subjects={(first_sample_id, real_probe_id)},
        )
        self.assertEqual(contracts.validate_adjudication(adjudication, campaign), [])

        assessment = contracts.build_oracle_assessment_template(
            self.suite,
            campaign,
            adjudication,
            verifier_id="oracle-verifier",
            verifier_kind="HUMAN",
        )
        self.assertEqual(
            contracts.validate_oracle_assessment(
                assessment,
                self.suite,
                campaign,
                adjudication,
                require_complete=False,
            ),
            [],
        )
        incomplete_errors = contracts.validate_oracle_assessment(
            assessment, self.suite, campaign, adjudication
        )
        self.assertTrue(
            any("classification is incomplete" in row for row in incomplete_errors)
        )

        invalid_exact = copy.deepcopy(assessment)
        invalid_exact["status"] = "FINAL"
        for index, row in enumerate(invalid_exact["assessments"]):
            row["classification"] = (
                "EXACT_ORACLE_MATCH"
                if index == 0
                else "ALTERNATIVE_VALID_FINDING"
            )
            row["rationale"] = "Synthetic post-blind oracle comparison."
        invalid_exact["contentSha256"] = contracts.sha256_json(
            {
                key: value
                for key, value in invalid_exact.items()
                if key != "contentSha256"
            }
        )
        invalid_errors = contracts.validate_oracle_assessment(
            invalid_exact, self.suite, campaign, adjudication
        )
        self.assertTrue(
            any("cannot classify an unraised response" in row for row in invalid_errors)
        )

        for index, row in enumerate(assessment["assessments"]):
            row["classification"] = (
                "MISSED" if index == 0 else "ALTERNATIVE_VALID_FINDING"
            )
            row["rationale"] = "Synthetic post-blind oracle comparison."
        assessment["status"] = "FINAL"
        assessment["contentSha256"] = contracts.sha256_json(
            {
                key: value
                for key, value in assessment.items()
                if key != "contentSha256"
            }
        )
        self.assertEqual(
            contracts.validate_oracle_assessment(
                assessment, self.suite, campaign, adjudication
            ),
            [],
        )

        drifted = copy.deepcopy(assessment)
        drifted["assessments"][0]["responseValidity"] = "CORRECT"
        drifted["contentSha256"] = contracts.sha256_json(
            {key: value for key, value in drifted.items() if key != "contentSha256"}
        )
        drift_errors = contracts.validate_oracle_assessment(
            drifted, self.suite, campaign, adjudication
        )
        self.assertTrue(any("responseValidity mismatch" in row for row in drift_errors))

        report = contracts.build_stability_report(
            self.suite, campaign, adjudication, assessment
        )
        self.assertEqual(
            report["schema"], "review-craft.eval-real-repository-stability.v2"
        )
        self.assertEqual(report["coverage"]["oracleSubjects"], 3)
        self.assertEqual(report["coverage"]["resolvedOracleSubjects"], 3)
        self.assertEqual(report["metrics"]["responseValidityRate"]["value"], 2 / 3)
        self.assertEqual(report["metrics"]["responseResolutionRate"]["value"], 1)
        self.assertEqual(report["metrics"]["exactOracleRecall"]["value"], 0)
        self.assertEqual(
            report["metrics"]["alternativeValidFindingRate"]["value"], 2 / 3
        )
        self.assertEqual(report["metrics"]["oracleMissRate"]["value"], 1 / 3)
        self.assertEqual(report["metrics"]["oracleResolutionRate"]["value"], 1)
        self.assertIsNone(report["metrics"]["oracleRootCauseOverlap"]["value"])
        self.assertEqual(
            contracts.validate_stability_report(
                report,
                self.suite,
                campaign,
                adjudication,
                assessment,
            ),
            [],
        )
        agent_verified = copy.deepcopy(assessment)
        agent_verified["verifier"]["kind"] = "AGENT_ASSISTED"
        agent_verified["contentSha256"] = contracts.sha256_json(
            {
                key: value
                for key, value in agent_verified.items()
                if key != "contentSha256"
            }
        )
        agent_report = contracts.build_stability_report(
            self.suite, campaign, adjudication, agent_verified
        )
        self.assertIn(
            "oracle assessment is agent-assisted, not human-verified",
            agent_report["limitations"],
        )

    def test_oracle_assessment_rejects_semantically_invalid_campaign(self) -> None:
        campaign, _blind = self._campaign()
        campaign["samples"] = campaign["samples"][:3]
        campaign["samples"][0]["sourceMutationDetected"] = True
        campaign["contentSha256"] = contracts.sha256_json(
            {key: value for key, value in campaign.items() if key != "contentSha256"}
        )
        adjudication = self._component_adjudication(campaign)

        with self.assertRaisesRegex(
            contracts.RealRepositoryError, "completed after source mutation"
        ):
            contracts.build_oracle_assessment_template(
                self.suite,
                campaign,
                adjudication,
                verifier_id="oracle-verifier",
                verifier_kind="HUMAN",
            )

    def test_oracle_assessment_cli_round_trip(self) -> None:
        campaign, _blind = self._campaign()
        campaign["samples"] = campaign["samples"][:3]
        campaign["contentSha256"] = contracts.sha256_json(
            {key: value for key, value in campaign.items() if key != "contentSha256"}
        )
        adjudication = self._component_adjudication(campaign)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite_path = root / "suite.json"
            campaign_path = root / "campaign.json"
            adjudication_path = root / "adjudication.json"
            assessment_path = root / "oracle-assessment.json"
            runner.write_json(suite_path, self.suite)
            runner.write_json(campaign_path, campaign)
            runner.write_json(adjudication_path, adjudication)
            with patch("builtins.print"):
                status = runner.command_prepare_oracle_assessment(
                    SimpleNamespace(
                        suite=str(suite_path),
                        campaign=str(campaign_path),
                        adjudication=str(adjudication_path),
                        verifier_id="oracle-verifier",
                        kind="HUMAN",
                        output=str(assessment_path),
                    )
                )
            self.assertEqual(status, 0)
            assessment = json.loads(assessment_path.read_text(encoding="utf-8"))
            for row in assessment["assessments"]:
                row["classification"] = "EXACT_ORACLE_MATCH"
                row["rationale"] = "Synthetic exact-match verification."
            runner.write_json(assessment_path, assessment)
            with patch("builtins.print"):
                status = runner.command_finalize_oracle_assessment(
                    SimpleNamespace(
                        suite=str(suite_path),
                        campaign=str(campaign_path),
                        adjudication=str(adjudication_path),
                        assessment=str(assessment_path),
                    )
                )
            self.assertEqual(status, 0)
            with patch("builtins.print"):
                status = runner.command_validate_oracle_assessment(
                    SimpleNamespace(
                        suite=str(suite_path),
                        campaign=str(campaign_path),
                        adjudication=str(adjudication_path),
                        assessment=str(assessment_path),
                    )
                )
            self.assertEqual(status, 0)
            finalized = json.loads(assessment_path.read_text(encoding="utf-8"))
            report = contracts.build_stability_report(
                self.suite, campaign, adjudication, finalized
            )
            self.assertEqual(report["metrics"]["exactOracleRecall"]["value"], 1)
            self.assertEqual(
                report["metrics"]["oracleRootCauseOverlap"]["value"], 1
            )

    def test_normalized_root_cause_identity_ignores_free_text_key_drift(self) -> None:
        campaign, _blind = self._campaign()
        for sample in campaign["samples"]:
            for probe in sample["output"]["probes"]:
                if probe["disposition"] == "VALIDATED" and probe["severity"] is not None:
                    probe["rootCauseKey"] = "free-text-" + sample["sampleId"]
        campaign["contentSha256"] = contracts.sha256_json(
            {key: value for key, value in campaign.items() if key != "contentSha256"}
        )
        report = contracts.build_stability_report(self.suite, campaign)
        self.assertEqual(report["metrics"]["rootCauseOverlap"]["value"], 0)
        self.assertEqual(
            report["metrics"]["rootCauseIdentityOverlap"]["value"], 1
        )

        legacy = copy.deepcopy(report)
        del legacy["metrics"]["rootCauseIdentityOverlap"]
        legacy["contentSha256"] = contracts.sha256_json(
            {key: value for key, value in legacy.items() if key != "contentSha256"}
        )
        self.assertEqual(
            contracts.validate_stability_report(legacy, self.suite, campaign), []
        )

    def test_additional_finding_identity_uses_evidence_location(self) -> None:
        campaign, _blind = self._campaign()
        repository = self.suite["repositories"][0]
        for index, sample in enumerate(campaign["samples"]):
            sample["output"]["additionalFindings"] = [
                {
                    "findingId": f"finding-{index}",
                    "title": f"Free text title {index}",
                    "decision": "CLEAN_UP",
                    "severity": "P3",
                    "rootCauseKey": f"free-text-root-{index}",
                    "locations": [
                        {
                            "path": repository["scope"][0],
                            "lineStart": 1,
                            "lineEnd": 1,
                        }
                    ],
                    "evidence": [
                        {
                            "claim": f"Free text evidence {index}",
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
        campaign["contentSha256"] = contracts.sha256_json(
            {key: value for key, value in campaign.items() if key != "contentSha256"}
        )
        report = contracts.build_stability_report(self.suite, campaign)
        self.assertLess(report["metrics"]["rootCauseOverlap"]["value"], 1)
        self.assertEqual(
            report["metrics"]["rootCauseIdentityOverlap"]["value"], 1
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
