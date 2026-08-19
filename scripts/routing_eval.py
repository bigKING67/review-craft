#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from eval_contracts import (
    ADAPTER_SCHEMA,
    EvalError,
    read_json,
    schema_errors,
    sha256_bytes,
    sha256_json,
    utc_now,
    write_json,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = ROOT / "evals/specs/routing-cases.json"
DEFAULT_SKILL = ROOT / "skills/review-craft"
CASES_SCHEMA = ROOT / "evals/schemas/eval-routing-cases.schema.json"
OUTPUT_SCHEMA = ROOT / "evals/schemas/eval-routing-output.schema.json"
RESULT_SCHEMA = ROOT / "evals/schemas/eval-routing-result.schema.json"
CURRENT_RESULT = ROOT / "evals/routing-results/current/result.json"
THRESHOLDS = {
    "implicitPrecision": 95.0,
    "implicitRecall": 85.0,
    "explicitActivationRate": 100.0,
    "workflowAccuracy": 90.0,
    "highCostFalseTriggerRate": 3.0,
}
ROUTES = {
    "REVIEW_CRAFT": (
        "Repository-wide, module-level, large-diff, or high-assurance focused engineering "
        "review with explicit coverage, evidence validation, deterministic reporting, or "
        "explicitly authorized fix and delivery verification."
    ),
    "NATIVE_REVIEW": "Quick PR, commit, small diff, single-function, or local code review.",
    "DESIGN_CRAFT": "Visual UI/UX, interaction, motion, or design-system critique.",
    "CODEX_SECURITY": (
        "Threat modeling, vulnerability discovery, exploitability, attack paths, PoCs, or "
        "security remediation validation."
    ),
    "DIRECT_TASK": "Direct implementation, explanation, lint, test, or other bounded task.",
}
WORKFLOWS = {
    "BOUNDED": "One narrow evidence-backed Review Craft decision without canonical artifacts.",
    "REVIEW": "Canonical repository review.",
    "DIFF": "Canonical large-diff review.",
    "FOCUS": "Canonical review limited to selected engineering dimensions.",
    "REMEDIATION": "Verification of an explicitly authorized fix.",
    "DELIVERY": "Post-commit delivery attestation.",
    "NONE": "No Review Craft workflow.",
}
LIMITATIONS = [
    (
        "ROUTING_DECISION measures structured route selection from the bound metadata and "
        "prompts; Codex JSONL does not expose a stable Skill activation receipt."
    ),
    (
        "The result is bound to one suite, metadata revision, model, reasoning profile, and "
        "adapter version and does not establish cross-model routing stability."
    ),
]


def _percent(numerator: int, denominator: int) -> float:
    if denominator == 0:
        raise EvalError("routing metric denominator is zero")
    return round(numerator * 100.0 / denominator, 2)


def _skill_description(skill_root: Path) -> str:
    text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = text.split("---\n", 2)[1]
    match = re.search(r"^description:\s*(.*)$", frontmatter, re.MULTILINE)
    if match is None:
        raise EvalError("SKILL.md description is missing")
    first = match.group(1).strip()
    if first in {">", "|", ">-", "|-"}:
        lines = frontmatter[match.end() :].splitlines()
        value = " ".join(line.strip() for line in lines if line.startswith("  "))
    else:
        value = first.strip('"')
    if not value:
        raise EvalError("SKILL.md description is empty")
    return value


def _agents_interface_bytes(skill_root: Path) -> bytes:
    text = (skill_root / "agents/openai.yaml").read_text(encoding="utf-8")
    interface = text.split("\npolicy:\n", 1)[0].rstrip() + "\n"
    return interface.encode("utf-8")


def skill_metadata(skill_root: Path) -> dict[str, str]:
    description_hash = sha256_bytes(_skill_description(skill_root).encode("utf-8"))
    interface_hash = sha256_bytes(_agents_interface_bytes(skill_root))
    return {
        "descriptionSha256": description_hash,
        "agentsInterfaceSha256": interface_hash,
        "bindingSha256": sha256_json(
            {"descriptionSha256": description_hash, "agentsInterfaceSha256": interface_hash}
        ),
    }


