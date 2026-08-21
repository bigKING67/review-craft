from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from real_repository_contracts import (
    TREATMENTS,
    RealRepositoryError,
    schema_errors,
    sha256_json,
)

ROOT = Path(__file__).resolve().parents[1]
PLAN_SCHEMA = ROOT / "evals/schemas/eval-real-repository-campaign-plan.schema.json"
RUN_STATE_SCHEMA = ROOT / "evals/schemas/eval-real-repository-run-state.schema.json"
MERGE_SCHEMA = ROOT / "evals/schemas/eval-real-repository-campaign-merge.schema.json"
CHECKPOINT_SCHEMA = ROOT / "evals/schemas/eval-real-repository-run-checkpoint.schema.json"
BUDGET_LEDGER_SCHEMA = ROOT / "evals/schemas/eval-real-repository-budget-ledger.schema.json"

INFRASTRUCTURE_FAILURE_CLASSES = {
    "AUTHENTICATION",
    "PROVIDER_CONNECTIVITY",
    "ADAPTER_CONTRACT",
    "MODEL_UNAVAILABLE",
}


def _without_content_hash(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "contentSha256"}


def seal(payload: dict[str, Any]) -> dict[str, Any]:
    payload["contentSha256"] = sha256_json(_without_content_hash(payload))
    return payload


def sample_id(
    repository_id: str,
    treatment: str,
    model_configuration_id: str,
    repetition: int,
) -> str:
    treatment_slug = treatment.lower().replace("_", "-")
    return (
        f"{repository_id}--{treatment_slug}--"
        f"{model_configuration_id}--r{repetition}"
    )


def build_campaign_plan(
    *,
    source_suite: dict[str, Any],
    blind_suite: dict[str, Any],
    materialization: dict[str, Any],
    adapter_config: dict[str, Any],
    model_configurations: list[dict[str, Any]],
    campaign_id: str,
    repository_ids: list[str],
    treatments: list[str],
    repetitions: int,
    sample_timeout_seconds: int,
    soft_wall_time_seconds: int,
    hard_wall_time_seconds: int,
    hard_reported_token_ceiling: int,
    max_consecutive_infrastructure_failures: int,
    max_unknown_usage_samples: int = 1,
    max_timed_out_samples_per_model_profile: int = 1,
    max_artifact_invalid_samples: int = 1,
) -> dict[str, Any]:
    model_ids = [row["id"] for row in model_configurations]
    all_repository_ids = [row["id"] for row in source_suite["repositories"]]
    full_matrix = (
        repository_ids == all_repository_ids
        and treatments == list(TREATMENTS)
        and len(model_ids)
        >= source_suite["protocol"]["minimumModelConfigurations"]
        and repetitions >= source_suite["protocol"]["repetitions"]
    )
    samples: list[dict[str, Any]] = []
    ordinal = 0
    for repository_id in repository_ids:
        for treatment in treatments:
            for model_id in model_ids:
                for repetition in range(1, repetitions + 1):
                    ordinal += 1
                    samples.append(
                        {
                            "ordinal": ordinal,
                            "sampleId": sample_id(
                                repository_id,
                                treatment,
                                model_id,
                                repetition,
                            ),
                            "repositoryId": repository_id,
                            "treatment": treatment,
                            "modelConfigurationId": model_id,
                            "repetition": repetition,
                            "shardId": repository_id,
                            "timeoutSeconds": sample_timeout_seconds,
                        }
                    )
    payload = {
        "schema": "review-craft.eval-real-repository-campaign-plan.v1",
        "campaignId": campaign_id,
        "suiteSha256": sha256_json(source_suite),
        "blindSuiteSha256": blind_suite["contentSha256"],
        "materializationContentSha256": materialization["contentSha256"],
        "adapterConfigSha256": sha256_json(adapter_config),
        "modelConfigurations": model_configurations,
        "selection": {
            "repositories": repository_ids,
            "treatments": treatments,
            "modelConfigurations": model_ids,
            "repetitions": repetitions,
            "fullMatrix": full_matrix,
        },
        "budgets": {
            "sampleTimeoutSeconds": sample_timeout_seconds,
            "softWallTimeSeconds": soft_wall_time_seconds,
            "hardWallTimeSeconds": hard_wall_time_seconds,
            "hardReportedTokenCeiling": hard_reported_token_ceiling,
            "maxConsecutiveInfrastructureFailures": (
                max_consecutive_infrastructure_failures
            ),
            "maxUnknownUsageSamples": max_unknown_usage_samples,
            "maxTimedOutSamplesPerModelProfile": (
                max_timed_out_samples_per_model_profile
            ),
            "maxArtifactInvalidSamples": max_artifact_invalid_samples,
        },
        "samples": samples,
        "contentSha256": "0" * 64,
    }
    seal(payload)
    errors = validate_campaign_plan(payload, source_suite, blind_suite)
    if errors:
        raise RealRepositoryError("invalid generated campaign plan: " + "; ".join(errors))
    return payload


