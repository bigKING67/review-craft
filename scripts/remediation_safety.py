from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

from eval_contracts import (
    ADAPTER_SCHEMA,
    SCHEMA_ROOT,
    EvalError,
    _tree_manifest,
    aggregate_usage,
    file_hash,
    overall_status,
    read_json,
    safe_artifact,
    schema_errors,
    sha256_bytes,
    sha256_json,
    source_metadata,
    source_stable,
    tree_sha256,
    unavailable_usage,
    utc_now,
    validate_usage_record,
    write_bytes,
    write_json,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = ROOT / "evals/specs/remediation-safety-cases.json"
DEFAULT_SKILL = ROOT / "skills/review-craft"
DEFAULT_VERIFIER = ROOT / "evals/verifiers/verify_remediation_case.py"
DEFAULT_OUTPUT_ROOT = Path(tempfile.gettempdir()) / "review-craft-remediation-safety"
REVIEW_OUTPUT_SCHEMA = SCHEMA_ROOT / "eval-remediation-review-output.schema.json"
REPAIR_OUTPUT_SCHEMA = SCHEMA_ROOT / "eval-remediation-repair-output.schema.json"
ORACLE_SCHEMA = SCHEMA_ROOT / "eval-remediation-oracle.schema.json"
RUN_SCHEMA = SCHEMA_ROOT / "eval-remediation-run.schema.json"
SUITE_SCHEMA = SCHEMA_ROOT / "eval-remediation-cases.schema.json"
TOOL_TRACE_SCHEMA = SCHEMA_ROOT / "eval-tool-trace.schema.json"
REVIEW_PROMPTS = {
    "ORDINARY_NAIVE_LOOP": ROOT / "evals/prompts/remediation-ordinary-review.md",
    "REVIEW_CRAFT_UNGATED_LOOP": ROOT / "evals/prompts/remediation-review-craft.md",
    "REVIEW_CRAFT_EVIDENCE_GATED_LOOP": ROOT
    / "evals/prompts/remediation-review-craft.md",
}
REPAIR_PROMPT = ROOT / "evals/prompts/remediation-repair.md"
ARMS = tuple(REVIEW_PROMPTS)
GATED_ARM = "REVIEW_CRAFT_EVIDENCE_GATED_LOOP"
ACTIONABLE_DECISIONS = {"CLEAN_UP", "MERGE", "REPLACE", "REWRITE", "DELETE"}
USAGE_OUTPUT_ENV = "REVIEW_CRAFT_EVAL_USAGE_OUTPUT"
TOOL_TRACE_OUTPUT_ENV = "REVIEW_CRAFT_EVAL_TOOL_TRACE_OUTPUT"
SENSITIVE_OUTPUT_PATTERNS = (
    re.compile(r"(?i)(Incorrect API key provided:\s*)[^\s,\"']+"),
    re.compile(
        r"(?i)((?:api[-_]?key|password|secret|access[-_]?token|refresh[-_]?token)"
        r"\s*[:=]\s*)[^\s,\"']+"
    ),
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,\"']+"),
)


def _percent(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "percent": round(numerator * 100.0 / denominator, 2) if denominator else None,
    }


def _safe_relative(value: str) -> bool:
    if not value or "\0" in value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and not any(
        ":" in part for part in path.parts
    )


def _redact_adapter_output(value: bytes) -> bytes:
    rendered = value.decode("utf-8", errors="replace")
    for pattern in SENSITIVE_OUTPUT_PATTERNS:
        rendered = pattern.sub(r"\1[REDACTED]", rendered)
    return rendered.encode("utf-8")


def validate_remediation_suite(payload: dict[str, Any]) -> list[str]:
    errors = schema_errors(payload, SUITE_SCHEMA)
    if errors:
        return errors
    cases = payload["cases"]
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        errors.append("suite case ids must be unique")
    pairs: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        pairs.setdefault(case["pairId"], []).append(case)
        fixture = (ROOT / case["fixture"]).resolve()
        fixture_root = (ROOT / "evals/fixtures").resolve()
        try:
            fixture.relative_to(fixture_root)
        except ValueError:
            errors.append(f"case {case['id']}: fixture escapes evals/fixtures")
            continue
        if not fixture.is_dir() or fixture.is_symlink():
            errors.append(f"case {case['id']}: fixture must be a real directory")
        for relative in case["allowedMutationPaths"]:
            if not _safe_relative(relative):
                errors.append(f"case {case['id']}: unsafe mutation path {relative!r}")
                continue
            candidate = fixture / relative
            if not candidate.is_file() or candidate.is_symlink():
                errors.append(
                    f"case {case['id']}: mutation path must name a regular fixture file: "
                    f"{relative}"
                )
        claim_ids = [claim["id"] for claim in case["claims"]]
        if len(claim_ids) != len(set(claim_ids)):
            errors.append(f"case {case['id']}: claim ids must be unique")
        if any(
            claim["role"] == "PRESERVATION" and claim["expectedBaseline"] != "PASS"
            for claim in case["claims"]
        ):
            errors.append(f"case {case['id']}: preservation claims must pass at baseline")
        failing_defects = [
            claim
            for claim in case["claims"]
            if claim["role"] == "DEFECT" and claim["expectedBaseline"] == "FAIL"
        ]
        if case["class"] == "positive" and not failing_defects:
            errors.append(f"case {case['id']}: positive case requires a failing defect claim")
        if case["class"] == "negative" and any(
            claim["expectedBaseline"] != "PASS" for claim in case["claims"]
        ):
            errors.append(f"case {case['id']}: negative case claims must all pass")
        if set(case["expectedDecisions"]) & set(case["prohibitedDecisions"]):
            errors.append(f"case {case['id']}: expected and prohibited decisions overlap")
    for pair_id, rows in pairs.items():
        if len(rows) != 2 or {row["class"] for row in rows} != {"positive", "negative"}:
            errors.append(f"pair {pair_id}: expected one positive and one negative case")
    return errors


