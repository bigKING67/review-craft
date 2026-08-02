#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/complexity-budget.json"
MESSAGE_PATTERN = re.compile(r"`(?P<function>[^`]+)` is too complex \((?P<value>\d+) > 0\)")


def _load_contract() -> list[dict[str, Any]]:
    payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if payload.get("schema") != "review-craft.complexity-budget.v1":
        raise ValueError("unsupported complexity budget schema")
    limits = payload.get("limits")
    if not isinstance(limits, list) or not limits:
        raise ValueError("complexity budget must contain limits")
    return limits


def _ruff_complexities(paths: list[str]) -> dict[tuple[str, str], int]:
    completed = subprocess.run(
        [
            "ruff",
            "check",
            "--select",
            "C901",
            "--output-format",
            "json",
            "--config",
            "lint.mccabe.max-complexity=0",
            *paths,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode not in {0, 1}:
        raise RuntimeError(completed.stderr.strip() or "ruff C901 audit failed")
    rows = json.loads(completed.stdout)
    result: dict[tuple[str, str], int] = {}
    for row in rows:
        match = MESSAGE_PATTERN.fullmatch(row.get("message", ""))
        if match is None:
            continue
        path = Path(row["filename"]).resolve().relative_to(ROOT).as_posix()
        result[(path, match.group("function"))] = int(match.group("value"))
    return result


def main() -> int:
    try:
        limits = _load_contract()
        paths = sorted({row["path"] for row in limits})
        measured = _ruff_complexities(paths)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, json.JSONDecodeError) as error:
        print(f"review-craft complexity budget: {error}", file=sys.stderr)
        return 2
    errors: list[str] = []
    results = []
    for row in limits:
        key = (row["path"], row["function"])
        actual = measured.get(key)
        maximum = row["maximum"]
        if actual is None:
            errors.append(f"{key[0]}:{key[1]}: function was not measured")
            continue
        if actual > maximum:
            errors.append(f"{key[0]}:{key[1]}: complexity {actual} exceeds budget {maximum}")
        results.append(
            {
                "path": key[0],
                "function": key[1],
                "actual": actual,
                "maximum": maximum,
            }
        )
    if errors:
        print("review-craft complexity budget failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "schema": "review-craft.complexity-budget-result.v1",
                "valid": True,
                "functions": results,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
