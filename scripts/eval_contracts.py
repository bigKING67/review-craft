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
CASES_SCHEMA = SCHEMA_ROOT / "eval-cases.schema.json"
HOST_OUTPUT_SCHEMA = SCHEMA_ROOT / "eval-host-output.schema.json"
ADAPTER_SCHEMA = SCHEMA_ROOT / "eval-adapter.schema.json"
ADJUDICATION_SCHEMA = SCHEMA_ROOT / "eval-adjudication.schema.json"
ADJUDICATION_RESULT_SCHEMA = SCHEMA_ROOT / "eval-adjudication-result.schema.json"
COMPARISON_SCHEMA = SCHEMA_ROOT / "eval-comparison.schema.json"
GOLDEN_SNAPSHOT_SCHEMA = SCHEMA_ROOT / "eval-golden-snapshot.schema.json"
USAGE_SCHEMA = SCHEMA_ROOT / "eval-usage.schema.json"
TOOL_TRACE_SCHEMA = SCHEMA_ROOT / "eval-tool-trace.schema.json"
LEGACY_ABLATION_TREATMENTS = (
    "ORDINARY_PROMPT",
    "ADVERSARIAL_PROMPT",
    "RISK_LENS_ADVERSARIAL",
    "REVIEW_CRAFT_EVIDENCE_LOOP",
)
ABLATION_TREATMENTS = (
    "ORDINARY_PROMPT",
    "RISK_LENS_REVIEW",
    "REVIEW_CRAFT_EVIDENCE_LOOP",
)
ABLATION_PROTOCOLS = {
    "v1": {
        "treatments": LEGACY_ABLATION_TREATMENTS,
        "deltas": (
            ("A_TO_B", "ORDINARY_PROMPT", "ADVERSARIAL_PROMPT"),
            ("B_TO_C", "ADVERSARIAL_PROMPT", "RISK_LENS_ADVERSARIAL"),
            ("C_TO_D", "RISK_LENS_ADVERSARIAL", "REVIEW_CRAFT_EVIDENCE_LOOP"),
            ("A_TO_D", "ORDINARY_PROMPT", "REVIEW_CRAFT_EVIDENCE_LOOP"),
        ),
    },
    "v2": {
        "treatments": ABLATION_TREATMENTS,
        "deltas": (
            ("A_TO_B", "ORDINARY_PROMPT", "RISK_LENS_REVIEW"),
            ("B_TO_C", "RISK_LENS_REVIEW", "REVIEW_CRAFT_EVIDENCE_LOOP"),
            ("A_TO_C", "ORDINARY_PROMPT", "REVIEW_CRAFT_EVIDENCE_LOOP"),
        ),
    },
}
ABLATION_SCHEMAS = {
    kind: {
        version: SCHEMA_ROOT
        / f"eval-ablation-{kind}{'-v2' if version == 'v2' else ''}.schema.json"
        for version in ABLATION_PROTOCOLS
    }
    for kind in (
        "schedule",
        "run",
        "blind-bundle",
        "adjudication",
        "adjudication-result",
        "comparison",
        "snapshot",
    )
}
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


def ablation_protocol(schema: str) -> tuple[str, dict[str, Any]]:
    version = schema.rsplit(".", 1)[-1]
    protocol = ABLATION_PROTOCOLS.get(version)
    if protocol is None:
        raise EvalError(f"unsupported ablation schema: {schema}")
    return version, protocol