def _artifact_path(run_dir: Path, path: Path) -> str:
    return path.relative_to(run_dir).as_posix()


def _copy_snapshot(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def _manifest(root: Path) -> list[dict[str, Any]]:
    rows = _tree_manifest(root)
    for row in rows:
        path = root / row["path"]
        if path.is_symlink():
            raise EvalError(f"source snapshot contains a symlink: {row['path']}")
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise EvalError(f"source snapshot is not UTF-8: {row['path']}") from error
    return rows


def _manifest_by_path(root: Path) -> dict[str, dict[str, Any]]:
    return {row["path"]: row for row in _manifest(root)}


def _workspace_boundary_manifest(workspace: Path) -> dict[str, str]:
    return {
        row["path"]: row["sha256"]
        for row in _tree_manifest(workspace)
        if not row["path"].startswith("target/")
    }


def _source_diff(before: Path, after: Path) -> tuple[list[dict[str, Any]], str, int]:
    before_rows = _manifest_by_path(before)
    after_rows = _manifest_by_path(after)
    changes = []
    patch_lines: list[str] = []
    line_churn = 0
    for relative in sorted(set(before_rows) | set(after_rows)):
        old = before_rows.get(relative)
        new = after_rows.get(relative)
        if old == new:
            continue
        status = "ADDED" if old is None else "DELETED" if new is None else "MODIFIED"
        old_lines = (
            (before / relative).read_text(encoding="utf-8").splitlines(keepends=True)
            if old is not None
            else []
        )
        new_lines = (
            (after / relative).read_text(encoding="utf-8").splitlines(keepends=True)
            if new is not None
            else []
        )
        rendered = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
            )
        )
        added = sum(line.startswith("+") and not line.startswith("+++") for line in rendered)
        deleted = sum(line.startswith("-") and not line.startswith("---") for line in rendered)
        line_churn += added + deleted
        patch_lines.extend(rendered)
        changes.append(
            {
                "path": relative,
                "status": status,
                "beforeSha256": old["sha256"] if old else None,
                "afterSha256": new["sha256"] if new else None,
                "addedLines": added,
                "deletedLines": deleted,
            }
        )
    return changes, "".join(patch_lines), line_churn


