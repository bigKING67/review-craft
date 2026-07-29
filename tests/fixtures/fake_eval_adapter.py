#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--fixture-root")
    parser.add_argument("--skill-root")
    parser.add_argument("--prompt-file")
    parser.add_argument("--output-schema")
    parser.add_argument("--output-file")
    parser.add_argument("--treatment")
    parser.add_argument(
        "--mode",
        choices=("valid", "invalid", "mutate-source", "duplicate-decisions"),
        default="valid",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.describe:
        print(
            json.dumps(
                {
                    "schema": "review-craft.eval-adapter.v2",
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
                        "ignoreUserConfig": True,
                        "ignoreRules": True,
                        "allowCodexHomeExtensions": False,
                        "codexHomeSystemFileCount": 0,
                        "codexHomeSystemTreeSha256": hashlib.sha256(b"[]").hexdigest(),
                        "codexHomeExtensionFileCount": 0,
                        "codexHomeExtensionTreeSha256": hashlib.sha256(b"[]").hexdigest(),
                    },
                },
                sort_keys=True,
            )
        )
        return 0
    if args.mode == "mutate-source":
        (Path.cwd() / ".eval-source-mutation-test").write_text(
            "mutated\n", encoding="utf-8"
        )
    fixture = Path(args.fixture_root)
    if (fixture / "storage.py").is_file():
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
    else:
        return 3
    if args.mode == "duplicate-decisions":
        output["decisions"].append(output["decisions"][0])
    rendered = (
        "{not valid json\n"
        if args.mode == "invalid"
        else json.dumps(output, ensure_ascii=False, indent=2) + "\n"
    )
    Path(args.output_file).write_text(rendered, encoding="utf-8")
    print("synthetic adapter completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
