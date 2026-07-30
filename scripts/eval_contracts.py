from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "evals/schemas"
RUN_SCHEMA = SCHEMA_ROOT / "eval-run.schema.json"
HOST_OUTPUT_SCHEMA = SCHEMA_ROOT / "eval-host-output.schema.json"
ADAPTER_SCHEMA = SCHEMA_ROOT / "eval-adapter.schema.json"
ADJUDICATION_SCHEMA = SCHEMA_ROOT / "eval-adjudication.schema.json"
ADJUDICATION_RESULT_SCHEMA = SCHEMA_ROOT / "eval-adjudication-result.schema.json"
COMPARISON_SCHEMA = SCHEMA_ROOT / "eval-comparison.schema.json"
GOLDEN_SNAPSHOT_SCHEMA = SCHEMA_ROOT / "eval-golden-snapshot.schema.json"
USAGE_SCHEMA = SCHEMA_ROOT / "eval-usage.schema.json"
USAGE_COUNT_FIELDS = (
    "inputTokens",
    "cachedInputTokens",
    "cacheWriteInputTokens",
    "outputTokens",
    "reasoningOutputTokens",
    "totalTokens",
    "turnCount",
)
TOOL_CALL_FIELDS = (
    "commandExecution",
    "fileChange",
    "mcpToolCall",
    "collabToolCall",
    "webSearch",
)
IGNORED_TREE_PARTS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}


class EvalError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def schema_errors(instance: Any, schema_path: Path) -> list[str]:
    schema = read_json(schema_path)
    registry = Registry()
    for candidate in sorted(SCHEMA_ROOT.glob("*.schema.json")):
        document = read_json(candidate)
        registry = registry.with_resource(document["$id"], Resource.from_contents(document))
    validator = Draft202012Validator(
        schema,
        registry=registry,
        format_checker=FormatChecker(),
    )
    errors = sorted(validator.iter_errors(instance), key=lambda item: list(item.path))
    rendered = []
    for error in errors:
        location = ".".join(str(part) for part in error.path) or "<root>"
        rendered.append(f"{location}: {error.message}")
    return rendered


def _tree_manifest(root: Path) -> list[dict[str, Any]]:
    if not root.is_dir():
        raise EvalError(f"expected directory: {root}")
    rows = []
    try:
        repository_relative = root.resolve().relative_to(ROOT.resolve())
    except ValueError:
        repository_relative = None
    paths: list[Path]
    if repository_relative is not None and (ROOT / ".git").is_dir():
        pathspec = repository_relative.as_posix() or "."
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(ROOT),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                pathspec,
            ],
            check=True,
            capture_output=True,
        )
        paths = []
        for item in completed.stdout.split(b"\0"):
            if not item:
                continue
            candidate = ROOT / item.decode("utf-8", errors="surrogateescape")
            if candidate.is_file() or candidate.is_symlink():
                paths.append(candidate)
    else:
        paths = [
            path for path in root.rglob("*") if path.is_file() or path.is_symlink()
        ]
    for path in sorted(paths):
        relative = path.relative_to(root)
        if any(part in IGNORED_TREE_PARTS for part in relative.parts):
            continue
        content = (
            os.readlink(path).encode("utf-8", errors="surrogateescape")
            if path.is_symlink()
            else path.read_bytes()
        )
        rows.append(
            {
                "path": relative.as_posix(),
                "size": len(content),
                "sha256": sha256_bytes(content),
            }
        )
    return rows


def tree_sha256(root: Path) -> str:
    return sha256_json(_tree_manifest(root))


def _git_output(*args: str) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.rstrip("\n")


def source_metadata() -> dict[str, Any]:
    status = _git_output("status", "--porcelain=v1", "--untracked-files=all") or ""
    runner_manifest = {
        name: sha256_bytes((ROOT / f"scripts/{name}").read_bytes())
        for name in ("eval_contracts.py", "run_evals.py")
    }
    return {
        "revision": _git_output("rev-parse", "HEAD"),
        "dirty": bool(status),
        "dirtyFingerprint": sha256_bytes(status.encode("utf-8")),
        "treeSha256": tree_sha256(ROOT),
        "runnerSha256": sha256_json(runner_manifest),
    }


