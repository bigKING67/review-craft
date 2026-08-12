#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--fixture-root")
    parser.add_argument("--skill-root")
    parser.add_argument("--evidence-root")
    parser.add_argument("--prompt-file")
    parser.add_argument("--output-schema")
    parser.add_argument("--output-file")
    parser.add_argument("--treatment")
    parser.add_argument("--case-id")
    parser.add_argument("--operation", choices=("review", "repair"), default="review")
    parser.add_argument("--workspace-marker")
    parser.add_argument("--workspace-key")
    parser.add_argument("--round-number", type=int)
    parser.add_argument(
        "--mode",
        choices=(
            "valid",
            "invalid",
            "mutate-source",
            "duplicate-decisions",
            "negative-finding",
            "usage",
            "invalid-usage",
            "remediation-claimed-mismatch",
            "remediation-regression",
            "remediation-scope-violation",
            "timeout",
        ),
        default="valid",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.describe:
        print(
            json.dumps(
                {
                    "schema": "review-craft.eval-adapter.v5",
                    "name": "synthetic-contract-adapter",
                    "version": "test-only",
                    "model": "deterministic-fixture",
                    "reasoning": "none",
                    "adapterVersion": "0.1.0",
                    "evidenceKind": "SYNTHETIC_CONTRACT",
                    "provider": {
                        "name": "synthetic",
                        "baseUrl": None,
                        "wireApi": "responses",
                        "requiresOpenAIAuth": False,
                        "supportsWebsockets": False,
                    },
                    "isolation": {
                        "homeMatchesCodexHome": True,
                        "ignoreUserConfig": True,
                        "ignoreRules": True,
                        "allowCodexHomeExtensions": False,
                        "codexHomeSystemFileCount": 0,
                        "codexHomeSystemTreeSha256": hashlib.sha256(b"[]").hexdigest(),
                        "codexHomeExtensionFileCount": 0,
                        "codexHomeExtensionTreeSha256": hashlib.sha256(b"[]").hexdigest(),
                    },
                    "usage": {
                        "protocol": "review-craft.eval-usage.v1",
                        "transport": "ENV_PATH",
                        "environmentVariable": "REVIEW_CRAFT_EVAL_USAGE_OUTPUT",
                    },
                    "toolTrace": {
                        "protocol": "review-craft.eval-tool-trace.v1",
                        "transport": "ENV_PATH",
                        "environmentVariable": "REVIEW_CRAFT_EVAL_TOOL_TRACE_OUTPUT",
                    },
                    "capabilities": {
                        "operations": ["REVIEW", "REPAIR"],
                        "reviewSandbox": "read-only",
                        "repairSandbox": "workspace-write",
                        "fixtureMutationBoundary": "RUNNER_STAGED_ROOT",
                    },
                },
                sort_keys=True,
            )
        )
        return 0
    ablation_treatments = {
        "ORDINARY_PROMPT",
        "RISK_LENS_REVIEW",
        "REVIEW_CRAFT_EVIDENCE_LOOP",
    }
    remediation_treatments = {
        "ORDINARY_NAIVE_LOOP",
        "REVIEW_CRAFT_UNGATED_LOOP",
        "REVIEW_CRAFT_EVIDENCE_GATED_LOOP",
    }
    if args.mode == "timeout":
        time.sleep(2)
    if args.treatment in ablation_treatments:
        skill_entries = list(Path(args.skill_root).iterdir())
        evidence_expected = args.treatment == "REVIEW_CRAFT_EVIDENCE_LOOP"
        if evidence_expected:
            if args.evidence_root is None or not (Path(args.skill_root) / "VERSION").is_file():
                return 4
        elif args.evidence_root is not None or skill_entries:
            return 4
    if args.treatment in remediation_treatments:
        skill_expected = args.treatment != "ORDINARY_NAIVE_LOOP"
        if skill_expected != bool(list(Path(args.skill_root).iterdir())):
            return 4
    fixture = Path(args.fixture_root)
    if args.mode == "mutate-source":
        mutation_root = fixture if args.treatment in remediation_treatments else Path.cwd()
        (mutation_root / ".eval-source-mutation-test").write_text(
            "mutated\n", encoding="utf-8"
        )
    if args.treatment in remediation_treatments and args.operation == "repair":
        changed = False
        source = fixture / "bounded_add.py"
        if source.is_file():
            content = source.read_text(encoding="utf-8")
            if "> 0x100" in content:
                source.write_text(content.replace("> 0x100", "> 0xFF"), encoding="utf-8")
                changed = True
            elif args.mode == "remediation-regression" and "> 0xFF" in content:
                source.write_text(content.replace("> 0xFF", "> 0x100"), encoding="utf-8")
                changed = True
        claimed_paths = ["bounded_add.py"] if changed else []
        if args.mode == "remediation-scope-violation":
            (fixture / "unexpected.py").write_text("UNEXPECTED = True\n", encoding="utf-8")
            changed = True
            claimed_paths.append("unexpected.py")
        if args.mode == "remediation-claimed-mismatch":
            claimed_paths = []
        output = {
            "schema": "review-craft.eval-remediation-repair-output.v1",
            "outcome": "CHANGED" if changed else "NO_CHANGE",
            "claimedPaths": claimed_paths,
            "summary": "Synthetic remediation repair output.",
        }
    elif args.treatment in remediation_treatments:
        positive = bool(args.case_id and args.case_id.endswith("-positive"))
        if args.mode == "remediation-regression":
            positive = True
        source = next(path for path in fixture.glob("*.py"))
        output = {
            "schema": "review-craft.eval-remediation-review-output.v1",
            "findingDetected": positive,
            "decision": "CLEAN_UP" if positive else "KEEP",
            "hypothesis": "The risk surface may violate its declared invariant.",
            "confirmCondition": "The bound behavior oracle observes an invariant violation.",
            "falsifyCondition": "The bound behavior oracle passes every relevant claim.",
            "locations": [{"path": source.name, "lineStart": 1, "lineEnd": 20}],
            "evidence": ["Synthetic remediation review evidence."],
            "confidence": "HIGH",
            "summary": "Synthetic remediation review output.",
        }
    elif (fixture / "storage.py").is_file():
        output = {
            "schema": "review-craft.eval-host-output.v1",
            "findingDetected": True,
            "decisions": ["CLEAN_UP"],
            "locations": [{"path": "storage.py", "lineStart": 1, "lineEnd": 7}],
            "evidence": [
                {
                    "claim": "The exception path returns apparent success.",
                    "locations": [{"path": "storage.py", "lineStart": 1, "lineEnd": 7}],
                }
            ],
            "confidence": "HIGH",
            "summary": "Synthetic positive contract output.",
        }
    elif (fixture / "parser.py").is_file():
        output = {
            "schema": "review-craft.eval-host-output.v1",
            "findingDetected": False,
            "decisions": ["KEEP"],
            "locations": [{"path": "parser.py", "lineStart": 1, "lineEnd": 155}],
            "evidence": [
                {
                    "claim": "The state machine is cohesive and behaviorally tested.",
                    "locations": [{"path": "parser.py", "lineStart": 1, "lineEnd": 155}],
                }
            ],
            "confidence": "HIGH",
            "summary": "Synthetic negative contract output.",
        }
    elif (fixture / "tool.py").is_file():
        output = {
            "schema": "review-craft.eval-host-output.v1",
            "findingDetected": False,
            "decisions": ["KEEP"],
            "locations": [{"path": "tool.py", "lineStart": 1, "lineEnd": 18}],
            "evidence": [
                {
                    "claim": "The small CLI is cohesive and does not need service boundaries.",
                    "locations": [{"path": "tool.py", "lineStart": 1, "lineEnd": 18}],
                }
            ],
            "confidence": "HIGH",
            "summary": "Synthetic clean-negative contract output.",
        }
    elif args.case_id and args.case_id.endswith("-positive"):
        source = next(path for path in fixture.glob("*.py"))
        output = {
            "schema": "review-craft.eval-host-output.v1",
            "findingDetected": True,
            "decisions": ["CLEAN_UP"],
            "locations": [{"path": source.name, "lineStart": 1, "lineEnd": 20}],
            "evidence": [
                {
                    "claim": "Synthetic positive ablation output.",
                    "locations": [{"path": source.name, "lineStart": 1, "lineEnd": 20}],
                }
            ],
            "confidence": "HIGH",
            "summary": "Synthetic positive ablation output.",
        }
    elif args.case_id and args.case_id.endswith("-negative"):
        source = next(path for path in fixture.glob("*.py"))
        output = {
            "schema": "review-craft.eval-host-output.v1",
            "findingDetected": False,
            "decisions": ["KEEP"],
            "locations": [{"path": source.name, "lineStart": 1, "lineEnd": 20}],
            "evidence": [
                {
                    "claim": "Synthetic negative ablation output.",
                    "locations": [{"path": source.name, "lineStart": 1, "lineEnd": 20}],
                }
            ],
            "confidence": "HIGH",
            "summary": "Synthetic negative ablation output.",
        }
    else:
        return 3
    if args.mode == "negative-finding" and (fixture / "parser.py").is_file():
        output.update(
            {
                "findingDetected": True,
                "decisions": ["CLEAN_UP"],
                "evidence": [
                    {
                        "claim": "A separate valid issue exists in the negative fixture.",
                        "locations": [
                            {"path": "parser.py", "lineStart": 1, "lineEnd": 1}
                        ],
                    }
                ],
                "summary": "Synthetic contaminated-negative output.",
            }
        )
    if args.mode == "duplicate-decisions":
        output["decisions"].append(output["decisions"][0])
    rendered = (
        "{not valid json\n"
        if args.mode == "invalid"
        else json.dumps(output, ensure_ascii=False, indent=2) + "\n"
    )
    Path(args.output_file).write_text(rendered, encoding="utf-8")
    usage_output = os.environ.get("REVIEW_CRAFT_EVAL_USAGE_OUTPUT")
    if usage_output and args.mode == "usage":
        Path(usage_output).write_text(
            json.dumps(
                {
                    "schema": "review-craft.eval-usage.v1",
                    "availability": "AVAILABLE",
                    "collector": {
                        "name": "synthetic",
                        "version": "0.1.0",
                        "format": "synthetic-v1",
                    },
                    "inputTokens": 100,
                    "cachedInputTokens": 25,
                    "cacheWriteInputTokens": 5,
                    "outputTokens": 20,
                    "reasoningOutputTokens": 10,
                    "totalTokens": 120,
                    "turnCount": 1,
                    "toolCalls": {
                        "total": 2,
                        "byType": {
                            "commandExecution": 2,
                            "fileChange": 0,
                            "mcpToolCall": 0,
                            "collabToolCall": 0,
                            "webSearch": 0,
                        },
                    },
                    "unavailableReason": None,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    elif usage_output and args.mode == "invalid-usage":
        Path(usage_output).write_text("{}\n", encoding="utf-8")
    tool_trace_output = os.environ.get("REVIEW_CRAFT_EVAL_TOOL_TRACE_OUTPUT")
    if tool_trace_output:
        verification = args.treatment == "REVIEW_CRAFT_EVIDENCE_LOOP"
        case_id = args.case_id or fixture.name
        trace = {
            "schema": "review-craft.eval-tool-trace.v1",
            "items": (
                [
                    {
                        "sequence": 0,
                        "type": "commandExecution",
                        "status": "completed",
                        "command": f"python3 $EVIDENCE/verify_case.py --case {case_id} --target .",
                        "exitCode": 0,
                        "outputBytes": 2,
                        "outputSha256": hashlib.sha256(b"{} ").hexdigest(),
                    }
                ]
                if verification
                else []
            ),
        }
        Path(tool_trace_output).write_text(
            json.dumps(trace, sort_keys=True) + "\n", encoding="utf-8"
        )
    print("synthetic adapter completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