def validate_routing_suite(payload: dict[str, Any]) -> list[str]:
    errors = schema_errors(payload, CASES_SCHEMA)
    if errors:
        return errors
    cases = payload["cases"]
    identifiers = [case["id"] for case in cases]
    if len(set(identifiers)) != len(identifiers):
        errors.append("routing case ids must be unique")
    if sum(case["language"] == "zh-CN" for case in cases) != 30:
        errors.append("routing suite must contain exactly 30 zh-CN cases")
    if sum(case["language"] == "en" for case in cases) != 30:
        errors.append("routing suite must contain exactly 30 English cases")
    if sum(case["invocation"] == "implicit" for case in cases) != 50:
        errors.append("routing suite must contain exactly 50 implicit cases")
    if sum(case["invocation"] == "explicit" for case in cases) != 10:
        errors.append("routing suite must contain exactly 10 explicit cases")
    for case in cases:
        if case["expectedRoute"] not in case["allowedRoutes"]:
            errors.append(f"case {case['id']}: expectedRoute must be allowed")
        overlap = set(case["allowedRoutes"]) & set(case["forbiddenRoutes"])
        if overlap:
            errors.append(f"case {case['id']}: allowedRoutes and forbiddenRoutes overlap")
        explicitly_named = "$review-craft" in case["prompt"]
        if explicitly_named != (case["invocation"] == "explicit"):
            errors.append(f"case {case['id']}: invocation does not match explicit Skill naming")
        if case["invocation"] == "explicit" and case["expectedRoute"] != "REVIEW_CRAFT":
            errors.append(f"case {case['id']}: explicit invocation must expect REVIEW_CRAFT")
        if case["expectedRoute"] != "REVIEW_CRAFT" and case["expectedWorkflow"] != "NONE":
            errors.append(f"case {case['id']}: non-Review-Craft route must use workflow NONE")
    return errors


def _prompt(case: dict[str, Any], description: str) -> str:
    route_lines = "\n".join(f"- {name}: {text}" for name, text in ROUTES.items())
    workflow_lines = "\n".join(f"- {name}: {text}" for name, text in WORKFLOWS.items())
    return f"""You are evaluating a routing decision, not performing the requested task.

Choose exactly one route and one Review Craft workflow. Use NONE when the selected route is
not REVIEW_CRAFT. Explicit `$review-craft` naming overrides implicit routing ambiguity, but
does not change the product's safety or authorization boundaries.

Current Review Craft description:
{description}

Available routes:
{route_lines}

Review Craft workflows:
{workflow_lines}

Invocation form: {case['invocation']}
User request:
{case['prompt']}

Return only the JSON object required by the output schema. Keep rationale under 500 characters.
"""


