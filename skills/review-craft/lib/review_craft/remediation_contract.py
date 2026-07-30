from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import ARTIFACT_PATHS
from .contracts import ContractError
from .jsonio import read_json, sha256_bytes, sha256_json
from .repository import (
    fingerprint_inventory,
    inspect_git,
    inventory_for_configuration,
    source_inventory_configuration,
    worktree_fingerprint,
)
from .schema_validation import validate_instance

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def schema(name: str) -> dict[str, Any]:
    value = read_json(SCHEMA_ROOT / name)
    if not isinstance(value, dict):
        raise ValueError(f"schema {name}: expected an object")
    return value


def validate_schema(document: Any, schema_name: str) -> None:
    errors = [
        f"{schema_name}: {message}"
        for message in validate_instance(document, schema(schema_name))
    ]
    if errors:
        raise ContractError(errors)


def session_file(directory: Path, relative: str) -> Path:
    path = directory / relative
    if path.is_symlink():
        raise ContractError([f"fix artifact must not be a symlink: {relative}"])
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(directory)
    except (OSError, ValueError) as error:
        raise ContractError([f"invalid fix artifact {relative}: {error}"]) from error
    if not resolved.is_file():
        raise ContractError([f"fix artifact must be a file: {relative}"])
    return resolved


def file_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _status_fingerprint(status: str) -> str:
    return sha256_bytes(status.encode("utf-8", errors="surrogateescape"))


def stable_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = (
        "path",
        "kind",
        "sizeBytes",
        "sha256",
        "binary",
        "classification",
        "diffStatus",
        "previousPath",
        "untracked",
    )
    return [
        {field: row[field] for field in fields if field in row}
        for row in sorted(records, key=lambda item: item["path"])
    ]


