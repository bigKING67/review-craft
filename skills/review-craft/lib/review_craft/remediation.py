from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .constants import ACTIONABLE_DECISIONS, ARTIFACT_PATHS, FIX_SCHEMA_VERSION
from .contracts import ContractError, validate_run
from .evidence import run_configured_command
from .jsonio import read_json, sha256_bytes, sha256_json, write_json, write_jsonl
from .repository import fingerprint_inventory, inspect_git, inventory, worktree_fingerprint
from .schema_validation import validate_instance

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _schema(name: str) -> dict[str, Any]:
    value = read_json(SCHEMA_ROOT / name)
    if not isinstance(value, dict):
        raise ValueError(f"schema {name}: expected an object")
    return value


def _validate_schema(document: Any, schema_name: str) -> None:
    errors = [
        f"{schema_name}: {message}"
        for message in validate_instance(document, _schema(schema_name))
    ]
    if errors:
        raise ContractError(errors)


def _session_file(directory: Path, relative: str) -> Path:
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


def _file_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _status_fingerprint(status: str) -> str:
    return sha256_bytes(status.encode("utf-8", errors="surrogateescape"))


def _stable_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = ("path", "kind", "sizeBytes", "sha256", "binary", "classification")
    return [
        {field: row[field] for field in fields if field in row}
        for row in sorted(records, key=lambda item: item["path"])
    ]


