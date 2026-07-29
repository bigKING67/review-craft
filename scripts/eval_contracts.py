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
    return {
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
