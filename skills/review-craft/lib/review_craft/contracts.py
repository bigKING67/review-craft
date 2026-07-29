from __future__ import annotations

import re
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any

from .constants import (
    ARTIFACT_PATHS,
    DECISIONS,
    EVIDENCE_LEVELS,
    FINAL_COVERAGE_DISPOSITIONS,
    PERFORMANCE_CLASSES,
    PRIORITIES,
    REMEDIATION_PHASES,
    SCHEMA_VERSION,
    SCORE_DIMENSIONS,
    SEVERITIES,
    VALIDATION_STATUSES,
)
from .jsonio import read_json, read_jsonl, sha256_json
from .repository import (
    fingerprint_inventory,
    inventory,
    tracked_fingerprint,
)
from .schema_validation import validate_instance

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"
DOCUMENT_SCHEMAS = {
    "manifest": "review-manifest.schema.json",
    "qualityModel": "quality-model.schema.json",
    "coverage": "coverage.schema.json",
    "findings": "findings.schema.json",
    "decisions": "decisions.schema.json",
    "scorecard": "scorecard.schema.json",
    "remediationPlan": "remediation-plan.schema.json",
}


class ContractError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


@cache
def _schema(name: str) -> dict[str, Any]:
    value = read_json(SCHEMA_ROOT / name)
    if not isinstance(value, dict):
        raise ValueError(f"schema {name}: expected an object")
    return value


def _non_empty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any, *, allow_empty: bool = False) -> bool:
    return (
        isinstance(value, list)
        and (allow_empty or bool(value))
        and all(_non_empty(item) for item in value)
    )


def _safe_relative(value: Any) -> bool:
    if not _non_empty(value) or "\0" in value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and not any(
        ":" in part for part in path.parts
    )


def _artifact(run_dir: Path, key: str) -> Path:
    return run_dir / ARTIFACT_PATHS[key]


def load_run(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve(strict=True)
    result = {
        "manifest": read_json(run_dir / "review-manifest.json"),
        "qualityModel": read_json(_artifact(run_dir, "qualityModel")),
        "coverage": read_json(_artifact(run_dir, "coverage")),
        "candidates": read_jsonl(_artifact(run_dir, "candidateLedger")),
        "findings": read_json(_artifact(run_dir, "findings")),
        "decisions": read_json(_artifact(run_dir, "decisions")),
        "scorecard": read_json(_artifact(run_dir, "scorecard")),
        "remediationPlan": read_json(_artifact(run_dir, "remediationPlan")),
        "commands": read_jsonl(_artifact(run_dir, "commands")),
        "runState": read_json(run_dir / "run-state.json"),
    }
    return result


def _validate_document_header(name: str, document: Any, errors: list[str]) -> None:
    if not isinstance(document, dict):
        errors.append(f"{name}: expected a JSON object")
        return
    if document.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"{name}.schemaVersion: expected {SCHEMA_VERSION}")


def _validate_quality_model(model: dict[str, Any], errors: list[str], final: bool) -> None:
    for field in ("purpose", "audience"):
        if final and not _non_empty(model.get(field)):
            errors.append(f"quality-model.{field}: required for finalization")
    for field in (
        "criticalPaths",
        "invariants",
        "nonGoals",
        "compatibility",
        "performanceBudgets",
        "reliabilityRequirements",
        "authoritySources",
        "assumptions",
        "unknowns",
    ):
        value = model.get(field)
        if not isinstance(value, list) or not all(_non_empty(item) for item in value):
            errors.append(f"quality-model.{field}: expected an array of strings")
        elif final and field in {"criticalPaths", "invariants", "authoritySources"} and not value:
            errors.append(f"quality-model.{field}: must not be empty for finalization")


