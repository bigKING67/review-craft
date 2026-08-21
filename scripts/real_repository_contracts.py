from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from itertools import combinations
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "evals/schemas"
SUITE_SCHEMA = SCHEMA_ROOT / "eval-real-repository-suite.schema.json"
OUTPUT_SCHEMA = SCHEMA_ROOT / "eval-real-repository-output.schema.json"
BLIND_SUITE_SCHEMA = SCHEMA_ROOT / "eval-real-repository-blind-suite.schema.json"
MATERIALIZATION_SCHEMA = (
    SCHEMA_ROOT / "eval-real-repository-materialization.schema.json"
)
CAMPAIGN_SCHEMA = SCHEMA_ROOT / "eval-real-repository-campaign.schema.json"
ADJUDICATION_SCHEMA = SCHEMA_ROOT / "eval-real-repository-adjudication.schema.json"
ADJUDICATION_V2_SCHEMA = (
    SCHEMA_ROOT / "eval-real-repository-adjudication-v2.schema.json"
)
ADJUDICATION_PACKET_SCHEMA = (
    SCHEMA_ROOT / "eval-real-repository-adjudication-packet.schema.json"
)
ADJUDICATION_MAPPING_SCHEMA = (
    SCHEMA_ROOT / "eval-real-repository-adjudication-mapping.schema.json"
)
ADJUDICATION_SUBMISSION_SCHEMA = (
    SCHEMA_ROOT / "eval-real-repository-adjudication-submission.schema.json"
)
STABILITY_SCHEMA = SCHEMA_ROOT / "eval-real-repository-stability.schema.json"
ADAPTERS_SCHEMA = SCHEMA_ROOT / "eval-real-repository-adapters.schema.json"

TREATMENTS = (
    "ORDINARY_PROMPT",
    "RISK_LENS_REVIEW",
    "REVIEW_CRAFT_EVIDENCE_LOOP",
)
PROBE_KINDS = (
    "REAL_FINDING",
    "KEEP",
    "DECOY",
    "MEASUREMENT",
    "EVIDENCE_GAP",
)
METRICS = (
    "findingOverlap",
    "rootCauseOverlap",
    "decisionStability",
    "severityAgreement",
    "scoreVariance",
    "falsePositiveRate",
    "falsificationAccuracy",
    "completionRate",
    "wallTime",
    "tokenCost",
    "humanAgreement",
)
ECOSYSTEM_MINIMUMS = {
    "python": 2,
    "node": 2,
    "electron": 1,
    "go": 1,
    "rust": 1,
    "jvm": 1,
}