def _adapter_description(adapter_command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        [*adapter_command, "--describe"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise EvalError(completed.stderr.strip() or "routing adapter describe failed")
    payload = json.loads(completed.stdout)
    errors = schema_errors(payload, ADAPTER_SCHEMA)
    if errors:
        raise EvalError("routing adapter description is invalid: " + "; ".join(errors))
    return payload


def _run_case(
    *,
    adapter_command: list[str],
    case: dict[str, Any],
    repetition: int,
    skill_root: Path,
    fixture_root: Path,
    artifact_root: Path,
    timeout: int,
    description: str,
) -> dict[str, Any]:
    stem = f"r{repetition:02d}-{case['id']}"
    prompt_path = artifact_root / f"{stem}.md"
    output_path = artifact_root / f"{stem}.json"
    prompt_path.write_text(_prompt(case, description), encoding="utf-8")
    command = [
        *adapter_command,
        "--fixture-root",
        str(fixture_root),
        "--skill-root",
        str(skill_root),
        "--prompt-file",
        str(prompt_path),
        "--output-schema",
        str(OUTPUT_SCHEMA),
        "--output-file",
        str(output_path),
        "--treatment",
        "ROUTING_DECISION",
        "--case-id",
        case["id"],
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise EvalError(
            f"routing case {case['id']} repetition {repetition} failed: "
            + (completed.stderr.strip() or completed.stdout.strip() or "no output")
        )
    if not output_path.is_file():
        raise EvalError(f"routing adapter did not write {output_path.name}")
    output = read_json(output_path)
    output_errors = schema_errors(output, OUTPUT_SCHEMA)
    if output_errors:
        raise EvalError(
            f"routing case {case['id']} returned invalid output: " + "; ".join(output_errors)
        )
    route_correct = (
        output["selectedRoute"] in case["allowedRoutes"]
        and output["selectedRoute"] not in case["forbiddenRoutes"]
    )
    high_cost_false_trigger = bool(
        case["invocation"] == "implicit"
        and case["highCostNegative"]
        and output["selectedRoute"] == "REVIEW_CRAFT"
    )
    return {
        "id": case["id"],
        "repetition": repetition,
        "invocation": case["invocation"],
        "expectedRoute": case["expectedRoute"],
        "expectedWorkflow": case["expectedWorkflow"],
        "highCostNegative": case["highCostNegative"],
        "selectedRoute": output["selectedRoute"],
        "selectedWorkflow": output["selectedWorkflow"],
        "confidence": output["confidence"],
        "rationale": output["rationale"],
        "routeCorrect": route_correct,
        "workflowCorrect": (
            route_correct and output["selectedWorkflow"] == case["expectedWorkflow"]
        ),
        "highCostFalseTrigger": high_cost_false_trigger,
    }


def _metrics(cases: list[dict[str, Any]], repetition: int) -> dict[str, Any]:
    rows = [row for row in cases if row["repetition"] == repetition]
    implicit = [row for row in rows if row["invocation"] == "implicit"]
    selected = [row for row in implicit if row["selectedRoute"] == "REVIEW_CRAFT"]
    expected = [row for row in implicit if row["expectedRoute"] == "REVIEW_CRAFT"]
    explicit = [row for row in rows if row["invocation"] == "explicit"]
    high_cost = [
        row
        for row in implicit
        if row["expectedRoute"] != "REVIEW_CRAFT" and row["highCostNegative"]
    ]
    values = {
        "implicitPrecision": _percent(
            sum(row["expectedRoute"] == "REVIEW_CRAFT" for row in selected), len(selected)
        ),
        "implicitRecall": _percent(
            sum(row["selectedRoute"] == "REVIEW_CRAFT" for row in expected), len(expected)
        ),
        "explicitActivationRate": _percent(
            sum(row["routeCorrect"] for row in explicit), len(explicit)
        ),
        "workflowAccuracy": _percent(sum(row["workflowCorrect"] for row in rows), len(rows)),
        "highCostFalseTriggerRate": _percent(
            sum(row["highCostFalseTrigger"] for row in high_cost), len(high_cost)
        ),
    }
    passed = (
        values["implicitPrecision"] >= THRESHOLDS["implicitPrecision"]
        and values["implicitRecall"] >= THRESHOLDS["implicitRecall"]
        and values["explicitActivationRate"] >= THRESHOLDS["explicitActivationRate"]
        and values["workflowAccuracy"] >= THRESHOLDS["workflowAccuracy"]
        and values["highCostFalseTriggerRate"] <= THRESHOLDS["highCostFalseTriggerRate"]
    )
    return {"repetition": repetition, **values, "passed": passed}


def run_routing(
    *,
    suite_path: Path,
    skill_root: Path,
    output_root: Path,
    repetitions: int,
    timeout: int,
    adapter_command: list[str],
) -> Path:
    if repetitions < 2:
        raise EvalError("routing evaluation requires at least two repetitions")
    suite = read_json(suite_path)
    errors = validate_routing_suite(suite)
    if errors:
        raise EvalError("routing suite is invalid: " + "; ".join(errors))
    adapter = _adapter_description(adapter_command)
    started_at = utc_now()
    run_name = started_at.replace(":", "").replace("-", "")
    run_dir = output_root / f"routing-{run_name}"
    if run_dir.exists():
        raise EvalError(f"routing output already exists: {run_dir}")
    artifacts = run_dir / "artifacts"
    fixture_root = run_dir / "fixture"
    artifacts.mkdir(parents=True)
    fixture_root.mkdir()
    copied_suite = run_dir / "suite.json"
    shutil.copyfile(suite_path, copied_suite)
    description = _skill_description(skill_root)
    rows = []
    for repetition in range(1, repetitions + 1):
        for case in suite["cases"]:
            rows.append(
                _run_case(
                    adapter_command=adapter_command,
                    case=case,
                    repetition=repetition,
                    skill_root=skill_root,
                    fixture_root=fixture_root,
                    artifact_root=artifacts,
                    timeout=timeout,
                    description=description,
                )
            )
    metrics = [_metrics(rows, repetition) for repetition in range(1, repetitions + 1)]
    payload = {
        "schema": "review-craft.eval-routing-result.v1",
        "evaluationKind": "ROUTING_DECISION",
        "startedAt": started_at,
        "completedAt": utc_now(),
        "suite": {"artifact": copied_suite.name, "sha256": sha256_bytes(copied_suite.read_bytes())},
        "skillMetadata": skill_metadata(skill_root),
        "adapter": adapter,
        "repetitions": repetitions,
        "thresholds": THRESHOLDS,
        "cases": rows,
        "metrics": metrics,
        "passed": all(row["passed"] for row in metrics),
        "limitations": LIMITATIONS,
    }
    payload["contentSha256"] = sha256_json(payload)
    result_path = run_dir / "result.json"
    write_json(result_path, payload)
    result_errors = validate_routing_result(result_path, skill_root=skill_root)
    if result_errors:
        raise EvalError("routing result failed self-validation: " + "; ".join(result_errors))
    return result_path


def validate_routing_result(path: Path, *, skill_root: Path | None = None) -> list[str]:
    try:
        payload = read_json(path)
    except (OSError, json.JSONDecodeError) as error:
        return [str(error)]
    errors = schema_errors(payload, RESULT_SCHEMA)
    if errors:
        return errors
    expected_hash = sha256_json(
        {key: value for key, value in payload.items() if key != "contentSha256"}
    )
    if payload["contentSha256"] != expected_hash:
        errors.append("contentSha256 does not match routing result")
    suite_path = path.parent / payload["suite"]["artifact"]
    if not suite_path.is_file():
        errors.append("bound routing suite is missing")
        return errors
    if sha256_bytes(suite_path.read_bytes()) != payload["suite"]["sha256"]:
        errors.append("bound routing suite sha256 does not match")
        return errors
    suite = read_json(suite_path)
    errors.extend(f"suite:{error}" for error in validate_routing_suite(suite))
    if payload["thresholds"] != THRESHOLDS:
        errors.append("routing thresholds do not match the current evaluation contract")
    if len(payload["cases"]) != len(suite["cases"]) * payload["repetitions"]:
        errors.append("routing result case count does not match suite and repetitions")
    errors.extend(_result_case_errors(payload, suite))
    recomputed = [
        _metrics(payload["cases"], repetition)
        for repetition in range(1, payload["repetitions"] + 1)
    ]
    if payload["metrics"] != recomputed:
        errors.append("routing metrics do not match case results")
    if payload["passed"] != all(row["passed"] for row in recomputed):
        errors.append("routing passed flag does not match repetition metrics")
    if skill_root is not None and payload["skillMetadata"] != skill_metadata(skill_root):
        errors.append("routing result is not bound to current Skill metadata")
    return errors


def _result_case_errors(payload: dict[str, Any], suite: dict[str, Any]) -> list[str]:
    expected_by_id = {case["id"]: case for case in suite["cases"]}
    errors: list[str] = []
    for repetition in range(1, payload["repetitions"] + 1):
        rows = [row for row in payload["cases"] if row["repetition"] == repetition]
        identifiers = [row["id"] for row in rows]
        if len(identifiers) != len(set(identifiers)) or set(identifiers) != set(expected_by_id):
            errors.append(
                f"routing repetition {repetition} does not contain the suite exactly once"
            )
            continue
        for row in rows:
            case = expected_by_id[row["id"]]
            for field in ("invocation", "expectedRoute", "expectedWorkflow", "highCostNegative"):
                if row[field] != case[field]:
                    errors.append(
                        f"routing case {row['id']} repetition {repetition}: "
                        f"{field} binding mismatch"
                    )
            route_correct = (
                row["selectedRoute"] in case["allowedRoutes"]
                and row["selectedRoute"] not in case["forbiddenRoutes"]
            )
            workflow_correct = (
                route_correct and row["selectedWorkflow"] == case["expectedWorkflow"]
            )
            high_cost_false_trigger = bool(
                case["invocation"] == "implicit"
                and case["highCostNegative"]
                and row["selectedRoute"] == "REVIEW_CRAFT"
            )
            for field, expected in (
                ("routeCorrect", route_correct),
                ("workflowCorrect", workflow_correct),
                ("highCostFalseTrigger", high_cost_false_trigger),
            ):
                if row[field] != expected:
                    errors.append(
                        f"routing case {row['id']} repetition {repetition}: {field} mismatch"
                    )
    return errors


def _implicit_policy(skill_root: Path) -> bool:
    text = (skill_root / "agents/openai.yaml").read_text(encoding="utf-8")
    match = re.search(r"^\s*allow_implicit_invocation:\s*(true|false)\s*$", text, re.MULTILINE)
    if match is None:
        raise EvalError("agents/openai.yaml must declare allow_implicit_invocation")
    return match.group(1) == "true"


def routing_policy_errors(
    *, skill_root: Path = DEFAULT_SKILL, result_path: Path = CURRENT_RESULT
) -> list[str]:
    try:
        enabled = _implicit_policy(skill_root)
    except (OSError, EvalError) as error:
        return [str(error)]
    if not enabled:
        return []
    if not result_path.is_file():
        return ["implicit invocation requires a current, content-bound routing result"]
    errors = validate_routing_result(result_path, skill_root=skill_root)
    if not errors:
        payload = read_json(result_path)
        if payload.get("passed") is not True:
            errors.append("implicit invocation requires every routing repetition to pass")
        if payload.get("adapter", {}).get("evidenceKind") != "REAL_HOST":
            errors.append("implicit invocation requires REAL_HOST routing evidence")
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review Craft routing decision evaluation")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--result", required=True)
    validate.add_argument("--skill-root", default=str(DEFAULT_SKILL))
    summarize = subparsers.add_parser("summarize")
    summarize.add_argument("--result", required=True)
    gate = subparsers.add_parser("gate")
    gate.add_argument("--skill-root", default=str(DEFAULT_SKILL))
    gate.add_argument("--result", default=str(CURRENT_RESULT))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "gate":
        errors = routing_policy_errors(
            skill_root=Path(args.skill_root).resolve(), result_path=Path(args.result).resolve()
        )
    else:
        path = Path(args.result).resolve()
        errors = validate_routing_result(
            path,
            skill_root=Path(args.skill_root).resolve()
            if args.command == "validate"
            else None,
        )
    if errors:
        for error in errors:
            print(f"review-craft routing: {error}", file=sys.stderr)
        return 1
    if args.command == "summarize":
        payload = read_json(Path(args.result).resolve())
        print(
            json.dumps(
                {"passed": payload["passed"], "metrics": payload["metrics"]},
                sort_keys=True,
            )
        )
    else:
        print(json.dumps({"schema": "review-craft.routing-gate.v1", "valid": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (EvalError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"review-craft routing: {error}", file=sys.stderr)
        raise SystemExit(2) from None