def validate_campaign_plan(
    payload: dict[str, Any],
    source_suite: dict[str, Any],
    blind_suite: dict[str, Any],
) -> list[str]:
    errors = schema_errors(payload, PLAN_SCHEMA)
    if errors:
        return errors
    if payload["contentSha256"] != sha256_json(_without_content_hash(payload)):
        errors.append("campaign plan contentSha256 mismatch")
    if payload["suiteSha256"] != sha256_json(source_suite):
        errors.append("campaign plan suiteSha256 mismatch")
    if payload["blindSuiteSha256"] != blind_suite["contentSha256"]:
        errors.append("campaign plan blindSuiteSha256 mismatch")
    errors.extend(_plan_selection_errors(payload, source_suite))
    if payload["samples"] != _expected_plan_samples(payload):
        errors.append("campaign plan samples do not match the deterministic matrix")
    return errors


def _plan_selection_errors(
    payload: dict[str, Any], source_suite: dict[str, Any]
) -> list[str]:
    errors: list[str] = []

    selection = payload["selection"]
    suite_repository_ids = [row["id"] for row in source_suite["repositories"]]
    repository_ids = selection["repositories"]
    if repository_ids != [row for row in suite_repository_ids if row in repository_ids]:
        errors.append("campaign plan repositories must use unique canonical suite order")
    treatments = selection["treatments"]
    if treatments != [row for row in TREATMENTS if row in treatments]:
        errors.append("campaign plan treatments must use unique canonical order")
    model_ids = [row["id"] for row in payload["modelConfigurations"]]
    if len(model_ids) != len(set(model_ids)):
        errors.append("campaign plan contains duplicate model configuration ids")
    if selection["modelConfigurations"] != model_ids:
        errors.append("campaign plan selection model configuration order mismatch")
    budgets = payload["budgets"]
    if budgets["hardWallTimeSeconds"] < budgets["softWallTimeSeconds"]:
        errors.append("campaign plan hard wall time must not be below soft wall time")
    previous_failure_budgets = {
        "maxUnknownUsageSamples",
        "maxTimedOutSamplesPerModelProfile",
    }
    current_failure_budgets = previous_failure_budgets | {
        "maxArtifactInvalidSamples"
    }
    present_failure_budgets = frozenset(current_failure_budgets.intersection(budgets))
    if present_failure_budgets not in {
        frozenset(),
        frozenset(previous_failure_budgets),
        frozenset(current_failure_budgets),
    }:
        errors.append(
            "campaign plan cumulative failure budgets must be declared together"
        )

    expected_full_matrix = (
        repository_ids == suite_repository_ids
        and treatments == list(TREATMENTS)
        and len(model_ids)
        >= source_suite["protocol"]["minimumModelConfigurations"]
        and selection["repetitions"] >= source_suite["protocol"]["repetitions"]
    )
    if selection["fullMatrix"] != expected_full_matrix:
        errors.append("campaign plan selection fullMatrix is inconsistent")
    return errors


def _expected_plan_samples(payload: dict[str, Any]) -> list[dict[str, Any]]:
    selection = payload["selection"]
    repository_ids = selection["repositories"]
    treatments = selection["treatments"]
    model_ids = selection["modelConfigurations"]
    budgets = payload["budgets"]
    expected: list[dict[str, Any]] = []
    ordinal = 0
    for repository_id in repository_ids:
        for treatment in treatments:
            for model_id in model_ids:
                for repetition in range(1, selection["repetitions"] + 1):
                    ordinal += 1
                    expected.append(
                        {
                            "ordinal": ordinal,
                            "sampleId": sample_id(
                                repository_id,
                                treatment,
                                model_id,
                                repetition,
                            ),
                            "repositoryId": repository_id,
                            "treatment": treatment,
                            "modelConfigurationId": model_id,
                            "repetition": repetition,
                            "shardId": repository_id,
                            "timeoutSeconds": budgets["sampleTimeoutSeconds"],
                        }
                    )
    return expected