def current_source(
    target: Path,
    configuration: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records, _, _ = inventory_for_configuration(target, configuration)
    state = inspect_git(target)
    return records, {
        "revision": state.revision,
        "branch": state.branch,
        "remote": state.remote,
        "sourceFingerprint": fingerprint_inventory(records),
        "worktreeFingerprint": worktree_fingerprint(target, records=records),
        "statusFingerprint": _status_fingerprint(state.status),
    }


def fix_source_configuration(state: dict[str, Any]) -> dict[str, Any]:
    configuration = state.get("sourceConfiguration")
    if isinstance(configuration, dict):
        return source_inventory_configuration(configuration)
    try:
        run_dir = Path(state["reviewRunDir"]).expanduser().resolve(strict=True)
        manifest = read_json(session_file(run_dir, "review-manifest.json"))
        manifest_configuration = manifest["configuration"]
    except (KeyError, OSError, TypeError, ValueError, ContractError) as error:
        raise ContractError([f"fix source configuration is unavailable: {error}"]) from error
    if not isinstance(manifest_configuration, dict):
        raise ContractError(["fix source configuration: review configuration is invalid"])
    return source_inventory_configuration(manifest_configuration)


def load_fix(
    fix_dir_value: str | Path,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    fix_dir = Path(fix_dir_value).expanduser().resolve(strict=True)
    plan = read_json(session_file(fix_dir, "fix-plan.json"))
    state = read_json(session_file(fix_dir, "fix-state.json"))
    validate_schema(plan, "fix-plan.schema.json")
    if not isinstance(state, dict):
        raise ContractError(["fix-state.json: expected an object"])
    errors: list[str] = []
    if plan.get("fixId") != fix_dir.name:
        errors.append("fix-plan.fixId: must match the fix directory name")
    selection_ids = [row["findingId"] for row in plan["selections"]]
    if len(selection_ids) != len(set(selection_ids)):
        errors.append("fix-plan.selections: finding ids must be unique")
    for selection in plan["selections"]:
        for field in ("locationPaths", "verificationCriteria"):
            values = selection[field]
            if len(values) != len(set(values)):
                errors.append(
                    f"fix-plan selection {selection['findingId']}: {field} must be unique"
                )
    planned_commands = plan["verification"]["commands"]
    if len(planned_commands) != len(set(planned_commands)):
        errors.append("fix-plan.verification.commands: names must be unique")
    if state.get("planSha256") != sha256_json(plan):
        errors.append("fix-state.planSha256: does not match fix-plan.json")
    baseline_files = state.get("baselineFiles")
    if not isinstance(baseline_files, list) or not all(
        isinstance(row, dict) for row in baseline_files
    ):
        errors.append("fix-state.baselineFiles: expected an array of objects")
    elif fingerprint_inventory(baseline_files) != plan["baseline"]["sourceFingerprint"]:
        errors.append("fix-state.baselineFiles: does not match baseline source fingerprint")
    elif len({row.get("path") for row in baseline_files}) != len(baseline_files):
        errors.append("fix-state.baselineFiles: paths must be unique")
    commands = state.get("commands")
    if not isinstance(commands, dict):
        errors.append("fix-state.commands: expected an object")
    else:
        command_hash = sha256_json(commands)
        if command_hash != state.get("commandConfigSha256"):
            errors.append("fix-state.commandConfigSha256: does not match commands")
        if command_hash != plan["verification"]["commandConfigSha256"]:
            errors.append("fix-plan.verification.commandConfigSha256: does not match state")
        if sorted(commands) != sorted(plan["verification"]["commands"]):
            errors.append("fix-state.commands: names do not match fix plan")
    stored_source_configuration = state.get("sourceConfiguration")
    try:
        resolved_source_configuration = fix_source_configuration(state)
    except ContractError as error:
        errors.extend(error.errors)
    else:
        if (
            stored_source_configuration is not None
            and stored_source_configuration != resolved_source_configuration
        ):
            errors.append("fix-state.sourceConfiguration: is not canonical")
        source_configuration_hash = sha256_json(resolved_source_configuration)
        stored_source_configuration_hash = state.get("sourceConfigurationSha256")
        if (
            stored_source_configuration_hash is not None
            and stored_source_configuration_hash != source_configuration_hash
        ):
            errors.append(
                "fix-state.sourceConfigurationSha256: does not match sourceConfiguration"
            )
        # Legacy v1 sessions derive this field from their sealed review manifest.
        state["sourceConfiguration"] = resolved_source_configuration
        state["sourceConfigurationSha256"] = source_configuration_hash
    if errors:
        raise ContractError(errors)
    return fix_dir, plan, state


def validate_review_provenance(plan: dict[str, Any], state: dict[str, Any]) -> None:
    errors: list[str] = []
    try:
        run_dir = Path(state["reviewRunDir"]).expanduser().resolve(strict=True)
        manifest_path = session_file(run_dir, "review-manifest.json")
        manifest = read_json(manifest_path)
        findings_doc = read_json(session_file(run_dir, ARTIFACT_PATHS["findings"]))
        decisions_doc = read_json(session_file(run_dir, ARTIFACT_PATHS["decisions"]))
    except (KeyError, OSError, ValueError, ContractError) as error:
        raise ContractError([f"fix review provenance is unavailable: {error}"]) from error
    if file_sha256(manifest_path) != plan["review"]["manifestSha256"]:
        errors.append("fix-plan.review.manifestSha256: review manifest changed")
    if manifest.get("status") != "final" or not manifest.get("sealedAt"):
        errors.append("fix review provenance: source review is not sealed and final")
    if manifest.get("runId") != plan["review"]["runId"]:
        errors.append("fix-plan.review.runId: does not match review manifest")
    manifest_configuration = manifest.get("configuration", {})
    if not isinstance(manifest_configuration, dict):
        errors.append("fix review provenance: review configuration is invalid")
    else:
        expected_source_configuration = source_inventory_configuration(
            manifest_configuration
        )
        if state.get("sourceConfiguration") != expected_source_configuration:
            errors.append(
                "fix-state.sourceConfiguration: does not match review provenance"
            )
        if state.get("sourceConfigurationSha256") != sha256_json(
            expected_source_configuration
        ):
            errors.append(
                "fix-state.sourceConfigurationSha256: review provenance mismatch"
            )
    target = manifest.get("target") if isinstance(manifest, dict) else None
    if not isinstance(target, dict) or target.get("identity") != plan["review"]["targetIdentity"]:
        errors.append("fix-plan.review.targetIdentity: does not match review manifest")
    findings = {
        row.get("id"): row
        for row in findings_doc.get("findings", [])
        if isinstance(row, dict)
    }
    decisions = {
        row.get("id"): row
        for row in decisions_doc.get("decisions", [])
        if isinstance(row, dict)
    }
    original_commands = manifest.get("configuration", {}).get("commands", {})
    selected_commands = {
        name: original_commands.get(name) for name in plan["verification"]["commands"]
    }
    if any(value is None for value in selected_commands.values()):
        errors.append("fix-plan.verification.commands: command is absent from review provenance")
    elif sha256_json(selected_commands) != plan["verification"]["commandConfigSha256"]:
        errors.append("fix-plan.verification.commandConfigSha256: review provenance mismatch")
    for selection in plan["selections"]:
        finding = findings.get(selection["findingId"])
        decision = decisions.get(selection["decisionId"])
        if finding is None or sha256_json(finding) != selection["findingSha256"]:
            errors.append(f"fix selection {selection['findingId']}: finding provenance changed")
        if decision is None or sha256_json(decision) != selection["decisionSha256"]:
            errors.append(f"fix selection {selection['findingId']}: decision provenance changed")
        if finding is not None:
            expected_locations = sorted({row["path"] for row in finding.get("locations", [])})
            if selection["locationPaths"] != expected_locations:
                errors.append(
                    f"fix selection {selection['findingId']}: location paths do not match finding"
                )
            if finding.get("decisionId") != selection["decisionId"]:
                errors.append(
                    f"fix selection {selection['findingId']}: decision id does not match finding"
                )
        if decision is not None:
            expected_criteria = list(
                dict.fromkeys(
                    (finding.get("verification", []) if finding else [])
                    + decision.get("verification", [])
                )
            )
            if selection["verificationCriteria"] != expected_criteria:
                errors.append(
                    f"fix selection {selection['findingId']}: verification criteria changed"
                )
            if decision.get("decision") != selection["decision"]:
                errors.append(
                    f"fix selection {selection['findingId']}: decision action changed"
                )
            if selection["findingId"] not in decision.get("findingRefs", []):
                errors.append(
                    f"fix selection {selection['findingId']}: decision does not reference finding"
                )
    if errors:
        raise ContractError(errors)


def changes(
    baseline_files: list[dict[str, Any]], current_files: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    before = {row["path"]: row for row in baseline_files}
    after = {row["path"]: row for row in current_files}
    result: list[dict[str, Any]] = []
    for path in sorted(set(before) | set(after)):
        old = before.get(path)
        new = after.get(path)
        if old is None:
            status = "ADDED"
        elif new is None:
            status = "DELETED"
        elif old.get("sha256") == new.get("sha256") and old.get("kind") == new.get("kind"):
            continue
        else:
            status = "MODIFIED"
        result.append(
            {
                "path": path,
                "status": status,
                "beforeSha256": old.get("sha256") if old else None,
                "afterSha256": new.get("sha256") if new else None,
            }
        )
    return result


def assessment_rows(
    assessment: dict[str, Any], plan: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    validate_schema(assessment, "fix-assessment.schema.json")
    rows = assessment["findings"]
    identifiers = [row["findingId"] for row in rows]
    expected = [row["findingId"] for row in plan["selections"]]
    errors: list[str] = []
    if len(identifiers) != len(set(identifiers)):
        errors.append("fix-assessment.findings: finding ids must be unique")
    if set(identifiers) != set(expected):
        errors.append("fix-assessment.findings: must assess every selected finding exactly once")
    for row in rows:
        if len(row["evidenceRefs"]) != len(set(row["evidenceRefs"])):
            errors.append(
                f"fix-assessment {row['findingId']}: evidence references must be unique"
            )
    if errors:
        raise ContractError(errors)
    return {row["findingId"]: row for row in rows}


def validate_evidence_refs(
    *,
    assessment: dict[str, Any],
    changes: list[dict[str, Any]],
    command_results: list[dict[str, Any]],
) -> None:
    changed_paths = {row["path"] for row in changes}
    command_names = {row["name"] for row in command_results}
    errors: list[str] = []
    for result in assessment["findings"]:
        for reference in result["evidenceRefs"]:
            kind, separator, value = reference.partition(":")
            if not separator or not value:
                errors.append(
                    f"fix-assessment {result['findingId']}: invalid evidence ref {reference!r}"
                )
            elif kind == "change" and value not in changed_paths:
                errors.append(
                    f"fix-assessment {result['findingId']}: change evidence is not present: {value}"
                )
            elif kind == "command" and value not in command_names:
                errors.append(
                    f"fix-assessment {result['findingId']}: command evidence was not run: {value}"
                )
            elif kind == "manual" and assessment["kind"] != "HUMAN":
                errors.append(
                    f"fix-assessment {result['findingId']}: manual evidence requires HUMAN kind"
                )
            elif kind not in {"change", "command", "manual"}:
                errors.append(
                    f"fix-assessment {result['findingId']}: unsupported evidence ref {reference!r}"
                )
        if assessment["kind"] == "AUTOMATED" and result["status"] in {
            "RESOLVED",
            "LIKELY_RESOLVED",
        } and not any(ref.startswith("command:") for ref in result["evidenceRefs"]):
            errors.append(
                f"fix-assessment {result['findingId']}: automated resolution "
                "requires command evidence"
            )
    if errors:
        raise ContractError(errors)


def verification_status(
    *,
    source_changed: bool,
    command_results: list[dict[str, Any]],
    skipped_commands: list[str],
    statuses: list[str],
) -> str:
    if not source_changed:
        return "NO_CHANGES"
    if skipped_commands or any(
        row["exitCode"] != 0 or row["timedOut"] or row["repositoryMutationDetected"]
        for row in command_results
    ) or any(status in {"UNRESOLVED", "REGRESSED"} for status in statuses):
        return "FAILED"
    if any(status in {"LIKELY_RESOLVED", "PARTIAL"} for status in statuses):
        return "PARTIAL"
    return "VERIFIED"
