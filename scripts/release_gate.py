#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

COMMANDS = (
    (
        "tests",
        [
            "uv",
            "run",
            "--locked",
            "python",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_*.py",
        ],
    ),
    ("source validation", ["uv", "run", "--locked", "python", "scripts/validate.py"]),
    ("lint", ["uv", "run", "--locked", "ruff", "check", "."]),
    ("package boundary", [sys.executable, "scripts/package_check.py"]),
)


def main() -> int:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for name, command in COMMANDS:
        print(f"==> {name}", flush=True)
        completed = subprocess.run(command, cwd=ROOT, env=environment)
        if completed.returncode != 0:
            print(f"release gate failed at {name}", file=sys.stderr)
            return completed.returncode
    print("review-craft release gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
