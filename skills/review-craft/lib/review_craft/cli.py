from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .constants import ARTIFACT_PATHS, REMEDIATION_PHASES, SCHEMA_VERSION, SCORE_DIMENSIONS
from .contracts import ContractError, validate_run
from .jsonio import (
    canonical_compact,
    read_json,
    read_jsonl,
    sha256_bytes,
    sha256_json,
    write_json,
    write_jsonl,
)
from .report import finalize_run
from .repository import (
    fingerprint_inventory,
    inspect_git,
    inventory,
    repository_identity,
    tracked_fingerprint,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_config() -> dict[str, Any]:
    return {
        "profile": "generic",
        "scope": ["."],
        "exclude": [],
        "generated": [],
        "vendored": [],
        "commands": {},
        "policy": {
            "allowNetwork": False,
            "allowInstall": False,
            "allowRepositoryMutation": False,
            "outputOutsideRepository": True,
        },
        "reportLanguage": "zh-CN",
    }


def _load_config(path: Path | None) -> dict[str, Any]:
    config = _default_config()
    if path is None:
        return config
    supplied = read_json(path.expanduser().resolve(strict=True))
    if not isinstance(supplied, dict):
        raise ValueError("config: expected a JSON object")
    supplied = {key: value for key, value in supplied.items() if key != "$schema"}
    unknown = set(supplied) - set(config)
    if unknown:
        raise ValueError(f"config: unsupported fields {', '.join(sorted(unknown))}")
    for key, value in supplied.items():
        if key == "policy":
            if not isinstance(value, dict):
                raise ValueError("config.policy: expected an object")
            unknown_policy = set(value) - set(config["policy"])
            if unknown_policy:
                raise ValueError(
                    f"config.policy: unsupported fields {', '.join(sorted(unknown_policy))}"
                )
            if not all(isinstance(item, bool) for item in value.values()):
                raise ValueError("config.policy: expected boolean values")
            config["policy"].update(value)
        else:
            config[key] = value
    for field in ("scope", "exclude", "generated", "vendored"):
        if not isinstance(config[field], list) or not all(
            isinstance(item, str) and item for item in config[field]
        ):
            raise ValueError(f"config.{field}: expected an array of strings")
    if not config["scope"]:
        raise ValueError("config.scope: must not be empty")
    commands = config["commands"]
    if not isinstance(commands, dict):
        raise ValueError("config.commands: expected an object")
    for name, command in commands.items():
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", name) or not isinstance(command, dict):
            raise ValueError(f"config.commands.{name}: invalid command")
        argv = command.get("argv")
        if not isinstance(argv, list) or not argv or not all(
            isinstance(item, str) and item and "\0" not in item for item in argv
        ):
            raise ValueError(f"config.commands.{name}.argv: expected a non-empty string array")
        cwd = command.get("cwd", ".")
        if not isinstance(cwd, str) or not cwd:
            raise ValueError(f"config.commands.{name}.cwd: expected a string")
        timeout = command.get("timeoutSeconds", 600)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
            raise ValueError(f"config.commands.{name}.timeoutSeconds: expected a positive integer")
    return config


def _repository_name(state_root: Path, remote: str | None) -> str:
    if remote:
        tail = remote.rstrip("/").rsplit("/", 1)[-1]
        return tail.removesuffix(".git") or state_root.name
    return state_root.name


def _coverage_row(record: dict[str, Any]) -> dict[str, Any]:
    if record["kind"] == "unreadable":
        disposition = "UNREADABLE"
        reason = f"preflight could not read file ({record.get('readError', 'unknown')})"
    elif record["classification"] == "generated":
        disposition = "GENERATED"
        reason = "matched explicit generated pattern"
    elif record["classification"] == "vendored":
        disposition = "VENDORED"
        reason = "matched explicit vendored pattern"
    elif record["binary"]:
        disposition = "BINARY"
        reason = "binary or non-UTF-8 content"
    else:
        disposition = "PENDING"
        reason = "awaiting review"
    return {
        **record,
        "disposition": disposition,
        "reason": reason,
        "evidenceRefs": [],
    }


def _draft_artifacts(
    run_dir: Path,
    *,
    run_id: str,
    target: Path,
    config: dict[str, Any],
    records: list[dict[str, Any]],
    excluded: list[dict[str, str]],
) -> None:
    state = inspect_git(target)
    source_fingerprint = fingerprint_inventory(records)
    manifest = {
        "documentType": "review-craft.manifest",
        "schemaVersion": SCHEMA_VERSION,
        "toolVersion": __version__,
        "status": "draft",
        "runId": run_id,
        "createdAt": utc_now(),
        "sealedAt": None,
        "target": {
            "repositoryName": _repository_name(state.root, state.remote),
            "identity": repository_identity(state, records),
            "revision": state.revision,
            "branch": state.branch,
            "remote": state.remote,
            "dirty": bool(state.status),
            "unborn": state.unborn,
            "sourceFingerprint": source_fingerprint,
            "dirtyFingerprint": sha256_bytes(
                state.status.encode("utf-8", errors="surrogateescape")
            ),
        },
        "configuration": config,
        "configFingerprint": sha256_json(config),
        "artifacts": ARTIFACT_PATHS,
    }
    quality_model = {
        "documentType": "review-craft.quality-model",
        "schemaVersion": SCHEMA_VERSION,
        "purpose": "",
        "audience": "",
        "criticalPaths": [],
        "invariants": [],
        "nonGoals": [],
        "compatibility": [],
        "performanceBudgets": [],
        "reliabilityRequirements": [],
        "authoritySources": [],
        "assumptions": [],
        "unknowns": [],
    }
    coverage_files = [_coverage_row(row) for row in records]
    coverage = {
        "documentType": "review-craft.coverage",
        "schemaVersion": SCHEMA_VERSION,
        "inventoryFingerprint": source_fingerprint,
        "files": coverage_files,
        "excluded": excluded,
        "summary": {
            "total": len(coverage_files),
            "reviewed": 0,
            "deferred": sum(
                1
                for row in coverage_files
                if row["disposition"] in {"PENDING", "DEFERRED", "UNREADABLE"}
            ),
        },
    }
    findings = {
        "documentType": "review-craft.findings",
        "schemaVersion": SCHEMA_VERSION,
        "findings": [],
    }
    decisions = {
        "documentType": "review-craft.decisions",
        "schemaVersion": SCHEMA_VERSION,
        "decisions": [],
    }
    scorecard = {
        "documentType": "review-craft.scorecard",
        "schemaVersion": SCHEMA_VERSION,
        "status": "provisional",
        "evidenceLevel": "E0",
        "confidence": "LOW",
        "coveragePercent": 0.0,
        "unresolvedCandidates": 0,
        "dimensions": [
            {
                "id": identifier,
                "label": label,
                "maximum": maximum,
                "awarded": 0,
                "deductions": [
                    {
                        "points": maximum,
                        "reason": "尚未完成审查，当前不形成正式评分",
                        "evidenceRefs": ["coverage:draft"],
                    }
                ],
            }
            for identifier, label, maximum in SCORE_DIMENSIONS
        ],
        "total": 0,
    }
    remediation = {
        "documentType": "review-craft.remediation-plan",
        "schemaVersion": SCHEMA_VERSION,
        "changeClass": "LOCAL_OPTIMIZATION",
        "targetScore": 0,
        "targetEvidenceLevel": "E1",
        "targetArchitecture": {
            "overview": "",
            "moduleBoundaries": [],
            "dependencyDirection": [],
            "coreDataFlow": [],
            "stateAndErrors": [],
            "directoryStructure": [],
            "testingStructure": [],
            "deliveryFlow": [],
        },
        "phases": [
            {
                "id": identifier,
                "title": title,
                "modificationScope": [],
                "prerequisites": [],
                "expectedBenefits": [],
                "risks": [],
                "acceptanceCriteria": [],
            }
            for identifier, title in REMEDIATION_PHASES
        ],
    }
    write_json(run_dir / "review-manifest.json", manifest)
    write_json(run_dir / ARTIFACT_PATHS["qualityModel"], quality_model)
    write_json(run_dir / ARTIFACT_PATHS["coverage"], coverage)
    write_jsonl(run_dir / ARTIFACT_PATHS["candidateLedger"], [])
    write_json(run_dir / ARTIFACT_PATHS["findings"], findings)
    write_json(run_dir / ARTIFACT_PATHS["decisions"], decisions)
    write_json(run_dir / ARTIFACT_PATHS["scorecard"], scorecard)
    write_json(run_dir / ARTIFACT_PATHS["remediationPlan"], remediation)
    write_jsonl(run_dir / ARTIFACT_PATHS["commands"], [])
    write_json(
        run_dir / "run-state.json",
        {"targetRoot": str(state.root), "trackedFingerprint": tracked_fingerprint(state.root)},
        mode=0o600,
    )


def command_doctor(args: argparse.Namespace) -> int:
    temporary_directory = tempfile.gettempdir()
    temporary_directory_writable = os.access(temporary_directory, os.W_OK)
    payload = {
        "version": __version__,
        "python": platform.python_version(),
        "pythonSupported": sys.version_info >= (3, 10),
        "git": shutil.which("git"),
        "temporaryDirectory": temporary_directory,
        "temporaryDirectoryWritable": temporary_directory_writable,
        "ready": bool(
            sys.version_info >= (3, 10) and shutil.which("git") and temporary_directory_writable
        ),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True) if args.json else payload)
    return 0 if payload["ready"] else 2


