from __future__ import annotations

import re
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any

from .configuration import validate_config
from .constants import (
    ARTIFACT_PATHS,
    DECISIONS,
    EVIDENCE_LEVELS,
    FINAL_COVERAGE_DISPOSITIONS,
    LEGACY_ARTIFACT_PATHS,
    LEGACY_SCHEMA_VERSION,
    PERFORMANCE_CLASSES,
    PRIORITIES,
    PROFILES,
    REMEDIATION_PHASES,
    REVIEW_MODES,
    SCHEMA_VERSION,
    SCORE_DIMENSIONS,
    SEMANTIC_CLAIM_LEVELS,
    SEVERITIES,
    SUPPORTED_RUN_SCHEMA_VERSIONS,
    VALIDATION_STATUSES,
)
from .evidence import receipt_configuration_errors
from .evidence_registry import EVIDENCE_ID_PATTERN, registered_artifact_path
from .jsonio import read_json, read_jsonl, sha256_bytes, sha256_json
from .repository import (
    fingerprint_inventory,
    inspect_git,
    inventory_for_mode,
    worktree_fingerprint,
)
from .repository_analysis import build_dependency_map, build_module_map
from .schema_validation import validate_instance
from .semantic_evidence import receipt_identity_payload, receipt_semantic_errors

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"
DOCUMENT_SCHEMAS = {
    "manifest": "review-manifest.schema.json",
    "reviewScope": "review-scope.schema.json",
    "qualityModel": "quality-model.schema.json",
    "coverage": "coverage.schema.json",
    "moduleMap": "module-map.schema.json",
    "dependencyMap": "dependency-map.schema.json",
    "findings": "findings.schema.json",
    "decisions": "decisions.schema.json",
    "scorecard": "scorecard.schema.json",
    "remediationPlan": "remediation-plan.schema.json",
}
CURRENT_DOCUMENT_SCHEMAS = {
    **DOCUMENT_SCHEMAS,
    "evidenceRegistry": "evidence-registry.schema.json",
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


def _artifact_paths(schema_version: str) -> dict[str, str]:
    if schema_version == LEGACY_SCHEMA_VERSION:
        return LEGACY_ARTIFACT_PATHS
    if schema_version == SCHEMA_VERSION:
        return ARTIFACT_PATHS
    raise ContractError([f"review-manifest.schemaVersion: unsupported {schema_version!r}"])


def _document_schemas(schema_version: str) -> dict[str, str]:
    if schema_version == LEGACY_SCHEMA_VERSION:
        return DOCUMENT_SCHEMAS
    if schema_version == SCHEMA_VERSION:
        return CURRENT_DOCUMENT_SCHEMAS
    raise ContractError([f"review-manifest.schemaVersion: unsupported {schema_version!r}"])


def _artifact(run_dir: Path, artifact_paths: dict[str, str], key: str) -> Path:
    return _run_file(run_dir, artifact_paths[key])


def _run_file(run_dir: Path, relative: str) -> Path:
    path = run_dir / relative
    if path.is_symlink():
        raise ContractError([f"run artifact must not be a symlink: {relative}"])
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(run_dir)
    except (OSError, ValueError) as error:
        raise ContractError([f"invalid run artifact {relative}: {error}"]) from error
    if not resolved.is_file():
        raise ContractError([f"run artifact must be a file: {relative}"])
    return resolved


def load_run(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve(strict=True)
    manifest = read_json(_run_file(run_dir, "review-manifest.json"))
    if not isinstance(manifest, dict):
        raise ContractError(["manifest: expected a JSON object"])
    schema_version = manifest.get("schemaVersion")
    if schema_version not in SUPPORTED_RUN_SCHEMA_VERSIONS:
        raise ContractError(
            [f"review-manifest.schemaVersion: unsupported {schema_version!r}"]
        )
    artifact_paths = _artifact_paths(schema_version)
    result = {
        "manifest": manifest,
        "reviewScope": read_json(_artifact(run_dir, artifact_paths, "reviewScope")),
        "qualityModel": read_json(_artifact(run_dir, artifact_paths, "qualityModel")),
        "coverage": read_json(_artifact(run_dir, artifact_paths, "coverage")),
        "moduleMap": read_json(_artifact(run_dir, artifact_paths, "moduleMap")),
        "dependencyMap": read_json(_artifact(run_dir, artifact_paths, "dependencyMap")),
        "candidates": read_jsonl(_artifact(run_dir, artifact_paths, "candidateLedger")),
        "findings": read_json(_artifact(run_dir, artifact_paths, "findings")),
        "decisions": read_json(_artifact(run_dir, artifact_paths, "decisions")),
        "scorecard": read_json(_artifact(run_dir, artifact_paths, "scorecard")),
        "remediationPlan": read_json(
            _artifact(run_dir, artifact_paths, "remediationPlan")
        ),
        "commands": read_jsonl(_artifact(run_dir, artifact_paths, "commands")),
        "evidenceRegistry": (
            read_json(_artifact(run_dir, artifact_paths, "evidenceRegistry"))
            if schema_version == SCHEMA_VERSION
            else None
        ),
        "runState": read_json(_run_file(run_dir, "run-state.json")),
    }
    return result


def _validate_document_header(
    name: str,
    document: Any,
    schema_version: str,
    errors: list[str],
) -> None:
    if not isinstance(document, dict):
        errors.append(f"{name}: expected a JSON object")
        return
    if document.get("schemaVersion") != schema_version:
        errors.append(f"{name}.schemaVersion: expected {schema_version}")


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


def _validate_review_scope(
    review_scope: dict[str, Any],
    configuration: dict[str, Any],
    coverage_paths: set[str],
    errors: list[str],
) -> None:
    mode = review_scope.get("mode")
    if mode not in REVIEW_MODES or mode != configuration.get("mode"):
        errors.append("review-scope.mode: must match the canonical configuration")
    focus_dimensions = configuration.get("focusDimensions")
    diff_base = configuration.get("diffBase")
    if mode == "review" and focus_dimensions:
        errors.append("review-scope.mode: review mode cannot declare focus dimensions")
    if mode == "focus" and not focus_dimensions:
        errors.append("review-scope.mode: focus mode requires focus dimensions")
    if mode == "diff" and not _non_empty(diff_base):
        errors.append("review-scope.mode: diff mode requires an immutable diff base")
    if mode != "diff" and diff_base is not None:
        errors.append("review-scope.mode: diffBase is only valid in diff mode")
    dimensions = review_scope.get("dimensions")
    canonical_dimensions = [row[0] for row in SCORE_DIMENSIONS]
    if not isinstance(dimensions, list) or not dimensions or len(dimensions) != len(
        set(dimensions)
    ):
        errors.append("review-scope.dimensions: expected unique canonical dimensions")
    elif any(item not in canonical_dimensions for item in dimensions):
        errors.append("review-scope.dimensions: contains an unsupported dimension")
    expected_dimensions = configuration.get("focusDimensions") or canonical_dimensions
    if dimensions != expected_dimensions:
        errors.append("review-scope.dimensions: must match configuration focusDimensions")
    profile = review_scope.get("profile")
    if not isinstance(profile, dict):
        errors.append("review-scope.profile: expected an object")
    else:
        if profile.get("requested") != configuration.get("profile"):
            errors.append("review-scope.profile.requested: must match configuration profile")
        if profile.get("resolved") not in PROFILES - {"auto"}:
            errors.append("review-scope.profile.resolved: unsupported profile")
    diff = review_scope.get("diff")
    if mode != "diff":
        if diff is not None:
            errors.append("review-scope.diff: must be null outside diff mode")
        return
    if not isinstance(diff, dict):
        errors.append("review-scope.diff: required in diff mode")
        return
    if diff.get("baseRevision") != configuration.get("diffBase"):
        errors.append("review-scope.diff.baseRevision: must match configuration diffBase")
    changes = diff.get("changes")
    if not isinstance(changes, list):
        errors.append("review-scope.diff.changes: expected an array")
        return
    seen: set[str] = set()
    for index, change in enumerate(changes):
        prefix = f"review-scope.diff.changes[{index}]"
        if not isinstance(change, dict):
            errors.append(f"{prefix}: expected an object")
            continue
        path = change.get("path")
        if not _safe_relative(path) or path in seen:
            errors.append(f"{prefix}.path: expected a unique safe relative path")
            continue
        seen.add(path)
        if change.get("inScope") is True and path not in coverage_paths:
            errors.append(f"{prefix}.inScope: path is missing from coverage")
        if change.get("inScope") is False and path in coverage_paths:
            errors.append(f"{prefix}.inScope: excluded path appears in coverage")


def _validate_repository_maps(
    module_map: dict[str, Any],
    dependency_map: dict[str, Any],
    coverage_paths: set[str],
    errors: list[str],
) -> None:
    modules = module_map.get("modules")
    module_ids: set[str] = set()
    declared_files = 0
    if not isinstance(modules, list):
        errors.append("module-map.modules: expected an array")
    else:
        for index, module in enumerate(modules):
            prefix = f"module-map.modules[{index}]"
            if not isinstance(module, dict):
                errors.append(f"{prefix}: expected an object")
                continue
            identifier = module.get("id")
            if not _non_empty(identifier) or identifier in module_ids:
                errors.append(f"{prefix}.id: expected a unique module ID")
            else:
                module_ids.add(identifier)
            file_count = module.get("fileCount")
            if isinstance(file_count, int) and not isinstance(file_count, bool):
                declared_files += file_count
            for entry in module.get("entryPoints", []):
                if entry not in coverage_paths:
                    errors.append(f"{prefix}.entryPoints: {entry!r} is not in coverage")
        if declared_files != len(coverage_paths):
            errors.append(
                "module-map.modules: file counts total "
                f"{declared_files}, expected {len(coverage_paths)}"
            )
    for index, edge in enumerate(dependency_map.get("edges", [])):
        if not isinstance(edge, dict):
            continue
        for field in ("from", "to"):
            if edge.get(field) not in coverage_paths:
                errors.append(
                    f"dependency-map.edges[{index}].{field}: path is not in coverage"
                )
    for index, skipped in enumerate(dependency_map.get("filesSkipped", [])):
        if isinstance(skipped, dict) and skipped.get("path") not in coverage_paths:
            errors.append(f"dependency-map.filesSkipped[{index}].path: path is not in coverage")
    for index, edge in enumerate(dependency_map.get("moduleEdges", [])):
        if not isinstance(edge, dict):
            continue
        for field in ("from", "to"):
            if edge.get(field) not in module_ids:
                errors.append(
                    f"dependency-map.moduleEdges[{index}].{field}: unknown module"
                )


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
    commands: list[dict[str, Any]],
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
    accounted = sum(
        1 for row in files if row.get("disposition") in FINAL_COVERAGE_DISPOSITIONS
    )
    reviewed = sum(
        1
        for row in files
        if row.get("disposition") in {"REVIEWED", "COVERED_BY_PARENT"}
    )
    accounted_percent = round(100 * accounted / len(files), 2) if files else 100.0
    reviewed_percent = round(100 * reviewed / len(files), 2) if files else 100.0
    has_explicit_coverage_metrics = (
        "accountedPercent" in scorecard or "reviewedPercent" in scorecard
    )
    if has_explicit_coverage_metrics:
        if scorecard.get("accountedPercent") != accounted_percent:
            errors.append(f"scorecard.accountedPercent: expected {accounted_percent}")
        if scorecard.get("reviewedPercent") != reviewed_percent:
            errors.append(f"scorecard.reviewedPercent: expected {reviewed_percent}")
        if scorecard.get("coveragePercent") != reviewed_percent:
            errors.append(f"scorecard.coveragePercent: expected {reviewed_percent}")
    elif scorecard.get("coveragePercent") != accounted_percent:
        # Historical run.v3 artifacts used coveragePercent for accounting closure.
        errors.append(f"scorecard.coveragePercent: expected {accounted_percent}")
    successful_receipts = [
        row
        for row in commands
        if isinstance(row, dict)
        and row.get("exitCode") == 0
        and row.get("timedOut") is False
        and row.get("repositoryMutationDetected") is False
        and row.get("semanticEvidenceValid") is not False
    ]
    if EVIDENCE_LEVELS[level] >= EVIDENCE_LEVELS["E2"] and not successful_receipts:
        errors.append(
            "scorecard.evidenceLevel: E2+ requires a successful canonical command receipt"
        )
    semantic_receipts_present = any(
        isinstance(row, dict) and "semanticEvidenceValid" in row for row in commands
    )
    if semantic_receipts_present and EVIDENCE_LEVELS[level] >= EVIDENCE_LEVELS["E3"]:
        verified_levels = [
            EVIDENCE_LEVELS[SEMANTIC_CLAIM_LEVELS[claim["kind"]]]
            for row in successful_receipts
            for claim in row.get("evidenceClaims", [])
            if claim.get("status") == "VERIFIED" and claim.get("kind") in SEMANTIC_CLAIM_LEVELS
        ]
        if not verified_levels or max(verified_levels) < EVIDENCE_LEVELS[level]:
            errors.append(
                f"scorecard.evidenceLevel: {level} requires a matching verified semantic claim"
            )
    if final:
        if EVIDENCE_LEVELS[level] < 1:
            errors.append("scorecard.evidenceLevel: E1 is required for a numeric final score")
        review_gaps = sum(
            row.get("disposition") in {"PENDING", "DEFERRED", "UNREADABLE", "OUT_OF_SCOPE"}
            for row in files
            if isinstance(row, dict)
        )
        if scorecard.get("status") == "final" and (
            accounted_percent != 100.0 or unresolved
        ):
            errors.append("scorecard.status: final requires closed coverage and candidates")
        if (
            has_explicit_coverage_metrics
            and scorecard.get("status") == "final"
            and review_gaps
        ):
            errors.append(
                "scorecard.status: final requires no pending, deferred, unreadable, "
                "or out-of-scope review gaps"
            )
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


def _append_evidence_refs(
    result: list[tuple[str, str]],
    value: Any,
    prefix: str,
) -> None:
    if not isinstance(value, list):
        return
    for index, reference in enumerate(value):
        if isinstance(reference, str):
            result.append((f"{prefix}[{index}]", reference))


def _evidence_references(data: dict[str, Any]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    coverage = data.get("coverage")
    if isinstance(coverage, dict):
        for index, row in enumerate(coverage.get("files", [])):
            if isinstance(row, dict):
                _append_evidence_refs(
                    result,
                    row.get("evidenceRefs"),
                    f"coverage.files[{index}].evidenceRefs",
                )
    for index, candidate in enumerate(data.get("candidates", [])):
        if not isinstance(candidate, dict):
            continue
        for evidence_index, evidence in enumerate(candidate.get("evidence", [])):
            if isinstance(evidence, dict) and isinstance(evidence.get("ref"), str):
                result.append(
                    (
                        f"candidate-ledger[{index}].evidence[{evidence_index}].ref",
                        evidence["ref"],
                    )
                )
        validation = candidate.get("validation")
        if isinstance(validation, dict):
            _append_evidence_refs(
                result,
                validation.get("evidenceRefs"),
                f"candidate-ledger[{index}].validation.evidenceRefs",
            )
    findings = data.get("findings")
    if isinstance(findings, dict):
        for index, finding in enumerate(findings.get("findings", [])):
            if isinstance(finding, dict):
                _append_evidence_refs(
                    result,
                    finding.get("evidenceRefs"),
                    f"findings.findings[{index}].evidenceRefs",
                )
    scorecard = data.get("scorecard")
    if isinstance(scorecard, dict):
        for dimension_index, dimension in enumerate(scorecard.get("dimensions", [])):
            if not isinstance(dimension, dict):
                continue
            for deduction_index, deduction in enumerate(dimension.get("deductions", [])):
                if isinstance(deduction, dict):
                    _append_evidence_refs(
                        result,
                        deduction.get("evidenceRefs"),
                        (
                            f"scorecard.dimensions[{dimension_index}].deductions"
                            f"[{deduction_index}].evidenceRefs"
                        ),
                    )
    return result


def _validate_evidence_registry(
    run_dir: Path,
    registry: Any,
    references: list[tuple[str, str]],
    errors: list[str],
) -> None:
    if not isinstance(registry, dict):
        errors.append("evidence-registry: expected a JSON object")
        return
    artifacts = registry.get("artifacts")
    if not isinstance(artifacts, list):
        errors.append("evidence-registry.artifacts: expected an array")
        return
    identifiers: set[str] = set()
    paths: set[str] = set()
    for index, entry in enumerate(artifacts):
        prefix = f"evidence-registry.artifacts[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix}: expected an object")
            continue
        identifier = entry.get("id")
        if not isinstance(identifier, str) or EVIDENCE_ID_PATTERN.fullmatch(identifier) is None:
            errors.append(f"{prefix}.id: expected a canonical registered evidence ID")
        elif identifier in identifiers:
            errors.append(f"{prefix}.id: duplicate {identifier!r}")
        else:
            identifiers.add(identifier)
        path = entry.get("path")
        if not _safe_relative(path):
            errors.append(f"{prefix}.path: expected a safe run-relative path")
            continue
        if path in paths:
            errors.append(f"{prefix}.path: duplicate {path!r}")
        else:
            paths.add(path)
        if isinstance(identifier, str):
            expected_path = registered_artifact_path(identifier)
            if path != expected_path:
                errors.append(f"{prefix}.path: expected {expected_path}")
                continue
        try:
            artifact = _run_file(run_dir, path)
        except ContractError as error:
            errors.extend(f"{prefix}: {message}" for message in error.errors)
            continue
        content = artifact.read_bytes()
        if entry.get("sha256") != sha256_bytes(content):
            errors.append(f"{prefix}.sha256: does not match {path}")
        size_bytes = entry.get("sizeBytes")
        if (
            not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or size_bytes != len(content)
        ):
            errors.append(f"{prefix}.sizeBytes: does not match {path}")

    registered_root = run_dir / "evidence/registered"
    if registered_root.is_symlink():
        errors.append("evidence-registry.artifacts: registered root must not be a symlink")
    elif registered_root.exists():
        for artifact in sorted(registered_root.rglob("*")):
            if not (artifact.is_file() or artifact.is_symlink()):
                continue
            relative = artifact.relative_to(run_dir).as_posix()
            if relative not in paths:
                errors.append(
                    f"evidence-registry.artifacts: unregistered artifact path {relative}"
                )

    for prefix, reference in references:
        if not reference.startswith("artifact:"):
            continue
        identifier = reference.removeprefix("artifact:")
        if EVIDENCE_ID_PATTERN.fullmatch(identifier) is None:
            errors.append(f"{prefix}: expected artifact:<registered-id>")
        elif identifier not in identifiers:
            errors.append(f"{prefix}: unknown registered evidence ID {identifier!r}")


def validate_run(run_dir: Path, *, final: bool = True) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve(strict=True)
    data = load_run(run_dir)
    errors: list[str] = []
    manifest = data["manifest"]
    schema_version = manifest.get("schemaVersion")
    document_schemas = _document_schemas(schema_version)
    artifact_paths = _artifact_paths(schema_version)
    for name, schema_name in document_schemas.items():
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
    for name in document_schemas:
        _validate_document_header(name, data[name], schema_version, errors)
    if any(not isinstance(data[name], dict) for name in document_schemas):
        raise ContractError(errors)
    manifest_configuration = data["manifest"].get("configuration")
    errors.extend(
        f"config.schema.json: {error}"
        for error in validate_instance(manifest_configuration, _schema("config.schema.json"))
    )
    if not isinstance(manifest_configuration, dict):
        raise ContractError(errors)
    try:
        validate_config(manifest_configuration)
    except (KeyError, TypeError, ValueError) as error:
        errors.append(f"review-manifest.configuration: {error}")
    target = manifest.get("target")
    if not isinstance(target, dict):
        target = {}
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
    rebuilt_module_map: dict[str, Any] | None = None
    rebuilt_dependency_map: dict[str, Any] | None = None
    run_state = data["runState"]
    if not isinstance(run_state, dict) or not _non_empty(run_state.get("targetRoot")):
        errors.append("run-state.targetRoot: required")
    else:
        try:
            target_root = Path(run_state["targetRoot"]).resolve(strict=True)
            records, _, current_diff = inventory_for_mode(
                target_root,
                mode=manifest_configuration["mode"],
                scopes=manifest_configuration["scope"],
                excludes=manifest_configuration["exclude"],
                generated=manifest_configuration["generated"],
                vendored=manifest_configuration["vendored"],
                diff_base=manifest_configuration["diffBase"],
            )
            current_source = fingerprint_inventory(records)
            current_worktree = worktree_fingerprint(target_root, records=records)
            current_status = inspect_git(target_root).status
        except (OSError, KeyError, TypeError, ValueError, RuntimeError) as error:
            errors.append(f"run-state.targetRoot: source verification failed: {error}")
        else:
            rebuilt_module_map = build_module_map(records)
            rebuilt_dependency_map = build_dependency_map(target_root, records)
            rebuilt_module_map["schemaVersion"] = schema_version
            rebuilt_dependency_map["schemaVersion"] = schema_version
            if current_source != target.get("sourceFingerprint"):
                errors.append("run-state.targetRoot: source fingerprint changed after preflight")
            if manifest_configuration.get("mode") == "diff":
                stored_diff = data["reviewScope"].get("diff")
                if (
                    not isinstance(current_diff, dict)
                    or not isinstance(stored_diff, dict)
                    or current_diff.get("changes") != stored_diff.get("changes")
                ):
                    errors.append("run-state.targetRoot: diff scope changed after preflight")
            if current_worktree != run_state.get("worktreeFingerprint"):
                errors.append("run-state.targetRoot: worktree changed after preflight")
            current_status_fingerprint = sha256_bytes(
                current_status.encode("utf-8", errors="surrogateescape")
            )
            if current_status_fingerprint != run_state.get("statusFingerprint"):
                errors.append("run-state.targetRoot: Git status changed after preflight")
    if rebuilt_module_map is not None and data["moduleMap"] != rebuilt_module_map:
        errors.append("module-map: does not match the current inventory")
    if rebuilt_dependency_map is not None and data["dependencyMap"] != rebuilt_dependency_map:
        errors.append("dependency-map: does not match the current source projection")
    receipt_ids: set[str] = set()
    receipt_sequences: set[int] = set()
    receipt_artifacts: set[str] = set()
    for index, receipt in enumerate(data["commands"]):
        if not isinstance(receipt, dict):
            continue
        command_id = receipt.get("id")
        prefix = f"command receipt {command_id if isinstance(command_id, str) else index}"
        errors.extend(
            receipt_configuration_errors(
                receipt,
                manifest_configuration["commands"],
                prefix=prefix,
            )
        )
        if isinstance(command_id, str):
            if command_id in receipt_ids:
                errors.append(f"{prefix}: duplicate id")
            else:
                receipt_ids.add(command_id)
        sequence = receipt.get("sequence")
        if isinstance(sequence, int) and not isinstance(sequence, bool):
            if sequence in receipt_sequences:
                errors.append(f"{prefix}: duplicate sequence")
            else:
                receipt_sequences.add(sequence)
        expected_id = sha256_json(receipt_identity_payload(receipt))[:16]
        if command_id != expected_id:
            errors.append(f"{prefix}: id does not match receipt identity fields")
        if not isinstance(command_id, str) or re.fullmatch(r"[a-f0-9]{16}", command_id) is None:
            continue
        stdout_bytes: bytes | None = None
        for field, hash_field, suffix in (
            ("stdoutArtifact", "stdoutSha256", "stdout"),
            ("stderrArtifact", "stderrSha256", "stderr"),
        ):
            expected = f"evidence/commands/{command_id}.{suffix}"
            if receipt.get(field) != expected:
                errors.append(f"{prefix}: {field} must be {expected}")
                continue
            if expected in receipt_artifacts:
                errors.append(f"{prefix}: duplicate artifact path {expected}")
            else:
                receipt_artifacts.add(expected)
            try:
                artifact = _run_file(run_dir, expected)
            except ContractError as error:
                errors.extend(f"{prefix}: {message}" for message in error.errors)
                continue
            content = artifact.read_bytes()
            actual_hash = sha256_bytes(content)
            if receipt.get(hash_field) != actual_hash:
                errors.append(f"{prefix}: {hash_field} does not match {expected}")
            if field == "stdoutArtifact":
                stdout_bytes = content
        command = manifest_configuration["commands"].get(receipt.get("name"))
        if stdout_bytes is not None and isinstance(command, dict):
            errors.extend(
                receipt_semantic_errors(
                    receipt,
                    command,
                    stdout_bytes,
                    run_dir,
                    prefix=prefix,
                )
            )
    expected_sequences = set(range(len(data["commands"])))
    if receipt_sequences != expected_sequences:
        errors.append("command receipts: sequence values must be contiguous from zero")
    if isinstance(manifest, dict):
        artifacts = manifest.get("artifacts")
        if artifacts != artifact_paths:
            errors.append("review-manifest.artifacts: canonical artifact map mismatch")
        if final and manifest.get("status") not in {"draft", "final"}:
            errors.append("review-manifest.status: expected draft or final")
    if schema_version == SCHEMA_VERSION:
        _validate_evidence_registry(
            run_dir,
            data["evidenceRegistry"],
            _evidence_references(data),
            errors,
        )
    _validate_quality_model(data["qualityModel"], errors, final)
    coverage_paths = _validate_coverage(data["coverage"], errors, final)
    _validate_review_scope(data["reviewScope"], manifest_configuration, coverage_paths, errors)
    _validate_repository_maps(
        data["moduleMap"], data["dependencyMap"], coverage_paths, errors
    )
    candidates = _validate_candidates(data["candidates"], coverage_paths, errors, final)
    findings = _validate_findings(
        data["findings"], candidates, coverage_paths, errors
    )
    _validate_decisions(data["decisions"], findings, errors)
    _validate_scorecard(
        data["scorecard"],
        findings,
        data["candidates"],
        data["coverage"],
        data["commands"],
        errors,
        final,
    )
    _validate_remediation(data["remediationPlan"], errors, final)
    if errors:
        raise ContractError(errors)
    return data