def _validate_coverage(coverage: dict[str, Any], errors: list[str], final: bool) -> set[str]:
    files = coverage.get("files")
    if not isinstance(files, list):
        errors.append("coverage.files: expected an array")
        return set()
    paths: set[str] = set()
    for index, row in enumerate(files):
        prefix = f"coverage.files[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{prefix}: expected an object")
            continue
        path = row.get("path")
        if not _safe_relative(path):
            errors.append(f"{prefix}.path: expected a safe repository-relative POSIX path")
        elif path in paths:
            errors.append(f"{prefix}.path: duplicate {path}")
        else:
            paths.add(path)
        disposition = row.get("disposition")
        if final and disposition not in FINAL_COVERAGE_DISPOSITIONS:
            errors.append(f"{prefix}.disposition: unresolved disposition {disposition!r}")
        if disposition == "COVERED_BY_PARENT" and not _safe_relative(row.get("coveredBy")):
            errors.append(f"{prefix}.coveredBy: required for COVERED_BY_PARENT")
        if disposition in {"DEFERRED", "UNREADABLE", "OUT_OF_SCOPE"} and not _non_empty(
            row.get("reason")
        ):
            errors.append(f"{prefix}.reason: required for {disposition}")
        evidence = row.get("evidenceRefs", [])
        if not isinstance(evidence, list) or not all(_non_empty(item) for item in evidence):
            errors.append(f"{prefix}.evidenceRefs: expected an array of strings")
    summary = coverage.get("summary")
    declared = summary.get("total") if isinstance(summary, dict) else None
    if declared != len(files):
        errors.append(f"coverage.summary.total: expected {len(files)}, got {declared!r}")
    if isinstance(summary, dict):
        reviewed = sum(
            row.get("disposition") in {"REVIEWED", "COVERED_BY_PARENT"}
            for row in files
            if isinstance(row, dict)
        )
        deferred = sum(
            row.get("disposition") in {"PENDING", "DEFERRED", "UNREADABLE"}
            for row in files
            if isinstance(row, dict)
        )
        if summary.get("reviewed") != reviewed:
            errors.append(f"coverage.summary.reviewed: expected {reviewed}")
        if summary.get("deferred") != deferred:
            errors.append(f"coverage.summary.deferred: expected {deferred}")
    return paths


def _validate_location(
    location: Any, prefix: str, coverage_paths: set[str], errors: list[str]
) -> None:
    if not isinstance(location, dict):
        errors.append(f"{prefix}: expected an object")
        return
    path = location.get("path")
    if not _safe_relative(path):
        errors.append(f"{prefix}.path: expected a safe relative path")
    elif path not in coverage_paths:
        errors.append(f"{prefix}.path: {path} is not in coverage")
    start = location.get("lineStart")
    end = location.get("lineEnd", start)
    if not isinstance(start, int) or isinstance(start, bool) or start < 1:
        errors.append(f"{prefix}.lineStart: expected a positive integer")
    if (
        not isinstance(end, int)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or end < start
    ):
        errors.append(f"{prefix}.lineEnd: expected an integer >= lineStart")