def safe_artifact(run_dir: Path, relative: str) -> Path:
    candidate = (run_dir / relative).resolve()
    try:
        candidate.relative_to(run_dir.resolve())
    except ValueError as error:
        raise EvalError(f"artifact escapes run directory: {relative}") from error
    return candidate


def file_hash(path: Path) -> str:
    if not path.is_file():
        raise EvalError(f"missing artifact: {path}")
    return sha256_bytes(path.read_bytes())


def _percent(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator * 100.0 / denominator, 2)


def unavailable_usage(
    reason: str, *, collector: dict[str, str] | None = None
) -> dict[str, Any]:
    return {
        "schema": "review-craft.eval-usage.v1",
        "availability": "UNAVAILABLE",
        "collector": collector,
        "inputTokens": None,
        "cachedInputTokens": None,
        "cacheWriteInputTokens": None,
        "outputTokens": None,
        "reasoningOutputTokens": None,
        "totalTokens": None,
        "turnCount": None,
        "toolCalls": None,
        "unavailableReason": reason,
    }


def validate_usage_record(payload: dict[str, Any]) -> list[str]:
    errors = schema_errors(payload, USAGE_SCHEMA)
    if errors or payload["availability"] != "AVAILABLE":
        return errors
    if payload["totalTokens"] != payload["inputTokens"] + payload["outputTokens"]:
        errors.append("totalTokens must equal inputTokens plus outputTokens")
    tool_calls = payload["toolCalls"]
    if tool_calls["total"] != sum(tool_calls["byType"].values()):
        errors.append("toolCalls.total must equal the byType sum")
    return errors


def aggregate_usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    usages = [record["usage"] for record in records]
    reported = [usage for usage in usages if usage["availability"] == "AVAILABLE"]
    unavailable = [usage for usage in usages if usage["availability"] == "UNAVAILABLE"]
    reasons: dict[str, int] = {}
    for usage in unavailable:
        reason = usage["unavailableReason"]
        reasons[reason] = reasons.get(reason, 0) + 1
    reported_usage = None
    if reported:
        tool_by_type = {
            field: sum(usage["toolCalls"]["byType"][field] for usage in reported)
            for field in TOOL_CALL_FIELDS
        }
        reported_usage = {
            field: sum(usage[field] for usage in reported)
            for field in USAGE_COUNT_FIELDS
        }
        reported_usage["toolCalls"] = {
            "total": sum(tool_by_type.values()),
            "byType": tool_by_type,
        }
    return {
        "availability": (
            "COMPLETE"
            if not unavailable
            else "PARTIAL"
            if reported
            else "UNAVAILABLE"
        ),
        "reportedCases": len(reported),
        "unavailableCases": len(unavailable),
        "reportedUsage": reported_usage,
        "unavailableReasons": reasons,
    }


def source_stable(source: dict[str, Any]) -> bool:
    return bool(
        source.get("revision") == source.get("completedRevision")
        and source.get("dirty") == source.get("completedDirty")
        and source.get("dirtyFingerprint") == source.get("completedDirtyFingerprint")
        and source.get("treeSha256") == source.get("completedTreeSha256")
    )


def _normalized_outputs(run_dir: Path, records: list[dict[str, Any]]) -> dict[str, dict]:
    outputs = {}
    for record in records:
        artifact = record.get("normalizedOutputArtifact")
        if record.get("status") != "COMPLETED" or not isinstance(artifact, str):
            continue
        value = read_json(safe_artifact(run_dir, artifact))
        outputs[record["id"]] = value
    return outputs


