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
        token_ceiling: int = 60_000_000,
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
            repetitions=3,
            sample_timeout_seconds=1800,
            soft_wall_time_seconds=64800,
            hard_wall_time_seconds=86400,
            hard_reported_token_ceiling=token_ceiling,
            max_consecutive_infrastructure_failures=2,
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
        output = self._output(repository)
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
        self.assertEqual({row["timeoutSeconds"] for row in first["samples"]}, {1800})
        self.assertEqual(len({row["shardId"] for row in first["samples"]}), 8)

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