def _validate_candidates(
    candidates: list[dict[str, Any]], coverage_paths: set[str], errors: list[str], final: bool
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(candidates):
        prefix = f"candidate-ledger[{index}]"
        candidate_id = row.get("id")
        if not _non_empty(candidate_id) or not re.fullmatch(r"RC-[A-Z]+-[0-9]{3,}", candidate_id):
            errors.append(f"{prefix}.id: expected RC-CATEGORY-NNN")
        elif candidate_id in result:
            errors.append(f"{prefix}.id: duplicate {candidate_id}")
        else:
            result[candidate_id] = row
        for field in ("category", "type", "title", "confidence"):
            if not _non_empty(row.get(field)):
                errors.append(f"{prefix}.{field}: required")
        locations = row.get("locations")
        if not isinstance(locations, list) or not locations:
            errors.append(f"{prefix}.locations: expected a non-empty array")
        else:
            for location_index, location in enumerate(locations):
                _validate_location(
                    location, f"{prefix}.locations[{location_index}]", coverage_paths, errors
                )
        validation = row.get("validation")
        if not isinstance(validation, dict):
            errors.append(f"{prefix}.validation: expected an object")
            continue
        status = validation.get("status")
        if status not in VALIDATION_STATUSES:
            errors.append(f"{prefix}.validation.status: unsupported {status!r}")
        if final and status == "PENDING":
            errors.append(f"{prefix}.validation.status: unresolved candidate")
        if status not in {"PENDING", "BLOCKED"} and not _non_empty(validation.get("method")):
            errors.append(f"{prefix}.validation.method: required for {status}")
    return result


def _validate_findings(
    document: dict[str, Any],
    candidates: dict[str, dict[str, Any]],
    coverage_paths: set[str],
    errors: list[str],
) -> dict[str, dict[str, Any]]:
    rows = document.get("findings")
    if not isinstance(rows, list):
        errors.append("findings.findings: expected an array")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        prefix = f"findings.findings[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{prefix}: expected an object")
            continue
        finding_id = row.get("id")
        if not _non_empty(finding_id) or finding_id in result:
            errors.append(f"{prefix}.id: expected a unique string")
        else:
            result[finding_id] = row
        candidate_id = row.get("candidateId")
        if candidate_id not in candidates:
            errors.append(f"{prefix}.candidateId: unknown candidate {candidate_id!r}")
        else:
            candidate = candidates[candidate_id]
            candidate_status = candidate.get("validation", {}).get("status")
            if candidate_status not in {"CONFIRMED", "LIKELY"}:
                errors.append(f"{prefix}.candidateId: candidate is not reportable")
            if row.get("validationStatus") != candidate_status:
                errors.append(f"{prefix}.validationStatus: must match the candidate")
            if row.get("category") != candidate.get("category"):
                errors.append(f"{prefix}.category: must match the candidate")
        for field in (
            "title",
            "category",
            "rootCause",
            "currentImpact",
            "longTermRisk",
            "recommendation",
            "modificationCost",
            "modificationRisk",
            "decisionId",
        ):
            if not _non_empty(row.get(field)):
                errors.append(f"{prefix}.{field}: required")
        if row.get("validationStatus") not in {"CONFIRMED", "LIKELY"}:
            errors.append(f"{prefix}.validationStatus: expected CONFIRMED or LIKELY")
        if row.get("severity") not in SEVERITIES:
            errors.append(f"{prefix}.severity: unsupported")
        if row.get("priority") not in PRIORITIES:
            errors.append(f"{prefix}.priority: unsupported")
        performance = row.get("performanceClass")
        if performance is not None and performance not in PERFORMANCE_CLASSES:
            errors.append(f"{prefix}.performanceClass: unsupported")
        if performance in {"LIKELY_HOT_PATH", "UNVERIFIED_SUSPICION"}:
            errors.append(f"{prefix}.performanceClass: unmeasured suspicion must not be a finding")
        locations = row.get("locations")
        if not isinstance(locations, list) or not locations:
            errors.append(f"{prefix}.locations: expected a non-empty array")
        else:
            for location_index, location in enumerate(locations):
                _validate_location(
                    location, f"{prefix}.locations[{location_index}]", coverage_paths, errors
                )
        if not _string_list(row.get("evidenceRefs")):
            errors.append(f"{prefix}.evidenceRefs: expected a non-empty string array")
        if not _string_list(row.get("verification")):
            errors.append(f"{prefix}.verification: expected a non-empty string array")
    return result