def command_preflight(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser().resolve(strict=True)
    if not target.is_dir():
        raise ValueError("--target: expected a directory")
    config = _load_config(Path(args.config) if args.config else None)
    state = inspect_git(target)
    root = state.root
    records, excluded = inventory(
        root,
        scopes=config["scope"],
        excludes=config["exclude"],
        generated=config["generated"],
        vendored=config["vendored"],
    )
    state_hash = sha256_bytes(
        canonical_compact(
            {
                "identity": repository_identity(state, records),
                "config": config,
                "status": state.status,
            }
        ).encode("utf-8", errors="surrogateescape")
    )[:12]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"rc-{stamp}-{state_hash}"
    output_root = (
        Path(args.output_root).expanduser().resolve()
        if args.output_root
        else Path(tempfile.gettempdir()) / "review-craft-runs"
    )
    run_dir = output_root / _repository_name(root, state.remote) / run_id
    if config["policy"]["outputOutsideRepository"]:
        try:
            run_dir.resolve().relative_to(root)
        except ValueError:
            pass
        else:
            raise ValueError("--output-root resolves inside the target repository")
    suffix = 2
    while run_dir.exists():
        run_dir = run_dir.with_name(f"{run_id}-{suffix}")
        suffix += 1
    run_dir.mkdir(parents=True, mode=0o700)
    _draft_artifacts(
        run_dir,
        run_id=run_dir.name,
        target=root,
        config=config,
        records=records,
        excluded=excluded,
    )
    print(
        json.dumps(
            {"runId": run_dir.name, "runDir": str(run_dir), "files": len(records)},
            ensure_ascii=False,
        )
    )
    return 0


def _resolve_inside(root: Path, relative: str, field: str) -> Path:
    if Path(relative).is_absolute():
        raise ValueError(f"{field}: expected a repository-relative path")
    resolved = (root / relative).resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{field}: escapes the repository root") from error
    if not resolved.is_dir():
        raise ValueError(f"{field}: expected a directory")
    return resolved


def command_run_evidence(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve(strict=True)
    manifest = read_json(run_dir / "review-manifest.json")
    state = read_json(run_dir / "run-state.json")
    target = Path(state["targetRoot"]).resolve(strict=True)
    command = manifest["configuration"]["commands"].get(args.command)
    if not isinstance(command, dict):
        raise ValueError(f"unknown configured command: {args.command}")
    cwd = _resolve_inside(target, command.get("cwd", "."), "command.cwd")
    argv = command["argv"]
    timeout = command.get("timeoutSeconds", 600)
    before_status = inspect_git(target).status
    before_tracked = tracked_fingerprint(target)
    started_at = utc_now()
    start = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
            shell=False,
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as error:
        timed_out = True
        exit_code = 124
        stdout = error.stdout or b""
        stderr = error.stderr or b""
    duration_ms = round((time.monotonic() - start) * 1000)
    after_state = inspect_git(target)
    after_tracked = tracked_fingerprint(target)
    mutation = before_tracked != after_tracked
    command_id = sha256_bytes(
        canonical_compact(
            {
                "name": args.command,
                "argv": argv,
                "startedAt": started_at,
                "cwd": command.get("cwd", "."),
            }
        ).encode("utf-8")
    )[:16]
    evidence_dir = run_dir / "evidence" / "commands"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = evidence_dir / f"{command_id}.stdout"
    stderr_path = evidence_dir / f"{command_id}.stderr"
    stdout_path.write_bytes(stdout)
    stderr_path.write_bytes(stderr)
    receipt = {
        "id": command_id,
        "name": args.command,
        "argv": argv,
        "cwd": command.get("cwd", "."),
        "startedAt": started_at,
        "durationMs": duration_ms,
        "exitCode": exit_code,
        "timedOut": timed_out,
        "stdoutArtifact": stdout_path.relative_to(run_dir).as_posix(),
        "stderrArtifact": stderr_path.relative_to(run_dir).as_posix(),
        "beforeStatusSha256": sha256_bytes(before_status.encode("utf-8", errors="surrogateescape")),
        "afterStatusSha256": sha256_bytes(
            after_state.status.encode("utf-8", errors="surrogateescape")
        ),
        "repositoryMutationDetected": mutation,
    }
    commands_path = run_dir / ARTIFACT_PATHS["commands"]
    rows = read_jsonl(commands_path)
    rows.append(receipt)
    write_jsonl(commands_path, rows)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    if mutation and not manifest["configuration"]["policy"].get("allowRepositoryMutation", False):
        return 3
    return exit_code


def command_validate(args: argparse.Namespace) -> int:
    data = validate_run(Path(args.run_dir), final=not args.allow_draft)
    print(
        json.dumps(
            {
                "valid": True,
                "runId": data["manifest"]["runId"],
                "final": not args.allow_draft,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def command_finalize(args: argparse.Namespace) -> int:
    report = finalize_run(Path(args.run_dir), sealed_at=utc_now())
    print(json.dumps({"report": str(report)}, ensure_ascii=False, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review Craft deterministic runtime")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check runtime prerequisites")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(handler=command_doctor)

    preflight = subparsers.add_parser("preflight", help="Create a review run")
    preflight.add_argument("--target", required=True)
    preflight.add_argument("--config")
    preflight.add_argument("--output-root")
    preflight.set_defaults(handler=command_preflight)

    evidence = subparsers.add_parser("run-evidence", help="Run an allowlisted evidence command")
    evidence.add_argument("--run-dir", required=True)
    evidence.add_argument("--command", required=True)
    evidence.set_defaults(handler=command_run_evidence)

    validate = subparsers.add_parser("validate", help="Validate canonical artifacts")
    validate.add_argument("--run-dir", required=True)
    validate.add_argument("--allow-draft", action="store_true")
    validate.set_defaults(handler=command_validate)

    finalize = subparsers.add_parser("finalize", help="Generate report.md")
    finalize.add_argument("--run-dir", required=True)
    finalize.set_defaults(handler=command_finalize)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except ContractError as error:
        print("review-craft contract validation failed:", file=sys.stderr)
        for item in error.errors:
            print(f"- {item}", file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as error:
        print(f"review-craft: {error}", file=sys.stderr)
        return 2
