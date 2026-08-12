#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eval_contracts import (
    ABLATION_TREATMENTS,
    ADAPTER_SCHEMA,
    HOST_OUTPUT_SCHEMA,
    SCHEMA_ROOT,
    EvalError,
    ablation_protocol,
    build_ablation_adjudication_result,
    build_ablation_adjudication_template,
    build_adjudication_result,
    build_adjudication_template,
    file_hash,
    golden_eligible,
    overall_status,
    read_json,
    safe_artifact,
    schema_errors,
    score_cases,
    sha256_bytes,
    sha256_json,
    source_metadata,
    source_stable,
    tree_sha256,
    unavailable_usage,
    utc_now,
    validate_ablation_adjudication_result,
    validate_ablation_comparison,
    validate_ablation_run,
    validate_ablation_schedule,
    validate_ablation_snapshot,
    validate_adjudication_result,
    validate_comparison_payload,
    validate_eval_suite,
    validate_golden_snapshot,
    validate_run,
    validate_usage_record,
    write_bytes,
    write_json,
)
from remediation_safety import (
    DEFAULT_OUTPUT_ROOT as DEFAULT_REMEDIATION_OUTPUT_ROOT,
)
from remediation_safety import (
    DEFAULT_SKILL as DEFAULT_REMEDIATION_SKILL,
)
from remediation_safety import (
    DEFAULT_SUITE as DEFAULT_REMEDIATION_SUITE,
)
from remediation_safety import (
    run_remediation_safety,
    validate_remediation_run,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = ROOT / "evals/specs/cases.json"
DEFAULT_ABLATION_SUITE = ROOT / "evals/specs/self-correction-cases.json"
DEFAULT_SKILL = ROOT / "skills/review-craft"
DEFAULT_EVIDENCE_ROOT = ROOT / "evals/verifiers"
TOOL_TRACE_SCHEMA = SCHEMA_ROOT / "eval-tool-trace.schema.json"
PROMPTS = {
    "REVIEW_CRAFT": ROOT / "evals/prompts/review-craft.md",
    "ORDINARY_PROMPT": ROOT / "evals/prompts/ordinary-review.md",
    "RISK_LENS_REVIEW": ROOT / "evals/prompts/risk-lens-review.md",
    "REVIEW_CRAFT_EVIDENCE_LOOP": ROOT / "evals/prompts/review-craft-evidence-loop.md",
}
ABLATION_LIMITATIONS = {
    "v1": (
        (
            "This snapshot reports one matched four-arm run over the bound suite; it does not "
            "establish stability across reruns, models, repositories, or environments."
        ),
        (
            "The percentage-point deltas are narrow observations for the bound prompts, "
            "fixtures, host, and adjudication protocol, not a general causal claim."
        ),
        (
            "The fixtures are controlled evaluation cases. Adjudication withholds treatment "
            "labels and raw prompts, but outputs or tool traces may still reveal intervention "
            "characteristics; this is not guaranteed evaluator blinding."
        ),
    ),
    "v2": (
        (
            "This snapshot reports one matched three-arm run over the bound suite; it does not "
            "establish stability across reruns, models, repositories, or environments."
        ),
        (
            "The percentage-point deltas are narrow observations for the bound prompts, "
            "fixtures, host, and adjudication protocol, not a general causal claim; the "
            "B-to-C delta adds both Review Craft skill instructions and verifier feedback, "
            "so it does not isolate either contribution."
        ),
        (
            "The fixtures are controlled evaluation cases. Adjudication withholds treatment "
            "labels and raw prompts, but outputs or tool traces may still reveal intervention "
            "characteristics; this is not guaranteed evaluator blinding."
        ),
    ),
}
SENSITIVE_ARGUMENT = re.compile(
    r"^--?(?:api[-_]?key|password|secret|token)(?:=|$)", re.IGNORECASE
)
USAGE_OUTPUT_ENV = "REVIEW_CRAFT_EVAL_USAGE_OUTPUT"


def _validate_adapter_command(command: list[str]) -> None:
    if not command:
        raise EvalError("--adapter-command requires at least one argv item")
    for argument in command:
        if SENSITIVE_ARGUMENT.match(argument):
            raise EvalError("adapter command must not contain credential-bearing arguments")


def _describe_adapter(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        [*command, "--describe"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise EvalError(f"adapter describe failed: {detail}")
    try:
        description = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise EvalError(f"adapter describe returned invalid JSON: {error}") from error
    errors = schema_errors(description, ADAPTER_SCHEMA)
    if errors:
        raise EvalError("adapter description is invalid: " + "; ".join(errors))
    return description


def _adapter_command(args: argparse.Namespace) -> list[str]:
    command = list(args.adapter_command)
    if command and command[0] == "--":
        command = command[1:]
    _validate_adapter_command(command)
    return command


def command_run_remediation_safety(args: argparse.Namespace) -> int:
    adapter_command = _adapter_command(args)
    adapter = _describe_adapter(adapter_command)
    run_dir, payload = run_remediation_safety(
        suite_path=Path(args.suite).expanduser().resolve(strict=True),
        skill_root=Path(args.skill_root).expanduser().resolve(strict=True),
        output_root=Path(args.output_root).expanduser().resolve(),
        requested_cases=args.case,
        rounds=args.rounds,
        timeout_seconds=args.case_timeout,
        adapter_command=adapter_command,
        adapter=adapter,
    )
    print(
        json.dumps(
            {
                "runDir": str(run_dir),
                "runId": payload["runId"],
                "status": payload["status"],
                "metrics": payload["metrics"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if payload["status"] == "COMPLETED" else 2


def command_validate_remediation_safety(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve(strict=True)
    errors = validate_remediation_run(run_dir)
    if errors:
        print("review-craft remediation-safety validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 2
    payload = read_json(run_dir / "result.json")
    print(
        json.dumps(
            {
                "valid": True,
                "runId": payload["runId"],
                "status": payload["status"],
                "contentSha256": payload["contentSha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _prompt_for_treatment(treatment: str) -> Path:
    if treatment == "CODEX_NATIVE_REVIEW":
        raise EvalError("CODEX_NATIVE_REVIEW requires a future diff-aware adapter")
    return PROMPTS[treatment]


def _render_case_prompt(template: bytes, case: dict[str, Any], treatment: str) -> bytes:
    text = template.decode("utf-8")
    lens_marker = "{{RISK_LENS_JSON}}"
    verification_marker = "{{VERIFICATION_JSON}}"
    if treatment in {"RISK_LENS_REVIEW", "REVIEW_CRAFT_EVIDENCE_LOOP"}:
        lens = case.get("riskLens")
        if not isinstance(lens, dict):
            raise EvalError(f"case {case['id']}: treatment requires a risk lens")
        text = text.replace(
            lens_marker,
            json.dumps(lens, ensure_ascii=False, indent=2, sort_keys=True),
        )
    elif lens_marker in text:
        raise EvalError(f"treatment {treatment} unexpectedly requires a risk lens")
    if treatment == "REVIEW_CRAFT_EVIDENCE_LOOP":
        verification = case.get("verification")
        if not isinstance(verification, dict):
            raise EvalError(f"case {case['id']}: treatment requires verification guidance")
        text = text.replace(
            verification_marker,
            json.dumps(verification, ensure_ascii=False, indent=2, sort_keys=True),
        )
    elif verification_marker in text:
        raise EvalError(f"treatment {treatment} unexpectedly requires verification guidance")
    if lens_marker in text or verification_marker in text:
        raise EvalError(f"case {case['id']}: unresolved prompt template marker")
    return text.encode("utf-8")


def _case_record(
    *,
    run_dir: Path,
    ordinal: int,
    case: dict[str, Any],
    fixture_source: Path,
    skill_root: Path,
    prompt: bytes,
    adapter_command: list[str],
    treatment: str,
    timeout_seconds: int,
    stage_root: Path,
    evidence_root: Path | None = None,
    capture_tool_trace: bool = False,
    workspace_key: str | None = None,
) -> dict[str, Any]:
    prefix = f"cases/{ordinal:03d}"
    fixture_relative = f"fixtures/{ordinal:03d}"
    fixture_artifact = run_dir / fixture_relative
    shutil.copytree(fixture_source, fixture_artifact)
    prompt_relative = f"{prefix}/prompt.md"
    stdout_relative = f"{prefix}/stdout.txt"
    stderr_relative = f"{prefix}/stderr.txt"
    output_relative = f"{prefix}/output.json"
    usage_relative = f"{prefix}/usage.json"
    tool_trace_relative = f"{prefix}/tool-trace.json"
    prompt_path = run_dir / prompt_relative
    stdout_path = run_dir / stdout_relative
    stderr_path = run_dir / stderr_relative
    output_path = run_dir / output_relative
    usage_path = run_dir / usage_relative
    tool_trace_path = run_dir / tool_trace_relative
    write_bytes(prompt_path, prompt)

    workspace = stage_root / (workspace_key or f"case-{ordinal:03d}")
    staged_fixture = workspace / "target"
    staged_skill = workspace / "skill"
    adapter_usage_path = workspace / "adapter-usage.json"
    shutil.copytree(fixture_artifact, staged_fixture)
    if treatment in {"REVIEW_CRAFT", "REVIEW_CRAFT_EVIDENCE_LOOP"}:
        shutil.copytree(
            skill_root,
            staged_skill,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    else:
        staged_skill.mkdir()

    command = [
        *adapter_command,
        "--fixture-root",
        str(staged_fixture),
        "--skill-root",
        str(staged_skill),
        "--prompt-file",
        str(prompt_path),
        "--output-schema",
        str(HOST_OUTPUT_SCHEMA),
        "--output-file",
        str(output_path),
        "--treatment",
        treatment,
        "--case-id",
        case["id"],
    ]
    staged_evidence = None
    if evidence_root is not None:
        staged_evidence = workspace / "evidence"
        shutil.copytree(
            evidence_root,
            staged_evidence,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        command.extend(["--evidence-root", str(staged_evidence)])
    started = time.monotonic()
    status = "FAILED"
    exit_code: int | None = None
    failure_reason = ""
    stdout = b""
    stderr = b""
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            timeout=timeout_seconds,
            env={
                **os.environ,
                USAGE_OUTPUT_ENV: str(adapter_usage_path),
                **(
                    {"REVIEW_CRAFT_EVAL_TOOL_TRACE_OUTPUT": str(tool_trace_path)}
                    if capture_tool_trace
                    else {}
                ),
            },
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        if exit_code != 0:
            failure_reason = f"adapter exited with code {exit_code}"
        elif not output_path.is_file():
            failure_reason = "adapter did not create the normalized output artifact"
        else:
            try:
                output = read_json(output_path)
            except (OSError, json.JSONDecodeError) as error:
                failure_reason = f"normalized output is not valid JSON: {error}"
            else:
                output_errors = schema_errors(output, HOST_OUTPUT_SCHEMA)
                if output_errors:
                    failure_reason = "normalized output schema failed: " + "; ".join(
                        output_errors
                    )
                elif len(output["decisions"]) != len(set(output["decisions"])):
                    failure_reason = "normalized output contains duplicate decisions"
                else:
                    invalid_ranges = [
                        location
                        for location in output["locations"]
                        if location["lineEnd"] < location["lineStart"]
                    ]
                    if invalid_ranges:
                        failure_reason = "normalized output contains an inverted line range"
                    else:
                        status = "COMPLETED"
    except FileNotFoundError as error:
        status = "UNAVAILABLE"
        failure_reason = f"adapter executable unavailable: {error}"
        stderr = (failure_reason + "\n").encode("utf-8")
    except subprocess.TimeoutExpired as error:
        failure_reason = f"adapter timed out after {timeout_seconds} seconds"
        stdout = error.stdout or b""
        stderr = (error.stderr or b"") + (failure_reason + "\n").encode("utf-8")

    duration_ms = max(0, round((time.monotonic() - started) * 1000))
    write_bytes(stdout_path, stdout)
    write_bytes(stderr_path, stderr)
    usage = unavailable_usage("ADAPTER_DID_NOT_REPORT_USAGE")
    if adapter_usage_path.is_file():
        try:
            reported_usage = read_json(adapter_usage_path)
        except (OSError, json.JSONDecodeError):
            usage = unavailable_usage("ADAPTER_USAGE_INVALID")
        else:
            usage_errors = validate_usage_record(reported_usage)
            usage = (
                unavailable_usage("ADAPTER_USAGE_INVALID")
                if usage_errors
                else reported_usage
            )
    write_json(usage_path, usage)
    verification_executed = False
    verification_exit_code = None
    if capture_tool_trace:
        if not tool_trace_path.is_file():
            write_json(
                tool_trace_path,
                {"schema": "review-craft.eval-tool-trace.v1", "items": []},
            )
            if not failure_reason:
                failure_reason = "adapter did not create the tool trace artifact"
                status = "FAILED"
        else:
            try:
                tool_trace = read_json(tool_trace_path)
            except (OSError, json.JSONDecodeError) as error:
                failure_reason = f"tool trace is not valid JSON: {error}"
                status = "FAILED"
                tool_trace = {"schema": "review-craft.eval-tool-trace.v1", "items": []}
                write_json(tool_trace_path, tool_trace)
            trace_errors = schema_errors(tool_trace, TOOL_TRACE_SCHEMA)
            if trace_errors:
                failure_reason = "tool trace schema failed: " + "; ".join(trace_errors)
                status = "FAILED"
            marker = f"--case {case['id']}"
            matched = [
                item
                for item in tool_trace.get("items", [])
                if item.get("type") == "commandExecution"
                and marker in item.get("command", "")
            ]
            if matched:
                verification_executed = True
                verification_exit_code = matched[-1].get("exitCode")
    adapter_output_artifact = output_relative if output_path.is_file() else None
    adapter_output_hash = (
        file_hash(output_path) if adapter_output_artifact is not None else None
    )
    output_artifact = adapter_output_artifact if status == "COMPLETED" else None
    output_hash = adapter_output_hash if status == "COMPLETED" else None
    record = {
        "id": case["id"],
        "class": case["class"],
        "fixtureArtifact": fixture_relative,
        "fixtureTreeSha256": tree_sha256(fixture_artifact),
        "promptArtifact": prompt_relative,
        "promptSha256": file_hash(prompt_path),
        "status": status,
        "durationMs": duration_ms,
        "exitCode": exit_code,
        "stdoutArtifact": stdout_relative,
        "stdoutSha256": file_hash(stdout_path),
        "stderrArtifact": stderr_relative,
        "stderrSha256": file_hash(stderr_path),
        "adapterOutputArtifact": adapter_output_artifact,
        "adapterOutputSha256": adapter_output_hash,
        "normalizedOutputArtifact": output_artifact,
        "normalizedOutputSha256": output_hash,
        "failureReason": failure_reason,
        "usageArtifact": usage_relative,
        "usageSha256": file_hash(usage_path),
        "usage": usage,
    }
    if capture_tool_trace:
        record.update(
            {
                "toolTraceArtifact": tool_trace_relative,
                "toolTraceSha256": file_hash(tool_trace_path),
                "verificationExecuted": verification_executed,
                "verificationExitCode": verification_exit_code,
            }
        )
    return record


def _new_run_dir(output_root: Path, context_hash: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"rce-{stamp}-{context_hash[:12]}"
    run_dir = output_root / run_id
    suffix = 2
    while run_dir.exists():
        run_dir = output_root / f"{run_id}-{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True, mode=0o700)
    return run_dir


def _new_ablation_dir(output_root: Path, context_hash: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ablation_id = f"rca-{stamp}-{context_hash[:12]}"
    ablation_dir = output_root / ablation_id
    suffix = 2
    while ablation_dir.exists():
        ablation_dir = output_root / f"{ablation_id}-{suffix}"
        suffix += 1
    ablation_dir.mkdir(parents=True, mode=0o700)
    return ablation_dir


def _assert_prompt_no_answer_leak(
    case: dict[str, Any], treatment: str, prompt: bytes
) -> None:
    text = prompt.decode("utf-8")
    hidden_values = [
        case["seededIssue"],
        case["evidenceRequirement"],
        *case["expectedLocations"],
    ]
    for value in hidden_values:
        if value in text:
            raise EvalError(f"case {case['id']}: generated prompt leaks hidden evaluation data")
    lens_prompt = case["riskLens"]["prompt"]
    lens_expected = treatment in {
        "RISK_LENS_REVIEW",
        "REVIEW_CRAFT_EVIDENCE_LOOP",
    }
    if (lens_prompt in text) != lens_expected:
        raise EvalError(f"case {case['id']}: risk lens treatment boundary is invalid")
    verification_token = case["id"]
    verification_expected = treatment == "REVIEW_CRAFT_EVIDENCE_LOOP"
    if (verification_token in text) != verification_expected:
        raise EvalError(f"case {case['id']}: verification treatment boundary is invalid")


def command_run_ablation(args: argparse.Namespace) -> int:
    suite_path = Path(args.suite).expanduser().resolve(strict=True)
    skill_root = Path(args.skill_root).expanduser().resolve(strict=True)
    evidence_root = Path(args.evidence_root).expanduser().resolve(strict=True)
    output_root = Path(args.output_root).expanduser().resolve()
    adapter_command = list(args.adapter_command)
    if adapter_command and adapter_command[0] == "--":
        adapter_command = adapter_command[1:]
    _validate_adapter_command(adapter_command)
    adapter = _describe_adapter(adapter_command)
    suite = read_json(suite_path)
    suite_errors = validate_eval_suite(suite)
    if suite_errors:
        raise EvalError("evaluation suite is invalid: " + "; ".join(suite_errors))
    if suite["schema"] != "review-craft.eval-cases.v2":
        raise EvalError("run-ablation requires review-craft.eval-cases.v2")
    all_cases = suite["cases"]
    case_ids = [case["id"] for case in all_cases]
    requested = args.case or case_ids
    unknown = sorted(set(requested) - set(case_ids))
    if unknown:
        raise EvalError(f"unknown evaluation cases: {', '.join(unknown)}")
    requested_set = set(requested)
    selected_ids = [case_id for case_id in case_ids if case_id in requested_set]
    selected = [case for case in all_cases if case["id"] in requested_set]
    prompt_templates = {
        treatment: _prompt_for_treatment(treatment) for treatment in ABLATION_TREATMENTS
    }
    prompt_bytes = {
        treatment: path.read_bytes() for treatment, path in prompt_templates.items()
    }
    source = source_metadata()
    context_hash = sha256_json(
        {
            "adapter": adapter,
            "command": adapter_command,
            "source": source,
            "suite": sha256_bytes(suite_path.read_bytes()),
            "skill": tree_sha256(skill_root),
            "evidence": tree_sha256(evidence_root),
            "prompts": {
                treatment: sha256_bytes(value) for treatment, value in prompt_bytes.items()
            },
            "selection": selected_ids,
            "treatments": ABLATION_TREATMENTS,
        }
    )
    ablation_dir = _new_ablation_dir(output_root, context_hash)
    ablation_id = ablation_dir.name
    started_at = utc_now()
    suite_artifact = ablation_dir / "suite.json"
    schedule_artifact = ablation_dir / "schedule.json"
    shutil.copyfile(suite_path, suite_artifact)
    schedule = {
        "schema": "review-craft.eval-ablation-schedule.v2",
        "ablationId": ablation_id,
        "treatments": list(ABLATION_TREATMENTS),
        "cases": [
            {
                "id": case["id"],
                "order": list(
                    ABLATION_TREATMENTS[index % len(ABLATION_TREATMENTS) :]
                    + ABLATION_TREATMENTS[: index % len(ABLATION_TREATMENTS)]
                ),
            }
            for index, case in enumerate(selected)
        ],
    }
    schedule_errors = validate_ablation_schedule(schedule)
    if schedule_errors:
        raise EvalError("generated ablation schedule is invalid: " + "; ".join(schedule_errors))
    write_json(schedule_artifact, schedule)

    run_dirs = {}
    records = {treatment: [] for treatment in ABLATION_TREATMENTS}
    run_started = {treatment: utc_now() for treatment in ABLATION_TREATMENTS}
    for treatment in ABLATION_TREATMENTS:
        run_dir = _new_run_dir(
            ablation_dir,
            sha256_json({"ablationId": ablation_id, "treatment": treatment}),
        )
        run_dirs[treatment] = run_dir
        shutil.copyfile(suite_path, run_dir / "suite.json")
        shutil.copyfile(prompt_templates[treatment], run_dir / "prompt-template.md")
        shutil.copyfile(schedule_artifact, run_dir / "schedule.json")

    with tempfile.TemporaryDirectory(prefix="review-craft-ablation-stage-") as stage:
        stage_root = Path(stage)
        cases_by_id = {case["id"]: case for case in selected}
        for ordinal, scheduled in enumerate(schedule["cases"], start=1):
            case = cases_by_id[scheduled["id"]]
            fixture_source = (ROOT / case["fixture"]).resolve(strict=True)
            for treatment in scheduled["order"]:
                prompt = _render_case_prompt(prompt_bytes[treatment], case, treatment)
                _assert_prompt_no_answer_leak(case, treatment, prompt)
                records[treatment].append(
                    _case_record(
                        run_dir=run_dirs[treatment],
                        ordinal=ordinal,
                        case=case,
                        fixture_source=fixture_source,
                        skill_root=skill_root,
                        prompt=prompt,
                        adapter_command=adapter_command,
                        treatment=treatment,
                        timeout_seconds=args.case_timeout,
                        stage_root=stage_root,
                        evidence_root=(
                            evidence_root
                            if treatment == "REVIEW_CRAFT_EVIDENCE_LOOP"
                            else None
                        ),
                        capture_tool_trace=True,
                        workspace_key=f"case-{ordinal:03d}-{treatment.lower()}",
                    )
                )

    completed_source = source_metadata()
    treatment_summaries = []
    for treatment in ABLATION_TREATMENTS:
        run_dir = run_dirs[treatment]
        run_source = {
            **source,
            "completedRevision": completed_source["revision"],
            "completedDirty": completed_source["dirty"],
            "completedDirtyFingerprint": completed_source["dirtyFingerprint"],
            "completedTreeSha256": completed_source["treeSha256"],
        }
        run_source["stableThroughoutRun"] = source_stable(run_source)
        payload = {
            "schema": "review-craft.eval-run.v4",
            "runId": run_dir.name,
            "status": overall_status(records[treatment]),
            "startedAt": run_started[treatment],
            "completedAt": utc_now(),
            "treatment": treatment,
            "source": run_source,
            "suite": {
                "artifact": "suite.json",
                "sha256": file_hash(run_dir / "suite.json"),
                "selectedCaseIds": selected_ids,
                "fullSuite": selected_ids == case_ids,
            },
            "skill": {
                "version": (skill_root / "VERSION").read_text(encoding="utf-8").strip(),
                "treeSha256": tree_sha256(skill_root),
            },
            "promptTemplate": {
                "artifact": "prompt-template.md",
                "sha256": file_hash(run_dir / "prompt-template.md"),
            },
            "ablation": {
                "id": ablation_id,
                "scheduleArtifact": "schedule.json",
                "scheduleSha256": file_hash(run_dir / "schedule.json"),
            },
            "adapter": {"description": adapter, "command": adapter_command},
            "caseTimeoutSeconds": args.case_timeout,
            "cases": records[treatment],
            "metrics": score_cases(run_dir, suite, records[treatment]),
            "goldenEligible": False,
            "contentSha256": "0" * 64,
        }
        payload["goldenEligible"] = golden_eligible(payload)
        payload["contentSha256"] = sha256_json(
            {key: value for key, value in payload.items() if key != "contentSha256"}
        )
        write_json(run_dir / "result.json", payload)
        run_errors = validate_run(run_dir)
        if run_errors:
            raise EvalError(
                f"generated {treatment} run is invalid: " + "; ".join(run_errors)
            )
        treatment_summaries.append(
            {
                "treatment": treatment,
                "runDir": run_dir.relative_to(ablation_dir).as_posix(),
                "runId": payload["runId"],
                "runContentSha256": payload["contentSha256"],
                "status": payload["status"],
                "goldenEligible": payload["goldenEligible"],
            }
        )
    manifest = {
        "schema": "review-craft.eval-ablation-run.v2",
        "ablationId": ablation_id,
        "status": overall_status(
            [{"status": summary["status"]} for summary in treatment_summaries]
        ),
        "startedAt": started_at,
        "completedAt": utc_now(),
        "suite": {"artifact": "suite.json", "sha256": file_hash(suite_artifact)},
        "schedule": {
            "artifact": "schedule.json",
            "sha256": file_hash(schedule_artifact),
        },
        "treatments": treatment_summaries,
        "contentSha256": "0" * 64,
    }
    manifest["contentSha256"] = sha256_json(
        {key: value for key, value in manifest.items() if key != "contentSha256"}
    )
    write_json(ablation_dir / "ablation.json", manifest)
    errors = validate_ablation_run(ablation_dir)
    if errors:
        raise EvalError("generated ablation is invalid: " + "; ".join(errors))
    print(
        json.dumps(
            {
                "ablationDir": str(ablation_dir),
                "ablationId": ablation_id,
                "status": manifest["status"],
                "treatments": treatment_summaries,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if manifest["status"] == "COMPLETED" else 2


def command_validate_ablation(args: argparse.Namespace) -> int:
    ablation_dir = Path(args.ablation_dir).expanduser().resolve(strict=True)
    errors = validate_ablation_run(ablation_dir)
    if errors:
        raise EvalError("ablation validation failed: " + "; ".join(errors))
    manifest = read_json(ablation_dir / "ablation.json")
    print(
        json.dumps(
            {
                "valid": True,
                "ablationId": manifest["ablationId"],
                "status": manifest["status"],
                "contentSha256": manifest["contentSha256"],
            },
            sort_keys=True,
        )
    )
    return 0


def command_prepare_ablation_adjudication(args: argparse.Namespace) -> int:
    ablation_dir = Path(args.ablation_dir).expanduser().resolve(strict=True)
    bundle, template = build_ablation_adjudication_template(
        ablation_dir,
        kind=args.kind,
        protocol=args.protocol,
    )
    bundle_path = Path(args.bundle_output).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    write_json(bundle_path, bundle)
    write_json(output_path, template)
    print(
        json.dumps(
            {
                "ablationId": template["ablationId"],
                "bundle": str(bundle_path),
                "bundleContentSha256": bundle["contentSha256"],
                "adjudication": str(output_path),
                "sampleCount": len(template["samples"]),
            },
            sort_keys=True,
        )
    )
    return 0


def command_adjudicate_ablation(args: argparse.Namespace) -> int:
    ablation_dir = Path(args.ablation_dir).expanduser().resolve(strict=True)
    bundle = read_json(Path(args.bundle).expanduser().resolve(strict=True))
    adjudication = read_json(Path(args.adjudication).expanduser().resolve(strict=True))
    result = build_ablation_adjudication_result(ablation_dir, bundle, adjudication)
    _write_and_print(result, args.output)
    return 0


def command_validate_ablation_adjudication(args: argparse.Namespace) -> int:
    ablation_dir = Path(args.ablation_dir).expanduser().resolve(strict=True)
    bundle = read_json(Path(args.bundle).expanduser().resolve(strict=True))
    result = read_json(Path(args.result).expanduser().resolve(strict=True))
    errors = validate_ablation_adjudication_result(ablation_dir, bundle, result)
    if errors:
        raise EvalError("ablation adjudication validation failed: " + "; ".join(errors))
    print(
        json.dumps(
            {
                "valid": True,
                "ablationId": result["ablation"]["id"],
                "contentSha256": result["contentSha256"],
                "semanticEvidenceValidation": {
                    row["treatment"]: row["metrics"]["semanticEvidenceValidation"]
                    for row in result["treatments"]
                },
            },
            sort_keys=True,
        )
    )
    return 0


def _validated_ablation_adjudication(
    ablation_dir: Path,
    bundle_path: Path,
    result_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    bundle = read_json(bundle_path)
    result = read_json(result_path)
    errors = validate_ablation_adjudication_result(ablation_dir, bundle, result)
    if errors:
        raise EvalError("ablation adjudication is invalid: " + "; ".join(errors))
    return bundle, result


def _ablation_runs(
    ablation_dir: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    errors = validate_ablation_run(ablation_dir)
    if errors:
        raise EvalError("ablation is invalid: " + "; ".join(errors))
    manifest = read_json(ablation_dir / "ablation.json")
    runs = {
        summary["treatment"]: read_json(
            safe_artifact(ablation_dir, summary["runDir"]) / "result.json"
        )
        for summary in manifest["treatments"]
    }
    _, protocol = ablation_protocol(manifest["schema"])
    if list(runs) != list(protocol["treatments"]):
        raise EvalError("ablation treatments are not in canonical protocol order")
    return manifest, runs


def _cost_ratio(numerator: Any, denominator: Any) -> float | None:
    if (
        isinstance(numerator, bool)
        or isinstance(denominator, bool)
        or not isinstance(numerator, (int, float))
        or not isinstance(denominator, (int, float))
        or denominator == 0
    ):
        return None
    return round(numerator / denominator, 4)


def _cost_values(run: dict[str, Any]) -> dict[str, int | None]:
    usage = run["metrics"].get("usage", {})
    reported = usage.get("reportedUsage") if isinstance(usage, dict) else None
    return {
        "duration": run["metrics"].get("totalDurationMs"),
        "totalTokens": reported.get("totalTokens") if isinstance(reported, dict) else None,
        "toolCalls": (
            reported.get("toolCalls", {}).get("total")
            if isinstance(reported, dict)
            else None
        ),
    }


def _cost_ratios(to_run: dict[str, Any], from_run: dict[str, Any]) -> dict[str, Any]:
    to_values = _cost_values(to_run)
    from_values = _cost_values(from_run)
    return {
        "durationRatio": _cost_ratio(to_values["duration"], from_values["duration"]),
        "totalTokensRatio": _cost_ratio(
            to_values["totalTokens"], from_values["totalTokens"]
        ),
        "toolCallsRatio": _cost_ratio(
            to_values["toolCalls"], from_values["toolCalls"]
        ),
    }


def build_ablation_comparison(
    ablation_dir: Path,
    bundle: dict[str, Any],
    adjudication: dict[str, Any],
) -> dict[str, Any]:
    manifest, runs = _ablation_runs(ablation_dir)
    version, protocol = ablation_protocol(manifest["schema"])
    treatments = protocol["treatments"]
    adjudication_errors = validate_ablation_adjudication_result(
        ablation_dir, bundle, adjudication
    )
    if adjudication_errors:
        raise EvalError(
            "ablation adjudication is invalid: " + "; ".join(adjudication_errors)
        )
    reference_fields = _comparison_fields(runs[treatments[0]])
    for treatment in treatments[1:]:
        fields = _comparison_fields(runs[treatment])
        mismatches = [
            field for field in reference_fields if fields[field] != reference_fields[field]
        ]
        if mismatches:
            raise EvalError(
                f"ablation run {treatment} is not matched: " + ", ".join(mismatches)
            )
    adjudication_by_treatment = {
        row["treatment"]: row for row in adjudication["treatments"]
    }
    if list(adjudication_by_treatment) != list(treatments):
        raise EvalError(
            "ablation adjudication treatments are not in canonical protocol order"
        )
    arms = []
    for treatment in treatments:
        run = runs[treatment]
        semantic = adjudication_by_treatment[treatment]
        if (
            semantic["runId"] != run["runId"]
            or semantic["runContentSha256"] != run["contentSha256"]
        ):
            raise EvalError(f"ablation adjudication {treatment} does not bind its child run")
        arms.append(
            {
                "treatment": treatment,
                "runId": run["runId"],
                "runContentSha256": run["contentSha256"],
                "goldenEligible": run["goldenEligible"],
                "structuralMetrics": run["metrics"],
                "adjudicationContentSha256": adjudication["contentSha256"],
                "semanticMetrics": semantic["metrics"],
            }
        )
    deltas = []
    for delta_id, from_treatment, to_treatment in protocol["deltas"]:
        from_run = runs[from_treatment]
        to_run = runs[to_treatment]
        deltas.append(
            {
                "id": delta_id,
                "from": from_treatment,
                "to": to_treatment,
                "structuralPercentagePoints": _metric_deltas(
                    to_run["metrics"], from_run["metrics"]
                ),
                "semanticPercentagePoints": _metric_deltas(
                    adjudication_by_treatment[to_treatment]["metrics"],
                    adjudication_by_treatment[from_treatment]["metrics"],
                ),
                "cost": _cost_ratios(to_run, from_run),
            }
        )
    payload = {
        "schema": f"review-craft.eval-ablation-comparison.{version}",
        "matched": True,
        "comparativeEligible": bool(
            all(arm["goldenEligible"] for arm in arms)
            and all(
                arm["semanticMetrics"]["semanticEvidenceValidation"] == "ADJUDICATED"
                for arm in arms
            )
        ),
        "ablation": {
            "id": manifest["ablationId"],
            "contentSha256": manifest["contentSha256"],
        },
        "matchedFields": reference_fields,
        "adjudicator": adjudication["adjudicator"],
        "arms": arms,
        "deltas": deltas,
        "contentSha256": "0" * 64,
    }
    payload["contentSha256"] = sha256_json(
        {key: value for key, value in payload.items() if key != "contentSha256"}
    )
    errors = validate_ablation_comparison(payload)
    if errors:
        raise EvalError("generated ablation comparison is invalid: " + "; ".join(errors))
    return payload


def build_ablation_snapshot(
    ablation_dir: Path,
    bundle: dict[str, Any],
    adjudication: dict[str, Any],
) -> dict[str, Any]:
    comparison = build_ablation_comparison(ablation_dir, bundle, adjudication)
    if not comparison["comparativeEligible"]:
        raise EvalError(
            "ablation export requires all matched child runs to be Golden-eligible "
            "with complete adjudication"
        )
    manifest, runs = _ablation_runs(ablation_dir)
    version, protocol = ablation_protocol(manifest["schema"])
    reference = runs[protocol["treatments"][0]]
    description = reference["adapter"]["description"]
    provider = description["provider"]
    snapshot = {
        "schema": f"review-craft.eval-ablation-snapshot.{version}",
        "source": {
            "revision": reference["source"]["revision"],
            "treeSha256": reference["source"]["treeSha256"],
            "runnerSha256": reference["source"]["runnerSha256"],
            "dirty": reference["source"]["dirty"],
            "stableThroughoutRun": reference["source"]["stableThroughoutRun"],
        },
        "suite": {
            "sha256": reference["suite"]["sha256"],
            "selectedCaseIds": reference["suite"]["selectedCaseIds"],
            "fullSuite": reference["suite"]["fullSuite"],
        },
        "skill": reference["skill"],
        "host": {
            "name": description["name"],
            "version": description["version"],
            "model": description["model"],
            "reasoning": description["reasoning"],
            "adapterVersion": description["adapterVersion"],
            "evidenceKind": description["evidenceKind"],
            "provider": {
                "name": provider["name"],
                "wireApi": provider["wireApi"],
                "requiresOpenAIAuth": provider["requiresOpenAIAuth"],
                "supportsWebsockets": provider["supportsWebsockets"],
            },
            "isolation": description["isolation"],
        },
        "ablation": {
            "id": manifest["ablationId"],
            "contentSha256": manifest["contentSha256"],
            "scheduleSha256": manifest["schedule"]["sha256"],
        },
        "comparison": {
            "contentSha256": comparison["contentSha256"],
            "matched": comparison["matched"],
            "comparativeEligible": comparison["comparativeEligible"],
            "adjudicator": comparison["adjudicator"],
        },
        "arms": comparison["arms"],
        "deltas": comparison["deltas"],
        "limitations": list(ABLATION_LIMITATIONS[version]),
        "contentSha256": "0" * 64,
    }
    snapshot["contentSha256"] = sha256_json(
        {key: value for key, value in snapshot.items() if key != "contentSha256"}
    )
    errors = validate_ablation_snapshot(snapshot)
    if errors:
        raise EvalError("generated ablation snapshot is invalid: " + "; ".join(errors))
    return snapshot


def command_compare_ablation(args: argparse.Namespace) -> int:
    ablation_dir = Path(args.ablation_dir).expanduser().resolve(strict=True)
    bundle, adjudication = _validated_ablation_adjudication(
        ablation_dir,
        Path(args.bundle).expanduser().resolve(strict=True),
        Path(args.adjudication_result).expanduser().resolve(strict=True),
    )
    comparison = build_ablation_comparison(ablation_dir, bundle, adjudication)
    _write_and_print(comparison, args.output)
    return 0


def command_export_ablation(args: argparse.Namespace) -> int:
    ablation_dir = Path(args.ablation_dir).expanduser().resolve(strict=True)
    bundle, adjudication = _validated_ablation_adjudication(
        ablation_dir,
        Path(args.bundle).expanduser().resolve(strict=True),
        Path(args.adjudication_result).expanduser().resolve(strict=True),
    )
    snapshot = build_ablation_snapshot(ablation_dir, bundle, adjudication)
    _write_and_print(snapshot, args.output)
    return 0


def command_run(args: argparse.Namespace) -> int:
    suite_path = Path(args.suite).expanduser().resolve(strict=True)
    skill_root = Path(args.skill_root).expanduser().resolve(strict=True)
    output_root = Path(args.output_root).expanduser().resolve()
    adapter_command = list(args.adapter_command)
    if adapter_command and adapter_command[0] == "--":
        adapter_command = adapter_command[1:]
    _validate_adapter_command(adapter_command)
    adapter = _describe_adapter(adapter_command)
    suite = read_json(suite_path)
    suite_validation = validate_eval_suite(suite)
    if suite_validation:
        raise EvalError("evaluation suite is invalid: " + "; ".join(suite_validation))
    all_cases = suite["cases"]
    case_ids = [case["id"] for case in all_cases]
    if len(set(case_ids)) != len(case_ids):
        raise EvalError("evaluation suite contains duplicate case ids")
    requested = args.case or case_ids
    unknown = sorted(set(requested) - set(case_ids))
    if unknown:
        raise EvalError(f"unknown evaluation cases: {', '.join(unknown)}")
    requested_set = set(requested)
    selected_ids = [case_id for case_id in case_ids if case_id in requested_set]
    selected = [case for case in all_cases if case["id"] in requested_set]
    prompt_template = _prompt_for_treatment(args.treatment)
    prompt = prompt_template.read_bytes()
    source = source_metadata()
    context_hash = sha256_json(
        {
            "adapter": adapter,
            "command": adapter_command,
            "source": source,
            "suite": sha256_bytes(suite_path.read_bytes()),
            "skill": tree_sha256(skill_root),
            "prompt": sha256_bytes(prompt),
            "selection": selected_ids,
            "treatment": args.treatment,
        }
    )
    run_dir = _new_run_dir(output_root, context_hash)
    suite_artifact = run_dir / "suite.json"
    prompt_artifact = run_dir / "prompt-template.md"
    shutil.copyfile(suite_path, suite_artifact)
    write_bytes(prompt_artifact, prompt)
    started_at = utc_now()
    records = []
    with tempfile.TemporaryDirectory(prefix="review-craft-eval-stage-") as stage:
        stage_root = Path(stage)
        for ordinal, case in enumerate(selected, start=1):
            fixture_source = (ROOT / case["fixture"]).resolve(strict=True)
            records.append(
                _case_record(
                    run_dir=run_dir,
                    ordinal=ordinal,
                    case=case,
                    fixture_source=fixture_source,
                    skill_root=skill_root,
                    prompt=prompt,
                    adapter_command=adapter_command,
                    treatment=args.treatment,
                    timeout_seconds=args.case_timeout,
                    stage_root=stage_root,
                )
            )
    completed_source = source_metadata()
    source.update(
        {
            "completedRevision": completed_source["revision"],
            "completedDirty": completed_source["dirty"],
            "completedDirtyFingerprint": completed_source["dirtyFingerprint"],
            "completedTreeSha256": completed_source["treeSha256"],
        }
    )
    source["stableThroughoutRun"] = source_stable(source)
    payload = {
        "schema": "review-craft.eval-run.v3",
        "runId": run_dir.name,
        "status": overall_status(records),
        "startedAt": started_at,
        "completedAt": utc_now(),
        "treatment": args.treatment,
        "source": source,
        "suite": {
            "artifact": "suite.json",
            "sha256": file_hash(suite_artifact),
            "selectedCaseIds": selected_ids,
            "fullSuite": selected_ids == case_ids,
        },
        "skill": {
            "version": (skill_root / "VERSION").read_text(encoding="utf-8").strip(),
            "treeSha256": tree_sha256(skill_root),
        },
        "promptTemplate": {
            "artifact": "prompt-template.md",
            "sha256": file_hash(prompt_artifact),
        },
        "adapter": {
            "description": adapter,
            "command": adapter_command,
        },
        "caseTimeoutSeconds": args.case_timeout,
        "cases": records,
        "metrics": score_cases(run_dir, suite, records),
        "goldenEligible": False,
        "contentSha256": "0" * 64,
    }
    payload["goldenEligible"] = golden_eligible(payload)
    payload["contentSha256"] = sha256_json(
        {key: value for key, value in payload.items() if key != "contentSha256"}
    )
    write_json(run_dir / "result.json", payload)
    errors = validate_run(run_dir)
    if errors:
        raise EvalError("generated evaluation run is invalid: " + "; ".join(errors))
    print(
        json.dumps(
            {
                "runDir": str(run_dir),
                "runId": payload["runId"],
                "status": payload["status"],
                "goldenEligible": payload["goldenEligible"],
                "metrics": payload["metrics"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if payload["status"] == "COMPLETED" else 2


def command_validate(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve(strict=True)
    errors = validate_run(run_dir)
    if errors:
        print("review-craft eval validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 2
    payload = read_json(run_dir / "result.json")
    print(
        json.dumps(
            {
                "valid": True,
                "runId": payload["runId"],
                "status": payload["status"],
                "goldenEligible": payload["goldenEligible"],
                "contentSha256": payload["contentSha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def command_adjudicate(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve(strict=True)
    adjudication_path = Path(args.adjudication).expanduser().resolve(strict=True)
    adjudication = read_json(adjudication_path)
    result = build_adjudication_result(run_dir, adjudication)
    if args.output:
        write_json(Path(args.output).expanduser().resolve(), result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def command_prepare_adjudication(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve(strict=True)
    template = build_adjudication_template(
        run_dir,
        kind=args.kind,
        protocol=args.protocol,
    )
    if args.output:
        write_json(Path(args.output).expanduser().resolve(), template)
    print(json.dumps(template, ensure_ascii=False, sort_keys=True))
    return 0


def command_validate_adjudication(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve(strict=True)
    result_path = Path(args.result).expanduser().resolve(strict=True)
    payload = read_json(result_path)
    errors = validate_adjudication_result(run_dir, payload)
    if errors:
        print("review-craft semantic adjudication validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "valid": True,
                "runId": payload["run"]["id"],
                "semanticEvidenceValidation": payload["metrics"][
                    "semanticEvidenceValidation"
                ],
                "contentSha256": payload["contentSha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _comparison_fields(payload: dict[str, Any]) -> dict[str, Any]:
    description = payload["adapter"]["description"]
    return {
        "sourceRevision": payload["source"]["revision"],
        "sourceDirty": payload["source"]["dirty"],
        "sourceDirtyFingerprint": payload["source"]["dirtyFingerprint"],
        "sourceTreeSha256": payload["source"]["treeSha256"],
        "sourceRunnerSha256": payload["source"]["runnerSha256"],
        "sourceCompletedRevision": payload["source"]["completedRevision"],
        "sourceCompletedDirty": payload["source"]["completedDirty"],
        "sourceCompletedDirtyFingerprint": payload["source"]["completedDirtyFingerprint"],
        "sourceCompletedTreeSha256": payload["source"]["completedTreeSha256"],
        "sourceStableThroughoutRun": payload["source"]["stableThroughoutRun"],
        "suiteSha256": payload["suite"]["sha256"],
        "selectedCaseIds": payload["suite"]["selectedCaseIds"],
        "fullSuite": payload["suite"]["fullSuite"],
        "skillTreeSha256": payload["skill"]["treeSha256"],
        "hostName": description["name"],
        "hostVersion": description["version"],
        "model": description["model"],
        "reasoning": description["reasoning"],
        "adapterVersion": description["adapterVersion"],
        "evidenceKind": description["evidenceKind"],
        "provider": description["provider"],
        "isolation": description["isolation"],
        "adapterCommand": payload["adapter"]["command"],
        "caseTimeoutSeconds": payload["caseTimeoutSeconds"],
    }


def _metric_deltas(
    review_metrics: dict[str, Any], baseline_metrics: dict[str, Any]
) -> dict[str, float | None]:
    deltas = {}
    for metric, value in review_metrics.items():
        baseline_value = baseline_metrics.get(metric)
        if not metric.endswith("Percent"):
            continue
        deltas[metric] = (
            round(value - baseline_value, 2)
            if isinstance(value, (int, float))
            and isinstance(baseline_value, (int, float))
            else None
        )
    return deltas


def _validated_adjudication(run_dir: Path, result_path: Path, *, label: str) -> dict[str, Any]:
    payload = read_json(result_path)
    errors = validate_adjudication_result(run_dir, payload)
    if errors:
        raise EvalError(f"{label} adjudication is invalid: " + "; ".join(errors))
    return payload


def build_comparison_payload(
    review_dir: Path,
    baseline_dir: Path,
    *,
    review_adjudication_path: Path | None = None,
    baseline_adjudication_path: Path | None = None,
) -> dict[str, Any]:
    validation_errors = {
        "reviewCraft": validate_run(review_dir),
        "baseline": validate_run(baseline_dir),
    }
    if any(validation_errors.values()):
        detail = [
            f"{label}: {error}"
            for label, errors in validation_errors.items()
            for error in errors
        ]
        raise EvalError("invalid comparison input: " + "; ".join(detail))
    review = read_json(review_dir / "result.json")
    baseline = read_json(baseline_dir / "result.json")
    if review["treatment"] != "REVIEW_CRAFT":
        raise EvalError("review-craft run must use REVIEW_CRAFT treatment")
    if baseline["treatment"] != "ORDINARY_PROMPT":
        raise EvalError("baseline run must use ORDINARY_PROMPT treatment")
    review_fields = _comparison_fields(review)
    baseline_fields = _comparison_fields(baseline)
    mismatches = [
        field for field in review_fields if review_fields[field] != baseline_fields[field]
    ]
    if mismatches:
        raise EvalError("eval runs are not matched: " + ", ".join(mismatches))
    if (review_adjudication_path is None) != (baseline_adjudication_path is None):
        raise EvalError("semantic comparison requires both adjudication results or neither")

    comparative_eligible = bool(review["goldenEligible"] and baseline["goldenEligible"])
    semantic = None
    if review_adjudication_path is not None and baseline_adjudication_path is not None:
        review_adjudication = _validated_adjudication(
            review_dir, review_adjudication_path, label="Review Craft"
        )
        baseline_adjudication = _validated_adjudication(
            baseline_dir, baseline_adjudication_path, label="baseline"
        )
        if review_adjudication["adjudicator"] != baseline_adjudication["adjudicator"]:
            raise EvalError("semantic adjudications must use the same kind and protocol")
        semantic = {
            "comparativeEligible": bool(
                comparative_eligible
                and review_adjudication["metrics"]["semanticEvidenceValidation"]
                == "ADJUDICATED"
                and baseline_adjudication["metrics"]["semanticEvidenceValidation"]
                == "ADJUDICATED"
            ),
            "adjudicator": review_adjudication["adjudicator"],
            "reviewCraft": {
                "runId": review_adjudication["run"]["id"],
                "runContentSha256": review_adjudication["run"]["contentSha256"],
                "resultContentSha256": review_adjudication["contentSha256"],
                "metrics": review_adjudication["metrics"],
            },
            "baseline": {
                "runId": baseline_adjudication["run"]["id"],
                "runContentSha256": baseline_adjudication["run"]["contentSha256"],
                "resultContentSha256": baseline_adjudication["contentSha256"],
                "metrics": baseline_adjudication["metrics"],
            },
            "metricDeltaPercentagePoints": _metric_deltas(
                review_adjudication["metrics"], baseline_adjudication["metrics"]
            ),
        }

    payload = {
        "schema": "review-craft.eval-comparison.v1",
        "matched": True,
        "comparativeEligible": comparative_eligible,
        "reviewCraft": {
            "runId": review["runId"],
            "contentSha256": review["contentSha256"],
            "metrics": review["metrics"],
        },
        "baseline": {
            "runId": baseline["runId"],
            "contentSha256": baseline["contentSha256"],
            "metrics": baseline["metrics"],
        },
        "metricDeltaPercentagePoints": _metric_deltas(
            review["metrics"], baseline["metrics"]
        ),
        "matchedFields": review_fields,
        "semantic": semantic,
        "contentSha256": "0" * 64,
    }
    payload["contentSha256"] = sha256_json(
        {key: value for key, value in payload.items() if key != "contentSha256"}
    )
    errors = validate_comparison_payload(payload)
    if errors:
        raise EvalError("generated comparison is invalid: " + "; ".join(errors))
    return payload


def build_golden_snapshot(
    comparison: dict[str, Any], review: dict[str, Any], baseline: dict[str, Any]
) -> dict[str, Any]:
    comparison_errors = validate_comparison_payload(comparison)
    if comparison_errors:
        raise EvalError("Golden export comparison is invalid: " + "; ".join(comparison_errors))
    expected_fields = _comparison_fields(review)
    if comparison["matchedFields"] != expected_fields or _comparison_fields(
        baseline
    ) != expected_fields:
        raise EvalError("Golden export inputs do not match the bound comparison")
    for label, summary, run in (
        ("Review Craft", comparison["reviewCraft"], review),
        ("baseline", comparison["baseline"], baseline),
    ):
        expected_summary = {
            "runId": run["runId"],
            "contentSha256": run["contentSha256"],
            "metrics": run["metrics"],
        }
        if summary != expected_summary:
            raise EvalError(f"Golden export {label} run does not match the comparison")
    semantic = comparison["semantic"]
    if not comparison["comparativeEligible"]:
        raise EvalError("Golden export requires two comparative-eligible full runs")
    if not isinstance(semantic, dict) or not semantic["comparativeEligible"]:
        raise EvalError("Golden export requires complete matched semantic adjudications")
    if (
        semantic["reviewCraft"]["runId"] != review["runId"]
        or semantic["reviewCraft"]["runContentSha256"] != review["contentSha256"]
        or semantic["baseline"]["runId"] != baseline["runId"]
        or semantic["baseline"]["runContentSha256"] != baseline["contentSha256"]
    ):
        raise EvalError("Golden export semantic results do not match their bound runs")
    description = review["adapter"]["description"]
    provider = description["provider"]
    host = {
        "name": description["name"],
        "version": description["version"],
        "model": description["model"],
        "reasoning": description["reasoning"],
        "adapterVersion": description["adapterVersion"],
        "evidenceKind": description["evidenceKind"],
        "provider": {
            "name": provider["name"],
            "wireApi": provider["wireApi"],
            "requiresOpenAIAuth": provider["requiresOpenAIAuth"],
            "supportsWebsockets": provider["supportsWebsockets"],
        },
        "isolation": description["isolation"],
    }
    if "usage" in description:
        host["usage"] = description["usage"]
    snapshot = {
        "schema": "review-craft.eval-golden-snapshot.v1",
        "source": {
            "revision": review["source"]["revision"],
            "treeSha256": review["source"]["treeSha256"],
            "runnerSha256": review["source"]["runnerSha256"],
            "dirty": review["source"]["dirty"],
            "stableThroughoutRun": review["source"]["stableThroughoutRun"],
        },
        "suite": {
            "sha256": review["suite"]["sha256"],
            "selectedCaseIds": review["suite"]["selectedCaseIds"],
            "fullSuite": review["suite"]["fullSuite"],
        },
        "skill": review["skill"],
        "host": host,
        "comparison": {
            "contentSha256": comparison["contentSha256"],
            "comparativeEligible": comparison["comparativeEligible"],
        },
        "results": {
            "reviewCraft": {
                "runId": review["runId"],
                "contentSha256": review["contentSha256"],
                "treatment": review["treatment"],
                "goldenEligible": review["goldenEligible"],
                "metrics": review["metrics"],
            },
            "baseline": {
                "runId": baseline["runId"],
                "contentSha256": baseline["contentSha256"],
                "treatment": baseline["treatment"],
                "goldenEligible": baseline["goldenEligible"],
                "metrics": baseline["metrics"],
            },
        },
        "semantic": {
            "adjudicator": semantic["adjudicator"],
            "comparativeEligible": semantic["comparativeEligible"],
            "reviewCraft": {
                "contentSha256": semantic["reviewCraft"]["resultContentSha256"],
                "metrics": semantic["reviewCraft"]["metrics"],
            },
            "baseline": {
                "contentSha256": semantic["baseline"]["resultContentSha256"],
                "metrics": semantic["baseline"]["metrics"],
            },
            "metricDeltaPercentagePoints": semantic["metricDeltaPercentagePoints"],
        },
        "contentSha256": "0" * 64,
    }
    snapshot["contentSha256"] = sha256_json(
        {key: value for key, value in snapshot.items() if key != "contentSha256"}
    )
    errors = validate_golden_snapshot(snapshot)
    if errors:
        raise EvalError("generated Golden snapshot is invalid: " + "; ".join(errors))
    return snapshot


def _write_and_print(payload: dict[str, Any], output: str | None) -> None:
    if output:
        write_json(Path(output).expanduser().resolve(), payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def command_compare(args: argparse.Namespace) -> int:
    review_dir = Path(args.review_craft_run).expanduser().resolve(strict=True)
    baseline_dir = Path(args.baseline_run).expanduser().resolve(strict=True)
    comparison = build_comparison_payload(
        review_dir,
        baseline_dir,
        review_adjudication_path=(
            Path(args.review_craft_adjudication).expanduser().resolve(strict=True)
            if args.review_craft_adjudication
            else None
        ),
        baseline_adjudication_path=(
            Path(args.baseline_adjudication).expanduser().resolve(strict=True)
            if args.baseline_adjudication
            else None
        ),
    )
    _write_and_print(comparison, args.output)
    return 0


def command_export_golden(args: argparse.Namespace) -> int:
    review_dir = Path(args.review_craft_run).expanduser().resolve(strict=True)
    baseline_dir = Path(args.baseline_run).expanduser().resolve(strict=True)
    comparison = build_comparison_payload(
        review_dir,
        baseline_dir,
        review_adjudication_path=Path(args.review_craft_adjudication)
        .expanduser()
        .resolve(strict=True),
        baseline_adjudication_path=Path(args.baseline_adjudication)
        .expanduser()
        .resolve(strict=True),
    )
    review = read_json(review_dir / "result.json")
    baseline = read_json(baseline_dir / "result.json")
    snapshot = build_golden_snapshot(comparison, review, baseline)
    _write_and_print(snapshot, args.output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run and validate Review Craft host evaluations")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run selected evaluation cases through a host adapter")
    run.add_argument("--suite", default=str(DEFAULT_SUITE))
    run.add_argument("--skill-root", default=str(DEFAULT_SKILL))
    run.add_argument(
        "--output-root",
        default=str(Path(tempfile.gettempdir()) / "review-craft-evals"),
    )
    run.add_argument(
        "--treatment",
        choices=("REVIEW_CRAFT", "ORDINARY_PROMPT", "CODEX_NATIVE_REVIEW"),
        default="REVIEW_CRAFT",
    )
    run.add_argument("--case", action="append", help="Run one case id; can be repeated")
    run.add_argument("--case-timeout", type=int, default=600)
    run.add_argument(
        "--adapter-command",
        nargs=argparse.REMAINDER,
        required=True,
        help="Adapter argv; this option must be last",
    )
    run.set_defaults(handler=command_run)

    run_ablation = subparsers.add_parser(
        "run-ablation",
        help="Run the balanced three-arm risk-lens and evidence-loop ablation",
    )
    run_ablation.add_argument("--suite", default=str(DEFAULT_ABLATION_SUITE))
    run_ablation.add_argument("--skill-root", default=str(DEFAULT_SKILL))
    run_ablation.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE_ROOT))
    run_ablation.add_argument(
        "--output-root",
        default=str(Path(tempfile.gettempdir()) / "review-craft-ablations"),
    )
    run_ablation.add_argument("--case", action="append", help="Run one case id; repeatable")
    run_ablation.add_argument("--case-timeout", type=int, default=900)
    run_ablation.add_argument(
        "--adapter-command",
        nargs=argparse.REMAINDER,
        required=True,
        help="Adapter argv; this option must be last",
    )
    run_ablation.set_defaults(handler=command_run_ablation)

    run_remediation = subparsers.add_parser(
        "run-remediation-safety",
        help="Run the isolated three-arm review-to-repair safety protocol",
    )
    run_remediation.add_argument("--suite", default=str(DEFAULT_REMEDIATION_SUITE))
    run_remediation.add_argument("--skill-root", default=str(DEFAULT_REMEDIATION_SKILL))
    run_remediation.add_argument(
        "--output-root", default=str(DEFAULT_REMEDIATION_OUTPUT_ROOT)
    )
    run_remediation.add_argument("--case", action="append", help="Run one case id; repeatable")
    run_remediation.add_argument("--rounds", type=int, default=3)
    run_remediation.add_argument("--case-timeout", type=int, default=900)
    run_remediation.add_argument(
        "--adapter-command",
        nargs=argparse.REMAINDER,
        required=True,
        help="Adapter argv; this option must be last",
    )
    run_remediation.set_defaults(handler=command_run_remediation_safety)

    validate = subparsers.add_parser("validate", help="Validate a completed eval run")
    validate.add_argument("--run-dir", required=True)
    validate.set_defaults(handler=command_validate)

    validate_ablation = subparsers.add_parser(
        "validate-ablation", help="Validate a supported ablation and all child runs"
    )
    validate_ablation.add_argument("--ablation-dir", required=True)
    validate_ablation.set_defaults(handler=command_validate_ablation)

    validate_remediation = subparsers.add_parser(
        "validate-remediation-safety",
        help="Validate a remediation-safety run and all content-bound artifacts",
    )
    validate_remediation.add_argument("--run-dir", required=True)
    validate_remediation.set_defaults(handler=command_validate_remediation_safety)

    prepare_ablation_adjudication = subparsers.add_parser(
        "prepare-ablation-adjudication",
        help="Create a treatment-blinded bundle and unresolved adjudication template",
    )
    prepare_ablation_adjudication.add_argument("--ablation-dir", required=True)
    prepare_ablation_adjudication.add_argument(
        "--kind", choices=("HUMAN", "AGENT_ASSISTED"), required=True
    )
    prepare_ablation_adjudication.add_argument("--protocol", required=True)
    prepare_ablation_adjudication.add_argument("--bundle-output", required=True)
    prepare_ablation_adjudication.add_argument("--output", required=True)
    prepare_ablation_adjudication.set_defaults(
        handler=command_prepare_ablation_adjudication
    )

    adjudicate_ablation = subparsers.add_parser(
        "adjudicate-ablation",
        help="Unblind explicit semantic decisions and compute per-treatment metrics",
    )
    adjudicate_ablation.add_argument("--ablation-dir", required=True)
    adjudicate_ablation.add_argument("--bundle", required=True)
    adjudicate_ablation.add_argument("--adjudication", required=True)
    adjudicate_ablation.add_argument("--output")
    adjudicate_ablation.set_defaults(handler=command_adjudicate_ablation)

    validate_ablation_adjudication = subparsers.add_parser(
        "validate-ablation-adjudication",
        help="Validate an unblinded adjudication against all bound runs",
    )
    validate_ablation_adjudication.add_argument("--ablation-dir", required=True)
    validate_ablation_adjudication.add_argument("--bundle", required=True)
    validate_ablation_adjudication.add_argument("--result", required=True)
    validate_ablation_adjudication.set_defaults(
        handler=command_validate_ablation_adjudication
    )

    compare_ablation = subparsers.add_parser(
        "compare-ablation",
        help="Compare matched ablation arms with bound semantic adjudication",
    )
    compare_ablation.add_argument("--ablation-dir", required=True)
    compare_ablation.add_argument("--bundle", required=True)
    compare_ablation.add_argument("--adjudication-result", required=True)
    compare_ablation.add_argument("--output")
    compare_ablation.set_defaults(handler=command_compare_ablation)

    export_ablation = subparsers.add_parser(
        "export-ablation",
        help="Export a sanitized snapshot from a complete comparative-eligible ablation",
    )
    export_ablation.add_argument("--ablation-dir", required=True)
    export_ablation.add_argument("--bundle", required=True)
    export_ablation.add_argument("--adjudication-result", required=True)
    export_ablation.add_argument("--output", required=True)
    export_ablation.set_defaults(handler=command_export_ablation)

    prepare_adjudication = subparsers.add_parser(
        "prepare-adjudication",
        help="Create an unresolved semantic adjudication template bound to a run",
    )
    prepare_adjudication.add_argument("--run-dir", required=True)
    prepare_adjudication.add_argument(
        "--kind",
        choices=("HUMAN", "AGENT_ASSISTED"),
        required=True,
    )
    prepare_adjudication.add_argument("--protocol", required=True)
    prepare_adjudication.add_argument("--output")
    prepare_adjudication.set_defaults(handler=command_prepare_adjudication)

    adjudicate = subparsers.add_parser(
        "adjudicate",
        help="Create content-bound semantic metrics from explicit case adjudications",
    )
    adjudicate.add_argument("--run-dir", required=True)
    adjudicate.add_argument("--adjudication", required=True)
    adjudicate.add_argument("--output")
    adjudicate.set_defaults(handler=command_adjudicate)

    validate_adjudication = subparsers.add_parser(
        "validate-adjudication",
        help="Validate a semantic adjudication result against its bound run",
    )
    validate_adjudication.add_argument("--run-dir", required=True)
    validate_adjudication.add_argument("--result", required=True)
    validate_adjudication.set_defaults(handler=command_validate_adjudication)

    compare = subparsers.add_parser(
        "compare", help="Compare matched Review Craft and baseline runs"
    )
    compare.add_argument("--review-craft-run", required=True)
    compare.add_argument("--baseline-run", required=True)
    compare.add_argument("--review-craft-adjudication")
    compare.add_argument("--baseline-adjudication")
    compare.add_argument("--output")
    compare.set_defaults(handler=command_compare)

    export_golden = subparsers.add_parser(
        "export-golden",
        help="Export a sanitized Golden snapshot from matched validated evidence",
    )
    export_golden.add_argument("--review-craft-run", required=True)
    export_golden.add_argument("--baseline-run", required=True)
    export_golden.add_argument("--review-craft-adjudication", required=True)
    export_golden.add_argument("--baseline-adjudication", required=True)
    export_golden.add_argument("--output", required=True)
    export_golden.set_defaults(handler=command_export_golden)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        timeout = getattr(args, "case_timeout", 1)
        if timeout < 1 or timeout > 7200:
            raise EvalError("--case-timeout must be between 1 and 7200 seconds")
        return args.handler(args)
    except (EvalError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"review-craft eval: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