def _validate_decisions(
    document: dict[str, Any], findings: dict[str, dict[str, Any]], errors: list[str]
) -> dict[str, dict[str, Any]]:
    rows = document.get("decisions")
    if not isinstance(rows, list):
        errors.append("decisions.decisions: expected an array")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        prefix = f"decisions.decisions[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{prefix}: expected an object")
            continue
        decision_id = row.get("id")
        if not _non_empty(decision_id) or decision_id in result:
            errors.append(f"{prefix}.id: expected a unique string")
        else:
            result[decision_id] = row
        if row.get("decision") not in DECISIONS:
            errors.append(f"{prefix}.decision: unsupported")
        for field in ("subject", "rationale"):
            if not _non_empty(row.get(field)):
                errors.append(f"{prefix}.{field}: required")
        finding_refs = row.get("findingRefs", [])
        if not isinstance(finding_refs, list) or not all(item in findings for item in finding_refs):
            errors.append(f"{prefix}.findingRefs: contains an unknown finding")
        if not _string_list(row.get("verification")):
            errors.append(f"{prefix}.verification: expected a non-empty string array")
        if row.get("decision") in {"DELETE", "REWRITE"}:
            for field in ("migration", "rollback"):
                if not _non_empty(row.get(field)):
                    errors.append(f"{prefix}.{field}: required for {row.get('decision')}")
            if not _string_list(row.get("compatibilityRisks")):
                errors.append(
                    f"{prefix}.compatibilityRisks: required for {row.get('decision')}"
                )
            if not _string_list(row.get("alternatives")):
                errors.append(f"{prefix}.alternatives: required for {row.get('decision')}")
            if not finding_refs:
                errors.append(f"{prefix}.findingRefs: required for {row.get('decision')}")
    for finding_id, finding in findings.items():
        decision_id = finding.get("decisionId")
        if decision_id not in result:
            errors.append(f"findings {finding_id}: unknown decisionId {decision_id!r}")
        elif finding_id not in result[decision_id].get("findingRefs", []):
            errors.append(f"decisions {decision_id}: does not reference finding {finding_id}")
    return result


def _validate_scorecard(
    scorecard: dict[str, Any],
    findings: dict[str, dict[str, Any]],
    candidates: list[dict[str, Any]],
    coverage: dict[str, Any],
    errors: list[str],
    final: bool,
) -> None:
    dimensions = scorecard.get("dimensions")
    if not isinstance(dimensions, list):
        errors.append("scorecard.dimensions: expected an array")
        return
    by_id = {row.get("id"): row for row in dimensions if isinstance(row, dict)}
    expected_ids = {item[0] for item in SCORE_DIMENSIONS}
    if set(by_id) != expected_ids:
        errors.append("scorecard.dimensions: expected the canonical eight dimensions")
        return
    total = 0
    for identifier, _, maximum in SCORE_DIMENSIONS:
        row = by_id[identifier]
        if row.get("maximum") != maximum:
            errors.append(f"scorecard.{identifier}.maximum: expected {maximum}")
        awarded = row.get("awarded")
        if not isinstance(awarded, int) or isinstance(awarded, bool) or not 0 <= awarded <= maximum:
            errors.append(f"scorecard.{identifier}.awarded: expected 0..{maximum}")
            continue
        deductions = row.get("deductions")
        if not isinstance(deductions, list):
            errors.append(f"scorecard.{identifier}.deductions: expected an array")
            continue
        points = 0
        for index, deduction in enumerate(deductions):
            prefix = f"scorecard.{identifier}.deductions[{index}]"
            if not isinstance(deduction, dict):
                errors.append(f"{prefix}: expected an object")
                continue
            value = deduction.get("points")
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                errors.append(f"{prefix}.points: expected a positive integer")
            else:
                points += value
            if not _non_empty(deduction.get("reason")):
                errors.append(f"{prefix}.reason: required")
            if not _string_list(deduction.get("evidenceRefs")):
                errors.append(f"{prefix}.evidenceRefs: required")
        if maximum - awarded != points:
            errors.append(
                f"scorecard.{identifier}: deductions {points} do not equal {maximum - awarded}"
            )
        total += awarded
    if scorecard.get("total") != total:
        errors.append(f"scorecard.total: expected {total}, got {scorecard.get('total')!r}")
    level = scorecard.get("evidenceLevel")
    if level not in EVIDENCE_LEVELS:
        errors.append("scorecard.evidenceLevel: expected E0..E4")
        return
    unresolved = sum(
        1
        for row in candidates
        if row.get("validation", {}).get("status") in {"PENDING", "BLOCKED"}
    )
    if scorecard.get("unresolvedCandidates") != unresolved:
        errors.append(f"scorecard.unresolvedCandidates: expected {unresolved}")
    files = coverage.get("files", [])
    resolved = sum(1 for row in files if row.get("disposition") in FINAL_COVERAGE_DISPOSITIONS)
    coverage_percent = round(100 * resolved / len(files), 2) if files else 100.0
    if scorecard.get("coveragePercent") != coverage_percent:
        errors.append(f"scorecard.coveragePercent: expected {coverage_percent}")
    if final:
        if EVIDENCE_LEVELS[level] < 1:
            errors.append("scorecard.evidenceLevel: E1 is required for a numeric final score")
        if scorecard.get("status") == "final" and (coverage_percent != 100.0 or unresolved):
            errors.append("scorecard.status: final requires closed coverage and candidates")
        if scorecard.get("status") == "final" and any(
            row.get("priority") in {"P0", "P1"}
            and row.get("validationStatus") != "CONFIRMED"
            for row in findings.values()
        ):
            errors.append("scorecard.status: unresolved P0/P1 finding prevents final status")
        if total >= 95 and EVIDENCE_LEVELS[level] < 3:
            errors.append("scorecard: scores >=95 require evidence level E3")
        if total >= 98 and EVIDENCE_LEVELS[level] < 4:
            errors.append("scorecard: scores >=98 require evidence level E4")