def _current_source(target: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records, _ = inventory(target)
    state = inspect_git(target)
    return records, {
        "revision": state.revision,
        "branch": state.branch,
        "remote": state.remote,
        "sourceFingerprint": fingerprint_inventory(records),
        "worktreeFingerprint": worktree_fingerprint(target),
        "statusFingerprint": _status_fingerprint(state.status),
    }


def _selected_commands(
    available: dict[str, Any], requested: list[str], all_commands: bool
) -> tuple[list[str], dict[str, Any]]:
    if all_commands and requested:
        raise ValueError("--command and --all-commands cannot be combined")
    names = sorted(available) if all_commands else requested
    if len(names) != len(set(names)):
        raise ValueError("verification command names must be unique")
    unknown = sorted(set(names) - set(available))
    if unknown:
        raise ValueError(f"unknown configured verification commands: {', '.join(unknown)}")
    return names, {name: available[name] for name in names}


def prepare_fix(
    run_dir_value: str | Path,
    *,
    finding_ids: list[str],
    all_actionable: bool,
    command_names: list[str],
    all_commands: bool,
    output_root: str | Path | None = None,
    created_at: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    run_dir = Path(run_dir_value).expanduser().resolve(strict=True)
    data = validate_run(run_dir, final=True)
    manifest = data["manifest"]
    if manifest.get("status") != "final" or not manifest.get("sealedAt"):
        raise ValueError("prepare-fix requires a sealed final review run")
    if all_actionable and finding_ids:
        raise ValueError("--finding and --all-actionable cannot be combined")
    if not all_actionable and not finding_ids:
        raise ValueError("select --finding or --all-actionable")
    if len(finding_ids) != len(set(finding_ids)):
        raise ValueError("finding selections must be unique")

    findings = {row["id"]: row for row in data["findings"]["findings"]}
    decisions = {row["id"]: row for row in data["decisions"]["decisions"]}
    if all_actionable:
        selected_ids = sorted(
            finding_id
            for finding_id, finding in findings.items()
            if decisions[finding["decisionId"]]["decision"] in ACTIONABLE_DECISIONS
        )
    else:
        unknown = sorted(set(finding_ids) - set(findings))
        if unknown:
            raise ValueError(f"unknown findings: {', '.join(unknown)}")
        selected_ids = sorted(finding_ids)
    if not selected_ids:
        raise ValueError("the review contains no actionable findings")

    selections: list[dict[str, Any]] = []
    for finding_id in selected_ids:
        finding = findings[finding_id]
        decision = decisions[finding["decisionId"]]
        if decision["decision"] not in ACTIONABLE_DECISIONS:
            raise ValueError(
                f"finding {finding_id} has non-actionable decision {decision['decision']}"
            )
        criteria = list(dict.fromkeys(finding["verification"] + decision["verification"]))
        selections.append(
            {
                "findingId": finding_id,
                "findingSha256": sha256_json(finding),
                "decisionId": decision["id"],
                "decisionSha256": sha256_json(decision),
                "decision": decision["decision"],
                "locationPaths": sorted({row["path"] for row in finding["locations"]}),
                "verificationCriteria": criteria,
            }
        )

    available_commands = manifest["configuration"]["commands"]
    commands, command_config = _selected_commands(
        available_commands, command_names, all_commands
    )
    run_state = data["runState"]
    target = Path(run_state["targetRoot"]).expanduser().resolve(strict=True)
    baseline_records, baseline = _current_source(target)
    manifest_path = _session_file(run_dir, "review-manifest.json")
    created_at = created_at or _utc_now()
    seed = {
        "review": _file_sha256(manifest_path),
        "baseline": baseline["sourceFingerprint"],
        "selections": selections,
        "commands": commands,
    }
    stamp = created_at.replace("-", "").replace(":", "").split(".", 1)[0].removesuffix("Z") + "Z"
    fix_id = f"rcf-{stamp}-{sha256_json(seed)[:12]}"
    root = (
        Path(output_root).expanduser().resolve()
        if output_root
        else Path(tempfile.gettempdir()) / "review-craft-fixes"
    )
    fix_dir = root / manifest["target"]["repositoryName"] / fix_id
    try:
        fix_dir.resolve().relative_to(target)
    except ValueError:
        pass
    else:
        raise ValueError("fix output resolves inside the target repository")
    suffix = 2
    while fix_dir.exists():
        fix_dir = fix_dir.with_name(f"{fix_id}-{suffix}")
        suffix += 1
    fix_dir.mkdir(parents=True, mode=0o700)

    plan = {
        "documentType": "review-craft.fix-plan",
        "schemaVersion": FIX_SCHEMA_VERSION,
        "toolVersion": __version__,
        "fixId": fix_dir.name,
        "createdAt": created_at,
        "review": {
            "runId": manifest["runId"],
            "manifestSha256": _file_sha256(manifest_path),
            "targetIdentity": manifest["target"]["identity"],
            "repositoryName": manifest["target"]["repositoryName"],
        },
        "baseline": baseline,
        "selections": selections,
        "verification": {
            "commands": commands,
            "commandConfigSha256": sha256_json(command_config),
        },
        "authorization": {
            "sourceMutation": "EXPLICIT_USER_REQUIRED",
            "runtimeMutatesSource": False,
        },
    }
    _validate_schema(plan, "fix-plan.schema.json")
    write_json(fix_dir / "fix-plan.json", plan, mode=0o600)
    write_jsonl(fix_dir / ARTIFACT_PATHS["commands"], [])
    write_json(
        fix_dir / "fix-state.json",
        {
            "targetRoot": str(target),
            "reviewRunDir": str(run_dir),
            "planSha256": sha256_json(plan),
            "baselineFiles": _stable_records(baseline_records),
            "commands": command_config,
            "commandConfigSha256": sha256_json(command_config),
        },
        mode=0o600,
    )
    from .remediation_validation import validate_fix

    validate_fix(fix_dir, require_verification=False)
    return fix_dir, plan


def _load_fix(fix_dir_value: str | Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    fix_dir = Path(fix_dir_value).expanduser().resolve(strict=True)
    plan = read_json(_session_file(fix_dir, "fix-plan.json"))
    state = read_json(_session_file(fix_dir, "fix-state.json"))
    _validate_schema(plan, "fix-plan.schema.json")
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
    if errors:
        raise ContractError(errors)
    return fix_dir, plan, state


def _validate_review_provenance(plan: dict[str, Any], state: dict[str, Any]) -> None:
    errors: list[str] = []
    try:
        run_dir = Path(state["reviewRunDir"]).expanduser().resolve(strict=True)
        manifest_path = _session_file(run_dir, "review-manifest.json")
        manifest = read_json(manifest_path)
        findings_doc = read_json(_session_file(run_dir, ARTIFACT_PATHS["findings"]))
        decisions_doc = read_json(_session_file(run_dir, ARTIFACT_PATHS["decisions"]))
    except (KeyError, OSError, ValueError, ContractError) as error:
        raise ContractError([f"fix review provenance is unavailable: {error}"]) from error
    if _file_sha256(manifest_path) != plan["review"]["manifestSha256"]:
        errors.append("fix-plan.review.manifestSha256: review manifest changed")
    if manifest.get("status") != "final" or not manifest.get("sealedAt"):
        errors.append("fix review provenance: source review is not sealed and final")
    if manifest.get("runId") != plan["review"]["runId"]:
        errors.append("fix-plan.review.runId: does not match review manifest")
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


def _changes(
    baseline_files: list[dict[str, Any]], current_files: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    before = {row["path"]: row for row in baseline_files}
    after = {row["path"]: row for row in current_files}
    changes: list[dict[str, Any]] = []
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
        changes.append(
            {
                "path": path,
                "status": status,
                "beforeSha256": old.get("sha256") if old else None,
                "afterSha256": new.get("sha256") if new else None,
            }
        )
    return changes


def _assessment_rows(
    assessment: dict[str, Any], plan: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    _validate_schema(assessment, "fix-assessment.schema.json")
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


def _validate_evidence_refs(
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


def _verification_status(
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


def verify_fix(
    fix_dir_value: str | Path,
    *,
    assessment_path: str | Path,
    verified_at: str | None = None,
) -> dict[str, Any]:
    fix_dir, plan, state = _load_fix(fix_dir_value)
    _validate_review_provenance(plan, state)
    assessment = read_json(Path(assessment_path).expanduser().resolve(strict=True))
    assessment_by_id = _assessment_rows(assessment, plan)
    target = Path(state["targetRoot"]).expanduser().resolve(strict=True)
    current_state = inspect_git(target)
    if current_state.remote != plan["baseline"]["remote"]:
        raise ContractError(["fix target remote changed after preparation"])

    command_results: list[dict[str, Any]] = []
    planned_commands = plan["verification"]["commands"]
    for name in planned_commands:
        _, receipt = run_configured_command(
            session_dir=fix_dir,
            target=target,
            commands=state["commands"],
            command_name=name,
            allow_repository_mutation=False,
        )
        command_results.append(
            {
                "name": name,
                "receiptId": receipt["id"],
                "receiptSha256": sha256_json(receipt),
                "exitCode": receipt["exitCode"],
                "timedOut": receipt["timedOut"],
                "repositoryMutationDetected": receipt["repositoryMutationDetected"],
            }
        )
        if receipt["repositoryMutationDetected"]:
            break
    skipped_commands = planned_commands[len(command_results) :]

    current_records, current = _current_source(target)
    changes = _changes(state["baselineFiles"], _stable_records(current_records))
    source_changed = current["sourceFingerprint"] != plan["baseline"]["sourceFingerprint"]
    _validate_evidence_refs(
        assessment=assessment,
        changes=changes,
        command_results=command_results,
    )
    changed_paths = {row["path"] for row in changes}
    finding_results = []
    for selection in plan["selections"]:
        row = assessment_by_id[selection["findingId"]]
        finding_results.append(
            {
                **row,
                "locationPathsChanged": sorted(
                    changed_paths.intersection(selection["locationPaths"])
                ),
            }
        )
    status = _verification_status(
        source_changed=source_changed,
        command_results=command_results,
        skipped_commands=skipped_commands,
        statuses=[row["status"] for row in finding_results],
    )
    write_json(fix_dir / "fix-assessment.json", assessment, mode=0o600)
    result = {
        "documentType": "review-craft.fix-verification",
        "schemaVersion": FIX_SCHEMA_VERSION,
        "toolVersion": __version__,
        "fixId": plan["fixId"],
        "verifiedAt": verified_at or _utc_now(),
        "planSha256": sha256_json(plan),
        "assessmentSha256": sha256_json(assessment),
        "status": status,
        "sourceChanged": source_changed,
        "current": current,
        "changes": changes,
        "commands": command_results,
        "skippedCommands": skipped_commands,
        "findingResults": finding_results,
        "assessmentKind": assessment["kind"],
        "remainingRisks": assessment["remainingRisks"],
    }
    _validate_schema(result, "fix-verification.schema.json")
    write_json(fix_dir / "fix-verification.json", result, mode=0o600)
    from .remediation_validation import validate_fix

    validate_fix(fix_dir, require_verification=True)
    return result