def _run_oracle(
    *,
    case: dict[str, Any],
    target: Path,
    output_dir: Path,
    label: str,
    run_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    stdout_path = output_dir / f"{label}-oracle.stdout.txt"
    stderr_path = output_dir / f"{label}-oracle.stderr.txt"
    artifact_path = output_dir / f"{label}-oracle.json"
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [
                os.environ.get("PYTHON", "python3"),
                str(DEFAULT_VERIFIER),
                "--case",
                case["id"],
                "--target",
                str(target),
            ],
            cwd=ROOT,
            capture_output=True,
            timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except subprocess.TimeoutExpired as error:
        write_bytes(stdout_path, error.stdout or b"")
        write_bytes(stderr_path, (error.stderr or b"") + b"oracle timed out\n")
        raise EvalError(f"case {case['id']}: oracle timed out") from error
    write_bytes(stdout_path, completed.stdout)
    write_bytes(stderr_path, completed.stderr)
    if completed.returncode != 0:
        raise EvalError(
            f"case {case['id']}: oracle exited with code {completed.returncode}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise EvalError(f"case {case['id']}: oracle output is invalid JSON") from error
    errors = schema_errors(payload, ORACLE_SCHEMA)
    if errors:
        raise EvalError(f"case {case['id']}: invalid oracle output: {'; '.join(errors)}")
    expected = {(row["id"], row["role"]) for row in case["claims"]}
    actual = {(row["id"], row["role"]) for row in payload["claims"]}
    if actual != expected or len(actual) != len(payload["claims"]):
        raise EvalError(f"case {case['id']}: oracle claim coverage mismatch")
    write_json(artifact_path, payload)
    receipt = {
        "artifact": _artifact_path(run_dir, artifact_path),
        "stdoutArtifact": _artifact_path(run_dir, stdout_path),
        "stderrArtifact": _artifact_path(run_dir, stderr_path),
        "durationMs": max(0, round((time.monotonic() - started) * 1000)),
    }
    return payload, receipt


def _baseline_matches(case: dict[str, Any], oracle: dict[str, Any]) -> bool:
    expected = {row["id"]: row["expectedBaseline"] for row in case["claims"]}
    actual = {row["id"]: row["status"] for row in oracle["claims"]}
    return actual == expected


def _render_review_prompt(template: bytes, case: dict[str, Any]) -> bytes:
    rendered = template.decode("utf-8").replace(
        "{{RISK_LENS_JSON}}",
        json.dumps(
            {"id": case["pairId"], "prompt": case["riskLens"]},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
    )
    if "{{" in rendered:
        raise EvalError(f"case {case['id']}: unresolved review prompt marker")
    return rendered.encode("utf-8")


def _oracle_evidence(
    case: dict[str, Any], oracle: dict[str, Any] | None
) -> dict[str, Any]:
    if oracle is None:
        return {"available": False, "claims": []}
    invariants = {row["id"]: row["invariant"] for row in case["claims"]}
    return {
        "available": True,
        "claims": [
            {
                "id": row["id"],
                "role": row["role"],
                "status": row["status"],
                "invariant": invariants[row["id"]],
                "observation": row["observation"],
            }
            for row in oracle["claims"]
            if row["status"] == "FAIL"
        ],
    }


def _render_repair_prompt(
    *,
    template: bytes,
    case: dict[str, Any],
    review: dict[str, Any],
    oracle: dict[str, Any] | None,
) -> bytes:
    rendered = template.decode("utf-8")
    values = {
        "{{ALLOWED_PATHS_JSON}}": case["allowedMutationPaths"],
        "{{REVIEW_JSON}}": review,
        "{{ORACLE_JSON}}": _oracle_evidence(case, oracle),
    }
    for marker, value in values.items():
        rendered = rendered.replace(
            marker,
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        )
    if "{{" in rendered:
        raise EvalError(f"case {case['id']}: unresolved repair prompt marker")
    return rendered.encode("utf-8")


def _invoke_adapter(
    *,
    adapter_command: list[str],
    case: dict[str, Any],
    arm: str,
    operation: str,
    prompt: bytes,
    target: Path,
    skill: Path,
    output_dir: Path,
    run_dir: Path,
    timeout_seconds: int,
    round_number: int | None = None,
    marker: Path | None = None,
    marker_key: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    prompt_path = output_dir / f"{operation}-prompt.md"
    output_path = output_dir / f"{operation}-output.json"
    stdout_path = output_dir / f"{operation}.stdout.txt"
    stderr_path = output_dir / f"{operation}.stderr.txt"
    usage_path = output_dir / f"{operation}-usage.json"
    trace_path = output_dir / f"{operation}-tool-trace.json"
    write_bytes(prompt_path, prompt)
    schema_path = REVIEW_OUTPUT_SCHEMA if operation == "review" else REPAIR_OUTPUT_SCHEMA
    command = [
        *adapter_command,
        "--fixture-root",
        str(target),
        "--skill-root",
        str(skill),
        "--prompt-file",
        str(prompt_path),
        "--output-schema",
        str(schema_path),
        "--output-file",
        str(output_path),
        "--treatment",
        arm,
        "--case-id",
        case["id"],
        "--operation",
        operation,
    ]
    if marker is not None and marker_key is not None and round_number is not None:
        command.extend(
            [
                "--workspace-marker",
                str(marker),
                "--workspace-key",
                marker_key,
                "--round-number",
                str(round_number),
            ]
        )
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
                USAGE_OUTPUT_ENV: str(usage_path),
                TOOL_TRACE_OUTPUT_ENV: str(trace_path),
            },
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        if exit_code != 0:
            failure_reason = f"adapter exited with code {exit_code}"
        elif not output_path.is_file():
            failure_reason = "adapter did not create normalized output"
        else:
            try:
                output = read_json(output_path)
            except (OSError, json.JSONDecodeError) as error:
                failure_reason = f"normalized output is invalid JSON: {error}"
            else:
                output_errors = schema_errors(output, schema_path)
                if output_errors:
                    failure_reason = "normalized output schema failed: " + "; ".join(
                        output_errors
                    )
                elif operation == "review" and any(
                    row["lineEnd"] < row["lineStart"] for row in output["locations"]
                ):
                    failure_reason = "review output contains an inverted line range"
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
    write_bytes(stdout_path, _redact_adapter_output(stdout))
    write_bytes(stderr_path, _redact_adapter_output(stderr))

    usage = unavailable_usage("ADAPTER_DID_NOT_REPORT_USAGE")
    if usage_path.is_file():
        try:
            reported_usage = read_json(usage_path)
        except (OSError, json.JSONDecodeError):
            usage = unavailable_usage("ADAPTER_USAGE_INVALID")
        else:
            usage = (
                unavailable_usage("ADAPTER_USAGE_INVALID")
                if validate_usage_record(reported_usage)
                else reported_usage
            )
    write_json(usage_path, usage)
    if not trace_path.is_file():
        write_json(
            trace_path,
            {"schema": "review-craft.eval-tool-trace.v1", "items": []},
        )
        if status == "COMPLETED":
            status = "FAILED"
            failure_reason = "adapter did not create tool trace"
    else:
        try:
            trace = read_json(trace_path)
        except (OSError, json.JSONDecodeError):
            trace = {"schema": "review-craft.eval-tool-trace.v1", "items": []}
            write_json(trace_path, trace)
            status = "FAILED"
            failure_reason = "tool trace is invalid JSON"
        trace_errors = schema_errors(trace, TOOL_TRACE_SCHEMA)
        if trace_errors:
            status = "FAILED"
            failure_reason = "tool trace schema failed: " + "; ".join(trace_errors)

    output = read_json(output_path) if status == "COMPLETED" else None
    receipt = {
        "operation": operation.upper(),
        "status": status,
        "exitCode": exit_code,
        "failureReason": failure_reason,
        "durationMs": max(0, round((time.monotonic() - started) * 1000)),
        "promptArtifact": _artifact_path(run_dir, prompt_path),
        "outputArtifact": (
            _artifact_path(run_dir, output_path) if output_path.is_file() else None
        ),
        "stdoutArtifact": _artifact_path(run_dir, stdout_path),
        "stderrArtifact": _artifact_path(run_dir, stderr_path),
        "usageArtifact": _artifact_path(run_dir, usage_path),
        "toolTraceArtifact": _artifact_path(run_dir, trace_path),
        "usage": usage,
    }
    return receipt, output


def _claim_transitions(
    before: dict[str, Any], after: dict[str, Any]
) -> list[dict[str, str]]:
    old = {row["id"]: row for row in before["claims"]}
    new = {row["id"]: row for row in after["claims"]}
    return [
        {
            "id": claim_id,
            "role": old[claim_id]["role"],
            "before": old[claim_id]["status"],
            "after": new[claim_id]["status"],
            "transition": f"{old[claim_id]['status']}_TO_{new[claim_id]['status']}",
        }
        for claim_id in sorted(old)
    ]


def _all_defects_pass(oracle: dict[str, Any]) -> bool:
    return all(
        row["status"] == "PASS"
        for row in oracle["claims"]
        if row["role"] == "DEFECT"
    )


def _all_claims_pass(oracle: dict[str, Any]) -> bool:
    return all(row["status"] == "PASS" for row in oracle["claims"])


def _run_arm(
    *,
    run_dir: Path,
    stage_root: Path,
    case_index: int,
    case: dict[str, Any],
    arm: str,
    baseline_source: Path,
    baseline_oracle: dict[str, Any],
    skill_root: Path,
    review_template: bytes,
    repair_template: bytes,
    adapter_command: list[str],
    rounds: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    arm_slug = arm.lower().replace("_", "-")
    workspace = stage_root / f"{case_index:03d}-{case['id']}" / arm_slug
    target = workspace / "target"
    staged_skill = workspace / "skill"
    _copy_snapshot(baseline_source, target)
    if arm == "ORDINARY_NAIVE_LOOP":
        staged_skill.mkdir(parents=True)
    else:
        shutil.copytree(
            skill_root,
            staged_skill,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    arm_dir = run_dir / f"cases/{case_index:03d}-{case['id']}/arms/{arm_slug}"
    arm_dir.mkdir(parents=True)
    initial_source = arm_dir / "initial-source"
    _copy_snapshot(target, initial_source)
    current_oracle = baseline_oracle
    round_records = []
    status = "COMPLETED"
    stop_reason = "ROUND_LIMIT"
    ever_regressed = False
    scope_violation = False
    sandbox_breach = False
    changed_paths: set[str] = set()
    total_churn = 0
    source_mutation_rounds = 0
    review_invocations = 0
    repair_invocations = 0

    for round_number in range(1, rounds + 1):
        round_dir = arm_dir / f"rounds/{round_number:03d}"
        round_dir.mkdir(parents=True)
        before_snapshot = round_dir / "source-before"
        _copy_snapshot(target, before_snapshot)
        source_before_review = _manifest(target)
        boundary_before_review = _workspace_boundary_manifest(workspace)
        review_prompt = _render_review_prompt(review_template, case)
        review_receipt, review_output = _invoke_adapter(
            adapter_command=adapter_command,
            case=case,
            arm=arm,
            operation="review",
            prompt=review_prompt,
            target=target,
            skill=staged_skill,
            output_dir=round_dir,
            run_dir=run_dir,
            timeout_seconds=timeout_seconds,
        )
        review_invocations += 1
        round_record: dict[str, Any] = {
            "round": round_number,
            "sourceBeforeTreeSha256": sha256_json(source_before_review),
            "sourceBeforeArtifact": _artifact_path(run_dir, before_snapshot),
            "review": review_receipt,
            "repair": None,
            "preOracle": None,
            "postOracle": None,
            "changes": [],
            "lineChurn": 0,
            "claimTransitions": [],
            "scopeViolation": False,
            "claimedPathsMismatch": False,
            "repairSucceeded": False,
        }
        round_records.append(round_record)
        if review_receipt["status"] != "COMPLETED" or review_output is None:
            status = review_receipt["status"]
            stop_reason = "REVIEW_FAILED"
            break
        if (
            _manifest(target) != source_before_review
            or _workspace_boundary_manifest(workspace) != boundary_before_review
        ):
            status = "FAILED"
            stop_reason = "REVIEW_MUTATED_WORKSPACE"
            sandbox_breach = True
            break
        if not review_output["findingDetected"] or review_output["decision"] not in (
            ACTIONABLE_DECISIONS
        ):
            stop_reason = "REVIEW_ABSTAINED"
            break

        pre_oracle, pre_receipt = _run_oracle(
            case=case,
            target=target,
            output_dir=round_dir,
            label="pre",
            run_dir=run_dir,
        )
        round_record["preOracle"] = pre_receipt
        current_oracle = pre_oracle
        if arm == GATED_ARM and _all_defects_pass(pre_oracle):
            stop_reason = "EVIDENCE_REJECTED"
            break

        marker = workspace / ".review-craft-remediation-workspace.json"
        marker_payload = {
            "schema": "review-craft.eval-remediation-workspace.v1",
            "caseId": case["id"],
            "arm": arm,
            "round": round_number,
        }
        write_json(marker, marker_payload)
        marker_key = sha256_bytes(marker.read_bytes())
        repair_prompt = _render_repair_prompt(
            template=repair_template,
            case=case,
            review=review_output,
            oracle=pre_oracle if arm == GATED_ARM else None,
        )
        boundary_before_repair = _workspace_boundary_manifest(workspace)
        repair_receipt, repair_output = _invoke_adapter(
            adapter_command=adapter_command,
            case=case,
            arm=arm,
            operation="repair",
            prompt=repair_prompt,
            target=target,
            skill=staged_skill,
            output_dir=round_dir,
            run_dir=run_dir,
            timeout_seconds=timeout_seconds,
            round_number=round_number,
            marker=marker,
            marker_key=marker_key,
        )
        repair_invocations += 1
        round_record["repair"] = repair_receipt
        if repair_receipt["status"] != "COMPLETED" or repair_output is None:
            status = repair_receipt["status"]
            stop_reason = "REPAIR_FAILED"
            break
        if _workspace_boundary_manifest(workspace) != boundary_before_repair:
            status = "FAILED"
            stop_reason = "REPAIR_ESCAPED_TARGET"
            sandbox_breach = True
            break

        after_snapshot = round_dir / "source-after"
        _copy_snapshot(target, after_snapshot)
        changes, patch, churn = _source_diff(before_snapshot, after_snapshot)
        diff_path = round_dir / "source-diff.json"
        patch_path = round_dir / "source.patch"
        write_json(diff_path, {"changes": changes, "lineChurn": churn})
        write_bytes(patch_path, patch.encode("utf-8"))
        round_record.update(
            {
                "sourceAfterArtifact": _artifact_path(run_dir, after_snapshot),
                "diffArtifact": _artifact_path(run_dir, diff_path),
                "patchArtifact": _artifact_path(run_dir, patch_path),
                "changes": changes,
                "lineChurn": churn,
            }
        )
        actual_paths = {row["path"] for row in changes}
        claimed_paths = set(repair_output["claimedPaths"])
        round_record["claimedPathsMismatch"] = actual_paths != claimed_paths
        if not changes:
            stop_reason = "NO_CHANGE"
            break

        source_mutation_rounds += 1
        changed_paths.update(actual_paths)
        total_churn += churn
        post_oracle, post_receipt = _run_oracle(
            case=case,
            target=target,
            output_dir=round_dir,
            label="post",
            run_dir=run_dir,
        )
        transitions = _claim_transitions(pre_oracle, post_oracle)
        ever_regressed = ever_regressed or any(
            row["transition"] == "PASS_TO_FAIL" for row in transitions
        )
        round_record["postOracle"] = post_receipt
        round_record["claimTransitions"] = transitions
        current_oracle = post_oracle
        disallowed = sorted(actual_paths - set(case["allowedMutationPaths"]))
        if disallowed:
            round_record["scopeViolation"] = True
            round_record["disallowedPaths"] = disallowed
            scope_violation = True
        round_record["repairSucceeded"] = (
            _all_claims_pass(post_oracle)
            and not disallowed
            and not any(row["transition"] == "PASS_TO_FAIL" for row in transitions)
        )
        if disallowed:
            stop_reason = "SCOPE_VIOLATION"
            break
        if arm == GATED_ARM and _all_claims_pass(post_oracle):
            stop_reason = "CLAIMS_SATISFIED"
            break

    return {
        "arm": arm,
        "status": status,
        "stopReason": stop_reason,
        "rounds": round_records,
        "finalSourceTreeSha256": tree_sha256(target),
        "finalClaims": [
            {"id": row["id"], "role": row["role"], "status": row["status"]}
            for row in current_oracle["claims"]
        ],
        "everRegressed": ever_regressed,
        "scopeViolation": scope_violation,
        "sandboxBreach": sandbox_breach,
        "sourceMutationRounds": source_mutation_rounds,
        "cumulativeChangedPaths": sorted(changed_paths),
        "cumulativeLineChurn": total_churn,
        "reviewInvocations": review_invocations,
        "repairInvocations": repair_invocations,
    }


def _arm_metrics(cases: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    rows = [
        (case, next(candidate for candidate in case["arms"] if candidate["arm"] == arm))
        for case in cases
    ]
    clean = [(case, result) for case, result in rows if case["class"] == "negative"]
    mutated_clean = sum(result["sourceMutationRounds"] > 0 for _, result in clean)
    transitions = [
        transition
        for _, result in rows
        for round_record in result["rounds"]
        for transition in round_record["claimTransitions"]
    ]
    regressions = sum(row["transition"] == "PASS_TO_FAIL" for row in transitions)
    baseline_failed_defects = [
        claim
        for case, _ in rows
        for claim in case["baselineClaims"]
        if claim["role"] == "DEFECT" and claim["status"] == "FAIL"
    ]
    final_defects = {
        (case["id"], claim["id"]): claim["status"]
        for case, result in rows
        for claim in result["finalClaims"]
        if claim["role"] == "DEFECT"
    }
    resolved = sum(
        final_defects.get((case["id"], claim["id"])) == "PASS"
        for case, _ in rows
        for claim in case["baselineClaims"]
        if claim["role"] == "DEFECT" and claim["status"] == "FAIL"
    )
    repair_rounds = [
        round_record
        for _, result in rows
        for round_record in result["rounds"]
        if round_record["repair"] is not None
    ]
    successful_repairs = sum(row["repairSucceeded"] for row in repair_rounds)
    baseline_passed = sum(
        claim["status"] == "PASS" for case, _ in rows for claim in case["baselineClaims"]
    )
    final_passed = sum(
        claim["status"] == "PASS" for _, result in rows for claim in result["finalClaims"]
    )
    usages = [
        invocation["usage"]
        for _, result in rows
        for round_record in result["rounds"]
        for invocation in (round_record["review"], round_record["repair"])
        if invocation is not None
    ]
    durations = [
        invocation["durationMs"]
        for _, result in rows
        for round_record in result["rounds"]
        for invocation in (round_record["review"], round_record["repair"])
        if invocation is not None
    ]
    return {
        "caseCount": len(rows),
        "cleanCaseMutationRate": _percent(mutated_clean, len(clean)),
        "cleanCaseNoChangeRate": _percent(len(clean) - mutated_clean, len(clean)),
        "claimRegressionRate": _percent(regressions, len(transitions)),
        "defectClaimResolutionRate": _percent(resolved, len(baseline_failed_defects)),
        "repairSuccessRate": _percent(successful_repairs, len(repair_rounds)),
        "scopeViolationRate": _percent(
            sum(result["scopeViolation"] for _, result in rows), len(rows)
        ),
        "everRegressedCaseCount": sum(result["everRegressed"] for _, result in rows),
        "netPassedClaimDelta": final_passed - baseline_passed,
        "sourceMutationRounds": sum(result["sourceMutationRounds"] for _, result in rows),
        "cumulativeChangedPaths": sorted(
            {path for _, result in rows for path in result["cumulativeChangedPaths"]}
        ),
        "cumulativeLineChurn": sum(result["cumulativeLineChurn"] for _, result in rows),
        "reviewInvocations": sum(result["reviewInvocations"] for _, result in rows),
        "repairInvocations": sum(result["repairInvocations"] for _, result in rows),
        "totalDurationMs": sum(durations),
        "usage": aggregate_usage([{"usage": usage} for usage in usages]),
    }


def compute_metrics(cases: list[dict[str, Any]]) -> dict[str, Any]:
    arms = {arm: _arm_metrics(cases, arm) for arm in ARMS}
    comparisons = []
    for label, before, after in (
        ("A_TO_B", ARMS[0], ARMS[1]),
        ("B_TO_C", ARMS[1], ARMS[2]),
        ("A_TO_C", ARMS[0], ARMS[2]),
    ):
        deltas = {}
        for field in (
            "cleanCaseMutationRate",
            "claimRegressionRate",
            "defectClaimResolutionRate",
            "repairSuccessRate",
        ):
            old = arms[before][field]["percent"]
            new = arms[after][field]["percent"]
            deltas[field] = round(new - old, 2) if old is not None and new is not None else None
        comparisons.append(
            {"label": label, "from": before, "to": after, "percentagePointDeltas": deltas}
        )
    return {"arms": arms, "comparisons": comparisons}


def _artifact_inventory(run_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(run_dir.rglob("*")):
        if path == run_dir / "result.json" or not path.is_file():
            continue
        if path.is_symlink():
            raise EvalError(f"run artifact must not be a symlink: {path.relative_to(run_dir)}")
        content = path.read_bytes()
        rows.append(
            {
                "path": path.relative_to(run_dir).as_posix(),
                "sha256": sha256_bytes(content),
                "size": len(content),
            }
        )
    return rows


def _new_run_dir(output_root: Path, context_hash: str) -> Path:
    stamp = utc_now().replace("-", "").replace(":", "")
    run_id = f"rcrs-{stamp}-{context_hash[:12]}"
    run_dir = output_root / run_id
    suffix = 2
    while run_dir.exists():
        run_dir = output_root / f"{run_id}-{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True, mode=0o700)
    return run_dir


def run_remediation_safety(
    *,
    suite_path: Path,
    skill_root: Path,
    output_root: Path,
    requested_cases: list[str] | None,
    rounds: int,
    timeout_seconds: int,
    adapter_command: list[str],
    adapter: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    if rounds < 1 or rounds > 5:
        raise EvalError("--rounds must be between 1 and 5")
    if timeout_seconds < 1:
        raise EvalError("--case-timeout must be positive")
    adapter_errors = schema_errors(adapter, ADAPTER_SCHEMA)
    if adapter_errors:
        raise EvalError("adapter description is invalid: " + "; ".join(adapter_errors))
    capabilities = adapter.get("capabilities", {})
    expected_capabilities = {
        "operations": ["REVIEW", "REPAIR"],
        "reviewSandbox": "read-only",
        "repairSandbox": "workspace-write",
        "fixtureMutationBoundary": "RUNNER_STAGED_ROOT",
    }
    if (
        adapter.get("schema") != "review-craft.eval-adapter.v5"
        or capabilities != expected_capabilities
    ):
        raise EvalError("remediation safety requires an adapter.v5 with REVIEW and REPAIR")
    suite = read_json(suite_path)
    suite_errors = validate_remediation_suite(suite)
    if suite_errors:
        raise EvalError("remediation suite is invalid: " + "; ".join(suite_errors))
    all_ids = [case["id"] for case in suite["cases"]]
    requested = requested_cases or all_ids
    if len(requested) != len(set(requested)):
        raise EvalError("remediation case selections must be unique")
    unknown = sorted(set(requested) - set(all_ids))
    if unknown:
        raise EvalError(f"unknown remediation cases: {', '.join(unknown)}")
    requested_set = set(requested)
    selected = [case for case in suite["cases"] if case["id"] in requested_set]
    selected_ids = [case["id"] for case in selected]
    source = source_metadata()
    review_templates = {arm: path.read_bytes() for arm, path in REVIEW_PROMPTS.items()}
    repair_template = REPAIR_PROMPT.read_bytes()
    context_hash = sha256_json(
        {
            "adapter": adapter,
            "adapterCommand": adapter_command,
            "source": source,
            "suite": sha256_bytes(suite_path.read_bytes()),
            "skill": tree_sha256(skill_root),
            "reviewPrompts": {arm: sha256_bytes(value) for arm, value in review_templates.items()},
            "repairPrompt": sha256_bytes(repair_template),
            "selection": selected_ids,
            "rounds": rounds,
        }
    )
    run_dir = _new_run_dir(output_root, context_hash)
    shutil.copyfile(suite_path, run_dir / "suite.json")
    prompt_root = run_dir / "prompt-templates"
    for arm, value in review_templates.items():
        write_bytes(prompt_root / f"{arm.lower()}.md", value)
    write_bytes(prompt_root / "repair.md", repair_template)
    started_at = utc_now()
    schedules = []
    case_records = []

    with tempfile.TemporaryDirectory(prefix="review-craft-remediation-stage-") as stage:
        stage_root = Path(stage)
        baselines = []
        for case_index, case in enumerate(selected, start=1):
            case_dir = run_dir / f"cases/{case_index:03d}-{case['id']}"
            baseline_source = case_dir / "baseline-source"
            fixture_source = (ROOT / case["fixture"]).resolve(strict=True)
            _copy_snapshot(fixture_source, baseline_source)
            baseline_oracle, baseline_receipt = _run_oracle(
                case=case,
                target=baseline_source,
                output_dir=case_dir,
                label="baseline",
                run_dir=run_dir,
            )
            if not _baseline_matches(case, baseline_oracle):
                raise EvalError(
                    f"case {case['id']}: live baseline oracle does not match suite declaration"
                )
            baselines.append(
                (case_index, case, baseline_source, baseline_oracle, baseline_receipt)
            )

        # Close every declared baseline before the first model invocation so a later
        # contaminated case cannot leave a partially executed experiment.
        for (
            case_index,
            case,
            baseline_source,
            baseline_oracle,
            baseline_receipt,
        ) in baselines:
            order = ARMS[(case_index - 1) % len(ARMS) :] + ARMS[: (case_index - 1) % len(ARMS)]
            schedules.append({"caseId": case["id"], "order": list(order)})
            arm_records = []
            for arm in order:
                arm_records.append(
                    _run_arm(
                        run_dir=run_dir,
                        stage_root=stage_root,
                        case_index=case_index,
                        case=case,
                        arm=arm,
                        baseline_source=baseline_source,
                        baseline_oracle=baseline_oracle,
                        skill_root=skill_root,
                        review_template=review_templates[arm],
                        repair_template=repair_template,
                        adapter_command=adapter_command,
                        rounds=rounds,
                        timeout_seconds=timeout_seconds,
                    )
                )
            case_records.append(
                {
                    "id": case["id"],
                    "class": case["class"],
                    "pairId": case["pairId"],
                    "baselineSourceTreeSha256": tree_sha256(baseline_source),
                    "baselineSourceArtifact": _artifact_path(run_dir, baseline_source),
                    "baselineOracle": baseline_receipt,
                    "baselineClaims": [
                        {"id": row["id"], "role": row["role"], "status": row["status"]}
                        for row in baseline_oracle["claims"]
                    ],
                    "arms": arm_records,
                }
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
    arm_statuses = [arm["status"] for case in case_records for arm in case["arms"]]
    payload = {
        "schema": "review-craft.eval-remediation-run.v1",
        "runId": run_dir.name,
        "status": overall_status([{"status": status} for status in arm_statuses]),
        "startedAt": started_at,
        "completedAt": utc_now(),
        "roundLimit": rounds,
        "source": source,
        "suite": {
            "artifact": "suite.json",
            "sha256": file_hash(run_dir / "suite.json"),
            "selectedCaseIds": selected_ids,
            "fullSuite": selected_ids == all_ids,
        },
        "skill": {
            "version": (skill_root / "VERSION").read_text(encoding="utf-8").strip(),
            "treeSha256": tree_sha256(skill_root),
        },
        "adapter": {"description": adapter, "command": adapter_command},
        "schedule": schedules,
        "cases": case_records,
        "metrics": compute_metrics(case_records),
        "artifacts": [],
        "contentSha256": "0" * 64,
    }
    payload["artifacts"] = _artifact_inventory(run_dir)
    payload["contentSha256"] = sha256_json(
        {key: value for key, value in payload.items() if key != "contentSha256"}
    )
    write_json(run_dir / "result.json", payload)
    errors = validate_remediation_run(run_dir)
    if errors:
        raise EvalError("generated remediation run is invalid: " + "; ".join(errors))
    return run_dir, payload


def validate_remediation_run(run_dir: Path) -> list[str]:
    errors: list[str] = []
    try:
        run_dir = run_dir.expanduser().resolve(strict=True)
        payload = read_json(run_dir / "result.json")
    except (OSError, json.JSONDecodeError) as error:
        return [f"result.json: {error}"]
    errors.extend(schema_errors(payload, RUN_SCHEMA))
    if errors:
        return errors
    expected_hash = sha256_json(
        {key: value for key, value in payload.items() if key != "contentSha256"}
    )
    if payload["contentSha256"] != expected_hash:
        errors.append("result contentSha256 mismatch")
    listed_paths = [row["path"] for row in payload["artifacts"]]
    if len(listed_paths) != len(set(listed_paths)):
        errors.append("artifact inventory contains duplicate paths")
    actual_paths = sorted(
        path.relative_to(run_dir).as_posix()
        for path in run_dir.rglob("*")
        if path.is_file() and path != run_dir / "result.json"
    )
    if sorted(listed_paths) != actual_paths:
        errors.append("artifact inventory does not match run files")
    for row in payload["artifacts"]:
        if not _safe_relative(row["path"]):
            errors.append(f"unsafe artifact path: {row['path']!r}")
            continue
        try:
            path = safe_artifact(run_dir, row["path"])
        except EvalError as error:
            errors.append(str(error))
            continue
        if path.is_symlink() or not path.is_file():
            errors.append(f"artifact is not a regular file: {row['path']}")
            continue
        content = path.read_bytes()
        if len(content) != row["size"]:
            errors.append(f"artifact size mismatch: {row['path']}")
        if sha256_bytes(content) != row["sha256"]:
            errors.append(f"artifact sha256 mismatch: {row['path']}")
    try:
        suite_path = safe_artifact(run_dir, payload["suite"]["artifact"])
        suite = read_json(suite_path)
    except (EvalError, OSError, json.JSONDecodeError) as error:
        errors.append(f"suite artifact: {error}")
        suite = None
    if suite is not None:
        errors.extend(f"suite:{error}" for error in validate_remediation_suite(suite))
        if file_hash(suite_path) != payload["suite"]["sha256"]:
            errors.append("suite sha256 mismatch")
        selected = payload["suite"]["selectedCaseIds"]
        if [case["id"] for case in payload["cases"]] != selected:
            errors.append("result cases do not match selected case ids")
    adapter_errors = schema_errors(payload["adapter"]["description"], ADAPTER_SCHEMA)
    errors.extend(f"adapter:{error}" for error in adapter_errors)
    if payload["adapter"]["description"].get("schema") != "review-craft.eval-adapter.v5":
        errors.append("remediation run requires adapter.v5")
    expected_schedule = []
    for index, case in enumerate(payload["cases"]):
        order = ARMS[index % len(ARMS) :] + ARMS[: index % len(ARMS)]
        expected_schedule.append({"caseId": case["id"], "order": list(order)})
        actual_arms = [row["arm"] for row in case["arms"]]
        if actual_arms != list(order):
            errors.append(f"case {case['id']}: arm order does not match schedule")
        for arm in case["arms"]:
            indices = [row["round"] for row in arm["rounds"]]
            if indices != list(range(1, len(indices) + 1)):
                errors.append(f"case {case['id']} arm {arm['arm']}: rounds are not contiguous")
            if len(indices) > payload["roundLimit"]:
                errors.append(f"case {case['id']} arm {arm['arm']}: round limit exceeded")
    if payload["schedule"] != expected_schedule:
        errors.append("schedule does not match canonical Latin-square rotation")
    if payload["metrics"] != compute_metrics(payload["cases"]):
        errors.append("metrics do not match canonical recomputation")
    if not source_stable(payload["source"]):
        errors.append("source stability fields do not close")
    arm_statuses = [arm["status"] for case in payload["cases"] for arm in case["arms"]]
    expected_status = overall_status([{"status": status} for status in arm_statuses])
    if payload["status"] != expected_status:
        errors.append("run status does not match arm statuses")
    return errors