def _validate_remediation(plan: dict[str, Any], errors: list[str], final: bool) -> None:
    if final and plan.get("changeClass") not in {
        "LOCAL_OPTIMIZATION",
        "MODULE_REFACTOR",
        "ARCHITECTURE_ADJUSTMENT",
    }:
        errors.append("remediation-plan.changeClass: unsupported")
    target = plan.get("targetArchitecture")
    if not isinstance(target, dict):
        errors.append("remediation-plan.targetArchitecture: expected an object")
    else:
        for field in (
            "overview",
            "moduleBoundaries",
            "dependencyDirection",
            "coreDataFlow",
            "stateAndErrors",
            "directoryStructure",
            "testingStructure",
            "deliveryFlow",
        ):
            value = target.get(field)
            if final and not (_non_empty(value) or _string_list(value)):
                errors.append(f"remediation-plan.targetArchitecture.{field}: required")
    phases = plan.get("phases")
    if not isinstance(phases, list):
        errors.append("remediation-plan.phases: expected an array")
        return
    expected = [item[0] for item in REMEDIATION_PHASES]
    actual = [row.get("id") for row in phases if isinstance(row, dict)]
    if actual != expected:
        errors.append(f"remediation-plan.phases: expected {expected}")
    for index, row in enumerate(phases):
        if not isinstance(row, dict):
            continue
        prefix = f"remediation-plan.phases[{index}]"
        for field in (
            "modificationScope",
            "prerequisites",
            "expectedBenefits",
            "risks",
            "acceptanceCriteria",
        ):
            value = row.get(field)
            if not isinstance(value, list) or not all(_non_empty(item) for item in value):
                errors.append(f"{prefix}.{field}: expected an array of strings")
            elif final and not value:
                errors.append(f"{prefix}.{field}: must not be empty for finalization")