def score_cases(
    run_dir: Path,
    suite: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    cases_by_id = {case["id"]: case for case in suite["cases"]}
    outputs = _normalized_outputs(run_dir, records)
    selected = [cases_by_id[record["id"]] for record in records]
    positive = [case for case in selected if case["class"] == "positive"]
    negative = [case for case in selected if case["class"] == "negative"]
    completed = [record for record in records if record["status"] == "COMPLETED"]
    failed = [record for record in records if record["status"] == "FAILED"]
    unavailable = [record for record in records if record["status"] == "UNAVAILABLE"]

    detected_positive = sum(
        bool(outputs.get(case["id"], {}).get("findingDetected")) for case in positive
    )
    detected_negative = sum(
        bool(outputs.get(case["id"], {}).get("findingDetected")) for case in negative
    )
    total_detections = detected_positive + detected_negative

    location_matches = 0
    evidence_present = 0
    decision_matches = 0
    rewrite_traps = [case for case in negative if "REWRITE" in case["prohibitedDecisions"]]
    restrained_rewrites = 0
    for case in selected:
        output = outputs.get(case["id"])
        if output is None:
            continue
        decisions = set(output["decisions"])
        expected = set(case["expectedDecisions"])
        prohibited = set(case["prohibitedDecisions"])
        if decisions & expected and not decisions & prohibited:
            decision_matches += 1
        if case["class"] == "positive" and output["findingDetected"]:
            actual_paths = {location["path"] for location in output["locations"]}
            if actual_paths & set(case["expectedLocations"]):
                location_matches += 1
            if output["evidence"]:
                evidence_present += 1
        if case in rewrite_traps and "REWRITE" not in decisions:
            restrained_rewrites += 1

    total = len(records)
    metrics = {
        "totalCases": total,
        "completedCases": len(completed),
        "failedCases": len(failed),
        "unavailableCases": len(unavailable),
        "totalDurationMs": sum(record["durationMs"] for record in records),
        "executionCoveragePercent": _percent(len(completed), total),
        "candidateRecallPercent": _percent(detected_positive, len(positive)),
        "findingPrecisionPercent": _percent(detected_positive, total_detections),
        "falsePositiveRatePercent": _percent(detected_negative, len(negative)),
        "locationAccuracyPercent": _percent(location_matches, len(positive)),
        "evidencePresencePercent": _percent(evidence_present, len(positive)),
        "decisionAccuracyPercent": _percent(decision_matches, total),
        "rewriteRestraintPercent": _percent(restrained_rewrites, len(rewrite_traps)),
        "semanticEvidenceValidation": "NOT_AUTOMATED",
    }
    if records and all("usage" in record for record in records):
        metrics["usage"] = aggregate_usage(records)
    return metrics


def overall_status(records: list[dict[str, Any]]) -> str:
    statuses = [record["status"] for record in records]
    if all(status == "COMPLETED" for status in statuses):
        return "COMPLETED"
    if all(status == "UNAVAILABLE" for status in statuses):
        return "UNAVAILABLE"
    if any(status == "COMPLETED" for status in statuses):
        return "PARTIAL"
    return "FAILED"


def golden_eligible(payload: dict[str, Any]) -> bool:
    return bool(
        payload["status"] == "COMPLETED"
        and payload["suite"]["fullSuite"]
        and payload["adapter"]["description"]["evidenceKind"] == "REAL_HOST"
        and not payload["source"]["dirty"]
        and payload["source"]["stableThroughoutRun"]
    )


def validate_run(run_dir: Path) -> list[str]:
    errors = []
    result_path = run_dir / "result.json"
    try:
        payload = read_json(result_path)
    except (OSError, json.JSONDecodeError) as error:
        return [f"result.json: {error}"]
    errors.extend(f"result.json:{error}" for error in schema_errors(payload, RUN_SCHEMA))
    if errors:
        return errors

    def check_artifact(relative: str, expected_hash: str, label: str) -> Path | None:
        try:
            path = safe_artifact(run_dir, relative)
            actual = file_hash(path)
        except EvalError as error:
            errors.append(f"{label}: {error}")
            return None
        if actual != expected_hash:
            errors.append(f"{label}: sha256 mismatch")
        return path

    suite_path = check_artifact(
        payload["suite"]["artifact"], payload["suite"]["sha256"], "suite"
    )
    check_artifact(
        payload["promptTemplate"]["artifact"],
        payload["promptTemplate"]["sha256"],
        "promptTemplate",
    )
    seen_case_ids = set()
    for record in payload["cases"]:
        case_id = record["id"]
        if case_id in seen_case_ids:
            errors.append(f"case {case_id}: duplicate result")
        seen_case_ids.add(case_id)
        check_artifact(record["promptArtifact"], record["promptSha256"], f"case {case_id} prompt")
        check_artifact(record["stdoutArtifact"], record["stdoutSha256"], f"case {case_id} stdout")
        check_artifact(record["stderrArtifact"], record["stderrSha256"], f"case {case_id} stderr")
        usage_artifact = record.get("usageArtifact")
        usage_hash = record.get("usageSha256")
        usage_record = record.get("usage")
        usage_fields = (usage_artifact, usage_hash, usage_record)
        if any(value is not None for value in usage_fields) and not all(
            value is not None for value in usage_fields
        ):
            errors.append(f"case {case_id}: usage artifact, hash, and record must be paired")
        if (
            isinstance(usage_artifact, str)
            and isinstance(usage_hash, str)
            and isinstance(usage_record, dict)
        ):
            usage_path = check_artifact(
                usage_artifact,
                usage_hash,
                f"case {case_id} usage",
            )
            if usage_path is not None:
                try:
                    artifact_usage = read_json(usage_path)
                except (OSError, json.JSONDecodeError) as error:
                    errors.append(f"case {case_id} usage: {error}")
                else:
                    usage_errors = validate_usage_record(artifact_usage)
                    errors.extend(
                        f"case {case_id} usage:{error}" for error in usage_errors
                    )
                    if artifact_usage != usage_record:
                        errors.append(f"case {case_id}: usage artifact does not match result")
        try:
            fixture = safe_artifact(run_dir, record["fixtureArtifact"])
            actual_tree = tree_sha256(fixture)
        except EvalError as error:
            errors.append(f"case {case_id} fixture: {error}")
        else:
            if actual_tree != record["fixtureTreeSha256"]:
                errors.append(f"case {case_id} fixture: tree sha256 mismatch")
        adapter_output_artifact = record["adapterOutputArtifact"]
        adapter_output_hash = record["adapterOutputSha256"]
        if isinstance(adapter_output_artifact, str) != isinstance(adapter_output_hash, str):
            errors.append(f"case {case_id}: adapter output artifact and hash must be paired")
        if isinstance(adapter_output_artifact, str) and isinstance(adapter_output_hash, str):
            check_artifact(
                adapter_output_artifact,
                adapter_output_hash,
                f"case {case_id} adapter output",
            )
        output_artifact = record["normalizedOutputArtifact"]
        output_hash = record["normalizedOutputSha256"]
        if record["status"] == "COMPLETED" and (
            not isinstance(output_artifact, str) or not isinstance(output_hash, str)
        ):
            errors.append(f"case {case_id}: completed result requires normalized output")
        if record["status"] == "COMPLETED" and (
            output_artifact != adapter_output_artifact or output_hash != adapter_output_hash
        ):
            errors.append(f"case {case_id}: normalized output must bind the adapter output")
        if record["status"] != "COMPLETED" and (
            output_artifact is not None or output_hash is not None
        ):
            errors.append(f"case {case_id}: failed result cannot claim normalized output")
        if isinstance(output_artifact, str) and isinstance(output_hash, str):
            output_path = check_artifact(
                output_artifact, output_hash, f"case {case_id} normalized output"
            )
            if output_path is not None:
                try:
                    output = read_json(output_path)
                except (OSError, json.JSONDecodeError) as error:
                    errors.append(f"case {case_id} normalized output: {error}")
                else:
                    errors.extend(
                        f"case {case_id} normalized output:{error}"
                        for error in schema_errors(output, HOST_OUTPUT_SCHEMA)
                    )
                    for location in output.get("locations", []):
                        if location["lineEnd"] < location["lineStart"]:
                            errors.append(f"case {case_id}: inverted line range")
    if suite_path is not None:
        try:
            suite = read_json(suite_path)
            cases_by_id = {case["id"]: case for case in suite["cases"]}
            selected = payload["suite"]["selectedCaseIds"]
            if [record["id"] for record in payload["cases"]] != selected:
                errors.append("suite.selectedCaseIds does not match ordered case results")
            if any(case_id not in cases_by_id for case_id in selected):
                errors.append("suite.selectedCaseIds contains an unknown case")
            expected_full = selected == [case["id"] for case in suite["cases"]]
            if payload["suite"]["fullSuite"] != expected_full:
                errors.append("suite.fullSuite does not match selected cases")
            if not errors:
                expected_metrics = score_cases(run_dir, suite, payload["cases"])
                if payload["metrics"] != expected_metrics:
                    errors.append("metrics do not match deterministic scoring")
        except (KeyError, TypeError, OSError, json.JSONDecodeError, EvalError) as error:
            errors.append(f"suite scoring failed: {error}")
    expected_status = overall_status(payload["cases"])
    if payload["status"] != expected_status:
        errors.append("status does not match case results")
    if payload["goldenEligible"] != golden_eligible(payload):
        errors.append("goldenEligible does not match provenance and completeness gates")
    if payload["source"]["stableThroughoutRun"] != source_stable(payload["source"]):
        errors.append("source.stableThroughoutRun does not match start/completion metadata")
    expected_content = sha256_json(
        {key: value for key, value in payload.items() if key != "contentSha256"}
    )
    if payload["contentSha256"] != expected_content:
        errors.append("contentSha256 does not match result metadata")
    return errors


def validate_content_bound_payload(
    payload: dict[str, Any], schema_path: Path, *, label: str
) -> list[str]:
    errors = [f"{label}:{error}" for error in schema_errors(payload, schema_path)]
    if errors:
        return errors
    expected = sha256_json(
        {key: value for key, value in payload.items() if key != "contentSha256"}
    )
    if payload["contentSha256"] != expected:
        errors.append(f"{label}:contentSha256 does not match the canonical payload")
    return errors


def validate_comparison_payload(payload: dict[str, Any]) -> list[str]:
    return validate_content_bound_payload(
        payload,
        COMPARISON_SCHEMA,
        label="comparison",
    )


def validate_golden_snapshot(payload: dict[str, Any]) -> list[str]:
    return validate_content_bound_payload(
        payload,
        GOLDEN_SNAPSHOT_SCHEMA,
        label="golden snapshot",
    )


def _adjudication_context(
    run_dir: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    run_errors = validate_run(run_dir)
    if run_errors:
        raise EvalError("evaluation run is invalid: " + "; ".join(run_errors))
    run = read_json(run_dir / "result.json")
    if run["status"] != "COMPLETED":
        raise EvalError("semantic adjudication requires a completed evaluation run")
    suite = read_json(safe_artifact(run_dir, run["suite"]["artifact"]))
    cases_by_id = {case["id"]: case for case in suite["cases"]}
    outputs = _normalized_outputs(run_dir, run["cases"])
    return run, cases_by_id, outputs


def _validate_adjudication_entry(
    *,
    case: dict[str, Any],
    output: dict[str, Any],
    entry: dict[str, Any],
) -> None:
    outcome = entry["outcome"]
    finding_detected = output["findingDetected"]
    if finding_detected:
        allowed = {
            "SEEDED_ISSUE_MATCH",
            "OTHER_VALID_FINDING",
            "FALSE_POSITIVE",
            "UNRESOLVED",
        }
    else:
        allowed = {"MISS", "NO_FINDING_CORRECT", "UNRESOLVED"}
    if outcome not in allowed:
        raise EvalError(
            f"case {case['id']}: outcome {outcome} conflicts with findingDetected="
            f"{str(finding_detected).lower()}"
        )
    if case["class"] == "positive" and outcome == "NO_FINDING_CORRECT":
        raise EvalError(f"case {case['id']}: a positive case cannot be NO_FINDING_CORRECT")
    if case["class"] == "negative" and outcome in {"SEEDED_ISSUE_MATCH", "MISS"}:
        raise EvalError(f"case {case['id']}: a negative case cannot use outcome {outcome}")


def _semantic_metrics(
    *,
    entries: list[dict[str, Any]],
    cases_by_id: dict[str, dict[str, Any]],
    outputs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    positive = [entry for entry in entries if cases_by_id[entry["id"]]["class"] == "positive"]
    negative = [entry for entry in entries if cases_by_id[entry["id"]]["class"] == "negative"]
    unresolved = [
        entry
        for entry in entries
        if entry["outcome"] == "UNRESOLVED"
        or entry["decisionDisposition"] == "UNRESOLVED"
    ]
    seeded_matches = sum(entry["outcome"] == "SEEDED_ISSUE_MATCH" for entry in positive)
    missed_seeded = sum(
        entry["outcome"] in {"OTHER_VALID_FINDING", "FALSE_POSITIVE", "MISS"}
        for entry in positive
    )
    valid_findings = sum(
        entry["outcome"] in {"SEEDED_ISSUE_MATCH", "OTHER_VALID_FINDING"}
        for entry in entries
    )
    false_positive_findings = sum(
        entry["outcome"] == "FALSE_POSITIVE" for entry in entries
    )
    negative_false_positives = sum(
        entry["outcome"] == "FALSE_POSITIVE" for entry in negative
    )
    contaminated_negative = sum(
        entry["outcome"] == "OTHER_VALID_FINDING" for entry in negative
    )

    unresolved_positive = any(entry["outcome"] == "UNRESOLVED" for entry in positive)
    detected = [entry for entry in entries if outputs[entry["id"]]["findingDetected"]]
    unresolved_detection = any(entry["outcome"] == "UNRESOLVED" for entry in detected)
    unresolved_negative = any(entry["outcome"] == "UNRESOLVED" for entry in negative)
    clean_negative_count = len(negative) - contaminated_negative
    unresolved_decision = any(
        entry["decisionDisposition"] == "UNRESOLVED" for entry in entries
    )

    return {
        "totalCases": len(entries),
        "resolvedCases": len(entries) - len(unresolved),
        "unresolvedCases": len(unresolved),
        "positiveCases": len(positive),
        "negativeCases": len(negative),
        "seededIssueMatches": seeded_matches,
        "missedSeededIssues": missed_seeded,
        "validFindings": valid_findings,
        "falsePositiveFindings": false_positive_findings,
        "contaminatedNegativeCases": contaminated_negative,
        "semanticSeededRecallPercent": (
            None if unresolved_positive else _percent(seeded_matches, len(positive))
        ),
        "semanticFindingPrecisionPercent": (
            None if unresolved_detection else _percent(valid_findings, len(detected))
        ),
        "semanticFalsePositiveRatePercent": (
            None
            if unresolved_negative
            else _percent(negative_false_positives, clean_negative_count)
        ),
        "semanticDecisionAccuracyPercent": (
            None
            if unresolved_decision
            else _percent(
                sum(entry["decisionDisposition"] == "APPROPRIATE" for entry in entries),
                len(entries),
            )
        ),
        "semanticEvidenceValidation": "PARTIAL" if unresolved else "ADJUDICATED",
    }


def build_adjudication_template(
    run_dir: Path,
    *,
    kind: str,
    protocol: str,
) -> dict[str, Any]:
    run, _, _ = _adjudication_context(run_dir)
    template = {
        "schema": "review-craft.eval-adjudication.v1",
        "runId": run["runId"],
        "runContentSha256": run["contentSha256"],
        "adjudicator": {
            "kind": kind,
            "protocol": protocol,
        },
        "cases": [
            {
                "id": record["id"],
                "normalizedOutputSha256": record["normalizedOutputSha256"],
                "outcome": "UNRESOLVED",
                "decisionDisposition": "UNRESOLVED",
                "rationale": "Pending semantic adjudication.",
            }
            for record in run["cases"]
        ],
    }
    errors = schema_errors(template, ADJUDICATION_SCHEMA)
    if errors:
        raise EvalError("generated semantic adjudication template is invalid: " + "; ".join(errors))
    return template


def build_adjudication_result(run_dir: Path, adjudication: dict[str, Any]) -> dict[str, Any]:
    input_errors = schema_errors(adjudication, ADJUDICATION_SCHEMA)
    if input_errors:
        raise EvalError("semantic adjudication input is invalid: " + "; ".join(input_errors))
    run, cases_by_id, outputs = _adjudication_context(run_dir)
    if adjudication["runId"] != run["runId"]:
        raise EvalError("semantic adjudication runId does not match the evaluation run")
    if adjudication["runContentSha256"] != run["contentSha256"]:
        raise EvalError("semantic adjudication runContentSha256 does not match the evaluation run")

    entries_by_id: dict[str, dict[str, Any]] = {}
    for entry in adjudication["cases"]:
        case_id = entry["id"]
        if case_id in entries_by_id:
            raise EvalError(f"semantic adjudication contains duplicate case id: {case_id}")
        entries_by_id[case_id] = entry
    selected_ids = [record["id"] for record in run["cases"]]
    missing = sorted(set(selected_ids) - set(entries_by_id))
    extra = sorted(set(entries_by_id) - set(selected_ids))
    if missing or extra:
        detail = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if extra:
            detail.append("extra=" + ",".join(extra))
        raise EvalError("semantic adjudication case coverage mismatch: " + "; ".join(detail))

    records_by_id = {record["id"]: record for record in run["cases"]}
    entries = []
    for case_id in selected_ids:
        record = records_by_id[case_id]
        entry = entries_by_id[case_id]
        if entry["normalizedOutputSha256"] != record["normalizedOutputSha256"]:
            raise EvalError(f"case {case_id}: normalized output sha256 does not match the run")
        _validate_adjudication_entry(
            case=cases_by_id[case_id],
            output=outputs[case_id],
            entry=entry,
        )
        entries.append(entry)

    normalized_input = {
        "schema": adjudication["schema"],
        "runId": adjudication["runId"],
        "runContentSha256": adjudication["runContentSha256"],
        "adjudicator": adjudication["adjudicator"],
        "cases": entries,
    }
    result = {
        "schema": "review-craft.eval-adjudication-result.v1",
        "run": {
            "id": run["runId"],
            "contentSha256": run["contentSha256"],
            "suiteSha256": run["suite"]["sha256"],
            "treatment": run["treatment"],
        },
        "adjudicator": adjudication["adjudicator"],
        "inputContentSha256": sha256_json(normalized_input),
        "cases": entries,
        "metrics": _semantic_metrics(
            entries=entries,
            cases_by_id=cases_by_id,
            outputs=outputs,
        ),
        "contentSha256": "0" * 64,
    }
    result["contentSha256"] = sha256_json(
        {key: value for key, value in result.items() if key != "contentSha256"}
    )
    result_errors = schema_errors(result, ADJUDICATION_RESULT_SCHEMA)
    if result_errors:
        raise EvalError("generated semantic adjudication is invalid: " + "; ".join(result_errors))
    return result


def validate_adjudication_result(run_dir: Path, payload: dict[str, Any]) -> list[str]:
    errors = [
        f"result:{error}" for error in schema_errors(payload, ADJUDICATION_RESULT_SCHEMA)
    ]
    if errors:
        return errors
    adjudication = {
        "schema": "review-craft.eval-adjudication.v1",
        "runId": payload["run"]["id"],
        "runContentSha256": payload["run"]["contentSha256"],
        "adjudicator": payload["adjudicator"],
        "cases": payload["cases"],
    }
    try:
        expected = build_adjudication_result(run_dir, adjudication)
    except (EvalError, KeyError, TypeError, OSError, json.JSONDecodeError) as error:
        return [str(error)]
    if payload != expected:
        errors.append("semantic adjudication result does not match the bound run and decisions")
    return errors
