from __future__ import annotations

import copy
import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
campaign_runtime = importlib.import_module("real_repository_campaign")
contracts = importlib.import_module("real_repository_contracts")
runner = importlib.import_module("real_repository_benchmark")


SUITE_PATH = ROOT / "evals/specs/real-repositories.json"
CURRENT_ROOT = ROOT / "evals/real-repositories/current"


class RealRepositoryCampaignHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.suite = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
        self.blind = json.loads(
            (CURRENT_ROOT / "blind-suite.json").read_text(encoding="utf-8")
        )
        self.receipt = json.loads(
            (CURRENT_ROOT / "materialization.json").read_text(encoding="utf-8")
        )
        self.adapter_config = {
            "schema": "review-craft.eval-real-repository-adapters.v1",
            "adapters": [
                {"id": "fixture-standard", "command": ["fixture-standard"]},
                {"id": "fixture-assured", "command": ["fixture-assured"]},
            ],
        }
        self.descriptions = {
            "fixture-standard": self._description("standard", "high"),
            "fixture-assured": self._description("assured", "xhigh"),
        }
        self.models = [
            runner._model_configuration(adapter["id"], self.descriptions[adapter["id"]])
            for adapter in self.adapter_config["adapters"]
        ]

    def _description(self, model: str, reasoning: str) -> dict:
        return {
            "schema": "review-craft.eval-adapter.v5",
            "name": "fixture-adapter",
            "version": "fixture-host-v1",
            "model": f"fixture-{model}",
            "reasoning": reasoning,
            "adapterVersion": "fixture-v1",
            "evidenceKind": "REAL_HOST",
            "provider": {"name": "fixture-provider"},
            "isolation": {"fixture": model},
        }

    def _plan(
        self,
        *,
        repositories: list[str] | None = None,
        repetitions: int = 3,
        token_ceiling: int = 60_000_000,
        max_unknown_usage_samples: int = 1,
        max_timed_out_samples_per_model_profile: int = 1,
        max_artifact_invalid_samples: int = 1,
        max_recovered_inactivity_samples_per_model_profile: int = 2,
        sample_input_token_ceiling: int = 1_250_000,
        sample_token_ceiling: int = 1_500_000,
        shard_input_token_ceiling: int = 7_000_000,
        shard_token_ceiling: int = 8_000_000,
        timeout_overrides: list[dict] | None = None,
    ) -> dict:
        repository_ids = repositories or [row["id"] for row in self.suite["repositories"]]
        return campaign_runtime.build_campaign_plan(
            source_suite=self.suite,
            blind_suite=self.blind,
            materialization=self.receipt,
            adapter_config=self.adapter_config,
            model_configurations=self.models,
            campaign_id="fixture-campaign-144",
            repository_ids=repository_ids,
            treatments=list(contracts.TREATMENTS),
            repetitions=repetitions,
            sample_timeout_seconds=1800,
            soft_wall_time_seconds=64800,
            hard_wall_time_seconds=86400,
            hard_reported_token_ceiling=token_ceiling,
            max_consecutive_infrastructure_failures=2,
            hard_reported_input_token_ceiling_per_sample=(
                sample_input_token_ceiling
            ),
            hard_reported_token_ceiling_per_sample=sample_token_ceiling,
            hard_reported_input_token_ceiling_per_shard=(
                shard_input_token_ceiling
            ),
            hard_reported_token_ceiling_per_shard=shard_token_ceiling,
            max_unknown_usage_samples=max_unknown_usage_samples,
            max_timed_out_samples_per_model_profile=(
                max_timed_out_samples_per_model_profile
            ),
            max_artifact_invalid_samples=max_artifact_invalid_samples,
            max_recovered_inactivity_samples_per_model_profile=(
                max_recovered_inactivity_samples_per_model_profile
            ),
            timeout_overrides=timeout_overrides,
        )

    def _output(self, repository: dict) -> dict:
        dispositions = {
            "REAL_FINDING": ("VALIDATED", "CLEAN_UP", "P2", "fixture-root"),
            "KEEP": ("VALIDATED", "KEEP", None, "fixture-keep"),
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
                    "rationale": "Synthetic campaign scheduler rehearsal.",
                }
            )
        return {
            "schema": "review-craft.eval-real-repository-output.v1",
            "repositoryId": repository["id"],
            "score": {"status": "FINAL", "value": 88},
            "probes": probes,
            "additionalFindings": [],
            "summary": "Synthetic campaign scheduler rehearsal.",
        }

    def _sample(self, **kwargs: object) -> dict:
        repository = kwargs["repository"]
        adapter = kwargs["adapter"]
        treatment = kwargs["treatment"]
        repetition = kwargs["repetition"]
        description = kwargs["description"]
        assert isinstance(repository, dict)
        assert isinstance(adapter, dict)
        assert isinstance(treatment, str)
        assert isinstance(repetition, int)
        assert isinstance(description, dict)
        oracle_repository = next(
            row for row in self.suite["repositories"] if row["id"] == repository["id"]
        )
        output = self._output(oracle_repository)
        return {
            "sampleId": campaign_runtime.sample_id(
                repository["id"], treatment, adapter["id"], repetition
            ),
            "repositoryId": repository["id"],
            "treatment": treatment,
            "modelConfiguration": runner._model_configuration(
                adapter["id"], description
            ),
            "repetition": repetition,
            "status": "COMPLETED",
            "durationSeconds": 0.01,
            "usage": {
                "inputTokens": 100,
                "outputTokens": 20,
                "totalTokens": 120,
                "toolCalls": 2,
            },
            "sourceMutationDetected": False,
            "output": output,
            "failureReason": None,
            "failureClass": None,
            "artifacts": {
                "promptSha256": "1" * 64,
                "stdoutSha256": "2" * 64,
                "stderrSha256": "3" * 64,
                "usageSha256": "4" * 64,
                "outputSha256": contracts.sha256_json(output),
                "toolTraceSha256": "5" * 64,
            },
        }

    def _failed_sample(self, failure_class: str, **kwargs: object) -> dict:
        sample = self._sample(**kwargs)
        sample.update(
            {
                "status": "FAILED",
                "usage": {
                    "inputTokens": None,
                    "outputTokens": None,
                    "totalTokens": None,
                    "toolCalls": None,
                },
                "output": None,
                "failureReason": "Synthetic infrastructure failure.",
                "failureClass": failure_class,
            }
        )
        sample["artifacts"]["outputSha256"] = None
        return sample

    def _timed_out_sample(self, **kwargs: object) -> dict:
        sample = self._failed_sample("TIMEOUT", **kwargs)
        sample["status"] = "TIMED_OUT"
        sample["failureReason"] = "Synthetic timeout."
        return sample

    def _artifact_invalid_sample(self, **kwargs: object) -> dict:
        sample = self._sample(**kwargs)
        sample.update(
            {
                "status": "FAILED",
                "output": None,
                "failureReason": "Synthetic normalized output failure.",
                "failureClass": "ARTIFACT_INVALID",
            }
        )
        sample["artifacts"]["outputSha256"] = None
        return sample

    def _recovered_inactivity_sample(self, **kwargs: object) -> dict:
        sample = self._sample(**kwargs)
        sample["lifecycle"] = {
            "schema": "review-craft.eval-progress.v1",
            "availability": "AVAILABLE",
            "startedAt": "2026-08-21T00:00:00Z",
            "threadStartedAt": "2026-08-21T00:00:01Z",
            "turnStartedAt": "2026-08-21T00:00:02Z",
            "firstItemAt": "2026-08-21T00:10:03Z",
            "lastEventAt": "2026-08-21T00:12:00Z",
            "lastEventType": "turn.completed",
            "eventCount": 4,
            "itemEventCount": 1,
            "timeToFirstItemSeconds": 601.0,
            "inactivityWarningSeconds": 300,
            "inactivityDiagnosticSeconds": 600,
            "inactivityState": "RECOVERED_DIAGNOSTIC",
            "inactivityAgeSeconds": 0.0,
            "maximumPreItemInactivitySeconds": 601.0,
            "diagnosticCapturedAt": "2026-08-21T00:10:02Z",
            "processAliveWhenDiagnosticCaptured": True,
            "terminationReason": "PROCESS_EXIT",
            "processTreeCleanup": "NOT_REQUIRED",
            "unavailableReason": None,
        }
        return sample

    def _write_inputs(
        self,
        root: Path,
        plan: dict,
        adapter_config: dict | None = None,
    ) -> SimpleNamespace:
        adapter_path = root / "adapters.json"
        plan_path = root / "plan-input.json"
        runner.write_json(adapter_path, adapter_config or self.adapter_config)
        runner.write_json(plan_path, plan)
        workspace = root / "workspace"
        for row in self.receipt["repositories"]:
            (workspace / row["checkout"]).mkdir(parents=True)
        return SimpleNamespace(
            suite=str(SUITE_PATH),
            blind_suite=str(CURRENT_ROOT / "blind-suite.json"),
            materialization=str(CURRENT_ROOT / "materialization.json"),
            workspace_root=str(workspace),
            adapter_config=str(adapter_path),
            plan=str(plan_path),
            run_dir=str(root / "run"),
            budget_ledger=str(root / "budget-ledger.json"),
            skill_root=str(ROOT / "skills/review-craft"),
            evidence_root=str(ROOT / "evals/real-repositories/verifiers"),
            shard=None,
            resume=False,
            allow_partial=True,
        )

    def _repository_state(self, repository_root: Path) -> dict[str, str]:
        receipt_row = next(
            row for row in self.receipt["repositories"] if row["id"] == repository_root.name
        )
        suite_row = next(
            row for row in self.suite["repositories"] if row["id"] == repository_root.name
        )
        return {"head": suite_row["revision"], "tree": receipt_row["tree"], "status": ""}

    def test_full_plan_is_deterministic_and_contains_144_unique_cells(self) -> None:
        first = self._plan()
        second = self._plan()
        self.assertEqual(first, second)
        self.assertEqual(campaign_runtime.validate_campaign_plan(first, self.suite, self.blind), [])
        self.assertTrue(first["selection"]["fullMatrix"])
        self.assertEqual(len(first["samples"]), 144)
        self.assertEqual(len({row["sampleId"] for row in first["samples"]}), 144)
        self.assertEqual(len({row["promptSha256"] for row in first["samples"]}), 24)
        self.assertEqual({row["timeoutSeconds"] for row in first["samples"]}, {1800})
        self.assertEqual(len({row["shardId"] for row in first["samples"]}), 8)
        self.assertEqual(first["budgets"]["maxUnknownUsageSamples"], 1)
        self.assertEqual(
            first["budgets"]["maxTimedOutSamplesPerModelProfile"], 1
        )
        self.assertEqual(first["budgets"]["maxArtifactInvalidSamples"], 1)
        self.assertEqual(first["budgets"]["inactivityWarningSeconds"], 300)
        self.assertEqual(first["budgets"]["inactivityDiagnosticSeconds"], 600)
        self.assertEqual(
            first["budgets"]["maxRecoveredInactivitySamplesPerModelProfile"], 2
        )
        self.assertEqual(
            first["budgets"]["hardReportedInputTokenCeilingPerSample"],
            1_250_000,
        )
        self.assertEqual(
            first["budgets"]["hardReportedTokenCeilingPerRepositoryShard"],
            8_000_000,
        )

        reordered = copy.deepcopy(first)
        reordered["samples"][0], reordered["samples"][1] = (
            reordered["samples"][1],
            reordered["samples"][0],
        )
        campaign_runtime.seal(reordered)
        self.assertIn(
            "campaign plan samples do not match the deterministic matrix",
            campaign_runtime.validate_campaign_plan(reordered, self.suite, self.blind),
        )

    def test_timeout_policy_is_content_bound_and_uses_specific_precedence(
        self,
    ) -> None:
        plan = self._plan(
            timeout_overrides=[
                {
                    "modelConfigurationId": "fixture-standard",
                    "treatment": "REVIEW_CRAFT_EVIDENCE_LOOP",
                    "timeoutSeconds": 2400,
                },
                {
                    "modelConfigurationId": "fixture-standard",
                    "timeoutSeconds": 1200,
                },
            ]
        )
        self.assertEqual(
            plan["timeoutPolicy"],
            {
                "defaultSeconds": 1800,
                "overrides": [
                    {
                        "modelConfigurationId": "fixture-standard",
                        "timeoutSeconds": 1200,
                    },
                    {
                        "modelConfigurationId": "fixture-standard",
                        "timeoutSeconds": 2400,
                        "treatment": "REVIEW_CRAFT_EVIDENCE_LOOP",
                    },
                ],
            },
        )
        timeouts = {
            (row["modelConfigurationId"], row["treatment"]): row["timeoutSeconds"]
            for row in plan["samples"]
        }
        self.assertEqual(timeouts[("fixture-standard", "RISK_LENS_REVIEW")], 1200)
        self.assertEqual(
            timeouts[("fixture-standard", "REVIEW_CRAFT_EVIDENCE_LOOP")],
            2400,
        )
        self.assertEqual(timeouts[("fixture-assured", "RISK_LENS_REVIEW")], 1800)
        self.assertEqual(
            campaign_runtime.validate_campaign_plan(plan, self.suite, self.blind),
            [],
        )

        tampered = copy.deepcopy(plan)
        tampered["samples"][0]["timeoutSeconds"] = 1
        campaign_runtime.seal(tampered)
        self.assertIn(
            "campaign plan samples do not match the deterministic matrix",
            campaign_runtime.validate_campaign_plan(tampered, self.suite, self.blind),
        )

    def test_timeout_policy_rejects_unknown_duplicate_and_noncanonical_overrides(
        self,
    ) -> None:
        plan = self._plan(
            timeout_overrides=[
                {
                    "modelConfigurationId": "fixture-standard",
                    "timeoutSeconds": 1200,
                },
                {
                    "modelConfigurationId": "fixture-standard",
                    "treatment": "RISK_LENS_REVIEW",
                    "timeoutSeconds": 1500,
                },
            ]
        )

        unknown = copy.deepcopy(plan)
        unknown["timeoutPolicy"]["overrides"][0]["modelConfigurationId"] = (
            "missing-profile"
        )
        campaign_runtime.seal(unknown)
        self.assertTrue(
            any(
                "unknown model configurations" in error
                for error in campaign_runtime.validate_campaign_plan(
                    unknown, self.suite, self.blind
                )
            )
        )

        duplicate = copy.deepcopy(plan)
        duplicate["timeoutPolicy"]["overrides"].append(
            {
                "modelConfigurationId": "fixture-standard",
                "timeoutSeconds": 1300,
            }
        )
        campaign_runtime.seal(duplicate)
        self.assertIn(
            "campaign timeout policy contains duplicate selectors",
            campaign_runtime.validate_campaign_plan(
                duplicate, self.suite, self.blind
            ),
        )

        noncanonical = copy.deepcopy(plan)
        noncanonical["timeoutPolicy"]["overrides"].reverse()
        campaign_runtime.seal(noncanonical)
        self.assertIn(
            "campaign timeout policy overrides must use canonical order",
            campaign_runtime.validate_campaign_plan(
                noncanonical, self.suite, self.blind
            ),
        )

        with self.assertRaisesRegex(
            contracts.RealRepositoryError,
            "selector is duplicated",
        ):
            self._plan(
                timeout_overrides=[
                    {
                        "modelConfigurationId": "fixture-standard",
                        "timeoutSeconds": 1200,
                    },
                    {
                        "modelConfigurationId": "fixture-standard",
                        "timeoutSeconds": 1300,
                    },
                ]
            )

    def test_timeout_override_cli_contract_is_unambiguous(self) -> None:
        args = runner.build_parser().parse_args(
            [
                "plan-campaign",
                "--blind-suite",
                "blind.json",
                "--materialization",
                "materialization.json",
                "--adapter-config",
                "adapters.json",
                "--output",
                "plan.json",
                "--campaign-id",
                "fixture-campaign",
                "--timeout-override",
                "fixture-standard",
                "1200",
                "--treatment-timeout-override",
                "fixture-standard",
                "REVIEW_CRAFT_EVIDENCE_LOOP",
                "2400",
            ]
        )
        self.assertEqual(
            runner._campaign_timeout_overrides(args),
            [
                {
                    "modelConfigurationId": "fixture-standard",
                    "timeoutSeconds": 1200,
                },
                {
                    "modelConfigurationId": "fixture-standard",
                    "treatment": "REVIEW_CRAFT_EVIDENCE_LOOP",
                    "timeoutSeconds": 2400,
                },
            ],
        )
        args.timeout_override[0][1] = "0"
        with self.assertRaisesRegex(
            contracts.RealRepositoryError,
            "positive integer",
        ):
            runner._campaign_timeout_overrides(args)

    def test_runner_consumes_expanded_sample_timeouts_without_inference(self) -> None:
        repository_id = self.suite["repositories"][0]["id"]
        plan = self._plan(
            repositories=[repository_id],
            repetitions=1,
            timeout_overrides=[
                {
                    "modelConfigurationId": "fixture-standard",
                    "timeoutSeconds": 1200,
                },
                {
                    "modelConfigurationId": "fixture-standard",
                    "treatment": "REVIEW_CRAFT_EVIDENCE_LOOP",
                    "timeoutSeconds": 2400,
                },
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._write_inputs(root, plan)
            with (
                patch.object(
                    runner,
                    "_describe_adapter",
                    side_effect=lambda _command, adapter_id: self.descriptions[
                        adapter_id
                    ],
                ),
                patch.object(
                    runner,
                    "_repository_state",
                    side_effect=self._repository_state,
                ),
                patch.object(
                    runner,
                    "_run_sample",
                    side_effect=self._sample,
                ) as run_sample,
            ):
                self.assertEqual(runner.command_run_campaign_plan(args), 0)
        observed = {
            (
                call.kwargs["adapter"]["id"],
                call.kwargs["treatment"],
            ): call.kwargs["timeout_seconds"]
            for call in run_sample.call_args_list
        }
        self.assertEqual(observed[("fixture-standard", "RISK_LENS_REVIEW")], 1200)
        self.assertEqual(
            observed[("fixture-standard", "REVIEW_CRAFT_EVIDENCE_LOOP")],
            2400,
        )
        self.assertEqual(observed[("fixture-assured", "RISK_LENS_REVIEW")], 1800)

    def test_legacy_plan_and_empty_state_remain_readable(self) -> None:
        plan = self._plan()
        previous = copy.deepcopy(plan)
        del previous["budgets"]["maxArtifactInvalidSamples"]
        campaign_runtime.seal(previous)
        self.assertEqual(
            campaign_runtime.validate_campaign_plan(
                previous, self.suite, self.blind
            ),
            [],
        )

        legacy = copy.deepcopy(previous)
        del legacy["budgets"]["maxUnknownUsageSamples"]
        del legacy["budgets"]["maxTimedOutSamplesPerModelProfile"]
        del legacy["budgets"]["inactivityWarningSeconds"]
        del legacy["budgets"]["inactivityDiagnosticSeconds"]
        del legacy["budgets"]["maxRecoveredInactivitySamplesPerModelProfile"]
        for key in (
            "hardReportedInputTokenCeilingPerSample",
            "hardReportedTokenCeilingPerSample",
            "hardReportedInputTokenCeilingPerRepositoryShard",
            "hardReportedTokenCeilingPerRepositoryShard",
        ):
            del legacy["budgets"][key]
        for sample in legacy["samples"]:
            del sample["promptSha256"]
        campaign_runtime.seal(legacy)
        self.assertEqual(
            campaign_runtime.validate_campaign_plan(legacy, self.suite, self.blind),
            [],
        )
        execution_errors = campaign_runtime.validate_campaign_plan_execution_safety(
            legacy
        )
        self.assertTrue(any("current execution budgets" in row for row in execution_errors))
        self.assertTrue(any("prompt hashes" in row for row in execution_errors))
        self.assertEqual(
            campaign_runtime.validate_campaign_plan_execution_safety(plan), []
        )

        partial_prompt_binding = copy.deepcopy(legacy)
        partial_prompt_binding["samples"][0]["promptSha256"] = "0" * 64
        campaign_runtime.seal(partial_prompt_binding)
        self.assertIn(
            "campaign plan prompt hashes must be declared for every sample",
            campaign_runtime.validate_campaign_plan(
                partial_prompt_binding, self.suite, self.blind
            ),
        )

        partial = copy.deepcopy(legacy)
        partial["budgets"]["maxUnknownUsageSamples"] = 1
        campaign_runtime.seal(partial)
        self.assertIn(
            "campaign plan cumulative failure budgets must be declared together",
            campaign_runtime.validate_campaign_plan(partial, self.suite, self.blind),
        )

        partial_inactivity = copy.deepcopy(legacy)
        partial_inactivity["budgets"]["inactivityWarningSeconds"] = 300
        campaign_runtime.seal(partial_inactivity)
        self.assertIn(
            "campaign plan inactivity budgets must be declared together",
            campaign_runtime.validate_campaign_plan(
                partial_inactivity, self.suite, self.blind
            ),
        )

        partial_cost = copy.deepcopy(legacy)
        partial_cost["budgets"]["hardReportedTokenCeilingPerSample"] = 100
        campaign_runtime.seal(partial_cost)
        self.assertIn(
            "campaign plan per-sample and per-shard token budgets must be declared together",
            campaign_runtime.validate_campaign_plan(
                partial_cost, self.suite, self.blind
            ),
        )

        state = campaign_runtime.new_run_state(
            plan=legacy,
            campaign_id=legacy["campaignId"],
            shard_id="ALL",
            now="2026-08-21T00:00:00Z",
        )
        del state["timedOutSamplesByModelProfile"]
        self.assertNotIn("artifactInvalidSamples", state)
        campaign_runtime.seal(state)
        campaign = {
            "campaignId": legacy["campaignId"],
            "samples": [],
            "contentSha256": None,
        }
        self.assertEqual(
            campaign_runtime.validate_run_state(state, legacy, campaign), []
        )
        ledger = campaign_runtime.new_budget_ledger(
            legacy, now="2026-08-21T00:00:00Z"
        )
        del ledger["timedOutSamplesByModelProfileByShard"]
        self.assertNotIn("artifactInvalidSamplesByShard", ledger)
        campaign_runtime.seal(ledger)
        self.assertEqual(
            campaign_runtime.validate_budget_ledger(ledger, legacy), []
        )

    def test_run_plan_rejects_validation_only_legacy_plan_before_spending(self) -> None:
        legacy = self._plan()
        for key in (
            "hardReportedInputTokenCeilingPerSample",
            "hardReportedTokenCeilingPerSample",
            "hardReportedInputTokenCeilingPerRepositoryShard",
            "hardReportedTokenCeilingPerRepositoryShard",
        ):
            del legacy["budgets"][key]
        for sample in legacy["samples"]:
            del sample["promptSha256"]
        campaign_runtime.seal(legacy)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._write_inputs(root, legacy)
            with (
                patch.object(runner, "_describe_adapter") as describe_adapter,
                patch.object(runner, "_run_sample") as run_sample,
                self.assertRaisesRegex(
                    contracts.RealRepositoryError,
                    "campaign plan is not execution-ready",
                ),
            ):
                runner.command_run_campaign_plan(args)
            describe_adapter.assert_not_called()
            run_sample.assert_not_called()
            self.assertFalse(Path(args.budget_ledger).exists())
            self.assertFalse(Path(args.run_dir).exists())

    def test_budget_decisions_are_fail_closed(self) -> None:
        budgets = self._plan()["budgets"]
        self.assertEqual(
            campaign_runtime.budget_stop_reason(
                budgets=budgets,
                elapsed_seconds=0,
                reported_tokens=60_000_000,
                consecutive_infrastructure_failures=0,
            ),
            "TOKEN_CEILING",
        )
        self.assertEqual(
            campaign_runtime.budget_stop_reason(
                budgets=budgets,
                elapsed_seconds=86400,
                reported_tokens=0,
                consecutive_infrastructure_failures=0,
            ),
            "HARD_WALL_TIME",
        )
        self.assertEqual(
            campaign_runtime.budget_stop_reason(
                budgets=budgets,
                elapsed_seconds=64800,
                reported_tokens=0,
                consecutive_infrastructure_failures=0,
            ),
            "SOFT_WALL_TIME",
        )
        self.assertEqual(
            campaign_runtime.budget_stop_reason(
                budgets=budgets,
                elapsed_seconds=0,
                reported_tokens=0,
                consecutive_infrastructure_failures=2,
            ),
            "INFRASTRUCTURE_CIRCUIT_BREAKER",
        )
        self.assertEqual(
            campaign_runtime.budget_stop_reason(
                budgets=budgets,
                elapsed_seconds=0,
                reported_tokens=0,
                consecutive_infrastructure_failures=0,
                unknown_usage_samples=1,
            ),
            "UNKNOWN_USAGE_BUDGET_EXCEEDED",
        )
        self.assertEqual(
            campaign_runtime.budget_stop_reason(
                budgets=budgets,
                elapsed_seconds=0,
                reported_tokens=0,
                consecutive_infrastructure_failures=0,
                unknown_usage_samples=1,
                timed_out_samples_by_model_profile={"fixture-assured": 1},
            ),
            "MODEL_PROFILE_TIMEOUT_BUDGET_EXCEEDED",
        )
        self.assertEqual(
            campaign_runtime.budget_stop_reason(
                budgets=budgets,
                elapsed_seconds=0,
                reported_tokens=0,
                consecutive_infrastructure_failures=0,
                artifact_invalid_samples=1,
            ),
            "ARTIFACT_INVALID_BUDGET_EXCEEDED",
        )
        self.assertEqual(
            campaign_runtime.budget_stop_reason(
                budgets=budgets,
                elapsed_seconds=0,
                reported_tokens=0,
                consecutive_infrastructure_failures=0,
                recovered_inactivity_samples_by_model_profile={
                    "fixture-assured": 2
                },
            ),
            "MODEL_PROFILE_INACTIVITY_BUDGET_EXCEEDED",
        )
        self.assertEqual(
            campaign_runtime.budget_stop_reason(
                budgets=budgets,
                elapsed_seconds=0,
                reported_tokens=0,
                consecutive_infrastructure_failures=0,
                sample_reported_input_tokens=1_250_000,
            ),
            "SAMPLE_INPUT_TOKEN_CEILING",
        )
        self.assertEqual(
            campaign_runtime.budget_stop_reason(
                budgets=budgets,
                elapsed_seconds=0,
                reported_tokens=0,
                consecutive_infrastructure_failures=0,
                sample_reported_tokens=1_500_000,
            ),
            "SAMPLE_TOKEN_CEILING",
        )
        self.assertEqual(
            campaign_runtime.budget_stop_reason(
                budgets=budgets,
                elapsed_seconds=0,
                reported_tokens=0,
                consecutive_infrastructure_failures=0,
                shard_reported_input_tokens=7_000_000,
            ),
            "SHARD_INPUT_TOKEN_CEILING",
        )
        self.assertEqual(
            campaign_runtime.budget_stop_reason(
                budgets=budgets,
                elapsed_seconds=0,
                reported_tokens=0,
                consecutive_infrastructure_failures=0,
                shard_reported_tokens=8_000_000,
            ),
            "SHARD_TOKEN_CEILING",
        )
        self.assertEqual(
            campaign_runtime.effective_sample_timeout(
                sample_timeout_seconds=1800,
                hard_wall_time_seconds=86400,
                elapsed_seconds=86390.5,
            ),
            10,
        )

    def test_budget_ledger_aggregates_shards_and_global_failure_tail(self) -> None:
        plan = self._plan()
        ledger = campaign_runtime.new_budget_ledger(
            plan, now="2026-08-20T00:00:00Z"
        )
        states = []
        for index, repository_id in enumerate(plan["selection"]["repositories"][:2]):
            state = campaign_runtime.new_run_state(
                plan=plan,
                campaign_id=f"fixture--{repository_id}",
                shard_id=repository_id,
                now="2026-08-20T00:00:00Z",
            )
            state.update(
                {
                    "status": "STOPPED",
                    "stopReason": "INFRASTRUCTURE_CIRCUIT_BREAKER",
                    "elapsedSeconds": index + 1,
                    "reportedTokens": 100 * (index + 1),
                    "unknownUsageSamples": index,
                    "artifactInvalidSamples": index,
                    "consecutiveInfrastructureFailures": 1,
                    "attemptedSampleIds": [plan["samples"][index]["sampleId"]],
                }
            )
            campaign_runtime.seal(state)
            campaign_runtime.update_budget_ledger(
                ledger, state, now="2026-08-20T00:00:01Z"
            )
            states.append(state)

        self.assertEqual(campaign_runtime.validate_budget_ledger(ledger, plan), [])
        self.assertEqual(
            campaign_runtime.budget_ledger_totals(ledger),
            (300, 1, 3, 2),
        )
        self.assertEqual(
            campaign_runtime.validate_budget_ledger_state(ledger, states[-1]), []
        )
        self.assertEqual(
            campaign_runtime.budget_ledger_artifact_invalid_samples(ledger), 1
        )
        self.assertEqual(
            campaign_runtime.budget_stop_reason(
                budgets=plan["budgets"],
                elapsed_seconds=0,
                reported_tokens=0,
                consecutive_infrastructure_failures=0,
                artifact_invalid_samples=(
                    campaign_runtime.budget_ledger_artifact_invalid_samples(ledger)
                ),
            ),
            "ARTIFACT_INVALID_BUDGET_EXCEEDED",
        )
        malformed = copy.deepcopy(ledger)
        del malformed["attemptedSamplesByShard"][states[-1]["shardId"]]
        campaign_runtime.seal(malformed)
        self.assertIn(
            "campaign budget ledger shard sets differ",
            campaign_runtime.validate_budget_ledger(malformed, plan),
        )
        self.assertIn(
            "campaign budget ledger is missing shard",
            campaign_runtime.validate_budget_ledger_state(malformed, states[-1])[0],
        )

    def test_adapter_failure_classification_and_credential_detection(self) -> None:
        self.assertEqual(
            runner._adapter_failure_class(b"", b"401 Unauthorized"),
            "AUTHENTICATION",
        )
        self.assertEqual(
            runner._adapter_failure_class(b"", b"connection refused"),
            "PROVIDER_CONNECTIVITY",
        )
        self.assertEqual(
            runner._adapter_failure_class(b"", b"model unavailable"),
            "MODEL_UNAVAILABLE",
        )
        self.assertTrue(
            runner._contains_sensitive_output(b"Authorization: Bearer fixture-secret")
        )
        self.assertEqual(
            runner._redact_output(b"Authorization: Bearer fixture-secret"),
            b"Authorization: Bearer [REDACTED]",
        )
        for source_expression in (
            b"user, password, ok := request.BasicAuth()",
            b"password == expected",
            b"password => handler",
        ):
            with self.subTest(source_expression=source_expression):
                self.assertFalse(runner._contains_sensitive_output(source_expression))
                self.assertEqual(runner._redact_output(source_expression), source_expression)
        for credential_echo, redacted in (
            (b"password=fixture-secret", b"password=[REDACTED]"),
            (b"password: fixture-secret", b"password: [REDACTED]"),
            (
                b'{"password": "fixture-secret"}',
                b'{"password": "[REDACTED]"}',
            ),
            (
                b'{"Authorization": "Bearer fixture-secret"}',
                b'{"Authorization": "Bearer [REDACTED]"}',
            ),
            (
                b"Incorrect API key provided: 'fixture-secret'",
                b"Incorrect API key provided: '[REDACTED]'",
            ),
        ):
            with self.subTest(credential_echo=credential_echo):
                self.assertTrue(runner._contains_sensitive_output(credential_echo))
                self.assertEqual(runner._redact_output(credential_echo), redacted)

        source_output = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "sed -n '1,240p' README.md",
                    "aggregated_output": "password=fixture-secret\n",
                    "status": "completed",
                    "exit_code": 0,
                },
            }
        ).encode()
        self.assertFalse(runner._contains_sensitive_output(source_output))
        nested_json_output = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "sed -n '1,240p' config.json",
                    "aggregated_output": '{"password": "fixture-secret"}',
                    "status": "completed",
                    "exit_code": 0,
                },
            }
        ).encode()
        self.assertFalse(runner._contains_sensitive_output(nested_json_output))
        redacted_nested_json = runner._redact_output(nested_json_output)
        self.assertNotIn(b"fixture-secret", redacted_nested_json)
        json.loads(redacted_nested_json)
        environment_output = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "printenv",
                    "aggregated_output": "password=fixture-secret\n",
                    "status": "completed",
                    "exit_code": 0,
                },
            }
        ).encode()
        self.assertTrue(runner._contains_sensitive_output(environment_output))
        source_search_output = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": 'rg -n "env|password" .',
                    "aggregated_output": "password=fixture-secret\n",
                    "status": "completed",
                    "exit_code": 0,
                },
            }
        ).encode()
        self.assertFalse(runner._contains_sensitive_output(source_search_output))
        bearer_source_output = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "sed -n '1,240p' README.md",
                    "aggregated_output": "Authorization: Bearer fixture-secret\n",
                    "status": "completed",
                    "exit_code": 0,
                },
            }
        ).encode()
        self.assertTrue(runner._contains_sensitive_output(bearer_source_output))
        shaped_token = b"sk-" + b"x" * 20
        self.assertTrue(runner._contains_sensitive_output(shaped_token))
        self.assertEqual(runner._redact_output(shaped_token), b"[REDACTED]")
        private_key = (
            b"-----BEGIN "
            b"PRIVATE KEY-----\nfixture-only\n-----END "
            b"PRIVATE KEY-----"
        )
        self.assertTrue(runner._contains_sensitive_output(private_key))
        self.assertEqual(runner._redact_output(private_key), b"[REDACTED]")

    def test_checkpoint_atomically_binds_campaign_and_exact_run_state(self) -> None:
        plan = self._plan(repositories=[self.suite["repositories"][0]["id"]])
        first = plan["samples"][0]
        repository = self.suite["repositories"][0]
        adapters = {row["id"]: row for row in self.adapter_config["adapters"]}
        sample = self._sample(
            repository=repository,
            adapter=adapters[first["modelConfigurationId"]],
            treatment=first["treatment"],
            repetition=first["repetition"],
            description=self.descriptions[first["modelConfigurationId"]],
        )
        campaign = {
            "schema": "review-craft.eval-real-repository-campaign.v1",
            "campaignId": plan["campaignId"],
            "status": "PARTIAL",
            "suiteSha256": plan["suiteSha256"],
            "blindSuiteSha256": plan["blindSuiteSha256"],
            "samples": [sample],
            "contentSha256": "0" * 64,
        }
        campaign_runtime.seal(campaign)
        state = campaign_runtime.new_run_state(
            plan=plan,
            campaign_id=campaign["campaignId"],
            shard_id="ALL",
            now="2026-08-20T00:00:00Z",
        )
        campaign_runtime.update_run_state(
            state,
            campaign=campaign,
            elapsed_seconds=1.25,
            now="2026-08-20T00:00:01Z",
        )
        checkpoint = campaign_runtime.build_checkpoint(
            plan=plan,
            campaign=campaign,
            state=state,
        )
        self.assertEqual(
            campaign_runtime.validate_checkpoint(checkpoint, plan, campaign), []
        )
        tampered = copy.deepcopy(checkpoint)
        tampered["state"]["elapsedSeconds"] = 0
        self.assertIn(
            "campaign run checkpoint contentSha256 mismatch",
            campaign_runtime.validate_checkpoint(tampered, plan, campaign),
        )

    def test_fake_144_rehearsal_resumes_without_replaying_completed_samples(self) -> None:
        plan = self._plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._write_inputs(root, plan)
            calls = 0

            def interrupted(**kwargs: object) -> dict:
                nonlocal calls
                calls += 1
                if calls == 50:
                    raise KeyboardInterrupt
                return self._sample(**kwargs)

            with (
                patch.object(
                    runner,
                    "_describe_adapter",
                    side_effect=lambda _command, adapter_id: self.descriptions[adapter_id],
                ),
                patch.object(runner, "_repository_state", side_effect=self._repository_state),
                patch.object(runner, "_run_sample", side_effect=interrupted),
                self.assertRaises(KeyboardInterrupt),
            ):
                runner.command_run_campaign_plan(args)

            partial = json.loads(
                (Path(args.run_dir) / "campaign.json").read_text(encoding="utf-8")
            )
            partial_state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(partial["samples"]), 49)
            self.assertEqual(partial_state["status"], "RUNNING")

            ledger_path = Path(args.budget_ledger)
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger["statusByShard"]["ALL"] = "COMPLETED"
            campaign_runtime.seal(ledger)
            runner.write_json(ledger_path, ledger)

            args.resume = True
            resumed_calls = 0

            def resumed(**kwargs: object) -> dict:
                nonlocal resumed_calls
                resumed_calls += 1
                return self._sample(**kwargs)

            with (
                patch.object(
                    runner,
                    "_describe_adapter",
                    side_effect=lambda _command, adapter_id: self.descriptions[adapter_id],
                ),
                patch.object(runner, "_repository_state", side_effect=self._repository_state),
                patch.object(runner, "_run_sample", side_effect=resumed),
            ):
                self.assertEqual(runner.command_run_campaign_plan(args), 0)

            completed = json.loads(
                (Path(args.run_dir) / "campaign.json").read_text(encoding="utf-8")
            )
            state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(resumed_calls, 95)
            self.assertEqual(len(completed["samples"]), 144)
            self.assertEqual(len({row["sampleId"] for row in completed["samples"]}), 144)
            self.assertEqual(completed["status"], "COMPLETED")
            self.assertEqual(state["status"], "COMPLETED")
            self.assertEqual(state["stopReason"], "SCHEDULE_COMPLETE")
            self.assertEqual(
                campaign_runtime.validate_run_state(state, plan, completed), []
            )
            with (
                patch.object(
                    runner,
                    "_describe_adapter",
                    side_effect=lambda _command, adapter_id: self.descriptions[adapter_id],
                ),
                patch.object(runner, "_repository_state", side_effect=self._repository_state),
                self.assertRaisesRegex(
                    contracts.RealRepositoryError,
                    "cannot resume terminal",
                ),
            ):
                runner.command_run_campaign_plan(args)

    def test_fake_adapter_executes_the_physical_144_sample_schedule(self) -> None:
        fake_adapter = ROOT / "tests/fixtures/fake_eval_adapter.py"
        adapter_config = {
            "schema": "review-craft.eval-real-repository-adapters.v1",
            "adapters": [
                {
                    "id": "fixture-standard",
                    "command": [
                        sys.executable,
                        str(fake_adapter),
                        "--mode",
                        "real-repository",
                    ],
                },
                {
                    "id": "fixture-assured",
                    "command": [
                        sys.executable,
                        str(fake_adapter),
                        "--mode",
                        "real-repository",
                    ],
                },
            ],
        }
        plan = campaign_runtime.build_campaign_plan(
            source_suite=self.suite,
            blind_suite=self.blind,
            materialization=self.receipt,
            adapter_config=adapter_config,
            model_configurations=self.models,
            campaign_id="fixture-physical-campaign-144",
            repository_ids=[row["id"] for row in self.suite["repositories"]],
            treatments=list(contracts.TREATMENTS),
            repetitions=3,
            sample_timeout_seconds=30,
            soft_wall_time_seconds=300,
            hard_wall_time_seconds=600,
            hard_reported_token_ceiling=1_000_000,
            max_consecutive_infrastructure_failures=2,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._write_inputs(root, plan, adapter_config)
            with (
                patch.object(
                    runner,
                    "_describe_adapter",
                    side_effect=lambda _command, adapter_id: self.descriptions[adapter_id],
                ),
                patch.object(runner, "_repository_state", side_effect=self._repository_state),
            ):
                self.assertEqual(runner.command_run_campaign_plan(args), 0)
            campaign = json.loads(
                (Path(args.run_dir) / "campaign.json").read_text(encoding="utf-8")
            )
            state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(encoding="utf-8")
            )
            sample_directories = list((Path(args.run_dir) / "samples").iterdir())
            self.assertEqual(len(sample_directories), 144)
            self.assertEqual(len(campaign["samples"]), 144)
            self.assertEqual(campaign["status"], "COMPLETED")
            self.assertEqual(state["status"], "COMPLETED")
            self.assertEqual(state["reportedTokens"], 17_280)

    def test_runner_stops_after_two_consecutive_infrastructure_failures(self) -> None:
        repository_id = self.suite["repositories"][0]["id"]
        plan = self._plan(
            repositories=[repository_id], max_unknown_usage_samples=3
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._write_inputs(root, plan)
            with (
                patch.object(
                    runner,
                    "_describe_adapter",
                    side_effect=lambda _command, adapter_id: self.descriptions[adapter_id],
                ),
                patch.object(runner, "_repository_state", side_effect=self._repository_state),
                patch.object(
                    runner,
                    "_run_sample",
                    side_effect=lambda **kwargs: self._failed_sample(
                        "AUTHENTICATION", **kwargs
                    ),
                ) as run_sample,
            ):
                self.assertEqual(runner.command_run_campaign_plan(args), 0)
            state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(run_sample.call_count, 2)
            self.assertEqual(state["status"], "STOPPED")
            self.assertEqual(state["stopReason"], "INFRASTRUCTURE_CIRCUIT_BREAKER")

    def test_runner_stops_after_two_nonconsecutive_timeouts_for_one_profile(
        self,
    ) -> None:
        repository_id = self.suite["repositories"][0]["id"]
        plan = self._plan(
            repositories=[repository_id],
            max_unknown_usage_samples=3,
            max_timed_out_samples_per_model_profile=2,
        )
        outcomes = iter(("timeout", "success", "timeout"))

        def sample_for_outcome(**kwargs: object) -> dict:
            if next(outcomes) == "timeout":
                return self._timed_out_sample(**kwargs)
            return self._sample(**kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._write_inputs(root, plan)
            with (
                patch.object(
                    runner,
                    "_describe_adapter",
                    side_effect=lambda _command, adapter_id: self.descriptions[adapter_id],
                ),
                patch.object(runner, "_repository_state", side_effect=self._repository_state),
                patch.object(
                    runner, "_run_sample", side_effect=sample_for_outcome
                ) as run_sample,
            ):
                self.assertEqual(runner.command_run_campaign_plan(args), 0)
            state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(encoding="utf-8")
            )
            ledger = json.loads(Path(args.budget_ledger).read_text(encoding="utf-8"))
            self.assertEqual(run_sample.call_count, 3)
            self.assertEqual(state["status"], "STOPPED")
            self.assertEqual(
                state["stopReason"], "MODEL_PROFILE_TIMEOUT_BUDGET_EXCEEDED"
            )
            self.assertEqual(
                state["timedOutSamplesByModelProfile"], {"fixture-standard": 2}
            )
            self.assertEqual(
                campaign_runtime.budget_ledger_timed_out_samples_by_model_profile(
                    ledger
                ),
                {"fixture-standard": 2},
            )

    def test_runner_stops_after_second_recovered_inactivity_for_one_profile(
        self,
    ) -> None:
        repository_id = self.suite["repositories"][0]["id"]
        plan = self._plan(
            repositories=[repository_id],
            max_recovered_inactivity_samples_per_model_profile=2,
        )
        outcomes = iter(("stalled", "success", "stalled"))

        def sample_for_outcome(**kwargs: object) -> dict:
            if next(outcomes) == "stalled":
                return self._recovered_inactivity_sample(**kwargs)
            return self._sample(**kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._write_inputs(root, plan)
            with (
                patch.object(
                    runner,
                    "_describe_adapter",
                    side_effect=lambda _command, adapter_id: self.descriptions[adapter_id],
                ),
                patch.object(runner, "_repository_state", side_effect=self._repository_state),
                patch.object(
                    runner, "_run_sample", side_effect=sample_for_outcome
                ) as run_sample,
            ):
                self.assertEqual(runner.command_run_campaign_plan(args), 0)
            state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(encoding="utf-8")
            )
            ledger = json.loads(Path(args.budget_ledger).read_text(encoding="utf-8"))
            self.assertEqual(run_sample.call_count, 3)
            self.assertEqual(state["status"], "STOPPED")
            self.assertEqual(
                state["stopReason"],
                "MODEL_PROFILE_INACTIVITY_BUDGET_EXCEEDED",
            )
            self.assertEqual(
                state["recoveredInactivitySamplesByModelProfile"],
                {"fixture-standard": 2},
            )
            self.assertEqual(
                campaign_runtime.budget_ledger_recovered_inactivity_samples_by_model_profile(
                    ledger
                ),
                {"fixture-standard": 2},
            )

    def test_runner_stops_after_first_unknown_usage_sample(self) -> None:
        repository_id = self.suite["repositories"][0]["id"]
        plan = self._plan(repositories=[repository_id])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._write_inputs(root, plan)
            with (
                patch.object(
                    runner,
                    "_describe_adapter",
                    side_effect=lambda _command, adapter_id: self.descriptions[adapter_id],
                ),
                patch.object(runner, "_repository_state", side_effect=self._repository_state),
                patch.object(
                    runner,
                    "_run_sample",
                    side_effect=lambda **kwargs: self._failed_sample(
                        "REVIEW_FAILURE", **kwargs
                    ),
                ) as run_sample,
            ):
                self.assertEqual(runner.command_run_campaign_plan(args), 0)
            state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(run_sample.call_count, 1)
            self.assertEqual(state["stopReason"], "UNKNOWN_USAGE_BUDGET_EXCEEDED")

    def test_runner_stops_after_first_artifact_invalid_sample(self) -> None:
        repository_id = self.suite["repositories"][0]["id"]
        plan = self._plan(repositories=[repository_id])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._write_inputs(root, plan)
            with (
                patch.object(
                    runner,
                    "_describe_adapter",
                    side_effect=lambda _command, adapter_id: self.descriptions[
                        adapter_id
                    ],
                ),
                patch.object(
                    runner,
                    "_repository_state",
                    side_effect=self._repository_state,
                ),
                patch.object(
                    runner,
                    "_run_sample",
                    side_effect=self._artifact_invalid_sample,
                ) as run_sample,
            ):
                self.assertEqual(runner.command_run_campaign_plan(args), 0)
            state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(
                    encoding="utf-8"
                )
            )
            ledger = json.loads(
                Path(args.budget_ledger).read_text(encoding="utf-8")
            )
            self.assertEqual(run_sample.call_count, 1)
            self.assertEqual(state["status"], "STOPPED")
            self.assertEqual(
                state["stopReason"], "ARTIFACT_INVALID_BUDGET_EXCEEDED"
            )
            self.assertEqual(state["artifactInvalidSamples"], 1)
            self.assertEqual(
                campaign_runtime.budget_ledger_artifact_invalid_samples(ledger),
                1,
            )

    def test_runner_enforces_reported_token_ceiling_after_checkpoint(self) -> None:
        repository_id = self.suite["repositories"][0]["id"]
        plan = self._plan(repositories=[repository_id], token_ceiling=200)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._write_inputs(root, plan)
            with (
                patch.object(
                    runner,
                    "_describe_adapter",
                    side_effect=lambda _command, adapter_id: self.descriptions[adapter_id],
                ),
                patch.object(runner, "_repository_state", side_effect=self._repository_state),
                patch.object(runner, "_run_sample", side_effect=self._sample) as run_sample,
            ):
                self.assertEqual(runner.command_run_campaign_plan(args), 0)
            state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(run_sample.call_count, 2)
            self.assertEqual(state["reportedTokens"], 240)
            self.assertEqual(state["stopReason"], "TOKEN_CEILING")

    def test_final_sample_cost_ceiling_completes_exhausted_schedule(self) -> None:
        repository_id = self.suite["repositories"][0]["id"]
        plan = self._plan(repositories=[repository_id], token_ceiling=2_160)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._write_inputs(root, plan)
            with (
                patch.object(
                    runner,
                    "_describe_adapter",
                    side_effect=lambda _command, adapter_id: self.descriptions[
                        adapter_id
                    ],
                ),
                patch.object(
                    runner,
                    "_repository_state",
                    side_effect=self._repository_state,
                ),
                patch.object(
                    runner, "_run_sample", side_effect=self._sample
                ) as run_sample,
            ):
                self.assertEqual(runner.command_run_campaign_plan(args), 0)
            state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(encoding="utf-8")
            )
            ledger = json.loads(
                Path(args.budget_ledger).read_text(encoding="utf-8")
            )
            self.assertEqual(run_sample.call_count, 18)
            self.assertEqual(state["status"], "COMPLETED")
            self.assertEqual(state["stopReason"], "SCHEDULE_COMPLETE")
            self.assertEqual(state["reportedTokens"], 2_160)
            self.assertEqual(campaign_runtime.budget_ledger_totals(ledger)[0], 2_160)

    def test_final_sample_failure_budgets_precede_schedule_completion(self) -> None:
        repository_id = self.suite["repositories"][0]["id"]
        plan = self._plan(repositories=[repository_id], repetitions=1)

        def unknown_usage_sample(**kwargs: object) -> dict:
            return self._failed_sample("REVIEW_FAILURE", **kwargs)

        outcomes = (
            (
                "timeout",
                self._timed_out_sample,
                "MODEL_PROFILE_TIMEOUT_BUDGET_EXCEEDED",
            ),
            (
                "unknown-usage",
                unknown_usage_sample,
                "UNKNOWN_USAGE_BUDGET_EXCEEDED",
            ),
        )
        for label, final_sample, expected_reason in outcomes:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                args = self._write_inputs(root, plan)
                position = {"value": 0}

                def sample_for_position(
                    *,
                    _position: dict[str, int] = position,
                    _final_sample: object = final_sample,
                    **kwargs: object,
                ) -> dict:
                    _position["value"] += 1
                    if _position["value"] == len(plan["samples"]):
                        assert callable(_final_sample)
                        return _final_sample(**kwargs)
                    return self._sample(**kwargs)

                with (
                    patch.object(
                        runner,
                        "_describe_adapter",
                        side_effect=lambda _command, adapter_id: self.descriptions[
                            adapter_id
                        ],
                    ),
                    patch.object(
                        runner,
                        "_repository_state",
                        side_effect=self._repository_state,
                    ),
                    patch.object(
                        runner,
                        "_run_sample",
                        side_effect=sample_for_position,
                    ) as run_sample,
                ):
                    self.assertEqual(runner.command_run_campaign_plan(args), 0)
                state = json.loads(
                    (Path(args.run_dir) / "run-state.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(run_sample.call_count, len(plan["samples"]))
                self.assertEqual(state["status"], "STOPPED")
                self.assertEqual(state["stopReason"], expected_reason)

    def test_terminal_state_priority_preserves_safety_and_clean_completion(
        self,
    ) -> None:
        self.assertEqual(
            runner._terminal_run_state(
                "SOURCE_MUTATION",
                "TOKEN_CEILING",
                schedule_complete=True,
            ),
            ("FAILED", "SOURCE_MUTATION"),
        )
        self.assertEqual(
            runner._terminal_run_state(
                "CREDENTIAL_EXPOSURE",
                "MODEL_PROFILE_INACTIVITY_BUDGET_EXCEEDED",
                schedule_complete=True,
            ),
            ("FAILED", "CREDENTIAL_EXPOSURE"),
        )
        self.assertEqual(
            runner._terminal_run_state(
                "TIMEOUT",
                "MODEL_PROFILE_TIMEOUT_BUDGET_EXCEEDED",
                schedule_complete=True,
            ),
            ("STOPPED", "MODEL_PROFILE_TIMEOUT_BUDGET_EXCEEDED"),
        )
        self.assertEqual(
            runner._terminal_run_state(
                None,
                "UNKNOWN_USAGE_BUDGET_EXCEEDED",
                schedule_complete=True,
            ),
            ("STOPPED", "UNKNOWN_USAGE_BUDGET_EXCEEDED"),
        )
        self.assertEqual(
            runner._terminal_run_state(None, None, schedule_complete=True),
            ("COMPLETED", "SCHEDULE_COMPLETE"),
        )
        self.assertEqual(
            runner._terminal_run_state(
                None,
                "TOKEN_CEILING",
                schedule_complete=True,
            ),
            ("COMPLETED", "SCHEDULE_COMPLETE"),
        )

    def test_runner_stops_after_sample_input_token_ceiling(self) -> None:
        repository_id = self.suite["repositories"][0]["id"]
        plan = self._plan(
            repositories=[repository_id],
            sample_input_token_ceiling=100,
            sample_token_ceiling=200,
            shard_input_token_ceiling=1_000,
            shard_token_ceiling=1_200,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._write_inputs(root, plan)
            with (
                patch.object(
                    runner,
                    "_describe_adapter",
                    side_effect=lambda _command, adapter_id: self.descriptions[adapter_id],
                ),
                patch.object(runner, "_repository_state", side_effect=self._repository_state),
                patch.object(runner, "_run_sample", side_effect=self._sample) as run_sample,
            ):
                self.assertEqual(runner.command_run_campaign_plan(args), 0)
            state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(run_sample.call_count, 1)
            self.assertEqual(state["stopReason"], "SAMPLE_INPUT_TOKEN_CEILING")

    def test_shared_budget_prevents_a_second_shard_from_spending_again(self) -> None:
        repositories = [row["id"] for row in self.suite["repositories"][:2]]
        plan = self._plan(repositories=repositories, token_ceiling=200)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._write_inputs(root, plan)
            args.shard = repositories[0]
            with (
                patch.object(
                    runner,
                    "_describe_adapter",
                    side_effect=lambda _command, adapter_id: self.descriptions[
                        adapter_id
                    ],
                ),
                patch.object(runner, "_repository_state", side_effect=self._repository_state),
                patch.object(runner, "_run_sample", side_effect=self._sample) as first,
            ):
                self.assertEqual(runner.command_run_campaign_plan(args), 0)
            self.assertEqual(first.call_count, 2)

            args.run_dir = str(root / "run-second")
            args.shard = repositories[1]
            with (
                patch.object(
                    runner,
                    "_describe_adapter",
                    side_effect=lambda _command, adapter_id: self.descriptions[
                        adapter_id
                    ],
                ),
                patch.object(runner, "_repository_state", side_effect=self._repository_state),
                patch.object(runner, "_run_sample", side_effect=self._sample) as second,
            ):
                self.assertEqual(runner.command_run_campaign_plan(args), 0)
            self.assertEqual(second.call_count, 0)
            second_state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(encoding="utf-8")
            )
            ledger = json.loads(
                Path(args.budget_ledger).read_text(encoding="utf-8")
            )
            self.assertEqual(second_state["stopReason"], "TOKEN_CEILING")
            self.assertEqual(
                campaign_runtime.budget_ledger_totals(ledger)[0], 240
            )

    def test_recovered_inactivity_budget_accumulates_across_shards(self) -> None:
        repositories = [row["id"] for row in self.suite["repositories"][:2]]
        plan = self._plan(
            repositories=repositories,
            max_recovered_inactivity_samples_per_model_profile=2,
        )
        first_attempt = True

        def first_shard_sample(**kwargs: object) -> dict:
            nonlocal first_attempt
            if first_attempt:
                first_attempt = False
                return self._recovered_inactivity_sample(**kwargs)
            return self._sample(**kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._write_inputs(root, plan)
            args.shard = repositories[0]
            with (
                patch.object(
                    runner,
                    "_describe_adapter",
                    side_effect=lambda _command, adapter_id: self.descriptions[
                        adapter_id
                    ],
                ),
                patch.object(runner, "_repository_state", side_effect=self._repository_state),
                patch.object(
                    runner, "_run_sample", side_effect=first_shard_sample
                ) as first,
            ):
                self.assertEqual(runner.command_run_campaign_plan(args), 0)
            self.assertEqual(first.call_count, 18)
            first_state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(first_state["status"], "COMPLETED")
            self.assertEqual(
                first_state["recoveredInactivitySamplesByModelProfile"],
                {"fixture-standard": 1},
            )

            args.run_dir = str(root / "run-second")
            args.shard = repositories[1]
            with (
                patch.object(
                    runner,
                    "_describe_adapter",
                    side_effect=lambda _command, adapter_id: self.descriptions[
                        adapter_id
                    ],
                ),
                patch.object(runner, "_repository_state", side_effect=self._repository_state),
                patch.object(
                    runner,
                    "_run_sample",
                    side_effect=self._recovered_inactivity_sample,
                ) as second,
            ):
                self.assertEqual(runner.command_run_campaign_plan(args), 0)
            self.assertEqual(second.call_count, 1)
            second_state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(encoding="utf-8")
            )
            ledger = json.loads(
                Path(args.budget_ledger).read_text(encoding="utf-8")
            )
            self.assertEqual(second_state["status"], "STOPPED")
            self.assertEqual(
                second_state["stopReason"],
                "MODEL_PROFILE_INACTIVITY_BUDGET_EXCEEDED",
            )
            self.assertEqual(
                campaign_runtime.budget_ledger_recovered_inactivity_samples_by_model_profile(
                    ledger
                ),
                {"fixture-standard": 2},
            )

    def test_final_recovered_inactivity_completes_shard_and_blocks_next_shard(
        self,
    ) -> None:
        repositories = [row["id"] for row in self.suite["repositories"][:2]]
        plan = self._plan(
            repositories=repositories,
            repetitions=1,
            max_recovered_inactivity_samples_per_model_profile=2,
        )
        first_calls = 0

        def first_shard_sample(**kwargs: object) -> dict:
            nonlocal first_calls
            first_calls += 1
            if first_calls in {4, 6}:
                return self._recovered_inactivity_sample(**kwargs)
            return self._sample(**kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._write_inputs(root, plan)
            args.shard = repositories[0]
            with (
                patch.object(
                    runner,
                    "_describe_adapter",
                    side_effect=lambda _command, adapter_id: self.descriptions[
                        adapter_id
                    ],
                ),
                patch.object(
                    runner,
                    "_repository_state",
                    side_effect=self._repository_state,
                ),
                patch.object(
                    runner, "_run_sample", side_effect=first_shard_sample
                ) as first,
            ):
                self.assertEqual(runner.command_run_campaign_plan(args), 0)
            first_state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(first.call_count, 6)
            self.assertEqual(first_state["status"], "COMPLETED")
            self.assertEqual(first_state["stopReason"], "SCHEDULE_COMPLETE")
            self.assertEqual(
                first_state["recoveredInactivitySamplesByModelProfile"],
                {"fixture-assured": 2},
            )

            args.run_dir = str(root / "run-second")
            args.shard = repositories[1]
            with (
                patch.object(
                    runner,
                    "_describe_adapter",
                    side_effect=lambda _command, adapter_id: self.descriptions[
                        adapter_id
                    ],
                ),
                patch.object(
                    runner,
                    "_repository_state",
                    side_effect=self._repository_state,
                ),
                patch.object(
                    runner, "_run_sample", side_effect=self._sample
                ) as second,
            ):
                self.assertEqual(runner.command_run_campaign_plan(args), 0)
            second_state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(encoding="utf-8")
            )
            ledger = json.loads(
                Path(args.budget_ledger).read_text(encoding="utf-8")
            )
            self.assertEqual(second.call_count, 0)
            self.assertEqual(second_state["status"], "STOPPED")
            self.assertEqual(
                second_state["stopReason"],
                "MODEL_PROFILE_INACTIVITY_BUDGET_EXCEEDED",
            )
            self.assertEqual(
                campaign_runtime.budget_ledger_recovered_inactivity_samples_by_model_profile(
                    ledger
                ),
                {"fixture-assured": 2},
            )

    def test_eight_repository_shards_merge_to_the_same_complete_matrix(self) -> None:
        plan = self._plan()
        samples = []
        repositories = {row["id"]: row for row in self.suite["repositories"]}
        descriptions = self.descriptions
        adapters = {row["id"]: row for row in self.adapter_config["adapters"]}
        for plan_sample in plan["samples"]:
            samples.append(
                self._sample(
                    repository=repositories[plan_sample["repositoryId"]],
                    adapter=adapters[plan_sample["modelConfigurationId"]],
                    treatment=plan_sample["treatment"],
                    repetition=plan_sample["repetition"],
                    description=descriptions[plan_sample["modelConfigurationId"]],
                )
            )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = root / "plan.json"
            ledger_path = root / "budget-ledger.json"
            runner.write_json(plan_path, plan)
            ledger = campaign_runtime.new_budget_ledger(
                plan, now="2026-08-20T00:00:00Z"
            )
            run_dirs = []
            for repository_id in plan["selection"]["repositories"]:
                run_dir = root / f"run-{repository_id}"
                run_dir.mkdir()
                shard_samples = [
                    row for row in samples if row["repositoryId"] == repository_id
                ]
                campaign = {
                    "schema": "review-craft.eval-real-repository-campaign.v1",
                    "campaignId": f"{plan['campaignId']}--shard-{repository_id}",
                    "status": "PARTIAL",
                    "suiteSha256": plan["suiteSha256"],
                    "blindSuiteSha256": plan["blindSuiteSha256"],
                    "samples": shard_samples,
                    "contentSha256": "0" * 64,
                }
                campaign_runtime.seal(campaign)
                state = campaign_runtime.new_run_state(
                    plan=plan,
                    campaign_id=campaign["campaignId"],
                    shard_id=repository_id,
                    now="2026-08-20T00:00:00Z",
                )
                campaign_runtime.update_run_state(
                    state,
                    campaign=campaign,
                    elapsed_seconds=1,
                    now="2026-08-20T00:00:01Z",
                    status="COMPLETED",
                    stop_reason="SCHEDULE_COMPLETE",
                )
                self.assertEqual(
                    campaign_runtime.validate_run_state(state, plan, campaign), []
                )
                campaign_runtime.update_budget_ledger(
                    ledger, state, now="2026-08-20T00:00:01Z"
                )
                runner.write_json(run_dir / "plan.json", plan)
                runner.write_json(run_dir / "campaign.json", campaign)
                runner.write_json(run_dir / "run-state.json", state)
                runner.write_json(
                    run_dir / "checkpoint.json",
                    campaign_runtime.build_checkpoint(
                        plan=plan,
                        campaign=campaign,
                        state=state,
                    ),
                )
                run_dirs.append(str(run_dir))
            runner.write_json(ledger_path, ledger)
            output_dir = root / "merged"
            args = SimpleNamespace(
                suite=str(SUITE_PATH),
                blind_suite=str(CURRENT_ROOT / "blind-suite.json"),
                plan=str(plan_path),
                run_dir=run_dirs,
                budget_ledger=str(ledger_path),
                output_dir=str(output_dir),
                allow_partial=False,
            )
            self.assertEqual(runner.command_merge_campaign_runs(args), 0)
            merged = json.loads(
                (output_dir / "campaign.json").read_text(encoding="utf-8")
            )
            receipt = json.loads(
                (output_dir / "merge.json").read_text(encoding="utf-8")
            )
            self.assertEqual(merged["status"], "COMPLETED")
            self.assertEqual(len(merged["samples"]), 144)
            self.assertEqual(len(receipt["inputs"]), 8)
            self.assertEqual(
                receipt["budgetLedgerContentSha256"], ledger["contentSha256"]
            )
            self.assertEqual(
                json.loads(
                    (output_dir / "budget-ledger.json").read_text(encoding="utf-8")
                ),
                ledger,
            )
            self.assertEqual(contracts.validate_campaign(merged, self.suite, self.blind), [])


if __name__ == "__main__":
    unittest.main()