def ablation_schema(kind: str, version: str) -> Path:
    try:
        return ABLATION_SCHEMAS[kind][version]
    except KeyError as error:
        raise EvalError(f"unsupported ablation {kind} schema version: {version}") from error


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
    runner_names = ("eval_contracts.py", "run_evals.py", "remediation_safety.py")
    runner_manifest = {
        name: sha256_bytes((ROOT / f"scripts/{name}").read_bytes())
        for name in runner_names
        if (ROOT / f"scripts/{name}").is_file()
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


def validate_eval_suite(payload: dict[str, Any]) -> list[str]:
    errors = schema_errors(payload, CASES_SCHEMA)
    if errors or payload.get("schema") != "review-craft.eval-cases.v2":
        return errors
    pairs: dict[str, list[dict[str, Any]]] = {}
    for case in payload["cases"]:
        pairs.setdefault(case["pairId"], []).append(case)
        argv = case["verification"]["argv"]
        if any("\0" in argument for argument in argv):
            errors.append(f"case {case['id']}: verification argv contains NUL")
        if case["id"] not in argv:
            errors.append(f"case {case['id']}: verification argv must bind the case id")
    for pair_id, cases in sorted(pairs.items()):
        classes = sorted(case["class"] for case in cases)
        if classes != ["negative", "positive"]:
            errors.append(f"pair {pair_id}: expected one positive and one negative case")
            continue
        lenses = {sha256_json(case["riskLens"]) for case in cases}
        if len(lenses) != 1:
            errors.append(f"pair {pair_id}: positive and negative cases must share one risk lens")
    return errors


def validate_ablation_schedule(payload: dict[str, Any]) -> list[str]:
    try:
        version, protocol = ablation_protocol(payload.get("schema", ""))
    except EvalError as error:
        return [str(error)]
    errors = schema_errors(payload, ablation_schema("schedule", version))
    if errors:
        return errors
    treatments = payload["treatments"]
    if treatments != list(protocol["treatments"]):
        errors.append("treatments must use the canonical protocol order")
    for index, case in enumerate(payload["cases"]):
        expected = treatments[index % len(treatments) :] + treatments[: index % len(treatments)]
        if case["order"] != expected:
            errors.append(f"case {case['id']}: order does not match Latin-square rotation")
    return errors


def validate_ablation_run(ablation_dir: Path) -> list[str]:
    errors = []
    manifest_path = ablation_dir / "ablation.json"
    try:
        payload = read_json(manifest_path)
    except (OSError, json.JSONDecodeError) as error:
        return [f"ablation.json: {error}"]
    try:
        version, protocol = ablation_protocol(payload.get("schema", ""))
    except EvalError as error:
        return [f"ablation.json:{error}"]
    errors.extend(
        f"ablation.json:{error}"
        for error in schema_errors(payload, ablation_schema("run", version))
    )
    if errors:
        return errors
    if [row["treatment"] for row in payload["treatments"]] != list(
        protocol["treatments"]
    ):
        errors.append("ablation.json:treatments must use the canonical protocol order")
    suite = None
    schedule = None
    schedule_path = None
    for binding_name in ("suite", "schedule"):
        binding = payload[binding_name]
        try:
            path = safe_artifact(ablation_dir, binding["artifact"])
            actual = file_hash(path)
        except EvalError as error:
            errors.append(f"{binding_name}: {error}")
            continue
        if actual != binding["sha256"]:
            errors.append(f"{binding_name}: sha256 mismatch")
        if binding_name == "suite":
            try:
                suite = read_json(path)
            except (OSError, json.JSONDecodeError) as error:
                errors.append(f"suite: {error}")
            else:
                if not isinstance(suite, dict):
                    errors.append("suite: expected a JSON object")
                    suite = None
                    continue
                errors.extend(
                    f"suite:{error}" for error in validate_eval_suite(suite)
                )
        else:
            schedule_path = path
            try:
                schedule = read_json(path)
            except (OSError, json.JSONDecodeError) as error:
                errors.append(f"schedule: {error}")
            else:
                if not isinstance(schedule, dict):
                    errors.append("schedule: expected a JSON object")
                    schedule = None
                    continue
                errors.extend(
                    f"schedule:{error}"
                    for error in validate_ablation_schedule(schedule)
                )
                try:
                    schedule_version, _ = ablation_protocol(schedule.get("schema", ""))
                except EvalError:
                    schedule_version = None
                if schedule_version != version:
                    errors.append("schedule schema version does not match the manifest")
                if schedule.get("ablationId") != payload["ablationId"]:
                    errors.append("schedule.ablationId does not match the manifest")
                if schedule.get("treatments") != [
                    row["treatment"] for row in payload["treatments"]
                ]:
                    errors.append("schedule treatments do not match the manifest")
    schedule_case_ids = None
    if isinstance(schedule, dict) and isinstance(schedule.get("cases"), list):
        case_ids = [
            case.get("id")
            for case in schedule["cases"]
            if isinstance(case, dict)
        ]
        if len(case_ids) == len(schedule["cases"]) and all(
            isinstance(case_id, str) for case_id in case_ids
        ):
            schedule_case_ids = case_ids
            if len(set(case_ids)) != len(case_ids):
                errors.append("schedule case ids must be unique")
            if isinstance(suite, dict) and isinstance(suite.get("cases"), list):
                suite_case_ids = {
                    case.get("id")
                    for case in suite["cases"]
                    if isinstance(case, dict)
                }
                if any(case_id not in suite_case_ids for case_id in case_ids):
                    errors.append("schedule case ids contain an unknown suite case")
    seen = set()
    statuses = []
    schedule_selection_mismatch_reported = False
    for treatment in payload["treatments"]:
        name = treatment["treatment"]
        if name in seen:
            errors.append(f"treatment {name}: duplicate run")
        seen.add(name)
        statuses.append(treatment["status"])
        try:
            run_dir = safe_artifact(ablation_dir, treatment["runDir"])
        except EvalError as error:
            errors.append(f"treatment {name}: {error}")
            continue
        if not run_dir.is_dir():
            errors.append(f"treatment {name}: runDir must be a directory")
            continue
        run_errors = validate_run(run_dir)
        errors.extend(f"treatment {name}:{error}" for error in run_errors)
        if run_errors:
            continue
        run = read_json(run_dir / "result.json")
        expected = {
            "treatment": run["treatment"],
            "runDir": treatment["runDir"],
            "runId": run["runId"],
            "runContentSha256": run["contentSha256"],
            "status": run["status"],
            "goldenEligible": run["goldenEligible"],
        }
        if treatment != expected:
            errors.append(f"treatment {name}: summary does not match the bound run")
        if run.get("ablation", {}).get("id") != payload["ablationId"]:
            errors.append(f"treatment {name}: ablation id mismatch")
        selected_case_ids = run.get("suite", {}).get("selectedCaseIds")
        if schedule_case_ids is not None and selected_case_ids != schedule_case_ids:
            if not schedule_selection_mismatch_reported:
                errors.append("schedule case ids do not match suite selected case ids")
                schedule_selection_mismatch_reported = True
            errors.append(f"treatment {name}: selected case ids do not match schedule")
        child_schedule_binding = run.get("ablation", {})
        try:
            child_schedule_path = safe_artifact(
                run_dir, child_schedule_binding["scheduleArtifact"]
            )
            child_schedule_hash = file_hash(child_schedule_path)
        except (EvalError, KeyError, TypeError) as error:
            errors.append(f"treatment {name}: schedule: {error}")
            continue
        if child_schedule_hash != child_schedule_binding.get("scheduleSha256"):
            errors.append(f"treatment {name}: schedule sha256 mismatch")
        if schedule_path is not None:
            try:
                matches_manifest_schedule = (
                    child_schedule_path.read_bytes() == schedule_path.read_bytes()
                )
            except OSError as error:
                errors.append(f"treatment {name}: schedule: {error}")
            else:
                if not matches_manifest_schedule:
                    errors.append(
                        f"treatment {name}: schedule does not match manifest schedule"
                    )
        try:
            child_schedule = read_json(child_schedule_path)
        except (OSError, json.JSONDecodeError) as error:
            errors.append(f"treatment {name}: schedule: {error}")
            continue
        if not isinstance(child_schedule, dict):
            errors.append(f"treatment {name}: schedule: expected a JSON object")
            continue
        child_schedule_errors = validate_ablation_schedule(child_schedule)
        errors.extend(
            f"treatment {name}: schedule:{error}"
            for error in child_schedule_errors
        )
        try:
            child_schedule_version, _ = ablation_protocol(
                child_schedule.get("schema", "")
            )
        except (AttributeError, EvalError):
            child_schedule_version = None
        if child_schedule_version != version:
            errors.append(
                f"treatment {name}: schedule schema version does not match manifest"
            )
        if child_schedule.get("ablationId") != payload["ablationId"]:
            errors.append(f"treatment {name}: schedule ablation id mismatch")
    expected_status = overall_status([{"status": status} for status in statuses])
    if payload["status"] != expected_status:
        errors.append("ablation status does not match treatment runs")
    expected_content = sha256_json(
        {key: value for key, value in payload.items() if key != "contentSha256"}
    )
    if payload["contentSha256"] != expected_content:
        errors.append("ablation contentSha256 does not match the manifest")
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
    if records and all("verificationExecuted" in record for record in records):
        metrics["verificationExecutionPercent"] = _percent(
            sum(bool(record["verificationExecuted"]) for record in records),
            len(records),
        )
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
    base = bool(
        payload["status"] == "COMPLETED"
        and payload["suite"]["fullSuite"]
        and payload["adapter"]["description"]["evidenceKind"] == "REAL_HOST"
        and not payload["source"]["dirty"]
        and payload["source"]["stableThroughoutRun"]
    )
    if payload.get("schema") != "review-craft.eval-run.v4":
        return base
    return bool(
        base
        and payload.get("metrics", {}).get("usage", {}).get("availability") == "COMPLETE"
        and _verification_treatment_valid(payload)
    )


def _verification_treatment_valid(payload: dict[str, Any]) -> bool:
    treatment = payload.get("treatment")
    records = payload.get("cases", [])
    if treatment == "REVIEW_CRAFT_EVIDENCE_LOOP":
        return bool(
            records
            and all(
                record.get("verificationExecuted") is True
                and record.get("verificationExitCode") == 0
                for record in records
            )
        )
    non_evidence_treatments = set(ABLATION_TREATMENTS) | set(
        LEGACY_ABLATION_TREATMENTS
    )
    non_evidence_treatments.remove("REVIEW_CRAFT_EVIDENCE_LOOP")
    if treatment in non_evidence_treatments:
        return all(
            record.get("verificationExecuted") is False
            and record.get("verificationExitCode") is None
            for record in records
        )
    return True


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
    if payload["schema"] == "review-craft.eval-run.v4":
        check_artifact(
            payload["ablation"]["scheduleArtifact"],
            payload["ablation"]["scheduleSha256"],
            "ablation schedule",
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
        if payload["schema"] == "review-craft.eval-run.v4":
            trace_path = check_artifact(
                record["toolTraceArtifact"],
                record["toolTraceSha256"],
                f"case {case_id} tool trace",
            )
            if trace_path is not None:
                try:
                    trace = read_json(trace_path)
                except (OSError, json.JSONDecodeError) as error:
                    errors.append(f"case {case_id} tool trace: {error}")
                else:
                    errors.extend(
                        f"case {case_id} tool trace:{error}"
                        for error in schema_errors(trace, TOOL_TRACE_SCHEMA)
                    )
                    matched = [
                        item
                        for item in trace.get("items", [])
                        if item.get("type") == "commandExecution"
                        and f"--case {case_id}" in item.get("command", "")
                    ]
                    expected_executed = bool(matched)
                    expected_exit = matched[-1].get("exitCode") if matched else None
                    if record["verificationExecuted"] != expected_executed:
                        errors.append(
                            f"case {case_id}: verificationExecuted does not match tool trace"
                        )
                    if record["verificationExitCode"] != expected_exit:
                        errors.append(
                            f"case {case_id}: verificationExitCode does not match tool trace"
                        )
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
            errors.extend(f"suite:{error}" for error in validate_eval_suite(suite))
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


def validate_ablation_comparison(payload: dict[str, Any]) -> list[str]:
    try:
        version, protocol = ablation_protocol(payload.get("schema", ""))
    except EvalError as error:
        return [str(error)]
    errors = validate_content_bound_payload(
        payload,
        ablation_schema("comparison", version),
        label="ablation comparison",
    )
    if errors:
        return errors
    arms = payload["arms"]
    treatments = [arm["treatment"] for arm in arms]
    if treatments != list(protocol["treatments"]):
        errors.append("ablation comparison:arms must use canonical protocol order")
    expected_deltas = list(protocol["deltas"])
    actual_deltas = [
        (row["id"], row["from"], row["to"]) for row in payload["deltas"]
    ]
    if actual_deltas != expected_deltas:
        errors.append("ablation comparison:deltas must use canonical protocol order")
    adjudication_hashes = {arm["adjudicationContentSha256"] for arm in arms}
    if len(adjudication_hashes) != 1:
        errors.append("ablation comparison:all arms must bind one adjudication result")
    expected_eligible = bool(
        all(arm["goldenEligible"] for arm in arms)
        and all(
            arm["semanticMetrics"]["semanticEvidenceValidation"] == "ADJUDICATED"
            for arm in arms
        )
    )
    if payload["comparativeEligible"] != expected_eligible:
        errors.append(
            "ablation comparison:comparativeEligible does not match arm and adjudication gates"
        )
    return errors


def _snapshot_sanitization_errors(value: Any, *, location: str = "<root>") -> list[str]:
    errors = []
    forbidden_keys = {
        "adapterCommand",
        "baseUrl",
        "command",
        "fixtureArtifact",
        "normalizedOutputArtifact",
        "output",
        "prompt",
        "promptArtifact",
        "promptTemplate",
        "rawOutput",
        "runDir",
        "stderr",
        "stderrArtifact",
        "stdout",
        "stdoutArtifact",
        "toolTrace",
        "toolTraceArtifact",
    }
    absolute_path_patterns = (
        "/Users/",
        "/home/",
        "/private/",
        "C:\\Users\\",
    )
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key in forbidden_keys:
                errors.append(f"ablation snapshot:{child_location} is forbidden")
            errors.extend(_snapshot_sanitization_errors(child, location=child_location))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(
                _snapshot_sanitization_errors(child, location=f"{location}.{index}")
            )
    elif isinstance(value, str) and any(
        marker in value for marker in absolute_path_patterns
    ):
        errors.append(f"ablation snapshot:{location} contains an absolute path")
    return errors


def validate_ablation_snapshot(payload: dict[str, Any]) -> list[str]:
    try:
        version, protocol = ablation_protocol(payload.get("schema", ""))
    except EvalError as error:
        return [str(error)]
    errors = validate_content_bound_payload(
        payload,
        ablation_schema("snapshot", version),
        label="ablation snapshot",
    )
    if errors:
        return errors
    errors.extend(_snapshot_sanitization_errors(payload))
    treatments = [arm["treatment"] for arm in payload["arms"]]
    if treatments != list(protocol["treatments"]):
        errors.append("ablation snapshot:arms must use canonical protocol order")
    if not all(arm["goldenEligible"] for arm in payload["arms"]):
        errors.append("ablation snapshot:all arms must be Golden-eligible")
    if not all(
        arm["semanticMetrics"]["semanticEvidenceValidation"] == "ADJUDICATED"
        for arm in payload["arms"]
    ):
        errors.append("ablation snapshot:all semantic metrics must be adjudicated")
    return errors


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


def _ablation_sample_id(run: dict[str, Any], record: dict[str, Any]) -> str:
    digest = sha256_json(
        {
            "runContentSha256": run["contentSha256"],
            "caseId": record["id"],
            "normalizedOutputSha256": record["normalizedOutputSha256"],
            "toolTraceSha256": record["toolTraceSha256"],
        }
    )
    return f"sample-{digest[:16]}"


def _ablation_context(
    ablation_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    errors = validate_ablation_run(ablation_dir)
    if errors:
        raise EvalError("ablation is invalid: " + "; ".join(errors))
    manifest = read_json(ablation_dir / "ablation.json")
    suite = read_json(safe_artifact(ablation_dir, manifest["suite"]["artifact"]))
    cases_by_id = {case["id"]: case for case in suite["cases"]}
    samples = {}
    for treatment_summary in manifest["treatments"]:
        treatment = treatment_summary["treatment"]
        run_dir = safe_artifact(ablation_dir, treatment_summary["runDir"])
        run = read_json(run_dir / "result.json")
        if run["status"] != "COMPLETED":
            raise EvalError(
                "ablation adjudication requires all child runs to be completed; "
                f"{treatment} is {run['status']}"
            )
        for record in run["cases"]:
            if record["status"] != "COMPLETED":
                raise EvalError(
                    "ablation adjudication requires completed cases; "
                    f"{treatment}/{record['id']} is {record['status']}"
                )
            sample_id = _ablation_sample_id(run, record)
            if sample_id in samples:
                raise EvalError("ablation blind sample id collision")
            samples[sample_id] = {
                "sampleId": sample_id,
                "treatment": treatment,
                "case": cases_by_id[record["id"]],
                "run": run,
                "record": record,
                "output": read_json(
                    safe_artifact(run_dir, record["normalizedOutputArtifact"])
                ),
                "toolTrace": read_json(safe_artifact(run_dir, record["toolTraceArtifact"])),
            }
    return manifest, suite, samples


def build_ablation_adjudication_template(
    ablation_dir: Path,
    *,
    kind: str,
    protocol: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest, _, samples = _ablation_context(ablation_dir)
    version, _ = ablation_protocol(manifest["schema"])
    bundle = {
        "schema": f"review-craft.eval-ablation-blind-bundle.{version}",
        "ablationId": manifest["ablationId"],
        "ablationContentSha256": manifest["contentSha256"],
        "samples": [
            {
                "sampleId": sample_id,
                "case": {
                    key: value
                    for key, value in context["case"].items()
                    if key != "fixture"
                },
                "output": context["output"],
                "toolTrace": context["toolTrace"],
            }
            for sample_id, context in sorted(samples.items())
        ],
        "contentSha256": "0" * 64,
    }
    bundle["contentSha256"] = sha256_json(
        {key: value for key, value in bundle.items() if key != "contentSha256"}
    )
    bundle_errors = validate_content_bound_payload(
        bundle,
        ablation_schema("blind-bundle", version),
        label="ablation blind bundle",
    )
    if bundle_errors:
        raise EvalError("generated blind bundle is invalid: " + "; ".join(bundle_errors))
    template = {
        "schema": f"review-craft.eval-ablation-adjudication.{version}",
        "ablationId": manifest["ablationId"],
        "ablationContentSha256": manifest["contentSha256"],
        "bundleContentSha256": bundle["contentSha256"],
        "adjudicator": {"kind": kind, "protocol": protocol},
        "samples": [
            {
                "sampleId": sample_id,
                "outcome": "UNRESOLVED",
                "decisionDisposition": "UNRESOLVED",
                "evidenceDisposition": "UNRESOLVED",
                "falsificationDisposition": "UNRESOLVED",
                "externalFeedbackDisposition": "UNRESOLVED",
                "rationale": "Pending blinded semantic adjudication.",
            }
            for sample_id in sorted(samples)
        ],
    }
    errors = schema_errors(template, ablation_schema("adjudication", version))
    if errors:
        raise EvalError("generated ablation adjudication template is invalid: " + "; ".join(errors))
    return bundle, template


def _ablation_semantic_metrics(
    entries: list[dict[str, Any]],
    contexts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    normalized_entries = [
        {
            "id": contexts[entry["sampleId"]]["case"]["id"],
            "outcome": entry["outcome"],
            "decisionDisposition": entry["decisionDisposition"],
        }
        for entry in entries
    ]
    cases_by_id = {
        context["case"]["id"]: context["case"] for context in contexts.values()
    }
    outputs = {
        context["case"]["id"]: context["output"] for context in contexts.values()
    }
    metrics = _semantic_metrics(
        entries=normalized_entries,
        cases_by_id=cases_by_id,
        outputs=outputs,
    )
    unresolved_entries = [
        entry
        for entry in entries
        if "UNRESOLVED"
        in {
            entry["outcome"],
            entry["decisionDisposition"],
            entry["evidenceDisposition"],
            entry["falsificationDisposition"],
            entry["externalFeedbackDisposition"],
        }
    ]
    unresolved = bool(unresolved_entries)
    applicable_falsification = [
        entry for entry in entries if entry["falsificationDisposition"] != "NOT_APPLICABLE"
    ]
    metrics.update(
        {
            "resolvedCases": len(entries) - len(unresolved_entries),
            "unresolvedCases": len(unresolved_entries),
            "semanticEvidenceAdequacyPercent": (
                None
                if unresolved
                else _percent(
                    sum(entry["evidenceDisposition"] == "DECISIVE" for entry in entries),
                    len(entries),
                )
            ),
            "semanticFalsificationAdequacyPercent": (
                None
                if unresolved
                else _percent(
                    sum(
                        entry["falsificationDisposition"] == "ADEQUATE"
                        for entry in applicable_falsification
                    ),
                    len(applicable_falsification),
                )
            ),
            "semanticDecisiveExternalFeedbackPercent": (
                None
                if unresolved
                else _percent(
                    sum(
                        entry["externalFeedbackDisposition"] == "DECISIVE"
                        for entry in entries
                    ),
                    len(entries),
                )
            ),
            "semanticEvidenceValidation": "PARTIAL" if unresolved else "ADJUDICATED",
        }
    )
    return metrics


def build_ablation_adjudication_result(
    ablation_dir: Path,
    bundle: dict[str, Any],
    adjudication: dict[str, Any],
) -> dict[str, Any]:
    try:
        bundle_version, _ = ablation_protocol(bundle.get("schema", ""))
        input_version, _ = ablation_protocol(adjudication.get("schema", ""))
    except EvalError as error:
        raise EvalError(str(error)) from error
    if bundle_version != input_version:
        raise EvalError("ablation bundle and adjudication schema versions do not match")
    bundle_errors = validate_content_bound_payload(
        bundle,
        ablation_schema("blind-bundle", bundle_version),
        label="ablation blind bundle",
    )
    if bundle_errors:
        raise EvalError("blind bundle is invalid: " + "; ".join(bundle_errors))
    input_errors = schema_errors(
        adjudication, ablation_schema("adjudication", input_version)
    )
    if input_errors:
        raise EvalError("ablation adjudication input is invalid: " + "; ".join(input_errors))
    manifest, _, contexts = _ablation_context(ablation_dir)
    manifest_version, _ = ablation_protocol(manifest["schema"])
    if manifest_version != bundle_version:
        raise EvalError("ablation evidence schema version does not match the run")
    if adjudication["ablationId"] != manifest["ablationId"]:
        raise EvalError("ablation adjudication id does not match the run")
    if adjudication["ablationContentSha256"] != manifest["contentSha256"]:
        raise EvalError("ablation adjudication hash does not match the run")
    if adjudication["bundleContentSha256"] != bundle["contentSha256"]:
        raise EvalError("ablation adjudication does not match the blind bundle")
    bundle_ids = [sample["sampleId"] for sample in bundle["samples"]]
    entry_ids = [entry["sampleId"] for entry in adjudication["samples"]]
    if len(entry_ids) != len(set(entry_ids)) or set(entry_ids) != set(bundle_ids):
        raise EvalError("ablation adjudication sample coverage mismatch")
    entries_by_id = {entry["sampleId"]: entry for entry in adjudication["samples"]}
    grouped = []
    for treatment_summary in manifest["treatments"]:
        treatment = treatment_summary["treatment"]
        treatment_contexts = {
            sample_id: context
            for sample_id, context in contexts.items()
            if context["treatment"] == treatment
        }
        entries = []
        for sample_id, context in sorted(
            treatment_contexts.items(), key=lambda pair: pair[1]["case"]["id"]
        ):
            entry = entries_by_id[sample_id]
            _validate_adjudication_entry(
                case=context["case"], output=context["output"], entry=entry
            )
            entries.append(
                {
                    "caseId": context["case"]["id"],
                    "sampleId": sample_id,
                    "normalizedOutputSha256": context["record"]["normalizedOutputSha256"],
                    "toolTraceSha256": context["record"]["toolTraceSha256"],
                    **{key: value for key, value in entry.items() if key != "sampleId"},
                }
            )
        grouped.append(
            {
                "treatment": treatment,
                "runId": treatment_summary["runId"],
                "runContentSha256": treatment_summary["runContentSha256"],
                "cases": entries,
                "metrics": _ablation_semantic_metrics(
                    [entries_by_id[sample_id] for sample_id in treatment_contexts],
                    treatment_contexts,
                ),
            }
        )
    result = {
        "schema": f"review-craft.eval-ablation-adjudication-result.{manifest_version}",
        "ablation": {
            "id": manifest["ablationId"],
            "contentSha256": manifest["contentSha256"],
        },
        "adjudicator": adjudication["adjudicator"],
        "bundleContentSha256": bundle["contentSha256"],
        "inputContentSha256": sha256_json(adjudication),
        "treatments": grouped,
        "contentSha256": "0" * 64,
    }
    result["contentSha256"] = sha256_json(
        {key: value for key, value in result.items() if key != "contentSha256"}
    )
    errors = validate_content_bound_payload(
        result,
        ablation_schema("adjudication-result", manifest_version),
        label="ablation adjudication result",
    )
    if errors:
        raise EvalError("generated ablation adjudication is invalid: " + "; ".join(errors))
    return result


def validate_ablation_adjudication_result(
    ablation_dir: Path,
    bundle: dict[str, Any],
    payload: dict[str, Any],
) -> list[str]:
    try:
        version, _ = ablation_protocol(payload.get("schema", ""))
    except EvalError as error:
        return [str(error)]
    errors = validate_content_bound_payload(
        payload,
        ablation_schema("adjudication-result", version),
        label="ablation adjudication result",
    )
    if errors:
        return errors
    samples = []
    for treatment in payload["treatments"]:
        for case in treatment["cases"]:
            samples.append(
                {
                    "sampleId": case["sampleId"],
                    "outcome": case["outcome"],
                    "decisionDisposition": case["decisionDisposition"],
                    "evidenceDisposition": case["evidenceDisposition"],
                    "falsificationDisposition": case["falsificationDisposition"],
                    "externalFeedbackDisposition": case["externalFeedbackDisposition"],
                    "rationale": case["rationale"],
                }
            )
    adjudication = {
        "schema": f"review-craft.eval-ablation-adjudication.{version}",
        "ablationId": payload["ablation"]["id"],
        "ablationContentSha256": payload["ablation"]["contentSha256"],
        "bundleContentSha256": payload["bundleContentSha256"],
        "adjudicator": payload["adjudicator"],
        "samples": sorted(samples, key=lambda entry: entry["sampleId"]),
    }
    try:
        expected = build_ablation_adjudication_result(ablation_dir, bundle, adjudication)
    except (EvalError, KeyError, TypeError, OSError, json.JSONDecodeError) as error:
        return [str(error)]
    if payload != expected:
        errors.append("ablation adjudication result does not match the bound evidence")
    return errors
