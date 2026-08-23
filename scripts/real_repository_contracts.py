from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from functools import lru_cache
from itertools import combinations
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

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
ADJUDICATION_V3_SCHEMA = (
    SCHEMA_ROOT / "eval-real-repository-adjudication-v3.schema.json"
)
ORACLE_ASSESSMENT_SCHEMA = (
    SCHEMA_ROOT / "eval-real-repository-oracle-assessment.schema.json"
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
ADJUDICATION_PACKET_V2_SCHEMA = (
    SCHEMA_ROOT / "eval-real-repository-adjudication-packet-v2.schema.json"
)
ADJUDICATION_SUBMISSION_V2_SCHEMA = (
    SCHEMA_ROOT / "eval-real-repository-adjudication-submission-v2.schema.json"
)
STABILITY_SCHEMA = SCHEMA_ROOT / "eval-real-repository-stability.schema.json"
STABILITY_V2_SCHEMA = (
    SCHEMA_ROOT / "eval-real-repository-stability-v2.schema.json"
)
ADAPTERS_SCHEMA = SCHEMA_ROOT / "eval-real-repository-adapters.schema.json"

TREATMENTS = (
    "ORDINARY_PROMPT",
    "RISK_LENS_REVIEW",
    "REVIEW_CRAFT_EVIDENCE_LOOP",
)
ADJUDICATION_COMPONENT_KEYS = {
    "PROBE_RESPONSE": (
        "disposition",
        "decision",
        "severity",
        "evidence",
        "rationale",
    ),
    "ADDITIONAL_FINDING": (
        "actionability",
        "decision",
        "severity",
        "evidence",
    ),
}
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
KEEP_PROMPT_PREFIX = "Determine whether evidence supports keeping "
PRESERVATION_DECISIONS = frozenset({"KEEP", "DEFER", "DOCUMENT"})
MATERIALIZATION_BINDING_KIND = "SOURCE_MATERIALIZATION_V1"
ORACLE_MATCH_RUBRIC_VERSION = (
    "review-craft.real-repository-oracle-match-rubric.v1"
)


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


def materialization_suite_projection(payload: dict[str, Any]) -> dict[str, Any]:
    """Return only suite fields that determine materialized source identity."""
    repositories = []
    for repository in payload["repositories"]:
        real_probe = next(
            probe for probe in repository["probes"] if probe["kind"] == "REAL_FINDING"
        )
        repositories.append(
            {
                "id": repository["id"],
                "remote": repository["remote"],
                "revision": repository["revision"],
                "fixRevision": real_probe["upstreamFix"]["revision"],
                "scope": repository["scope"],
            }
        )
    return {
        "schema": "review-craft.eval-real-repository-materialization-source.v1",
        "repositories": repositories,
    }


def materialization_suite_sha256(payload: dict[str, Any]) -> str:
    return sha256_json(materialization_suite_projection(payload))


@lru_cache(maxsize=1)
def _schema_registry() -> Registry:
    registry = Registry()
    for local_path in SCHEMA_ROOT.glob("*.schema.json"):
        local_schema = read_json(local_path)
        resource = Resource.from_contents(local_schema)
        registry = registry.with_resource(local_path.as_uri(), resource)
        if isinstance(local_schema.get("$id"), str):
            registry = registry.with_resource(local_schema["$id"], resource)
    return registry


def schema_errors(payload: Any, schema_path: Path) -> list[str]:
    schema = read_json(schema_path)
    validator = Draft202012Validator(
        schema,
        registry=_schema_registry(),
        format_checker=FormatChecker(),
    )
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


def _output_location_errors(
    prefix: str,
    locations: list[dict[str, Any]],
    scopes: list[str],
) -> list[str]:
    errors: list[str] = []
    for index, location in enumerate(locations):
        path = location["path"]
        if not _safe_scope(path):
            errors.append(f"{prefix}[{index}]: unsafe location: {path}")
        elif not _location_in_scope(path, scopes):
            errors.append(
                f"{prefix}[{index}]: location is outside declared scope: {path}"
            )
    return errors


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
            elif probe["kind"] == "KEEP":
                if not probe["publicPrompt"].startswith(KEEP_PROMPT_PREFIX):
                    errors.append(
                        f"{repository_id}/{probe_id}: KEEP prompt must anchor the "
                        "candidate as the preservation decision"
                    )
                if probe["expectedDispositions"] != ["VALIDATED"]:
                    errors.append(
                        f"{repository_id}/{probe_id}: KEEP probe must expect VALIDATED"
                    )
                unsupported_decisions = set(probe["expectedDecisions"]) - (
                    PRESERVATION_DECISIONS
                )
                if unsupported_decisions:
                    errors.append(
                        f"{repository_id}/{probe_id}: KEEP probe has non-preservation "
                        f"decisions: {sorted(unsupported_decisions)}"
                    )
                if upstream_fix is not None:
                    errors.append(
                        f"{repository_id}/{probe_id}: only REAL_FINDING may bind an "
                        "upstream fix"
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
    binding_kind = payload["suite"].get("bindingKind")
    suite_hash = (
        materialization_suite_sha256(source_suite)
        if binding_kind == MATERIALIZATION_BINDING_KIND
        else sha256_json(source_suite)
    )
    if payload["suite"]["sha256"] != suite_hash:
        errors.append(
            "materialization suite source binding hash mismatch"
            if binding_kind == MATERIALIZATION_BINDING_KIND
            else "materialization suite hash mismatch"
        )
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
    scopes = repository["scope"]
    for probe_index, probe in enumerate(payload["probes"]):
        if probe["disposition"] == "BLOCKED" and probe["severity"] is not None:
            errors.append(
                f"probes[{probe_index}].severity must be null when disposition is BLOCKED"
            )
        errors.extend(
            _output_location_errors(
                f"probes[{probe_index}].locations",
                probe["locations"],
                scopes,
            )
        )
        for evidence_index, evidence in enumerate(probe["evidence"]):
            errors.extend(
                _output_location_errors(
                    f"probes[{probe_index}].evidence[{evidence_index}].locations",
                    evidence["locations"],
                    scopes,
                )
            )
    score = payload["score"]
    if score["status"] == "NOT_PRODUCED" and score["value"] is not None:
        errors.append("score.value must be null when score.status is NOT_PRODUCED")
    if score["status"] != "NOT_PRODUCED" and score["value"] is None:
        errors.append("score.value is required for FINAL or PROVISIONAL scores")
    finding_ids = [finding["findingId"] for finding in payload["additionalFindings"]]
    if len(finding_ids) != len(set(finding_ids)):
        errors.append("additionalFindings: duplicate findingId")
    for finding_index, finding in enumerate(payload["additionalFindings"]):
        errors.extend(
            _output_location_errors(
                f"additionalFindings[{finding_index}].locations",
                finding["locations"],
                scopes,
            )
        )
        for evidence_index, evidence in enumerate(finding["evidence"]):
            errors.extend(
                _output_location_errors(
                    f"additionalFindings[{finding_index}].evidence[{evidence_index}].locations",
                    evidence["locations"],
                    scopes,
                )
            )
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
            if (
                model_configuration.get("isolationReceiptProtocol")
                == "review-craft.eval-isolation-receipt.v1"
                and sample["artifacts"].get("isolationReceiptSha256") is None
            ):
                errors.append(
                    f"campaign sample {sample_id} completed without isolationReceiptSha256"
                )
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


def adjudication_subject_content_hashes(
    campaign: dict[str, Any],
) -> dict[tuple[str, str, str], str]:
    hashes: dict[tuple[str, str, str], str] = {}
    for sample in campaign["samples"]:
        if sample["status"] != "COMPLETED" or sample["output"] is None:
            continue
        for subject_type, key_name, rows in (
            ("PROBE_RESPONSE", "probeId", sample["output"]["probes"]),
            (
                "ADDITIONAL_FINDING",
                "findingId",
                sample["output"]["additionalFindings"],
            ),
        ):
            for response in rows:
                key = (sample["sampleId"], subject_type, response[key_name])
                hashes[key] = sha256_json(
                    {"subjectType": subject_type, "response": response}
                )
    return hashes


def derived_component_label(components: list[dict[str, Any]]) -> str | None:
    labels = [row.get("label") for row in components]
    if any(label is None for label in labels):
        return None
    if "INCORRECT" in labels:
        return "INCORRECT"
    if "UNRESOLVED" in labels:
        return "UNRESOLVED"
    return "CORRECT"


def build_adjudication_resolutions(
    labels: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for row in labels:
        grouped[(row["sampleId"], row["subjectType"], row["subjectKey"])].append(
            row["label"]
        )
    resolutions = []
    for (sample_id, subject_type, subject_key), values in sorted(grouped.items()):
        if set(values) == {"UNRESOLVED"}:
            status, resolved = "ALL_UNRESOLVED", "UNRESOLVED"
        elif len(set(values)) == 1:
            status, resolved = "UNANIMOUS", values[0]
        else:
            status, resolved = "SPLIT", None
        resolutions.append(
            {
                "sampleId": sample_id,
                "subjectType": subject_type,
                "subjectKey": subject_key,
                "status": status,
                "resolvedLabel": resolved,
            }
        )
    return resolutions


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


def _validate_adjudication_v3(
    payload: dict[str, Any], campaign: dict[str, Any]
) -> list[str]:
    errors = schema_errors(payload, ADJUDICATION_V3_SCHEMA)
    if errors:
        return errors
    errors.extend(_content_hash_errors(payload, "adjudication"))
    if payload["campaignContentSha256"] != campaign["contentSha256"]:
        errors.append("adjudication campaignContentSha256 mismatch")

    adjudicator_ids = [row["id"] for row in payload["adjudicators"]]
    if len(adjudicator_ids) != len(set(adjudicator_ids)):
        errors.append("adjudication contains duplicate adjudicator ids")
    if len({row["kind"] for row in payload["adjudicators"]}) != 1:
        errors.append("adjudication mixes adjudicator kinds")
    expected_subjects = adjudication_subjects(campaign)
    expected_hashes = adjudication_subject_content_hashes(campaign)
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
        elif label["subjectContentSha256"] != expected_hashes[subject]:
            errors.append(f"adjudication label subject content hash mismatch {subject}")
        component_keys = tuple(row["key"] for row in label["components"])
        if component_keys != ADJUDICATION_COMPONENT_KEYS[label["subjectType"]]:
            errors.append(f"adjudication label has invalid component rubric {key}")
        if label["label"] != derived_component_label(label["components"]):
            errors.append(f"adjudication label differs from component verdicts {key}")
    missing = expected_labels - actual_labels
    extra = actual_labels - expected_labels
    if missing:
        errors.append(f"adjudication is missing {len(missing)} independent labels")
    if extra:
        errors.append(f"adjudication contains {len(extra)} unexpected labels")
    expected_resolutions = build_adjudication_resolutions(payload["labels"])
    if payload["subjectResolutions"] != expected_resolutions:
        errors.append("adjudication subjectResolutions do not match raw labels")
    return errors


def validate_adjudication(
    payload: dict[str, Any], campaign: dict[str, Any]
) -> list[str]:
    schema = payload.get("schema") if isinstance(payload, dict) else None
    if schema == "review-craft.eval-real-repository-adjudication.v1":
        return _validate_adjudication_v1(payload, campaign)
    if schema == "review-craft.eval-real-repository-adjudication.v2":
        return _validate_adjudication_v2(payload, campaign)
    if schema == "review-craft.eval-real-repository-adjudication.v3":
        return _validate_adjudication_v3(payload, campaign)
    return [f"adjudication has unsupported schema {schema!r}"]


def _oracle_content_sha256(repository: dict[str, Any], probe: dict[str, Any]) -> str:
    return sha256_json(
        {
            "repositoryId": repository["id"],
            "probeId": probe["id"],
            "rationale": probe["rationale"],
            "upstreamFix": probe["upstreamFix"],
        }
    )


def _oracle_response_validity(resolution: dict[str, Any]) -> str:
    resolved = resolution["resolvedLabel"]
    return resolved if resolved in {"CORRECT", "INCORRECT"} else "UNRESOLVED"


def oracle_assessment_rows(
    source_suite: dict[str, Any],
    campaign: dict[str, Any],
    adjudication: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the immutable portion of the post-blind oracle assessment."""
    repositories = {row["id"]: row for row in source_suite["repositories"]}
    resolutions = {
        (row["sampleId"], row["subjectType"], row["subjectKey"]): row
        for row in adjudication["subjectResolutions"]
    }
    subject_hashes = adjudication_subject_content_hashes(campaign)
    rows = []
    for sample in campaign["samples"]:
        if sample["status"] != "COMPLETED" or sample["output"] is None:
            continue
        repository = repositories[sample["repositoryId"]]
        probes_by_id = {row["probeId"]: row for row in sample["output"]["probes"]}
        for probe in repository["probes"]:
            if probe["kind"] != "REAL_FINDING":
                continue
            subject = (sample["sampleId"], "PROBE_RESPONSE", probe["id"])
            response = probes_by_id[probe["id"]]
            rows.append(
                {
                    "sampleId": sample["sampleId"],
                    "repositoryId": repository["id"],
                    "probeId": probe["id"],
                    "subjectContentSha256": subject_hashes[subject],
                    "oracleContentSha256": _oracle_content_sha256(
                        repository, probe
                    ),
                    "responseValidity": _oracle_response_validity(
                        resolutions[subject]
                    ),
                    "observedRootCauseKey": response["rootCauseKey"],
                    "classification": None,
                    "rationale": None,
                }
            )
    return sorted(rows, key=lambda row: (row["sampleId"], row["probeId"]))


def build_oracle_assessment_template(
    source_suite: dict[str, Any],
    campaign: dict[str, Any],
    adjudication: dict[str, Any],
    *,
    verifier_id: str,
    verifier_kind: str,
) -> dict[str, Any]:
    suite_errors = validate_suite(source_suite)
    if suite_errors:
        raise RealRepositoryError("invalid source suite: " + "; ".join(suite_errors))
    campaign_errors = validate_campaign(
        campaign, source_suite, blind_suite(source_suite)
    )
    if campaign_errors:
        raise RealRepositoryError("invalid campaign: " + "; ".join(campaign_errors))
    adjudication_errors = validate_adjudication(adjudication, campaign)
    if adjudication_errors:
        raise RealRepositoryError(
            "invalid adjudication: " + "; ".join(adjudication_errors)
        )
    if adjudication["schema"] != "review-craft.eval-real-repository-adjudication.v3":
        raise RealRepositoryError("oracle assessment requires adjudication.v3")
    source_suite_sha256 = sha256_json(source_suite)
    if campaign["suiteSha256"] != source_suite_sha256:
        raise RealRepositoryError("campaign suiteSha256 does not match source suite")
    payload = {
        "schema": "review-craft.eval-real-repository-oracle-assessment.v1",
        "status": "DRAFT",
        "sourceSuiteSha256": source_suite_sha256,
        "campaignContentSha256": campaign["contentSha256"],
        "adjudicationContentSha256": adjudication["contentSha256"],
        "rubricVersion": ORACLE_MATCH_RUBRIC_VERSION,
        "verifier": {"id": verifier_id, "kind": verifier_kind},
        "assessments": oracle_assessment_rows(
            source_suite, campaign, adjudication
        ),
        "contentSha256": "0" * 64,
    }
    payload["contentSha256"] = sha256_json(_without_content_hash(payload))
    errors = validate_oracle_assessment(
        payload,
        source_suite,
        campaign,
        adjudication,
        require_complete=False,
    )
    if errors:
        raise RealRepositoryError(
            "generated oracle assessment template is invalid: " + "; ".join(errors)
        )
    return payload


def _oracle_assessment_context_errors(
    payload: dict[str, Any],
    source_suite: dict[str, Any],
    campaign: dict[str, Any],
    adjudication: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    suite_errors = validate_suite(source_suite)
    if suite_errors:
        errors.extend(f"source suite: {error}" for error in suite_errors)
        return errors
    campaign_errors = validate_campaign(
        campaign, source_suite, blind_suite(source_suite)
    )
    if campaign_errors:
        errors.extend(f"campaign: {error}" for error in campaign_errors)
        return errors
    adjudication_errors = validate_adjudication(adjudication, campaign)
    if adjudication_errors:
        errors.extend(f"adjudication: {error}" for error in adjudication_errors)
        return errors
    if adjudication["schema"] != "review-craft.eval-real-repository-adjudication.v3":
        errors.append("oracle assessment requires adjudication.v3")
        return errors

    source_suite_sha256 = sha256_json(source_suite)
    if payload["sourceSuiteSha256"] != source_suite_sha256:
        errors.append("oracle assessment sourceSuiteSha256 mismatch")
    if campaign["suiteSha256"] != source_suite_sha256:
        errors.append("campaign suiteSha256 does not match source suite")
    if payload["campaignContentSha256"] != campaign["contentSha256"]:
        errors.append("oracle assessment campaignContentSha256 mismatch")
    if payload["adjudicationContentSha256"] != adjudication["contentSha256"]:
        errors.append("oracle assessment adjudicationContentSha256 mismatch")
    return errors


def _oracle_assessment_row_errors(
    row: dict[str, Any],
    expected: dict[str, Any],
    sample: dict[str, Any],
    *,
    require_complete: bool,
) -> list[str]:
    key = (row["sampleId"], row["probeId"])
    errors = [
        f"oracle assessment {field} mismatch for {key}"
        for field in (
            "repositoryId",
            "subjectContentSha256",
            "oracleContentSha256",
            "responseValidity",
            "observedRootCauseKey",
        )
        if row[field] != expected[field]
    ]
    classification = row["classification"]
    rationale = row["rationale"]
    if require_complete and classification is None:
        errors.append(f"oracle assessment classification is incomplete for {key}")
    if require_complete and (
        not isinstance(rationale, str) or not rationale.strip()
    ):
        errors.append(f"oracle assessment rationale is incomplete for {key}")
    if classification not in {
        "EXACT_ORACLE_MATCH",
        "ALTERNATIVE_VALID_FINDING",
    }:
        return errors
    response = next(
        probe
        for probe in sample["output"]["probes"]
        if probe["probeId"] == row["probeId"]
    )
    raised = (
        response["disposition"] == "VALIDATED"
        and response["severity"] is not None
        and response["rootCauseKey"] is not None
    )
    if not raised:
        errors.append(
            "oracle assessment cannot classify an unraised response as "
            f"{classification} for {key}"
        )
    return errors


def validate_oracle_assessment(
    payload: dict[str, Any],
    source_suite: dict[str, Any],
    campaign: dict[str, Any],
    adjudication: dict[str, Any],
    *,
    require_complete: bool = True,
) -> list[str]:
    errors = schema_errors(payload, ORACLE_ASSESSMENT_SCHEMA)
    if errors:
        return errors
    errors.extend(_content_hash_errors(payload, "oracle assessment"))
    expected_status = "FINAL" if require_complete else "DRAFT"
    if payload["status"] != expected_status:
        errors.append(f"oracle assessment status must be {expected_status}")
    context_errors = _oracle_assessment_context_errors(
        payload, source_suite, campaign, adjudication
    )
    errors.extend(context_errors)
    if context_errors:
        return errors

    expected_rows = oracle_assessment_rows(source_suite, campaign, adjudication)
    expected_by_key = {
        (row["sampleId"], row["probeId"]): row for row in expected_rows
    }
    actual_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    sample_by_id = {row["sampleId"]: row for row in campaign["samples"]}
    for row in payload["assessments"]:
        key = (row["sampleId"], row["probeId"])
        if key in actual_by_key:
            errors.append(f"oracle assessment contains duplicate subject {key}")
            continue
        actual_by_key[key] = row
        expected = expected_by_key.get(key)
        if expected is None:
            errors.append(f"oracle assessment references unexpected subject {key}")
            continue
        errors.extend(
            _oracle_assessment_row_errors(
                row,
                expected,
                sample_by_id[row["sampleId"]],
                require_complete=require_complete,
            )
        )
    missing = set(expected_by_key) - set(actual_by_key)
    if missing:
        errors.append(f"oracle assessment is missing {len(missing)} subjects")
    return errors


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


def _location_identity(locations: list[dict[str, Any]]) -> str:
    normalized = sorted(
        {
            (
                location["path"],
                location["lineStart"],
                location["lineEnd"],
            )
            for location in locations
        }
    )
    return "location:" + sha256_json(normalized)


def _root_cause_identity_set(output: dict[str, Any]) -> set[str]:
    identities = {
        f"probe:{probe['probeId']}"
        for probe in output["probes"]
        if probe["disposition"] == "VALIDATED" and probe["severity"] is not None
    }
    identities.update(
        _location_identity(finding["locations"])
        for finding in output["additionalFindings"]
    )
    return identities


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
    modern = adjudication["schema"] in {
        "review-craft.eval-real-repository-adjudication.v2",
        "review-craft.eval-real-repository-adjudication.v3",
    }
    by_finding: dict[tuple[str, str, str], dict[str, str]] = defaultdict(dict)
    for row in adjudication["labels"]:
        subject_type = row["subjectType"] if modern else "ADDITIONAL_FINDING"
        subject_key = row["subjectKey"] if modern else row["findingKey"]
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
        strict_resolution = (
            adjudication["schema"]
            == "review-craft.eval-real-repository-adjudication.v3"
        )
        resolved_label = (
            values[0]
            if strict_resolution
            and values
            and values[0] != "UNRESOLVED"
            and len(set(values)) == 1
            else decisive[0]
            if not strict_resolution and decisive and len(set(decisive)) == 1
            else None
        )
        if resolved_label is not None and subject_type == "ADDITIONAL_FINDING":
            resolved += 1
            false_positives += resolved_label in {"FALSE_POSITIVE", "INCORRECT"}
    return _ratio(agreements, comparisons), (false_positives, resolved)


def _repeated_output_metrics(
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]],
) -> dict[str, Any]:
    finding_numerator = 0
    finding_denominator = 0
    root_numerator = 0
    root_denominator = 0
    identity_numerator = 0
    identity_denominator = 0
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
        identity_sets = [_root_cause_identity_set(output) for output in outputs]
        finding_ratio = _pairwise_jaccard(finding_sets)
        root_ratio = _pairwise_jaccard(root_sets)
        identity_ratio = _pairwise_jaccard(identity_sets)
        finding_numerator += finding_ratio["numerator"]
        finding_denominator += finding_ratio["denominator"]
        root_numerator += root_ratio["numerator"]
        root_denominator += root_ratio["denominator"]
        identity_numerator += identity_ratio["numerator"]
        identity_denominator += identity_ratio["denominator"]
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
    return {
        "findingOverlap": _ratio(finding_numerator, finding_denominator),
        "rootCauseOverlap": _ratio(root_numerator, root_denominator),
        "rootCauseIdentityOverlap": _ratio(
            identity_numerator, identity_denominator
        ),
        "decisionStability": _ratio(decision_numerator, decision_denominator),
        "severityAgreement": _ratio(severity_numerator, severity_denominator),
        "scoreVariance": {
            "sampleGroups": len(score_mads),
            "medianAbsoluteDeviation": (
                statistics.median(score_mads) if score_mads else None
            ),
            "maximumRange": max(score_ranges) if score_ranges else None,
        },
    }


def _oracle_stability_projection(
    oracle_assessment: dict[str, Any],
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]],
) -> dict[str, Any]:
    rows = oracle_assessment["assessments"]
    by_sample: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row["classification"] == "EXACT_ORACLE_MATCH":
            by_sample[row["sampleId"]].add(row["oracleContentSha256"])
    overlap_numerator = 0
    overlap_denominator = 0
    for samples in grouped.values():
        ratio = _pairwise_jaccard(
            [by_sample[sample["sampleId"]] for sample in samples]
        )
        overlap_numerator += ratio["numerator"]
        overlap_denominator += ratio["denominator"]

    total = len(rows)
    response_resolved = sum(
        row["responseValidity"] != "UNRESOLVED" for row in rows
    )
    oracle_resolved = sum(
        row["classification"] != "UNRESOLVED" for row in rows
    )
    limitations = []
    if oracle_resolved != total:
        limitations.append("oracle assessment contains unresolved classifications")
    if oracle_assessment["verifier"]["kind"] != "HUMAN":
        limitations.append("oracle assessment is agent-assisted, not human-verified")
    return {
        "coverage": {
            "oracleSubjects": total,
            "resolvedOracleSubjects": oracle_resolved,
        },
        "metrics": {
            "responseValidityRate": _ratio(
                sum(row["responseValidity"] == "CORRECT" for row in rows),
                response_resolved,
            ),
            "responseResolutionRate": _ratio(response_resolved, total),
            "exactOracleRecall": _ratio(
                sum(row["classification"] == "EXACT_ORACLE_MATCH" for row in rows),
                total,
            ),
            "alternativeValidFindingRate": _ratio(
                sum(
                    row["classification"] == "ALTERNATIVE_VALID_FINDING"
                    for row in rows
                ),
                total,
            ),
            "oracleMissRate": _ratio(
                sum(row["classification"] == "MISSED" for row in rows), total
            ),
            "oracleResolutionRate": _ratio(oracle_resolved, total),
            "oracleRootCauseOverlap": _ratio(
                overlap_numerator, overlap_denominator
            ),
        },
        "limitations": limitations,
    }


def _stability_limitations(
    source_suite: dict[str, Any],
    adjudicator_kinds: set[str],
    repository_ids: set[str],
    treatment_ids: set[str],
    model_ids: set[str],
    minimum_repetitions: int,
    required_matrix: set[tuple[str, str, str, int]],
    completed_matrix: set[tuple[str, str, str, int]],
) -> list[str]:
    limitations = []
    if repository_ids != {row["id"] for row in source_suite["repositories"]}:
        limitations.append("not all pinned repositories are represented")
    if treatment_ids != set(TREATMENTS):
        limitations.append("not all canonical treatments are represented")
    if len(model_ids) < source_suite["protocol"]["minimumModelConfigurations"]:
        limitations.append("fewer than the required model configurations are represented")
    if minimum_repetitions < source_suite["protocol"]["repetitions"]:
        limitations.append("fewer than the required repetitions are represented")
    if not required_matrix <= completed_matrix:
        limitations.append("the required campaign matrix is not fully completed")
    if not adjudicator_kinds:
        limitations.append("independent human adjudication is not attached")
    elif adjudicator_kinds != {"HUMAN"}:
        limitations.append(
            "adjudication is agent-assisted, not independent human adjudication"
        )
    return limitations


def build_stability_report(
    source_suite: dict[str, Any],
    campaign: dict[str, Any],
    adjudication: dict[str, Any] | None = None,
    oracle_assessment: dict[str, Any] | None = None,
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
    if oracle_assessment is not None:
        if adjudication is None:
            raise RealRepositoryError(
                "oracle assessment requires an attached adjudication"
            )
        oracle_errors = validate_oracle_assessment(
            oracle_assessment,
            source_suite,
            campaign,
            adjudication,
        )
        if oracle_errors:
            raise RealRepositoryError(
                "invalid oracle assessment: " + "; ".join(oracle_errors)
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

    repeated_metrics = _repeated_output_metrics(grouped)
    oracle_projection = (
        _oracle_stability_projection(oracle_assessment, grouped)
        if oracle_assessment is not None
        else None
    )
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

    limitations = _stability_limitations(
        source_suite,
        adjudicator_kinds,
        repository_ids,
        treatments,
        model_ids,
        minimum_repetitions,
        expected,
        completed_matrix,
    )
    if oracle_projection is not None:
        limitations.extend(oracle_projection["limitations"])
    complete = not limitations and campaign["status"] == "COMPLETED"

    durations = [float(sample["durationSeconds"]) for sample in campaign["samples"]]
    tokens = [
        sample["usage"]["totalTokens"]
        for sample in campaign["samples"]
        if sample["usage"]["totalTokens"] is not None
    ]
    stability_v2 = oracle_assessment is not None
    report = {
        "schema": (
            "review-craft.eval-real-repository-stability.v2"
            if stability_v2
            else "review-craft.eval-real-repository-stability.v1"
        ),
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
            **repeated_metrics,
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
    if oracle_assessment is not None:
        report["oracleAssessmentContentSha256"] = oracle_assessment[
            "contentSha256"
        ]
        report["coverage"].update(oracle_projection["coverage"])
        report["metrics"].update(oracle_projection["metrics"])
    report["contentSha256"] = sha256_json(_without_content_hash(report))
    stability_errors = schema_errors(
        report, STABILITY_V2_SCHEMA if stability_v2 else STABILITY_SCHEMA
    )
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
    oracle_assessment: dict[str, Any] | None = None,
) -> list[str]:
    schema = payload.get("schema") if isinstance(payload, dict) else None
    if schema == "review-craft.eval-real-repository-stability.v1":
        selected_schema = STABILITY_SCHEMA
    elif schema == "review-craft.eval-real-repository-stability.v2":
        selected_schema = STABILITY_V2_SCHEMA
    else:
        return [f"stability report has unsupported schema {schema!r}"]
    errors = schema_errors(payload, selected_schema)
    if errors:
        return errors
    errors.extend(_content_hash_errors(payload, "stability report"))
    expected = build_stability_report(
        source_suite, campaign, adjudication, oracle_assessment
    )
    if payload == expected:
        return errors
    if (
        schema == "review-craft.eval-real-repository-stability.v1"
        and oracle_assessment is None
        and "rootCauseIdentityOverlap" not in payload["metrics"]
    ):
        legacy_metrics = dict(expected["metrics"])
        legacy_metrics.pop("rootCauseIdentityOverlap")
        legacy_expected = {**expected, "metrics": legacy_metrics}
        legacy_expected["contentSha256"] = sha256_json(
            _without_content_hash(legacy_expected)
        )
        if payload == legacy_expected:
            return errors
    errors.append("stability report does not match deterministic campaign analysis")
    return errors
