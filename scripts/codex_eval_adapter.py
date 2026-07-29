#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ADAPTER_VERSION = "0.1.0"


def codex_version() -> str:
    executable = shutil.which("codex")
    if executable is None:
        return "unavailable"
    completed = subprocess.run(
        [executable, "--version"],
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() or completed.stderr.strip() or "unknown"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Codex CLI adapter for Review Craft evals")
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning", required=True)
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--fixture-root")
    parser.add_argument("--skill-root")
    parser.add_argument("--prompt-file")
    parser.add_argument("--output-schema")
    parser.add_argument("--output-file")
    parser.add_argument("--treatment")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.describe:
        print(
            json.dumps(
                {
                    "schema": "review-craft.eval-adapter.v1",
                    "name": "codex-cli",
                    "version": codex_version(),
                    "model": args.model,
                    "reasoning": args.reasoning,
                    "adapterVersion": ADAPTER_VERSION,
                    "evidenceKind": "REAL_HOST",
                },
                sort_keys=True,
            )
        )
        return 0
    required = {
        "fixture-root": args.fixture_root,
        "skill-root": args.skill_root,
        "prompt-file": args.prompt_file,
        "output-schema": args.output_schema,
        "output-file": args.output_file,
        "treatment": args.treatment,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        print(f"missing adapter arguments: {', '.join(missing)}", file=sys.stderr)
        return 2
    if args.treatment == "CODEX_NATIVE_REVIEW":
        print("CODEX_NATIVE_REVIEW is not implemented by this adapter", file=sys.stderr)
        return 2
    executable = shutil.which("codex")
    if executable is None:
        print("codex executable is unavailable", file=sys.stderr)
        return 127
    fixture_root = Path(args.fixture_root).resolve(strict=True)
    skill_root = Path(args.skill_root).resolve(strict=True)
    prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    command = [
        executable,
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--color",
        "never",
        "--cd",
        str(fixture_root),
        "--add-dir",
        str(skill_root),
        "--model",
        args.model,
        "--config",
        f'model_reasoning_effort="{args.reasoning}"',
        "--output-schema",
        str(Path(args.output_schema).resolve(strict=True)),
        "--output-last-message",
        str(Path(args.output_file).resolve()),
        "-",
    ]
    completed = subprocess.run(command, input=prompt, text=True)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