class RealRepositoryError(ValueError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def schema_errors(payload: Any, schema_path: Path) -> list[str]:
    schema = read_json(schema_path)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [
        f"{error.json_path}: {error.message}"
        for error in sorted(
            validator.iter_errors(payload),
            key=lambda item: tuple(str(part) for part in item.path),
        )
    ]


def _safe_scope(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def _location_in_scope(path: str, scopes: list[str]) -> bool:
    candidate = PurePosixPath(path)
    for scope in scopes:
        root = PurePosixPath(scope)
        if candidate == root or root in candidate.parents:
            return True
    return False


def validate_suite(payload: dict[str, Any]) -> list[str]:
    errors = schema_errors(payload, SUITE_SCHEMA)
    if errors:
        return errors
    protocol = payload["protocol"]
    repositories = payload["repositories"]
    if len(repositories) < protocol["minimumRepositories"]:
        errors.append("repositories: does not satisfy protocol.minimumRepositories")
    if tuple(protocol["treatments"]) != TREATMENTS:
        errors.append("protocol.treatments: must use the canonical treatment order")
    if tuple(protocol["requiredProbeKinds"]) != PROBE_KINDS:
        errors.append("protocol.requiredProbeKinds: must use the canonical probe order")
    if tuple(protocol["metrics"]) != METRICS:
        errors.append("protocol.metrics: must declare the complete canonical metric set")

    repository_ids = [repository["id"] for repository in repositories]
    if len(repository_ids) != len(set(repository_ids)):
        errors.append("repositories: duplicate repository id")
    remotes = [repository["remote"] for repository in repositories]
    if len(remotes) != len(set(remotes)):
        errors.append("repositories: duplicate remote")
    probe_ids: set[str] = set()
    ecosystem_counts = {ecosystem: 0 for ecosystem in ECOSYSTEM_MINIMUMS}
    legacy_count = 0
    for repository in repositories:
        repository_id = repository["id"]
        ecosystem_counts[repository["ecosystem"]] += 1
        legacy_count += repository["projectType"] == "legacy-compatibility"
        scopes = repository["scope"]
        for scope in scopes:
            if not _safe_scope(scope):
                errors.append(f"{repository_id}: unsafe scope {scope!r}")
        kinds = [probe["kind"] for probe in repository["probes"]]
        if tuple(kinds) != PROBE_KINDS:
            errors.append(f"{repository_id}: probes must use canonical kind order")
        for probe in repository["probes"]:
            probe_id = probe["id"]
            if probe_id in probe_ids:
                errors.append(f"{repository_id}: duplicate global probe id {probe_id}")
            probe_ids.add(probe_id)
            upstream_fix = probe.get("upstreamFix")
            if probe["kind"] == "REAL_FINDING":
                if upstream_fix is None:
                    errors.append(f"{repository_id}/{probe_id}: missing upstream fix")
                    continue
                if upstream_fix["revision"] == repository["revision"]:
                    errors.append(
                        f"{repository_id}/{probe_id}: fix revision equals benchmark revision"
                    )
                for location in upstream_fix["locations"]:
                    if not _safe_scope(location["path"]):
                        errors.append(
                            f"{repository_id}/{probe_id}: unsafe fix location "
                            f"{location['path']!r}"
                        )
                    elif not _location_in_scope(location["path"], scopes):
                        errors.append(
                            f"{repository_id}/{probe_id}: fix location is outside scope: "
                            f"{location['path']}"
                        )
            elif upstream_fix is not None:
                errors.append(
                    f"{repository_id}/{probe_id}: only REAL_FINDING may bind an upstream fix"
                )
    for ecosystem, minimum in ECOSYSTEM_MINIMUMS.items():
        if ecosystem_counts[ecosystem] < minimum:
            errors.append(
                f"repositories: ecosystem {ecosystem} requires at least {minimum} entries"
            )
    if legacy_count < 1:
        errors.append("repositories: requires at least one legacy-compatibility project")
    return errors


def blind_suite(payload: dict[str, Any]) -> dict[str, Any]:
    errors = validate_suite(payload)
    if errors:
        raise RealRepositoryError("invalid suite: " + "; ".join(errors))
    blinded = {
        "schema": "review-craft.eval-real-repository-blind-suite.v1",
        "sourceSuiteSha256": sha256_json(payload),
        "protocol": {
            "repetitions": payload["protocol"]["repetitions"],
            "minimumModelConfigurations": payload["protocol"][
                "minimumModelConfigurations"
            ],
            "treatments": payload["protocol"]["treatments"],
        },
        "repositories": [
            {
                "id": repository["id"],
                "displayName": repository["displayName"],
                "ecosystem": repository["ecosystem"],
                "projectType": repository["projectType"],
                "remote": repository["remote"],
                "revision": repository["revision"],
                "scope": repository["scope"],
                "probes": [
                    {
                        "id": probe["id"],
                        "publicPrompt": probe["publicPrompt"],
                    }
                    for probe in repository["probes"]
                ],
            }
            for repository in payload["repositories"]
        ],
        "contentSha256": "0" * 64,
    }
    blinded["contentSha256"] = sha256_json(
        {key: value for key, value in blinded.items() if key != "contentSha256"}
    )
    return blinded


def validate_blind_suite(
    payload: dict[str, Any], source_suite: dict[str, Any]
) -> list[str]:
    errors = schema_errors(payload, BLIND_SUITE_SCHEMA)
    if errors:
        return errors
    expected = blind_suite(source_suite)
    if payload != expected:
        errors.append("blind suite does not match the bound oracle suite")
    return errors


def validate_materialization_receipt(
    payload: dict[str, Any], source_suite: dict[str, Any]
) -> list[str]:
    errors = schema_errors(payload, MATERIALIZATION_SCHEMA)
    if errors:
        return errors
    expected_hash = sha256_json(
        {key: value for key, value in payload.items() if key != "contentSha256"}
    )
    if payload["contentSha256"] != expected_hash:
        errors.append("materialization contentSha256 mismatch")
    suite_hash = sha256_json(source_suite)
    if payload["suite"]["sha256"] != suite_hash:
        errors.append("materialization suite hash mismatch")
    suite_by_id = {
        repository["id"]: repository for repository in source_suite["repositories"]
    }
    selected_ids = payload["suite"]["selectedRepositoryIds"]
    materialized_ids = [repository["id"] for repository in payload["repositories"]]
    if len(selected_ids) != len(set(selected_ids)):
        errors.append("materialization selectedRepositoryIds contains duplicates")
    if materialized_ids != selected_ids:
        errors.append("materialization repositories do not match selectedRepositoryIds")
    if payload["suite"]["fullSuite"] != (
        selected_ids == [repository["id"] for repository in source_suite["repositories"]]
    ):
        errors.append("materialization fullSuite is inconsistent with selection")
    for materialized in payload["repositories"]:
        source = suite_by_id.get(materialized["id"])
        if source is None:
            errors.append(f"materialization contains unknown repository {materialized['id']}")
            continue
        real_probe = next(
            probe for probe in source["probes"] if probe["kind"] == "REAL_FINDING"
        )
        expected_projection = {
            "remote": source["remote"],
            "revision": source["revision"],
            "fixRevision": real_probe["upstreamFix"]["revision"],
            "scope": source["scope"],
            "checkout": f"repositories/{source['id']}",
        }
        for field, value in expected_projection.items():
            if materialized[field] != value:
                errors.append(
                    f"materialization {source['id']}.{field} does not match suite"
                )
    return errors


def validate_host_output(
    payload: dict[str, Any], repository: dict[str, Any]
) -> list[str]:
    errors = schema_errors(payload, OUTPUT_SCHEMA)
    if errors:
        return errors
    if payload["repositoryId"] != repository["id"]:
        errors.append("repositoryId does not match the scheduled repository")
    expected_probe_ids = [probe["id"] for probe in repository["probes"]]
    actual_probe_ids = [probe["probeId"] for probe in payload["probes"]]
    if len(actual_probe_ids) != len(set(actual_probe_ids)):
        errors.append("probes: duplicate probeId")
    if actual_probe_ids != expected_probe_ids:
        errors.append("probes: must cover repository probes in canonical order")
    score = payload["score"]
    if score["status"] == "NOT_PRODUCED" and score["value"] is not None:
        errors.append("score.value must be null when score.status is NOT_PRODUCED")
    if score["status"] != "NOT_PRODUCED" and score["value"] is None:
        errors.append("score.value is required for FINAL or PROVISIONAL scores")
    finding_ids = [finding["findingId"] for finding in payload["additionalFindings"]]
    if len(finding_ids) != len(set(finding_ids)):
        errors.append("additionalFindings: duplicate findingId")
    return errors


def _without_content_hash(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "contentSha256"}


def _content_hash_errors(payload: dict[str, Any], artifact: str) -> list[str]:
    expected = sha256_json(_without_content_hash(payload))
    if payload["contentSha256"] == expected:
        return []
    return [f"{artifact} contentSha256 mismatch"]


def _campaign_matrix(
    payload: dict[str, Any], source_suite: dict[str, Any]
) -> tuple[set[tuple[str, str, str, int]], set[tuple[str, str, str, int]]]:
    model_ids = {
        sample["modelConfiguration"]["id"] for sample in payload["samples"]
    }
    repetitions = range(1, source_suite["protocol"]["repetitions"] + 1)
    expected = {
        (repository["id"], treatment, model_id, repetition)
        for repository in source_suite["repositories"]
        for treatment in TREATMENTS
        for model_id in model_ids
        for repetition in repetitions
    }
    completed = {
        (
            sample["repositoryId"],
            sample["treatment"],
            sample["modelConfiguration"]["id"],
            sample["repetition"],
        )
        for sample in payload["samples"]
        if sample["status"] == "COMPLETED"
        and not sample["sourceMutationDetected"]
    }
    return expected, completed


def validate_campaign(
    payload: dict[str, Any],
    source_suite: dict[str, Any],
    blind_payload: dict[str, Any],
) -> list[str]:
    errors = schema_errors(payload, CAMPAIGN_SCHEMA)
    if errors:
        return errors
    errors.extend(_content_hash_errors(payload, "campaign"))
    if payload["suiteSha256"] != sha256_json(source_suite):
        errors.append("campaign suiteSha256 mismatch")
    if payload["blindSuiteSha256"] != blind_payload["contentSha256"]:
        errors.append("campaign blindSuiteSha256 mismatch")

    repositories = {
        repository["id"]: repository for repository in source_suite["repositories"]
    }
    sample_ids: set[str] = set()
    sample_keys: set[tuple[str, str, str, int]] = set()
    model_configurations: dict[str, dict[str, Any]] = {}
    for sample in payload["samples"]:
        sample_id = sample["sampleId"]
        if sample_id in sample_ids:
            errors.append(f"campaign duplicate sampleId {sample_id}")
        sample_ids.add(sample_id)
        model_configuration = sample["modelConfiguration"]
        model_id = model_configuration["id"]
        known_model = model_configurations.setdefault(model_id, model_configuration)
        if known_model != model_configuration:
            errors.append(
                f"campaign modelConfiguration {model_id} changes within the campaign"
            )
        sample_key = (
            sample["repositoryId"],
            sample["treatment"],
            model_id,
            sample["repetition"],
        )
        if sample_key in sample_keys:
            errors.append(f"campaign duplicate scheduled sample {sample_key}")
        sample_keys.add(sample_key)
        repository = repositories.get(sample["repositoryId"])
        if repository is None:
            errors.append(
                f"campaign sample {sample_id} references unknown repository "
                f"{sample['repositoryId']}"
            )
            continue
        usage = sample["usage"]
        if (
            usage["inputTokens"] is not None
            and usage["outputTokens"] is not None
            and usage["totalTokens"] is not None
            and usage["totalTokens"]
            != usage["inputTokens"] + usage["outputTokens"]
        ):
            errors.append(f"campaign sample {sample_id} has inconsistent token usage")
        if sample["status"] == "COMPLETED":
            if sample["sourceMutationDetected"]:
                errors.append(
                    f"campaign sample {sample_id} completed after source mutation"
                )
            if sample["output"] is None:
                errors.append(f"campaign sample {sample_id} is missing completed output")
            else:
                errors.extend(
                    f"campaign sample {sample_id}: {error}"
                    for error in validate_host_output(sample["output"], repository)
                )
            if sample["failureReason"] is not None:
                errors.append(
                    f"campaign sample {sample_id} completed with a failureReason"
                )
            if sample.get("failureClass") is not None:
                errors.append(
                    f"campaign sample {sample_id} completed with a failureClass"
                )
            if sample["artifacts"]["outputSha256"] is None:
                errors.append(
                    f"campaign sample {sample_id} completed without outputSha256"
                )
            elif sample["output"] is not None and sample["artifacts"][
                "outputSha256"
            ] != sha256_json(sample["output"]):
                errors.append(
                    f"campaign sample {sample_id} outputSha256 mismatch"
                )
        elif sample["output"] is not None:
            errors.append(
                f"campaign sample {sample_id} must not attach output when status is "
                f"{sample['status']}"
            )
        else:
            if not sample["failureReason"]:
                errors.append(
                    f"campaign sample {sample_id} requires a failureReason when status is "
                    f"{sample['status']}"
                )
            if "failureClass" in sample and sample["failureClass"] is None:
                errors.append(
                    f"campaign sample {sample_id} requires failureClass when present"
                )
            if sample["artifacts"]["outputSha256"] is not None:
                errors.append(
                    f"campaign sample {sample_id} failed with canonical outputSha256"
                )

    expected, completed = _campaign_matrix(payload, source_suite)
    minimum_models = source_suite["protocol"]["minimumModelConfigurations"]
    has_minimum_models = len(model_configurations) >= minimum_models
    full_matrix = has_minimum_models and expected <= completed
    if payload["status"] == "COMPLETED" and not full_matrix:
        errors.append("campaign status COMPLETED requires the full successful matrix")
    if payload["status"] == "FAILED" and completed:
        errors.append("campaign status FAILED cannot contain completed samples")
    return errors


def validate_adapter_config(payload: dict[str, Any]) -> list[str]:
    errors = schema_errors(payload, ADAPTERS_SCHEMA)
    if errors:
        return errors
    adapter_ids = [adapter["id"] for adapter in payload["adapters"]]
    if len(adapter_ids) != len(set(adapter_ids)):
        errors.append("adapter configuration contains duplicate ids")
    return errors


def _validate_adjudication_v1(
    payload: dict[str, Any], campaign: dict[str, Any]
) -> list[str]:
    errors = schema_errors(payload, ADJUDICATION_SCHEMA)
    if errors:
        return errors
    errors.extend(_content_hash_errors(payload, "adjudication"))
    if payload["campaignContentSha256"] != campaign["contentSha256"]:
        errors.append("adjudication campaignContentSha256 mismatch")

    adjudicator_ids = [row["id"] for row in payload["adjudicators"]]
    if len(adjudicator_ids) != len(set(adjudicator_ids)):
        errors.append("adjudication contains duplicate adjudicator ids")
    expected_findings = {
        (sample["sampleId"], finding["findingId"])
        for sample in campaign["samples"]
        if sample["status"] == "COMPLETED" and sample["output"] is not None
        for finding in sample["output"]["additionalFindings"]
    }
    expected_labels = {
        (adjudicator_id, sample_id, finding_id)
        for adjudicator_id in adjudicator_ids
        for sample_id, finding_id in expected_findings
    }
    actual_labels: set[tuple[str, str, str]] = set()
    for label in payload["labels"]:
        key = (label["adjudicatorId"], label["sampleId"], label["findingKey"])
        if key in actual_labels:
            errors.append(f"adjudication contains duplicate label {key}")
        actual_labels.add(key)
        if label["adjudicatorId"] not in adjudicator_ids:
            errors.append(
                f"adjudication label references unknown adjudicator "
                f"{label['adjudicatorId']}"
            )
        finding_key = (label["sampleId"], label["findingKey"])
        if finding_key not in expected_findings:
            errors.append(
                f"adjudication label references unknown finding {finding_key}"
            )
        if label["label"] == "TRUE_POSITIVE" and label["rootCauseKey"] is None:
            errors.append(f"adjudication TRUE_POSITIVE {key} requires rootCauseKey")
        if label["label"] == "FALSE_POSITIVE" and label["rootCauseKey"] is not None:
            errors.append(f"adjudication FALSE_POSITIVE {key} forbids rootCauseKey")
    missing = expected_labels - actual_labels
    extra = actual_labels - expected_labels
    if missing:
        errors.append(f"adjudication is missing {len(missing)} independent labels")
    if extra:
        errors.append(f"adjudication contains {len(extra)} unexpected labels")
    return errors


def adjudication_subjects(campaign: dict[str, Any]) -> set[tuple[str, str, str]]:
    subjects: set[tuple[str, str, str]] = set()
    for sample in campaign["samples"]:
        if sample["status"] != "COMPLETED" or sample["output"] is None:
            continue
        subjects.update(
            (sample["sampleId"], "PROBE_RESPONSE", probe["probeId"])
            for probe in sample["output"]["probes"]
        )
        subjects.update(
            (sample["sampleId"], "ADDITIONAL_FINDING", finding["findingId"])
            for finding in sample["output"]["additionalFindings"]
        )
    return subjects


def _validate_adjudication_v2(
    payload: dict[str, Any], campaign: dict[str, Any]
) -> list[str]:
    errors = schema_errors(payload, ADJUDICATION_V2_SCHEMA)
    if errors:
        return errors
    errors.extend(_content_hash_errors(payload, "adjudication"))
    if payload["campaignContentSha256"] != campaign["contentSha256"]:
        errors.append("adjudication campaignContentSha256 mismatch")

    adjudicator_ids = [row["id"] for row in payload["adjudicators"]]
    if len(adjudicator_ids) != len(set(adjudicator_ids)):
        errors.append("adjudication contains duplicate adjudicator ids")
    adjudicator_kinds = {row["kind"] for row in payload["adjudicators"]}
    if len(adjudicator_kinds) != 1:
        errors.append("adjudication mixes adjudicator kinds")
    expected_subjects = adjudication_subjects(campaign)
    expected_labels = {
        (adjudicator_id, *subject)
        for adjudicator_id in adjudicator_ids
        for subject in expected_subjects
    }
    actual_labels: set[tuple[str, str, str, str]] = set()
    item_ids: set[tuple[str, str]] = set()
    for label in payload["labels"]:
        key = (
            label["adjudicatorId"],
            label["sampleId"],
            label["subjectType"],
            label["subjectKey"],
        )
        if key in actual_labels:
            errors.append(f"adjudication contains duplicate label {key}")
        actual_labels.add(key)
        item_key = (label["adjudicatorId"], label["itemId"])
        if item_key in item_ids:
            errors.append(f"adjudication contains duplicate itemId {item_key}")
        item_ids.add(item_key)
        if label["adjudicatorId"] not in adjudicator_ids:
            errors.append(
                "adjudication label references unknown adjudicator "
                f"{label['adjudicatorId']}"
            )
        subject = (label["sampleId"], label["subjectType"], label["subjectKey"])
        if subject not in expected_subjects:
            errors.append(f"adjudication label references unknown subject {subject}")
    missing = expected_labels - actual_labels
    extra = actual_labels - expected_labels
    if missing:
        errors.append(f"adjudication is missing {len(missing)} independent labels")
    if extra:
        errors.append(f"adjudication contains {len(extra)} unexpected labels")
    return errors


def validate_adjudication(
    payload: dict[str, Any], campaign: dict[str, Any]
) -> list[str]:
    schema = payload.get("schema") if isinstance(payload, dict) else None
    if schema == "review-craft.eval-real-repository-adjudication.v1":
        return _validate_adjudication_v1(payload, campaign)
    if schema == "review-craft.eval-real-repository-adjudication.v2":
        return _validate_adjudication_v2(payload, campaign)
    return [f"adjudication has unsupported schema {schema!r}"]


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def _pairwise_jaccard(sets: list[set[str]]) -> dict[str, Any]:
    numerator = 0
    denominator = 0
    for left, right in combinations(sets, 2):
        numerator += len(left & right)
        denominator += len(left | right)
    return _ratio(numerator, denominator)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _item_maps(output: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    decisions: dict[str, str] = {}
    severities: dict[str, str] = {}
    for probe in output["probes"]:
        key = f"probe:{probe['probeId']}"
        if probe["decision"] is not None:
            decisions[key] = probe["decision"]
        if probe["severity"] is not None:
            severities[key] = probe["severity"]
    for finding in output["additionalFindings"]:
        key = f"root:{finding['rootCauseKey']}"
        if finding["decision"] is not None:
            decisions[key] = finding["decision"]
        if finding["severity"] is not None:
            severities[key] = finding["severity"]
    return decisions, severities


def _agreement(maps: list[dict[str, str]]) -> dict[str, Any]:
    numerator = 0
    denominator = 0
    for left, right in combinations(maps, 2):
        for key in left.keys() & right.keys():
            denominator += 1
            numerator += left[key] == right[key]
    return _ratio(numerator, denominator)


def _adjudication_metrics(
    adjudication: dict[str, Any] | None,
) -> tuple[dict[str, Any], tuple[int, int]]:
    if adjudication is None:
        return _ratio(0, 0), (0, 0)
    v2 = adjudication["schema"] == "review-craft.eval-real-repository-adjudication.v2"
    by_finding: dict[tuple[str, str, str], dict[str, str]] = defaultdict(dict)
    for row in adjudication["labels"]:
        subject_type = row["subjectType"] if v2 else "ADDITIONAL_FINDING"
        subject_key = row["subjectKey"] if v2 else row["findingKey"]
        by_finding[(row["sampleId"], subject_type, subject_key)][
            row["adjudicatorId"]
        ] = row["label"]
    agreements = 0
    comparisons = 0
    false_positives = 0
    resolved = 0
    for (_sample_id, subject_type, _subject_key), labels in by_finding.items():
        values = list(labels.values())
        for left, right in combinations(values, 2):
            comparisons += 1
            agreements += left == right
        decisive = [value for value in values if value != "UNRESOLVED"]
        if (
            decisive
            and len(set(decisive)) == 1
            and subject_type == "ADDITIONAL_FINDING"
        ):
            resolved += 1
            false_positives += decisive[0] in {"FALSE_POSITIVE", "INCORRECT"}
    return _ratio(agreements, comparisons), (false_positives, resolved)


def build_stability_report(
    source_suite: dict[str, Any],
    campaign: dict[str, Any],
    adjudication: dict[str, Any] | None = None,
) -> dict[str, Any]:
    campaign_errors = schema_errors(campaign, CAMPAIGN_SCHEMA)
    if campaign_errors:
        raise RealRepositoryError("invalid campaign: " + "; ".join(campaign_errors))
    if adjudication is not None:
        adjudication_errors = validate_adjudication(adjudication, campaign)
        if adjudication_errors:
            raise RealRepositoryError(
                "invalid adjudication: " + "; ".join(adjudication_errors)
            )

    completed = [
        sample
        for sample in campaign["samples"]
        if sample["status"] == "COMPLETED"
        and not sample["sourceMutationDetected"]
        and sample["output"] is not None
    ]
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for sample in completed:
        grouped[
            (
                sample["repositoryId"],
                sample["treatment"],
                sample["modelConfiguration"]["id"],
            )
        ].append(sample)

    finding_overlap_numerator = 0
    finding_overlap_denominator = 0
    root_overlap_numerator = 0
    root_overlap_denominator = 0
    decision_numerator = 0
    decision_denominator = 0
    severity_numerator = 0
    severity_denominator = 0
    score_mads: list[float] = []
    score_ranges: list[float] = []
    for samples in grouped.values():
        outputs = [sample["output"] for sample in samples]
        finding_sets = [
            {
                *(
                    f"probe:{row['probeId']}"
                    for row in output["probes"]
                    if row["disposition"] == "VALIDATED"
                    and row["severity"] is not None
                ),
                *(
                    f"finding:{row['findingId']}"
                    for row in output["additionalFindings"]
                ),
            }
            for output in outputs
        ]
        root_sets = [
            {
                *(
                    row["rootCauseKey"]
                    for row in output["probes"]
                    if row["disposition"] == "VALIDATED"
                    and row["severity"] is not None
                    and row["rootCauseKey"] is not None
                ),
                *(row["rootCauseKey"] for row in output["additionalFindings"]),
            }
            for output in outputs
        ]
        finding_ratio = _pairwise_jaccard(finding_sets)
        root_ratio = _pairwise_jaccard(root_sets)
        finding_overlap_numerator += finding_ratio["numerator"]
        finding_overlap_denominator += finding_ratio["denominator"]
        root_overlap_numerator += root_ratio["numerator"]
        root_overlap_denominator += root_ratio["denominator"]
        item_maps = [_item_maps(output) for output in outputs]
        decision_ratio = _agreement([item[0] for item in item_maps])
        severity_ratio = _agreement([item[1] for item in item_maps])
        decision_numerator += decision_ratio["numerator"]
        decision_denominator += decision_ratio["denominator"]
        severity_numerator += severity_ratio["numerator"]
        severity_denominator += severity_ratio["denominator"]
        scores = [
            output["score"]["value"]
            for output in outputs
            if output["score"]["value"] is not None
        ]
        if len(scores) >= 2:
            center = statistics.median(scores)
            score_mads.append(statistics.median(abs(score - center) for score in scores))
            score_ranges.append(max(scores) - min(scores))

    decoy_results = []
    probe_kind_by_id = {
        probe["id"]: probe["kind"]
        for repository in source_suite["repositories"]
        for probe in repository["probes"]
    }
    for sample in completed:
        decoy_results.extend(
            probe
            for probe in sample["output"]["probes"]
            if probe_kind_by_id.get(probe["probeId"]) == "DECOY"
        )
    adjudicator_agreement, adjudicated_false_positives = _adjudication_metrics(
        adjudication
    )
    adjudicator_kinds = (
        {row["kind"] for row in adjudication["adjudicators"]}
        if adjudication is not None
        else set()
    )
    human_agreement = (
        adjudicator_agreement
        if adjudicator_kinds == {"HUMAN"}
        else _ratio(0, 0)
    )
    decoy_false_positives = sum(
        probe["disposition"] == "VALIDATED" for probe in decoy_results
    )
    false_positive_numerator = decoy_false_positives + adjudicated_false_positives[0]
    false_positive_denominator = len(decoy_results) + adjudicated_false_positives[1]

    expected, completed_matrix = _campaign_matrix(campaign, source_suite)
    model_ids = {
        sample["modelConfiguration"]["id"] for sample in campaign["samples"]
    }
    repository_ids = {sample["repositoryId"] for sample in campaign["samples"]}
    treatments = {sample["treatment"] for sample in campaign["samples"]}
    repetitions_by_group: dict[tuple[str, str, str], set[int]] = defaultdict(set)
    for sample in campaign["samples"]:
        repetitions_by_group[
            (
                sample["repositoryId"],
                sample["treatment"],
                sample["modelConfiguration"]["id"],
            )
        ].add(sample["repetition"])
    minimum_repetitions = min(
        (len(values) for values in repetitions_by_group.values()), default=0
    )

    limitations: list[str] = []
    if repository_ids != {row["id"] for row in source_suite["repositories"]}:
        limitations.append("not all pinned repositories are represented")
    if treatments != set(TREATMENTS):
        limitations.append("not all canonical treatments are represented")
    if len(model_ids) < source_suite["protocol"]["minimumModelConfigurations"]:
        limitations.append("fewer than the required model configurations are represented")
    if minimum_repetitions < source_suite["protocol"]["repetitions"]:
        limitations.append("fewer than the required repetitions are represented")
    if not expected <= completed_matrix:
        limitations.append("the required campaign matrix is not fully completed")
    if adjudication is None:
        limitations.append("independent human adjudication is not attached")
    elif adjudicator_kinds != {"HUMAN"}:
        limitations.append(
            "adjudication is agent-assisted, not independent human adjudication"
        )
    complete = not limitations and campaign["status"] == "COMPLETED"

    durations = [float(sample["durationSeconds"]) for sample in campaign["samples"]]
    tokens = [
        sample["usage"]["totalTokens"]
        for sample in campaign["samples"]
        if sample["usage"]["totalTokens"] is not None
    ]
    report = {
        "schema": "review-craft.eval-real-repository-stability.v1",
        "status": "COMPLETE" if complete else "PARTIAL",
        "campaignContentSha256": campaign["contentSha256"],
        "adjudicationContentSha256": (
            adjudication["contentSha256"] if adjudication is not None else None
        ),
        "coverage": {
            "repositories": len(repository_ids),
            "treatments": len(treatments),
            "modelConfigurations": len(model_ids),
            "minimumRepetitions": minimum_repetitions,
            "samples": len(campaign["samples"]),
            "completedSamples": len(completed),
        },
        "metrics": {
            "findingOverlap": _ratio(
                finding_overlap_numerator, finding_overlap_denominator
            ),
            "rootCauseOverlap": _ratio(root_overlap_numerator, root_overlap_denominator),
            "decisionStability": _ratio(decision_numerator, decision_denominator),
            "severityAgreement": _ratio(severity_numerator, severity_denominator),
            "scoreVariance": {
                "sampleGroups": len(score_mads),
                "medianAbsoluteDeviation": (
                    statistics.median(score_mads) if score_mads else None
                ),
                "maximumRange": max(score_ranges) if score_ranges else None,
            },
            "falsePositiveRate": _ratio(
                false_positive_numerator, false_positive_denominator
            ),
            "falsificationAccuracy": _ratio(
                sum(probe["disposition"] == "FALSIFIED" for probe in decoy_results),
                len(decoy_results),
            ),
            "completionRate": _ratio(len(completed_matrix & expected), len(expected)),
            "wallTime": {
                "samples": len(durations),
                "p50": statistics.median(durations) if durations else None,
                "p95": _percentile(durations, 0.95),
                "maximum": max(durations) if durations else None,
            },
            "tokenCost": {
                "availableSamples": len(tokens),
                "totalTokens": sum(tokens) if tokens else None,
                "medianTokens": statistics.median(tokens) if tokens else None,
            },
            "humanAgreement": human_agreement,
            "adjudicatorAgreement": adjudicator_agreement,
        },
        "limitations": limitations,
        "contentSha256": "0" * 64,
    }
    report["contentSha256"] = sha256_json(_without_content_hash(report))
    stability_errors = schema_errors(report, STABILITY_SCHEMA)
    if stability_errors:
        raise RealRepositoryError(
            "generated stability report is invalid: " + "; ".join(stability_errors)
        )
    return report


def validate_stability_report(
    payload: dict[str, Any],
    source_suite: dict[str, Any],
    campaign: dict[str, Any],
    adjudication: dict[str, Any] | None = None,
) -> list[str]:
    errors = schema_errors(payload, STABILITY_SCHEMA)
    if errors:
        return errors
    errors.extend(_content_hash_errors(payload, "stability report"))
    expected = build_stability_report(source_suite, campaign, adjudication)
    if payload != expected:
        errors.append("stability report does not match deterministic campaign analysis")
    return errors
