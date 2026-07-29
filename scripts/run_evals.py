#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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
    ADAPTER_SCHEMA,
    HOST_OUTPUT_SCHEMA,
    SCHEMA_ROOT,
    EvalError,
    file_hash,
    golden_eligible,
    overall_status,
    read_json,
    schema_errors,
    score_cases,
    sha256_bytes,
    sha256_json,
    source_metadata,
    source_stable,
    tree_sha256,
    utc_now,
    validate_run,
    write_bytes,
    write_json,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = ROOT / "evals/specs/cases.json"
DEFAULT_SKILL = ROOT / "skills/review-craft"
PROMPTS = {
    "REVIEW_CRAFT": ROOT / "evals/prompts/review-craft.md",
    "ORDINARY_PROMPT": ROOT / "evals/prompts/ordinary-review.md",
}
SENSITIVE_ARGUMENT = re.compile(
    r"^--?(?:api[-_]?key|password|secret|token)(?:=|$)", re.IGNORECASE
)


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


def _prompt_for_treatment(treatment: str) -> Path:
    if treatment == "CODEX_NATIVE_REVIEW":
        raise EvalError("CODEX_NATIVE_REVIEW requires a future diff-aware adapter")
    return PROMPTS[treatment]


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
) -> dict[str, Any]:
    prefix = f"cases/{ordinal:03d}"
    fixture_relative = f"fixtures/{ordinal:03d}"
    fixture_artifact = run_dir / fixture_relative
    shutil.copytree(fixture_source, fixture_artifact)
    prompt_relative = f"{prefix}/prompt.md"
    stdout_relative = f"{prefix}/stdout.txt"
    stderr_relative = f"{prefix}/stderr.txt"
    output_relative = f"{prefix}/output.json"
    prompt_path = run_dir / prompt_relative
    stdout_path = run_dir / stdout_relative
    stderr_path = run_dir / stderr_relative
    output_path = run_dir / output_relative
    write_bytes(prompt_path, prompt)

    workspace = stage_root / f"case-{ordinal:03d}"
    staged_fixture = workspace / "target"
    staged_skill = workspace / "skill"
    shutil.copytree(fixture_artifact, staged_fixture)
    shutil.copytree(skill_root, staged_skill, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

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
    ]
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
    adapter_output_artifact = output_relative if output_path.is_file() else None
    adapter_output_hash = (
        file_hash(output_path) if adapter_output_artifact is not None else None
    )
    output_artifact = adapter_output_artifact if status == "COMPLETED" else None
    output_hash = adapter_output_hash if status == "COMPLETED" else None
    return {
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
    }


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
    suite_validation = schema_errors(suite, SCHEMA_ROOT / "eval-cases.schema.json")
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
        "schema": "review-craft.eval-run.v1",
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
        "adapterCommand": payload["adapter"]["command"],
        "caseTimeoutSeconds": payload["caseTimeoutSeconds"],
    }


def command_compare(args: argparse.Namespace) -> int:
    review_dir = Path(args.review_craft_run).expanduser().resolve(strict=True)
    baseline_dir = Path(args.baseline_run).expanduser().resolve(strict=True)
    validation_errors = {
        "reviewCraft": validate_run(review_dir),
        "baseline": validate_run(baseline_dir),
    }
    if any(validation_errors.values()):
        for label, errors in validation_errors.items():
            for error in errors:
                print(f"{label}: {error}", file=sys.stderr)
        return 2
    review = read_json(review_dir / "result.json")
    baseline = read_json(baseline_dir / "result.json")
    if review["treatment"] != "REVIEW_CRAFT":
        print("review-craft run must use REVIEW_CRAFT treatment", file=sys.stderr)
        return 2
    if baseline["treatment"] != "ORDINARY_PROMPT":
        print("baseline run must use ORDINARY_PROMPT treatment", file=sys.stderr)
        return 2
    review_fields = _comparison_fields(review)
    baseline_fields = _comparison_fields(baseline)
    mismatches = [
        field for field in review_fields if review_fields[field] != baseline_fields[field]
    ]
    if mismatches:
        print(
            "eval runs are not matched: " + ", ".join(mismatches),
            file=sys.stderr,
        )
        return 2
    deltas = {}
    for metric, value in review["metrics"].items():
        baseline_value = baseline["metrics"].get(metric)
        if not metric.endswith("Percent"):
            continue
        deltas[metric] = (
            round(value - baseline_value, 2)
            if isinstance(value, (int, float))
            and isinstance(baseline_value, (int, float))
            else None
        )
    print(
        json.dumps(
            {
                "matched": True,
                "comparativeEligible": bool(
                    review["goldenEligible"] and baseline["goldenEligible"]
                ),
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
                "metricDeltaPercentagePoints": deltas,
                "matchedFields": review_fields,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
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

    validate = subparsers.add_parser("validate", help="Validate a completed eval run")
    validate.add_argument("--run-dir", required=True)
    validate.set_defaults(handler=command_validate)

    compare = subparsers.add_parser(
        "compare", help="Compare matched Review Craft and baseline runs"
    )
    compare.add_argument("--review-craft-run", required=True)
    compare.add_argument("--baseline-run", required=True)
    compare.set_defaults(handler=command_compare)
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