def validate_plan_inputs(
    payload: dict[str, Any],
    *,
    materialization: dict[str, Any],
    adapter_config: dict[str, Any],
    model_configurations: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    if payload["materializationContentSha256"] != materialization["contentSha256"]:
        errors.append("campaign plan materializationContentSha256 mismatch")
    if payload["adapterConfigSha256"] != sha256_json(adapter_config):
        errors.append("campaign plan adapterConfigSha256 mismatch")
    if payload["modelConfigurations"] != model_configurations:
        errors.append("campaign plan live adapter descriptions changed")
    materialized_ids = {row["id"] for row in materialization["repositories"]}
    missing = set(payload["selection"]["repositories"]) - materialized_ids
    if missing:
        errors.append(
            "campaign plan repositories are not materialized: "
            + ", ".join(sorted(missing))
        )
    return errors


def selected_plan_samples(
    plan: dict[str, Any], shard_id: str
) -> list[dict[str, Any]]:
    if shard_id == "ALL":
        return list(plan["samples"])
    selected = [row for row in plan["samples"] if row["shardId"] == shard_id]
    if not selected:
        raise RealRepositoryError(f"unknown or empty campaign shard: {shard_id}")
    return selected


def campaign_status(
    samples: list[dict[str, Any]],
    plan: dict[str, Any],
) -> str:
    completed_ids = {
        row["sampleId"]
        for row in samples
        if row["status"] == "COMPLETED" and not row["sourceMutationDetected"]
    }
    plan_ids = {row["sampleId"] for row in plan["samples"]}
    if plan["selection"]["fullMatrix"] and completed_ids == plan_ids:
        return "COMPLETED"
    if completed_ids:
        return "PARTIAL"
    return "FAILED"


def failure_tail(samples: list[dict[str, Any]]) -> int:
    count = 0
    for sample in reversed(samples):
        if sample.get("failureClass") not in INFRASTRUCTURE_FAILURE_CLASSES:
            break
        count += 1
    return count


def usage_totals(samples: list[dict[str, Any]]) -> tuple[int, int]:
    reported = 0
    unknown = 0
    for sample in samples:
        total = sample["usage"]["totalTokens"]
        if total is None:
            unknown += 1
        else:
            reported += total
    return reported, unknown


def timed_out_samples_by_model_profile(
    samples: list[dict[str, Any]],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for sample in samples:
        if sample["status"] != "TIMED_OUT" and sample.get("failureClass") != "TIMEOUT":
            continue
        model_id = sample["modelConfiguration"]["id"]
        counts[model_id] = counts.get(model_id, 0) + 1
    return counts


def artifact_invalid_samples(samples: list[dict[str, Any]]) -> int:
    return sum(
        sample.get("failureClass") == "ARTIFACT_INVALID" for sample in samples
    )


def new_run_state(
    *,
    plan: dict[str, Any],
    campaign_id: str,
    shard_id: str,
    now: str,
) -> dict[str, Any]:
    state = {
        "schema": "review-craft.eval-real-repository-run-state.v1",
        "planContentSha256": plan["contentSha256"],
        "campaignId": campaign_id,
        "shardId": shard_id,
        "status": "RUNNING",
        "stopReason": None,
        "startedAt": now,
        "updatedAt": now,
        "elapsedSeconds": 0.0,
        "reportedTokens": 0,
        "unknownUsageSamples": 0,
        "timedOutSamplesByModelProfile": {},
        "consecutiveInfrastructureFailures": 0,
        "attemptedSampleIds": [],
        "campaignContentSha256": None,
        "contentSha256": "0" * 64,
    }
    if "maxArtifactInvalidSamples" in plan["budgets"]:
        state["artifactInvalidSamples"] = 0
    return seal(state)


def new_budget_ledger(plan: dict[str, Any], *, now: str) -> dict[str, Any]:
    ledger = {
        "schema": "review-craft.eval-real-repository-budget-ledger.v1",
        "planContentSha256": plan["contentSha256"],
        "reportedTokensByShard": {},
        "unknownUsageSamplesByShard": {},
        "timedOutSamplesByModelProfileByShard": {},
        "elapsedSecondsByShard": {},
        "attemptedSamplesByShard": {},
        "infrastructureFailureTailByShard": {},
        "statusByShard": {},
        "executionOrder": [],
        "updatedAt": now,
        "contentSha256": "0" * 64,
    }
    if "maxArtifactInvalidSamples" in plan["budgets"]:
        ledger["artifactInvalidSamplesByShard"] = {}
    return seal(ledger)


def validate_budget_ledger(
    ledger: dict[str, Any], plan: dict[str, Any]
) -> list[str]:
    errors = schema_errors(ledger, BUDGET_LEDGER_SCHEMA)
    if errors:
        return errors
    if ledger["contentSha256"] != sha256_json(_without_content_hash(ledger)):
        errors.append("campaign budget ledger contentSha256 mismatch")
    if ledger["planContentSha256"] != plan["contentSha256"]:
        errors.append("campaign budget ledger planContentSha256 mismatch")
    key_sets = {
        frozenset(ledger["reportedTokensByShard"]),
        frozenset(ledger["unknownUsageSamplesByShard"]),
        frozenset(ledger["elapsedSecondsByShard"]),
        frozenset(ledger["attemptedSamplesByShard"]),
        frozenset(ledger["infrastructureFailureTailByShard"]),
        frozenset(ledger["statusByShard"]),
        frozenset(ledger["executionOrder"]),
    }
    timeout_map = ledger.get("timedOutSamplesByModelProfileByShard")
    if timeout_map is not None:
        key_sets.add(frozenset(timeout_map))
    artifact_map = ledger.get("artifactInvalidSamplesByShard")
    if artifact_map is not None:
        key_sets.add(frozenset(artifact_map))
    if (
        "maxTimedOutSamplesPerModelProfile" in plan["budgets"]
        and timeout_map is None
    ):
        errors.append(
            "campaign budget ledger is missing model-profile timeout accounting"
        )
    if "maxArtifactInvalidSamples" in plan["budgets"] and artifact_map is None:
        errors.append(
            "campaign budget ledger is missing artifact-invalid accounting"
        )
    if len(key_sets) != 1:
        errors.append("campaign budget ledger shard sets differ")
        return errors
    known_shards = {row["shardId"] for row in plan["samples"]} | {"ALL"}
    unknown = set(ledger["reportedTokensByShard"]) - known_shards
    if unknown:
        errors.append(
            "campaign budget ledger contains unknown shards: "
            + ", ".join(sorted(unknown))
        )
    if "ALL" in ledger["reportedTokensByShard"] and len(
        ledger["reportedTokensByShard"]
    ) > 1:
        errors.append("campaign budget ledger cannot mix ALL with repository shards")
    if len(ledger["executionOrder"]) != len(set(ledger["executionOrder"])):
        errors.append("campaign budget ledger executionOrder contains duplicates")
    for shard_id in ledger["executionOrder"]:
        attempted = ledger["attemptedSamplesByShard"][shard_id]
        failure_tail = ledger["infrastructureFailureTailByShard"][shard_id]
        if failure_tail > attempted:
            errors.append(
                f"campaign budget ledger shard {shard_id} failure tail exceeds attempts"
            )
        if timeout_map is not None:
            profile_counts = timeout_map[shard_id]
            known_model_ids = {
                row["id"] for row in plan["modelConfigurations"]
            }
            unknown_model_ids = set(profile_counts) - known_model_ids
            if unknown_model_ids:
                errors.append(
                    f"campaign budget ledger shard {shard_id} contains unknown model "
                    "profiles: " + ", ".join(sorted(unknown_model_ids))
                )
            if sum(profile_counts.values()) > attempted:
                errors.append(
                    f"campaign budget ledger shard {shard_id} timeout count exceeds "
                    "attempts"
                )
        if artifact_map is not None and artifact_map[shard_id] > attempted:
            errors.append(
                f"campaign budget ledger shard {shard_id} artifact-invalid count "
                "exceeds attempts"
            )
    running = [
        shard_id
        for shard_id, status in ledger["statusByShard"].items()
        if status == "RUNNING"
    ]
    if len(running) > 1:
        errors.append("campaign budget ledger contains multiple running shards")
    if running and ledger["executionOrder"][-1:] != running:
        errors.append("campaign budget ledger running shard must be last")
    return errors


def update_budget_ledger(
    ledger: dict[str, Any], state: dict[str, Any], *, now: str
) -> dict[str, Any]:
    shard_id = state["shardId"]
    existing = set(ledger["reportedTokensByShard"])
    if (shard_id == "ALL" and existing - {"ALL"}) or (
        shard_id != "ALL" and "ALL" in existing
    ):
        raise RealRepositoryError(
            "campaign budget ledger cannot mix ALL execution with repository shards"
        )
    if shard_id not in existing:
        running = [
            key for key, status in ledger["statusByShard"].items() if status == "RUNNING"
        ]
        if running:
            raise RealRepositoryError(
                "campaign budget ledger has an unfinished shard: " + running[0]
            )
        ledger["executionOrder"].append(shard_id)
    elif ledger["executionOrder"][-1] != shard_id:
        raise RealRepositoryError(
            "campaign budget ledger can update only its latest shard"
        )
    ledger["reportedTokensByShard"][shard_id] = state["reportedTokens"]
    ledger["unknownUsageSamplesByShard"][shard_id] = state[
        "unknownUsageSamples"
    ]
    if "timedOutSamplesByModelProfileByShard" in ledger:
        ledger["timedOutSamplesByModelProfileByShard"][shard_id] = state.get(
            "timedOutSamplesByModelProfile", {}
        )
    if "artifactInvalidSamplesByShard" in ledger:
        ledger["artifactInvalidSamplesByShard"][shard_id] = state.get(
            "artifactInvalidSamples", 0
        )
    ledger["elapsedSecondsByShard"][shard_id] = state["elapsedSeconds"]
    ledger["attemptedSamplesByShard"][shard_id] = len(
        state["attemptedSampleIds"]
    )
    ledger["infrastructureFailureTailByShard"][shard_id] = state[
        "consecutiveInfrastructureFailures"
    ]
    ledger["statusByShard"][shard_id] = state["status"]
    ledger["updatedAt"] = now
    return seal(ledger)


def budget_ledger_totals(ledger: dict[str, Any]) -> tuple[int, int, float, int]:
    infrastructure_failure_tail = 0
    for shard_id in ledger["executionOrder"]:
        attempted = ledger["attemptedSamplesByShard"][shard_id]
        local_tail = ledger["infrastructureFailureTailByShard"][shard_id]
        if attempted == 0:
            continue
        if local_tail == attempted:
            infrastructure_failure_tail += local_tail
        else:
            infrastructure_failure_tail = local_tail
    return (
        sum(ledger["reportedTokensByShard"].values()),
        sum(ledger["unknownUsageSamplesByShard"].values()),
        round(sum(ledger["elapsedSecondsByShard"].values()), 3),
        infrastructure_failure_tail,
    )


def budget_ledger_timed_out_samples_by_model_profile(
    ledger: dict[str, Any],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for profile_counts in ledger.get(
        "timedOutSamplesByModelProfileByShard", {}
    ).values():
        for model_id, count in profile_counts.items():
            counts[model_id] = counts.get(model_id, 0) + count
    return counts


def budget_ledger_artifact_invalid_samples(ledger: dict[str, Any]) -> int:
    return sum(ledger.get("artifactInvalidSamplesByShard", {}).values())


def validate_budget_ledger_state(
    ledger: dict[str, Any], state: dict[str, Any]
) -> list[str]:
    shard_id = state["shardId"]
    maps = (
        "reportedTokensByShard",
        "unknownUsageSamplesByShard",
        "elapsedSecondsByShard",
        "attemptedSamplesByShard",
        "infrastructureFailureTailByShard",
        "statusByShard",
    )
    if (
        "timedOutSamplesByModelProfileByShard" in ledger
        or "timedOutSamplesByModelProfile" in state
    ):
        maps = (*maps, "timedOutSamplesByModelProfileByShard")
    if (
        "artifactInvalidSamplesByShard" in ledger
        or "artifactInvalidSamples" in state
    ):
        maps = (*maps, "artifactInvalidSamplesByShard")
    if any(shard_id not in ledger.get(key, {}) for key in maps):
        return [f"campaign budget ledger is missing shard: {shard_id}"]
    expected = (
        state["reportedTokens"],
        state["unknownUsageSamples"],
        state["elapsedSeconds"],
        len(state["attemptedSampleIds"]),
        state["consecutiveInfrastructureFailures"],
        state["status"],
    )
    if "timedOutSamplesByModelProfileByShard" in maps:
        expected = (*expected, state.get("timedOutSamplesByModelProfile", {}))
    if "artifactInvalidSamplesByShard" in maps:
        expected = (*expected, state.get("artifactInvalidSamples", 0))
    actual = (
        ledger["reportedTokensByShard"][shard_id],
        ledger["unknownUsageSamplesByShard"][shard_id],
        ledger["elapsedSecondsByShard"][shard_id],
        ledger["attemptedSamplesByShard"][shard_id],
        ledger["infrastructureFailureTailByShard"][shard_id],
        ledger["statusByShard"][shard_id],
    )
    if "timedOutSamplesByModelProfileByShard" in maps:
        actual = (
            *actual,
            ledger["timedOutSamplesByModelProfileByShard"][shard_id],
        )
    if "artifactInvalidSamplesByShard" in maps:
        actual = (*actual, ledger["artifactInvalidSamplesByShard"][shard_id])
    if actual != expected:
        return [f"campaign budget ledger shard {shard_id} differs from run state"]
    return []


def update_run_state(
    state: dict[str, Any],
    *,
    campaign: dict[str, Any],
    elapsed_seconds: float,
    now: str,
    status: str = "RUNNING",
    stop_reason: str | None = None,
) -> dict[str, Any]:
    reported, unknown = usage_totals(campaign["samples"])
    state.update(
        {
            "status": status,
            "stopReason": stop_reason,
            "updatedAt": now,
            "elapsedSeconds": max(0.0, round(elapsed_seconds, 3)),
            "reportedTokens": reported,
            "unknownUsageSamples": unknown,
            "timedOutSamplesByModelProfile": timed_out_samples_by_model_profile(
                campaign["samples"]
            ),
            "consecutiveInfrastructureFailures": failure_tail(campaign["samples"]),
            "attemptedSampleIds": [row["sampleId"] for row in campaign["samples"]],
            "campaignContentSha256": campaign["contentSha256"],
        }
    )
    if "artifactInvalidSamples" in state:
        state["artifactInvalidSamples"] = artifact_invalid_samples(
            campaign["samples"]
        )
    return seal(state)


def validate_run_state(
    state: dict[str, Any],
    plan: dict[str, Any],
    campaign: dict[str, Any],
) -> list[str]:
    errors = schema_errors(state, RUN_STATE_SCHEMA)
    if errors:
        return errors
    if state["contentSha256"] != sha256_json(_without_content_hash(state)):
        errors.append("campaign run state contentSha256 mismatch")
    if state["planContentSha256"] != plan["contentSha256"]:
        errors.append("campaign run state planContentSha256 mismatch")
    if state["campaignContentSha256"] != campaign["contentSha256"]:
        errors.append("campaign run state campaignContentSha256 mismatch")
    if state["campaignId"] != campaign["campaignId"]:
        errors.append("campaign run state campaignId mismatch")
    selected = selected_plan_samples(plan, state["shardId"])
    selected_ids = [row["sampleId"] for row in selected]
    attempted_ids = [row["sampleId"] for row in campaign["samples"]]
    if attempted_ids != selected_ids[: len(attempted_ids)]:
        errors.append("campaign samples are not an exact plan prefix for the shard")
    if state["attemptedSampleIds"] != attempted_ids:
        errors.append("campaign run state attemptedSampleIds mismatch")
    reported, unknown = usage_totals(campaign["samples"])
    if state["reportedTokens"] != reported:
        errors.append("campaign run state reportedTokens mismatch")
    if state["unknownUsageSamples"] != unknown:
        errors.append("campaign run state unknownUsageSamples mismatch")
    expected_timeouts = timed_out_samples_by_model_profile(campaign["samples"])
    if (
        "maxTimedOutSamplesPerModelProfile" in plan["budgets"]
        or "timedOutSamplesByModelProfile" in state
    ) and state.get("timedOutSamplesByModelProfile") != expected_timeouts:
        errors.append("campaign run state model-profile timeout accounting mismatch")
    expected_artifact_invalid = artifact_invalid_samples(campaign["samples"])
    if (
        "maxArtifactInvalidSamples" in plan["budgets"]
        or "artifactInvalidSamples" in state
    ) and state.get("artifactInvalidSamples") != expected_artifact_invalid:
        errors.append("campaign run state artifact-invalid accounting mismatch")
    if state["consecutiveInfrastructureFailures"] != failure_tail(
        campaign["samples"]
    ):
        errors.append("campaign run state infrastructure failure tail mismatch")
    errors.extend(_run_state_status_errors(state, attempted_ids, selected_ids))
    return errors


def _run_state_status_errors(
    state: dict[str, Any], attempted_ids: list[str], selected_ids: list[str]
) -> list[str]:
    errors: list[str] = []
    if state["status"] == "COMPLETED":
        if state["stopReason"] != "SCHEDULE_COMPLETE":
            errors.append("completed campaign run state requires SCHEDULE_COMPLETE")
        if attempted_ids != selected_ids:
            errors.append("completed campaign run state requires the complete shard schedule")
    elif state["status"] == "RUNNING" and state["stopReason"] is not None:
        errors.append("running campaign run state must not contain stopReason")
    elif state["status"] == "STOPPED" and state["stopReason"] not in {
        "SOFT_WALL_TIME",
        "HARD_WALL_TIME",
        "TOKEN_CEILING",
        "INFRASTRUCTURE_CIRCUIT_BREAKER",
        "UNKNOWN_USAGE_BUDGET_EXCEEDED",
        "MODEL_PROFILE_TIMEOUT_BUDGET_EXCEEDED",
        "ARTIFACT_INVALID_BUDGET_EXCEEDED",
    }:
        errors.append("stopped campaign run state requires a budget or circuit stop")
    elif state["status"] == "FAILED" and state["stopReason"] not in {
        "SOURCE_MUTATION",
        "CREDENTIAL_EXPOSURE",
        "INTEGRITY_FAILURE",
    }:
        errors.append("failed campaign run state requires an integrity or safety stop")
    return errors


def build_checkpoint(
    *,
    plan: dict[str, Any],
    campaign: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    checkpoint = seal(
        {
            "schema": "review-craft.eval-real-repository-run-checkpoint.v1",
            "planContentSha256": plan["contentSha256"],
            "campaignContentSha256": campaign["contentSha256"],
            "state": state,
            "contentSha256": "0" * 64,
        }
    )
    errors = schema_errors(checkpoint, CHECKPOINT_SCHEMA)
    if errors:
        raise RealRepositoryError("invalid run checkpoint: " + "; ".join(errors))
    return checkpoint


def validate_checkpoint(
    checkpoint: dict[str, Any],
    plan: dict[str, Any],
    campaign: dict[str, Any],
) -> list[str]:
    errors = schema_errors(checkpoint, CHECKPOINT_SCHEMA)
    if errors:
        return errors
    if checkpoint["contentSha256"] != sha256_json(
        _without_content_hash(checkpoint)
    ):
        errors.append("campaign run checkpoint contentSha256 mismatch")
    if checkpoint["planContentSha256"] != plan["contentSha256"]:
        errors.append("campaign run checkpoint planContentSha256 mismatch")
    if checkpoint["campaignContentSha256"] != campaign["contentSha256"]:
        errors.append("campaign run checkpoint campaignContentSha256 mismatch")
    errors.extend(validate_run_state(checkpoint["state"], plan, campaign))
    return errors


def validate_sample_against_plan(
    sample: dict[str, Any],
    plan_sample: dict[str, Any],
    model_configurations: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    expected = (
        plan_sample["sampleId"],
        plan_sample["repositoryId"],
        plan_sample["treatment"],
        plan_sample["repetition"],
    )
    actual = (
        sample["sampleId"],
        sample["repositoryId"],
        sample["treatment"],
        sample["repetition"],
    )
    if actual != expected:
        errors.append(f"campaign sample {sample['sampleId']} does not match its plan cell")
    expected_model = model_configurations.get(plan_sample["modelConfigurationId"])
    if sample["modelConfiguration"] != expected_model:
        errors.append(
            f"campaign sample {sample['sampleId']} model configuration differs from plan"
        )
    return errors


def budget_stop_reason(
    *,
    budgets: dict[str, int],
    elapsed_seconds: float,
    reported_tokens: int,
    consecutive_infrastructure_failures: int,
    unknown_usage_samples: int = 0,
    timed_out_samples_by_model_profile: dict[str, int] | None = None,
    artifact_invalid_samples: int = 0,
) -> str | None:
    if reported_tokens >= budgets["hardReportedTokenCeiling"]:
        return "TOKEN_CEILING"
    if elapsed_seconds >= budgets["hardWallTimeSeconds"]:
        return "HARD_WALL_TIME"
    max_timeouts = budgets.get("maxTimedOutSamplesPerModelProfile")
    if max_timeouts is not None and any(
        count >= max_timeouts
        for count in (timed_out_samples_by_model_profile or {}).values()
    ):
        return "MODEL_PROFILE_TIMEOUT_BUDGET_EXCEEDED"
    max_artifact_invalid = budgets.get("maxArtifactInvalidSamples")
    if (
        max_artifact_invalid is not None
        and artifact_invalid_samples >= max_artifact_invalid
    ):
        return "ARTIFACT_INVALID_BUDGET_EXCEEDED"
    max_unknown_usage = budgets.get("maxUnknownUsageSamples")
    if (
        max_unknown_usage is not None
        and unknown_usage_samples >= max_unknown_usage
    ):
        return "UNKNOWN_USAGE_BUDGET_EXCEEDED"
    if elapsed_seconds >= budgets["softWallTimeSeconds"]:
        return "SOFT_WALL_TIME"
    if (
        consecutive_infrastructure_failures
        >= budgets["maxConsecutiveInfrastructureFailures"]
    ):
        return "INFRASTRUCTURE_CIRCUIT_BREAKER"
    return None


def effective_sample_timeout(
    *,
    sample_timeout_seconds: int,
    hard_wall_time_seconds: int,
    elapsed_seconds: float,
) -> int:
    remaining = hard_wall_time_seconds - elapsed_seconds
    if remaining <= 0:
        return 0
    return min(sample_timeout_seconds, max(1, math.ceil(remaining)))


def merge_campaigns(
    *,
    plan: dict[str, Any],
    campaigns: list[dict[str, Any]],
) -> dict[str, Any]:
    ordinal_by_id = {row["sampleId"]: row["ordinal"] for row in plan["samples"]}
    model_configurations = {
        row["id"]: row for row in plan["modelConfigurations"]
    }
    plan_by_id = {row["sampleId"]: row for row in plan["samples"]}
    samples_by_id: dict[str, dict[str, Any]] = {}
    for campaign in campaigns:
        for sample in campaign["samples"]:
            sample_key = sample["sampleId"]
            plan_sample = plan_by_id.get(sample_key)
            if plan_sample is None:
                raise RealRepositoryError(
                    f"campaign merge contains sample outside plan: {sample_key}"
                )
            if sample_key in samples_by_id:
                raise RealRepositoryError(
                    f"campaign merge contains duplicate sample: {sample_key}"
                )
            errors = validate_sample_against_plan(
                sample,
                plan_sample,
                model_configurations,
            )
            if errors:
                raise RealRepositoryError("; ".join(errors))
            samples_by_id[sample_key] = sample
    samples = sorted(samples_by_id.values(), key=lambda row: ordinal_by_id[row["sampleId"]])
    payload = {
        "schema": "review-craft.eval-real-repository-campaign.v1",
        "campaignId": plan["campaignId"],
        "status": "FAILED",
        "suiteSha256": plan["suiteSha256"],
        "blindSuiteSha256": plan["blindSuiteSha256"],
        "samples": samples,
        "contentSha256": "0" * 64,
    }
    payload["status"] = campaign_status(samples, plan)
    return seal(payload)


def build_merge_receipt(
    *,
    plan: dict[str, Any],
    campaign: dict[str, Any],
    budget_ledger: dict[str, Any],
    inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    receipt = seal(
        {
            "schema": "review-craft.eval-real-repository-campaign-merge.v1",
            "planContentSha256": plan["contentSha256"],
            "campaignContentSha256": campaign["contentSha256"],
            "budgetLedgerContentSha256": budget_ledger["contentSha256"],
            "status": campaign["status"],
            "samples": len(campaign["samples"]),
            "inputs": inputs,
            "contentSha256": "0" * 64,
        }
    )
    errors = schema_errors(receipt, MERGE_SCHEMA)
    if errors:
        raise RealRepositoryError("invalid merge receipt: " + "; ".join(errors))
    return receipt