def validate_run(run_dir: Path, *, final: bool = True) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve(strict=True)
    data = load_run(run_dir)
    errors: list[str] = []
    for name, schema_name in DOCUMENT_SCHEMAS.items():
        errors.extend(
            f"{schema_name}: {error}"
            for error in validate_instance(data[name], _schema(schema_name))
        )
    for index, candidate in enumerate(data["candidates"]):
        errors.extend(
            f"candidate.schema.json[{index}]: {error}"
            for error in validate_instance(candidate, _schema("candidate.schema.json"))
        )
    for index, receipt in enumerate(data["commands"]):
        errors.extend(
            f"command-receipt.schema.json[{index}]: {error}"
            for error in validate_instance(receipt, _schema("command-receipt.schema.json"))
        )
    manifest_configuration = data["manifest"].get("configuration")
    errors.extend(
        f"config.schema.json: {error}"
        for error in validate_instance(manifest_configuration, _schema("config.schema.json"))
    )
    manifest = data["manifest"]
    target = manifest.get("target", {})
    coverage = data["coverage"]
    if manifest.get("configFingerprint") != sha256_json(manifest_configuration):
        errors.append("review-manifest.configFingerprint: does not match configuration")
    if target.get("sourceFingerprint") != coverage.get("inventoryFingerprint"):
        errors.append("review-manifest.target.sourceFingerprint: does not match coverage")
    identity_seed = {
        "remote": target.get("remote"),
        "revision": target.get("revision"),
        "branch": target.get("branch"),
        "sourceFingerprint": target.get("sourceFingerprint"),
    }
    if target.get("identity") != sha256_json(identity_seed):
        errors.append("review-manifest.target.identity: does not match target fields")
    run_state = data["runState"]
    if not isinstance(run_state, dict) or not _non_empty(run_state.get("targetRoot")):
        errors.append("run-state.targetRoot: required")
    else:
        try:
            target_root = Path(run_state["targetRoot"]).resolve(strict=True)
            records, _ = inventory(
                target_root,
                scopes=manifest_configuration["scope"],
                excludes=manifest_configuration["exclude"],
                generated=manifest_configuration["generated"],
                vendored=manifest_configuration["vendored"],
            )
            current_source = fingerprint_inventory(records)
            current_tracked = tracked_fingerprint(target_root)
        except (OSError, KeyError, TypeError, ValueError, RuntimeError) as error:
            errors.append(f"run-state.targetRoot: source verification failed: {error}")
        else:
            if current_source != target.get("sourceFingerprint"):
                errors.append("run-state.targetRoot: source fingerprint changed after preflight")
            if current_tracked != run_state.get("trackedFingerprint"):
                errors.append("run-state.targetRoot: tracked source changed after preflight")
    for receipt in data["commands"]:
        if not isinstance(receipt, dict):
            continue
        command_id = receipt.get("id")
        for field, suffix in (("stdoutArtifact", "stdout"), ("stderrArtifact", "stderr")):
            expected = f"evidence/commands/{command_id}.{suffix}"
            if receipt.get(field) != expected:
                errors.append(f"command receipt {command_id}: {field} must be {expected}")
            elif not (run_dir / expected).is_file():
                errors.append(f"command receipt {command_id}: missing {expected}")
    for name in (
        "manifest",
        "qualityModel",
        "coverage",
        "findings",
        "decisions",
        "scorecard",
        "remediationPlan",
    ):
        _validate_document_header(name, data[name], errors)
    if isinstance(manifest, dict):
        artifacts = manifest.get("artifacts")
        if artifacts != ARTIFACT_PATHS:
            errors.append("review-manifest.artifacts: canonical artifact map mismatch")
        if final and manifest.get("status") not in {"draft", "final"}:
            errors.append("review-manifest.status: expected draft or final")
    _validate_quality_model(data["qualityModel"], errors, final)
    coverage_paths = _validate_coverage(data["coverage"], errors, final)
    candidates = _validate_candidates(data["candidates"], coverage_paths, errors, final)
    findings = _validate_findings(
        data["findings"], candidates, coverage_paths, errors
    )
    _validate_decisions(data["decisions"], findings, errors)
    _validate_scorecard(
        data["scorecard"], findings, data["candidates"], data["coverage"], errors, final
    )
    _validate_remediation(data["remediationPlan"], errors, final)
    if errors:
        raise ContractError(errors)
    return data
