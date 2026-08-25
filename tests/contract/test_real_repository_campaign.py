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
adapter_runtime = importlib.import_module("codex_eval_adapter")
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
        for repository in self.receipt["repositories"]:
            repository["fixObjectExcluded"] = True
        self.receipt["evaluatorBoundary"] = {
            "kind": contracts.EVALUATOR_BOUNDARY_KIND,
            "coordinatorArtifactsExcluded": True,
            "workspaceTopLevel": ["repositories"],
        }
        self.receipt["contentSha256"] = contracts.sha256_json(
            {
                key: value
                for key, value in self.receipt.items()
                if key != "contentSha256"
            }
        )
        isolation_patcher = patch.object(runner, "_assert_fix_object_excluded")
        isolation_patcher.start()
        self.addCleanup(isolation_patcher.stop)
        self.adapter_config = {
            "schema": "review-craft.eval-real-repository-adapters.v1",
            "adapters": [
                {"id": "fixture-standard", "command": ["fixture-standard"]},
                {"id": "fixture-assured", "command": ["fixture-assured"]},
            ],
        }
        self.descriptions = {
            "fixture-standard": self._description("gpt-5.6-terra", "high"),
            "fixture-assured": self._description("gpt-5.6-sol", "high"),
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
            "model": model,
            "reasoning": reasoning,
            "adapterVersion": "fixture-v1",
            "evidenceKind": "REAL_HOST",
            "provider": {"name": "fixture-provider"},
            "isolation": {"fixture": model},
            "toolTrace": {
                "protocol": "review-craft.eval-tool-trace.v1",
                "transport": "ENV_PATH",
                "environmentVariable": "REVIEW_CRAFT_EVAL_TOOL_TRACE_OUTPUT",
            },
            "toolBudgetControl": {
                "protocol": "review-craft.eval-tool-budget-control.v3",
                "transport": "ENV_VALUE",
                "repositoryLimitEnvironmentVariable": (
                    runner.REPOSITORY_TOOL_CALL_LIMIT_ENV
                ),
                "skillBootstrapLimitEnvironmentVariable": (
                    runner.SKILL_BOOTSTRAP_TOOL_CALL_LIMIT_ENV
                ),
                "enforcementEvent": "PreToolUse",
                "commandEnforcement": "PRE_EXECUTION_BLOCK",
                "nonCommandEnforcement": "ITEM_STARTED_EARLY_TERMINATION",
                "hookDecision": "block",
                "hookTrustMode": "AUTOMATION_BYPASS",
                "stateTransport": "ADAPTER_MANAGED_PATH",
                "skillBootstrapPrerequisite": "REQUIRED_WHEN_LIMIT_POSITIVE",
                "prerequisiteEnforcement": "RECOVERABLE_PRE_EXECUTION_BLOCK",
                "maxRecoverablePrerequisiteBlocks": 1,
                "prerequisiteFailureKind": "SKILL_BOOTSTRAP_REQUIRED",
                "bootstrapCommandPolicy": "DEDICATED_BOUND_SKILL_READ_V1",
                "hookConfigurationSha256": "1" * 64,
                "hookImplementationSha256": "2" * 64,
                "budgetExitCode": runner.TOOL_BUDGET_EXIT_CODE,
                "finalizationGraceSeconds": 30,
            },
        }

    def test_skill_bootstrap_classifier_requires_literal_bounded_reader(self) -> None:
        self.assertEqual(
            adapter_runtime.classify_skill_bootstrap_command(
                "sed -n '1,120p' $SKILL/SKILL.md"
            ),
            adapter_runtime.SKILL_BOOTSTRAP_ENTRYPOINT,
        )
        self.assertEqual(
            adapter_runtime.classify_skill_bootstrap_command(
                "rg -n '^## ' ${SKILL}/SKILL.md | head -n 110"
            ),
            adapter_runtime.SKILL_BOOTSTRAP_ENTRYPOINT,
        )
        self.assertEqual(
            adapter_runtime.classify_skill_bootstrap_command(
                "head -n 110 $SKILL/references/workflow.md"
            ),
            adapter_runtime.SKILL_BOOTSTRAP_REFERENCE,
        )
        for command in (
            "rg -n '^## ' $SKILL/SKILL.md",
            "./head -n 110 $SKILL/SKILL.md",
            "sed -n '1,120p' $SKILL/SKILL.md | ./head -n 110",
            "head -n 110 $SKILL/references/*.md",
            "head -n 110 $SKILL/references/${REFERENCE}.md",
        ):
            with self.subTest(command=command):
                self.assertIsNone(
                    adapter_runtime.classify_skill_bootstrap_command(command)
                )

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

    def _purpose_plan(self, purpose: str = "CANARY") -> dict:
        return campaign_runtime.build_purpose_campaign_plan(
            source_suite=self.suite,
            blind_suite=self.blind,
            materialization=self.receipt,
            adapter_config=self.adapter_config,
            model_configurations=self.models,
            campaign_id=f"fixture-{purpose.lower().replace('_', '-')}",
            campaign_purpose=purpose,
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

    def _sample_with_usage(
        self, *, input_tokens: int, total_tokens: int, **kwargs: object
    ) -> dict:
        sample = self._sample(**kwargs)
        sample["usage"].update(
            {
                "inputTokens": input_tokens,
                "outputTokens": total_tokens - input_tokens,
                "totalTokens": total_tokens,
            }
        )
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

    def _healthy_lifecycle_sample(self, **kwargs: object) -> dict:
        sample = self._recovered_inactivity_sample(**kwargs)
        sample["lifecycle"].update(
            {
                "completedAt": "2026-08-21T00:00:04Z",
                "firstItemAt": "2026-08-21T00:00:03Z",
                "lastEventAt": "2026-08-21T00:00:04Z",
                "timeToFirstItemSeconds": 3.0,
                "inactivityState": "NORMAL",
                "maximumPreItemInactivitySeconds": 3.0,
                "diagnosticCapturedAt": None,
                "processAliveWhenDiagnosticCaptured": None,
            }
        )
        return sample

    def _completed_purpose_evidence(
        self, purpose: str
    ) -> tuple[dict, dict, dict]:
        plan = self._purpose_plan(purpose)
        repositories = {row["id"]: row for row in self.blind["repositories"]}
        adapters = {row["id"]: row for row in self.adapter_config["adapters"]}
        samples = [
            self._healthy_lifecycle_sample(
                repository=repositories[row["repositoryId"]],
                adapter=adapters[row["modelConfigurationId"]],
                treatment=row["treatment"],
                repetition=row["repetition"],
                description=self.descriptions[row["modelConfigurationId"]],
            )
            for row in plan["samples"]
        ]
        campaign = campaign_runtime.merge_campaigns(
            plan=plan,
            campaigns=[{"samples": samples}],
        )
        state = campaign_runtime.new_run_state(
            plan=plan,
            campaign_id=campaign["campaignId"],
            shard_id="ALL",
            now="2026-08-21T00:00:00Z",
        )
        campaign_runtime.update_run_state(
            state,
            campaign=campaign,
            elapsed_seconds=1.0,
            now="2026-08-21T00:00:01Z",
            status="COMPLETED",
            stop_reason="SCHEDULE_COMPLETE",
        )
        ledger = campaign_runtime.new_budget_ledger(
            plan, now="2026-08-21T00:00:00Z"
        )
        campaign_runtime.update_budget_ledger(
            ledger, state, now="2026-08-21T00:00:01Z"
        )
        return plan, campaign, ledger

    def _quality_evidence(self, campaign: dict) -> tuple[dict, dict]:
        samples = {row["sampleId"]: row for row in campaign["samples"]}
        subject_hashes = contracts.adjudication_subject_content_hashes(campaign)
        labels = []
        for adjudicator_id in ("agent-a", "agent-b"):
            for sample_id, subject_type, subject_key in sorted(
                contracts.adjudication_subjects(campaign)
            ):
                ordinary_real_finding = (
                    samples[sample_id]["treatment"] == "ORDINARY_PROMPT"
                    and subject_type == "PROBE_RESPONSE"
                    and next(
                        row
                        for row in self.suite["repositories"]
                        if row["id"] == samples[sample_id]["repositoryId"]
                    )["probes"][0]["id"]
                    == subject_key
                )
                components = [
                    {
                        "key": key,
                        "label": (
                            "INCORRECT"
                            if ordinary_real_finding and index == 0
                            else "CORRECT"
                        ),
                        "rationale": "Synthetic promotion contract fixture.",
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
                        "label": (
                            "INCORRECT" if ordinary_real_finding else "CORRECT"
                        ),
                        "rationale": "Synthetic promotion contract fixture.",
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
                    "kind": "AGENT_ASSISTED",
                    "independent": True,
                    "packetContentSha256": contracts.sha256_json(
                        [adjudicator_id, "packet"]
                    ),
                    "submissionContentSha256": contracts.sha256_json(
                        [adjudicator_id, "submission"]
                    ),
                }
                for adjudicator_id in ("agent-a", "agent-b")
            ],
            "labels": labels,
            "subjectResolutions": contracts.build_adjudication_resolutions(labels),
            "contentSha256": "0" * 64,
        }
        campaign_runtime.seal(adjudication)
        assessment = contracts.build_oracle_assessment_template(
            self.suite,
            campaign,
            adjudication,
            verifier_id="agent-oracle",
            verifier_kind="AGENT_ASSISTED",
        )
        for row in assessment["assessments"]:
            row["classification"] = (
                "MISSED"
                if samples[row["sampleId"]]["treatment"] == "ORDINARY_PROMPT"
                else "EXACT_ORACLE_MATCH"
            )
            row["rationale"] = "Synthetic promotion oracle fixture."
        assessment["status"] = "FINAL"
        campaign_runtime.seal(assessment)
        return adjudication, assessment

    def _write_inputs(
        self,
        root: Path,
        plan: dict,
        adapter_config: dict | None = None,
    ) -> SimpleNamespace:
        adapter_path = root / "adapters.json"
        coordinator = root / "coordinator"
        coordinator.mkdir()
        materialization_path = coordinator / "materialization.json"
        plan_path = root / "plan-input.json"
        runner.write_json(adapter_path, adapter_config or self.adapter_config)
        runner.write_json(materialization_path, self.receipt)
        runner.write_json(plan_path, plan)
        workspace = root / "workspace"
        for row in self.receipt["repositories"]:
            (workspace / row["checkout"]).mkdir(parents=True)
        return SimpleNamespace(
            suite=str(SUITE_PATH),
            blind_suite=str(CURRENT_ROOT / "blind-suite.json"),
            materialization=str(materialization_path),
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
            authorize_plan_sha256=plan["contentSha256"],
            allow_golden_campaign_sha256=(
                plan["contentSha256"]
                if plan.get("campaignPurpose") == "GOLDEN"
                else None
            ),
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

    def test_purpose_policy_generates_only_the_five_fixed_matrices(self) -> None:
        expected_counts = {
            "CANARY": 6,
            "CORE_ITERATION": 16,
            "RISK_ITERATION": 24,
            "CANDIDATE": 48,
            "GOLDEN": 144,
        }
        for purpose, expected_count in expected_counts.items():
            with self.subTest(purpose=purpose):
                plan = self._purpose_plan(purpose)
                self.assertEqual(len(plan["samples"]), expected_count)
                self.assertEqual(plan["campaignPurpose"], purpose)
                self.assertEqual(
                    campaign_runtime.validate_campaign_plan(
                        plan, self.suite, self.blind
                    ),
                    [],
                )
                self.assertEqual(
                    campaign_runtime.validate_campaign_plan_execution_safety(plan),
                    [],
                )

    def test_canary_budget_keeps_headroom_over_preserved_real_host_baseline(
        self,
    ) -> None:
        budgets = campaign_runtime.PURPOSE_POLICY_V1["purposes"]["CANARY"][
            "budgets"
        ]

        # The retained six-cell v1 smoke used 577,162 input and 608,932 total tokens.
        self.assertGreaterEqual(
            budgets["hardReportedInputTokenCeilingPerRepositoryShard"], 750_000
        )
        self.assertGreaterEqual(
            budgets["hardReportedTokenCeilingPerRepositoryShard"], 800_000
        )
        self.assertGreaterEqual(budgets["hardReportedTokenCeiling"], 800_000)

    def test_core_budget_keeps_global_headroom_without_relaxing_cell_limits(
        self,
    ) -> None:
        budgets = campaign_runtime.PURPOSE_POLICY_V1["purposes"]["CORE_ITERATION"][
            "budgets"
        ]

        # A bounded eight-call live run used 1,773,156 tokens after six of eight shards.
        self.assertEqual(budgets["hardReportedTokenCeiling"], 3_000_000)
        self.assertEqual(
            budgets["hardReportedInputTokenCeilingPerSample"], 300_000
        )
        self.assertEqual(budgets["hardReportedTokenCeilingPerSample"], 350_000)
        self.assertEqual(
            budgets["hardReportedInputTokenCeilingPerRepositoryShard"], 400_000
        )
        self.assertEqual(
            budgets["hardReportedTokenCeilingPerRepositoryShard"], 450_000
        )

    def test_purpose_policy_rejects_matrix_model_and_budget_tampering(self) -> None:
        mutations = (
            ("repositories", lambda plan: plan["selection"]["repositories"].append("x")),
            ("treatments", lambda plan: plan["selection"]["treatments"].pop()),
            ("repetitions", lambda plan: plan["selection"].update(repetitions=2)),
            (
                "models",
                lambda plan: plan["modelConfigurations"][0].update(
                    model="gpt-5.6-sol"
                ),
            ),
            (
                "budgets",
                lambda plan: plan["budgets"].update(sampleTimeoutSeconds=901),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                plan = self._purpose_plan("CANARY")
                mutate(plan)
                campaign_runtime.seal(plan)
                self.assertTrue(
                    campaign_runtime.validate_campaign_plan(
                        plan, self.suite, self.blind
                    )
                )

    def test_purpose_execution_requires_bound_current_adapter_controls(self) -> None:
        plan = self._purpose_plan("CANARY")
        for field in ("toolBudgetControlSha256", "toolTraceProtocol"):
            with self.subTest(field=field):
                missing = copy.deepcopy(plan)
                del missing["modelConfigurations"][0][field]
                campaign_runtime.seal(missing)
                self.assertTrue(
                    campaign_runtime.validate_campaign_plan(
                        missing, self.suite, self.blind
                    )
                )
                self.assertTrue(
                    any(
                        "requires bound pre-execution tool budget control and tool trace"
                        in error
                        for error in campaign_runtime.validate_campaign_plan_execution_safety(
                            missing
                        )
                    )
                )

        missing_control = copy.deepcopy(self.descriptions)
        del missing_control["fixture-standard"]["toolBudgetControl"]
        with self.assertRaisesRegex(
            contracts.RealRepositoryError,
            "lacks required pre-execution tool budget control",
        ):
            runner._validate_purpose_adapter_controls(plan, missing_control)

        missing_trace = copy.deepcopy(self.descriptions)
        del missing_trace["fixture-standard"]["toolTrace"]
        with self.assertRaisesRegex(
            contracts.RealRepositoryError,
            "lacks required tool trace",
        ):
            runner._validate_purpose_adapter_controls(plan, missing_trace)

    def test_execution_authorization_is_bound_to_exact_plan_hash(self) -> None:
        canary = self._purpose_plan("CANARY")
        args = SimpleNamespace(
            authorize_plan_sha256="0" * 64,
            allow_golden_campaign_sha256=None,
        )
        with self.assertRaisesRegex(
            contracts.RealRepositoryError, "authorize-plan-sha256"
        ):
            runner._validate_campaign_execution_authorization(args, canary)
        args.authorize_plan_sha256 = canary["contentSha256"]
        runner._validate_campaign_execution_authorization(args, canary)

        golden = self._purpose_plan("GOLDEN")
        args.authorize_plan_sha256 = golden["contentSha256"]
        with self.assertRaisesRegex(
            contracts.RealRepositoryError, "allow-golden-campaign-sha256"
        ):
            runner._validate_campaign_execution_authorization(args, golden)
        args.allow_golden_campaign_sha256 = golden["contentSha256"]
        runner._validate_campaign_execution_authorization(args, golden)

    def test_execution_rejects_review_craft_source_drift(self) -> None:
        plan = self._purpose_plan("CANARY")
        args = SimpleNamespace(skill_root=str(ROOT / "skills/review-craft"))
        runner._validate_review_craft_source_binding(args, plan)
        with tempfile.TemporaryDirectory() as directory:
            drifted = Path(directory) / "review-craft"
            drifted.mkdir()
            (drifted / "SKILL.md").write_text("drift\n", encoding="utf-8")
            args.skill_root = str(drifted)
            with self.assertRaisesRegex(
                contracts.RealRepositoryError, "source content differs"
            ):
                runner._validate_review_craft_source_binding(args, plan)

    def test_unbound_run_command_is_disabled_before_filesystem_access(self) -> None:
        with self.assertRaisesRegex(
            contracts.RealRepositoryError, "unbound campaign execution is disabled"
        ):
            runner.command_run_campaign(SimpleNamespace())

    def test_canary_promotion_receipt_requires_clean_structural_evidence(self) -> None:
        plan, campaign, ledger = self._completed_purpose_evidence("CANARY")
        receipt = campaign_runtime.build_promotion_receipt(
            plan=plan,
            campaign=campaign,
            budget_ledger=ledger,
            source_suite=self.suite,
            blind_suite=self.blind,
        )
        self.assertEqual(receipt["status"], "ELIGIBLE")
        self.assertEqual(receipt["comparisons"], [])
        self.assertEqual(
            campaign_runtime.validate_promotion_receipt(
                receipt,
                plan=plan,
                campaign=campaign,
                budget_ledger=ledger,
                source_suite=self.suite,
                blind_suite=self.blind,
            ),
            [],
        )

        stalled = copy.deepcopy(campaign)
        stalled["samples"][0]["lifecycle"]["inactivityState"] = (
            "RECOVERED_DIAGNOSTIC"
        )
        campaign_runtime.seal(stalled)
        blocked = campaign_runtime.build_promotion_receipt(
            plan=plan,
            campaign=stalled,
            budget_ledger=ledger,
            source_suite=self.suite,
            blind_suite=self.blind,
        )
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertIn("lifecycle-clean", {
            row["id"] for row in blocked["checks"] if not row["passed"]
        })

        stopped_ledger = copy.deepcopy(ledger)
        stopped_ledger["statusByShard"]["ALL"] = "STOPPED"
        campaign_runtime.seal(stopped_ledger)
        blocked = campaign_runtime.build_promotion_receipt(
            plan=plan,
            campaign=campaign,
            budget_ledger=stopped_ledger,
            source_suite=self.suite,
            blind_suite=self.blind,
        )
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertIn("budget-clean", {
            row["id"] for row in blocked["checks"] if not row["passed"]
        })

    def test_candidate_promotion_is_blocked_without_quality_evidence(self) -> None:
        plan, campaign, ledger = self._completed_purpose_evidence("CANDIDATE")
        receipt = campaign_runtime.build_promotion_receipt(
            plan=plan,
            campaign=campaign,
            budget_ledger=ledger,
            source_suite=self.suite,
            blind_suite=self.blind,
        )
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("quality-evidence", {
            row["id"] for row in receipt["checks"] if not row["passed"]
        })

    def test_candidate_promotion_requires_nonregression_and_strict_gain(self) -> None:
        plan, campaign, ledger = self._completed_purpose_evidence("CANDIDATE")
        adjudication, assessment = self._quality_evidence(campaign)
        receipt = campaign_runtime.build_promotion_receipt(
            plan=plan,
            campaign=campaign,
            budget_ledger=ledger,
            source_suite=self.suite,
            blind_suite=self.blind,
            adjudication=adjudication,
            oracle_assessment=assessment,
        )
        self.assertEqual(receipt["status"], "ELIGIBLE")
        self.assertEqual(len(receipt["comparisons"]), 4)
        self.assertTrue(all(row["passed"] for row in receipt["comparisons"]))
        evidence_rows = [
            row
            for row in receipt["comparisons"]
            if row["treatment"] == "REVIEW_CRAFT_EVIDENCE_LOOP"
        ]
        self.assertTrue(all(row["correctRateDelta"] > 0 for row in evidence_rows))
        self.assertTrue(
            all(row["exactOracleRecallDelta"] > 0 for row in evidence_rows)
        )

    def test_run_plan_rejects_coordinator_artifact_before_adapter_description(self) -> None:
        plan = self._purpose_plan("CANARY")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._write_inputs(root, plan)
            workspace = Path(args.workspace_root)
            runner.write_json(workspace / "suite.json", self.suite)

            with (
                patch.object(runner, "_describe_adapter") as describe,
                self.assertRaisesRegex(
                    contracts.RealRepositoryError,
                    "evaluator workspace must contain only the repositories directory",
                ),
            ):
                runner.command_run_campaign_plan(args)

            describe.assert_not_called()

    def test_run_plan_rejects_run_directory_inside_evaluator_before_writes(self) -> None:
        plan = self._purpose_plan("CANARY")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._write_inputs(root, plan)
            args.run_dir = str(Path(args.workspace_root) / "run")

            with (
                patch.object(runner, "_describe_adapter") as describe,
                self.assertRaisesRegex(
                    contracts.RealRepositoryError,
                    "campaign run directory must be outside evaluator workspace root",
                ),
            ):
                runner.command_run_campaign_plan(args)

            describe.assert_not_called()
            self.assertFalse(Path(args.run_dir).exists())
            self.assertFalse(Path(args.budget_ledger).exists())

    def test_run_plan_rejects_non_directory_run_target_before_writes(self) -> None:
        plan = self._purpose_plan("CANARY")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._write_inputs(root, plan)
            run_target = Path(args.run_dir)
            run_target.write_text("not a directory\n", encoding="utf-8")

            with (
                patch.object(runner, "_describe_adapter") as describe,
                self.assertRaisesRegex(
                    contracts.RealRepositoryError,
                    "campaign run directory must be a directory",
                ),
            ):
                runner.command_run_campaign_plan(args)

            describe.assert_not_called()
            self.assertFalse(Path(args.budget_ledger).exists())

    def test_run_plan_rejects_control_input_hidden_in_evaluator(self) -> None:
        repository_id = self.suite["repositories"][0]["id"]
        plan = self._purpose_plan("CANARY")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._write_inputs(root, plan)
            hidden_root = Path(args.workspace_root) / "repositories" / repository_id / ".git"
            hidden_root.mkdir()
            hidden_plan = hidden_root / "campaign-plan.json"
            Path(args.plan).replace(hidden_plan)
            args.plan = str(hidden_plan)

            with (
                patch.object(runner, "_describe_adapter") as describe,
                self.assertRaisesRegex(
                    contracts.RealRepositoryError,
                    "campaign plan must be outside evaluator workspace root",
                ),
            ):
                runner.command_run_campaign_plan(args)

            describe.assert_not_called()
            self.assertFalse(Path(args.budget_ledger).exists())

    def test_run_plan_validates_unselected_materialized_checkout(self) -> None:
        unselected_id = self.suite["repositories"][1]["id"]
        plan = self._purpose_plan("CANARY")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self._write_inputs(root, plan)
            unselected = Path(args.workspace_root) / "repositories" / unselected_id
            unselected.rmdir()
            unselected.write_text("not a checkout\n", encoding="utf-8")

            with (
                patch.object(runner, "_describe_adapter") as describe,
                patch.object(
                    runner,
                    "_repository_state",
                    side_effect=self._repository_state,
                ),
                self.assertRaisesRegex(
                    contracts.RealRepositoryError,
                    "materialized checkout must be a real directory",
                ),
            ):
                runner.command_run_campaign_plan(args)

            describe.assert_not_called()
            self.assertFalse(Path(args.run_dir).exists())

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

    def test_plan_campaign_cli_requires_purpose_and_rejects_matrix_overrides(
        self,
    ) -> None:
        parser = runner.build_parser()
        base = [
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
        ]
        with self.assertRaises(SystemExit):
            parser.parse_args(base)
        with self.assertRaises(SystemExit):
            parser.parse_args(base + ["--purpose", "CANARY", "--repetitions", "3"])
        args = parser.parse_args(base + ["--purpose", "CANARY"])
        self.assertEqual(args.purpose, "CANARY")

    def test_runner_consumes_purpose_bound_timeouts_without_inference(self) -> None:
        plan = self._purpose_plan("CANARY")
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
        self.assertEqual(set(observed.values()), {900})
        self.assertTrue(
            all(
                call.kwargs["first_item_timeout_seconds"] == 300
                for call in run_sample.call_args_list
            )
        )

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
        self.assertTrue(
            any(
                "campaignPurpose" in row
                for row in campaign_runtime.validate_campaign_plan_execution_safety(
                    plan
                )
            )
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

    def test_interrupted_golden_attempt_is_terminal_and_cannot_be_resumed(self) -> None:
        plan = self._purpose_plan("GOLDEN")
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
            self.assertEqual(partial_state["status"], "INTERRUPTED")
            self.assertEqual(partial_state["stopReason"], "OPERATOR_INTERRUPT")
            checkpoint = json.loads(
                (Path(args.run_dir) / "checkpoint.json").read_text(encoding="utf-8")
            )
            self.assertEqual(checkpoint["state"]["status"], "INTERRUPTED")
            ledger = json.loads(
                Path(args.budget_ledger).read_text(encoding="utf-8")
            )
            self.assertEqual(ledger["statusByShard"]["ALL"], "INTERRUPTED")

            args.resume = True
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
        plan = campaign_runtime.build_purpose_campaign_plan(
            source_suite=self.suite,
            blind_suite=self.blind,
            materialization=self.receipt,
            adapter_config=adapter_config,
            model_configurations=self.models,
            campaign_id="fixture-physical-campaign-144",
            campaign_purpose="GOLDEN",
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

    def test_runner_stops_after_first_infrastructure_failure(self) -> None:
        plan = self._purpose_plan("CANARY")
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
            self.assertEqual(run_sample.call_count, 1)
            self.assertEqual(state["status"], "STOPPED")
            self.assertEqual(state["stopReason"], "INFRASTRUCTURE_CIRCUIT_BREAKER")

    def test_runner_stops_after_first_timeout_for_one_profile(
        self,
    ) -> None:
        plan = self._purpose_plan("CANARY")
        outcomes = iter(("timeout",))

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
            self.assertEqual(run_sample.call_count, 1)
            self.assertEqual(state["status"], "STOPPED")
            self.assertEqual(
                state["stopReason"], "MODEL_PROFILE_TIMEOUT_BUDGET_EXCEEDED"
            )
            self.assertEqual(
                state["timedOutSamplesByModelProfile"], {"fixture-standard": 1}
            )
            self.assertEqual(
                campaign_runtime.budget_ledger_timed_out_samples_by_model_profile(
                    ledger
                ),
                {"fixture-standard": 1},
            )

    def test_runner_stops_after_first_recovered_inactivity_for_one_profile(
        self,
    ) -> None:
        plan = self._purpose_plan("CANARY")
        outcomes = iter(("stalled",))

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
            self.assertEqual(run_sample.call_count, 1)
            self.assertEqual(state["status"], "STOPPED")
            self.assertEqual(
                state["stopReason"],
                "MODEL_PROFILE_INACTIVITY_BUDGET_EXCEEDED",
            )
            self.assertEqual(
                state["recoveredInactivitySamplesByModelProfile"],
                {"fixture-standard": 1},
            )
            self.assertEqual(
                campaign_runtime.budget_ledger_recovered_inactivity_samples_by_model_profile(
                    ledger
                ),
                {"fixture-standard": 1},
            )

    def test_runner_stops_after_first_unknown_usage_sample(self) -> None:
        plan = self._purpose_plan("CANARY")
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
        plan = self._purpose_plan("CANARY")
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

    def test_runner_enforces_sample_token_ceiling_after_checkpoint(self) -> None:
        plan = self._purpose_plan("CANARY")
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
                    side_effect=lambda **kwargs: self._sample_with_usage(
                        input_tokens=200_000,
                        total_tokens=350_001,
                        **kwargs,
                    ),
                ) as run_sample,
            ):
                self.assertEqual(runner.command_run_campaign_plan(args), 0)
            state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(run_sample.call_count, 1)
            self.assertEqual(state["reportedTokens"], 350_001)
            self.assertEqual(state["stopReason"], "SAMPLE_TOKEN_CEILING")

    def test_final_sample_cost_ceiling_precedes_schedule_completion(self) -> None:
        plan = self._purpose_plan("CANARY")
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
                    side_effect=lambda **kwargs: self._sample_with_usage(
                        input_tokens=120_000,
                        total_tokens=140_000,
                        **kwargs,
                    ),
                ) as run_sample,
            ):
                self.assertEqual(runner.command_run_campaign_plan(args), 0)
            state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(encoding="utf-8")
            )
            ledger = json.loads(
                Path(args.budget_ledger).read_text(encoding="utf-8")
            )
            self.assertEqual(run_sample.call_count, 6)
            self.assertEqual(state["status"], "STOPPED")
            self.assertEqual(state["stopReason"], "SHARD_TOKEN_CEILING")
            self.assertEqual(state["reportedTokens"], 840_000)
            self.assertEqual(campaign_runtime.budget_ledger_totals(ledger)[0], 840_000)

    def test_final_sample_failure_budgets_precede_schedule_completion(self) -> None:
        plan = self._purpose_plan("CANARY")

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
            ("STOPPED", "TOKEN_CEILING"),
        )

    def test_runner_stops_after_sample_input_token_ceiling(self) -> None:
        plan = self._purpose_plan("CANARY")
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
                    side_effect=lambda **kwargs: self._sample_with_usage(
                        input_tokens=300_001,
                        total_tokens=310_000,
                        **kwargs,
                    ),
                ) as run_sample,
            ):
                self.assertEqual(runner.command_run_campaign_plan(args), 0)
            state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(run_sample.call_count, 1)
            self.assertEqual(state["stopReason"], "SAMPLE_INPUT_TOKEN_CEILING")

    def test_shared_budget_ledger_executes_each_candidate_shard_once(self) -> None:
        repositories = [row["id"] for row in self.suite["repositories"][:2]]
        plan = self._purpose_plan("CANDIDATE")
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
            self.assertEqual(first.call_count, 6)

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
            self.assertEqual(second.call_count, 6)
            second_state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(encoding="utf-8")
            )
            ledger = json.loads(
                Path(args.budget_ledger).read_text(encoding="utf-8")
            )
            self.assertEqual(second_state["stopReason"], "SCHEDULE_COMPLETE")
            self.assertEqual(
                campaign_runtime.budget_ledger_totals(ledger)[0], 1_440
            )

    def test_recovered_inactivity_budget_blocks_later_candidate_shards(self) -> None:
        repositories = [row["id"] for row in self.suite["repositories"][:2]]
        plan = self._purpose_plan("CANDIDATE")
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
            self.assertEqual(first.call_count, 1)
            first_state = json.loads(
                (Path(args.run_dir) / "run-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(first_state["status"], "STOPPED")
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
            self.assertEqual(second.call_count, 0)
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
                {"fixture-standard": 1},
            )

    def test_final_recovered_inactivity_completes_shard_and_blocks_next_shard(
        self,
    ) -> None:
        repositories = [row["id"] for row in self.suite["repositories"][:2]]
        plan = self._purpose_plan("CANDIDATE")
        first_calls = 0

        def first_shard_sample(**kwargs: object) -> dict:
            nonlocal first_calls
            first_calls += 1
            if first_calls == 6:
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
            self.assertEqual(first_state["status"], "STOPPED")
            self.assertEqual(
                first_state["stopReason"],
                "MODEL_PROFILE_INACTIVITY_BUDGET_EXCEEDED",
            )
            self.assertEqual(
                first_state["recoveredInactivitySamplesByModelProfile"],
                {"fixture-assured": 1},
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
                {"fixture-assured": 1},
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
