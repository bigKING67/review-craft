#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "contracts/upstreams.json"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
ALLOWED_STATUSES = {"tracked", "selective_absorbed", "absorbed", "rejected"}
SOURCE_FIELDS = {
    "id",
    "repository",
    "branch",
    "reviewedRevision",
    "reviewedAt",
    "license",
    "status",
    "sourcePaths",
    "absorbedSurfaces",
    "excludedSurfaces",
}


class UpstreamContractError(ValueError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate pinned Review Craft upstreams and optionally compare remote heads"
    )
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser.add_argument(
        "--remote",
        action="store_true",
        help="explicitly enable git ls-remote checks; offline validation is the default",
    )
    return parser.parse_args()


def _string(source: dict[str, Any], field: str, *, index: int) -> str:
    value = source.get(field)
    if not isinstance(value, str) or not value.strip():
        raise UpstreamContractError(f"sources[{index}].{field}: expected a non-empty string")
    return value


def _string_list(source: dict[str, Any], field: str, *, index: int) -> list[str]:
    value = source.get(field)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        raise UpstreamContractError(
            f"sources[{index}].{field}: expected a non-empty string array"
        )
    if len(set(value)) != len(value):
        raise UpstreamContractError(f"sources[{index}].{field}: duplicate values")
    return value


def _validate_repository(identifier: str, repository: str, *, index: int) -> None:
    parsed = urlparse(repository)
    try:
        port = parsed.port
    except ValueError as error:
        raise UpstreamContractError(
            f"sources[{index}].repository: invalid URL port"
        ) from error
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise UpstreamContractError(
            f"sources[{index}].repository: expected a public https://github.com URL"
        )
    repository_id = parsed.path.strip("/").removesuffix(".git")
    if repository_id != identifier:
        raise UpstreamContractError(
            f"sources[{index}].repository: path must match id {identifier!r}"
        )


def _validate_source_paths(paths: list[str], *, index: int) -> None:
    for value in paths:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
            raise UpstreamContractError(
                f"sources[{index}].sourcePaths: unsafe repository-relative path {value!r}"
            )


def load_contract(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise UpstreamContractError("upstream contract must be a JSON object")
    if payload.get("schema") != "review-craft.upstreams.v1":
        raise UpstreamContractError("unsupported upstream contract schema")
    if set(payload) != {"schema", "sources"}:
        raise UpstreamContractError("upstream contract contains unknown or missing root fields")
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise UpstreamContractError("upstream contract must contain at least one source")
    seen: set[str] = set()
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise UpstreamContractError(f"sources[{index}]: expected an object")
        unexpected = sorted(set(source) - SOURCE_FIELDS)
        missing = sorted(SOURCE_FIELDS - set(source))
        if unexpected or missing:
            raise UpstreamContractError(
                f"sources[{index}]: unexpected fields {unexpected}; missing fields {missing}"
            )
        identifier = _string(source, "id", index=index)
        if identifier in seen:
            raise UpstreamContractError(f"sources[{index}].id: duplicate source {identifier!r}")
        seen.add(identifier)
        repository = _string(source, "repository", index=index)
        _validate_repository(identifier, repository, index=index)
        branch = _string(source, "branch", index=index)
        if (
            BRANCH_PATTERN.fullmatch(branch) is None
            or ".." in branch
            or "//" in branch
            or branch.endswith("/")
        ):
            raise UpstreamContractError(f"sources[{index}].branch: invalid Git branch")
        revision = _string(source, "reviewedRevision", index=index)
        if SHA_PATTERN.fullmatch(revision) is None:
            raise UpstreamContractError(
                f"sources[{index}].reviewedRevision: expected a lowercase full Git SHA"
            )
        reviewed_at = _string(source, "reviewedAt", index=index)
        try:
            date.fromisoformat(reviewed_at)
        except ValueError as error:
            raise UpstreamContractError(
                f"sources[{index}].reviewedAt: expected YYYY-MM-DD"
            ) from error
        _string(source, "license", index=index)
        status = _string(source, "status", index=index)
        if status not in ALLOWED_STATUSES:
            raise UpstreamContractError(f"sources[{index}].status: unsupported status {status!r}")
        _validate_source_paths(_string_list(source, "sourcePaths", index=index), index=index)
        absorbed = _string_list(source, "absorbedSurfaces", index=index)
        excluded = _string_list(source, "excludedSurfaces", index=index)
        overlap = sorted(set(absorbed) & set(excluded))
        if overlap:
            raise UpstreamContractError(
                f"sources[{index}]: absorbed and excluded surfaces overlap: {overlap}"
            )
    return payload


def _remote_revision(source: dict[str, Any]) -> str:
    reference = f"refs/heads/{source['branch']}"
    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        completed = subprocess.run(
            ["git", "ls-remote", "--exit-code", source["repository"], reference],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("git ls-remote was unavailable or timed out") from error
    if completed.returncode != 0:
        raise RuntimeError(f"git ls-remote exited {completed.returncode}")
    rows = [line.split() for line in completed.stdout.splitlines() if line.strip()]
    matches = [row[0] for row in rows if len(row) == 2 and row[1] == reference]
    if len(matches) != 1 or SHA_PATTERN.fullmatch(matches[0]) is None:
        raise RuntimeError("git ls-remote returned an invalid branch result")
    return matches[0]


def evaluate(contract: dict[str, Any], *, remote: bool) -> tuple[dict[str, Any], int]:
    results: list[dict[str, Any]] = []
    exit_code = 0
    for source in contract["sources"]:
        result = {
            "id": source["id"],
            "branch": source["branch"],
            "reviewedRevision": source["reviewedRevision"],
            "status": "NOT_CHECKED",
        }
        if remote:
            try:
                revision = _remote_revision(source)
                result["remoteRevision"] = revision
                if revision == source["reviewedRevision"]:
                    result["status"] = "CURRENT"
                else:
                    result["status"] = "UPDATED"
                    exit_code = max(exit_code, 1)
            except RuntimeError as error:
                result["status"] = "UNREACHABLE"
                result["error"] = str(error)
                exit_code = 2
        results.append(result)
    return {
        "schema": "review-craft.upstream-check.v1",
        "mode": "remote" if remote else "offline",
        "sources": results,
    }, exit_code


def main() -> int:
    args = parse_args()
    try:
        contract = load_contract(Path(args.contract).expanduser().resolve(strict=True))
        payload, exit_code = evaluate(contract, remote=args.remote)
    except (OSError, json.JSONDecodeError, UpstreamContractError) as error:
        print(f"review-craft upstream check: {error}", file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
