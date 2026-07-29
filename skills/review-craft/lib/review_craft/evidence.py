from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from .constants import ARTIFACT_PATHS
from .jsonio import (
    canonical_compact,
    read_json,
    read_jsonl,
    sha256_bytes,
    write_jsonl,
)
from .repository import inspect_git, worktree_fingerprint


def resolve_repository_directory(root: Path, relative: str, field: str) -> Path:
    if Path(relative).is_absolute():
        raise ValueError(f"{field}: expected a repository-relative path")
    resolved = (root / relative).resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{field}: escapes the repository root") from error
    if not resolved.is_dir():
        raise ValueError(f"{field}: expected a directory")
    return resolved


def run_evidence_command(
    run_dir_value: str | Path,
    command_name: str,
    *,
    started_at: str,
) -> tuple[int, dict[str, Any]]:
    run_dir = Path(run_dir_value).expanduser().resolve(strict=True)
    manifest = read_json(run_dir / "review-manifest.json")
    state = read_json(run_dir / "run-state.json")
    target = Path(state["targetRoot"]).resolve(strict=True)
    command = manifest["configuration"]["commands"].get(command_name)
    if not isinstance(command, dict):
        raise ValueError(f"unknown configured command: {command_name}")
    cwd = resolve_repository_directory(target, command.get("cwd", "."), "command.cwd")
    argv = command["argv"]
    timeout = command.get("timeoutSeconds", 600)
    before_status = inspect_git(target).status
    before_worktree = worktree_fingerprint(target)
    start = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
            shell=False,
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as error:
        timed_out = True
        exit_code = 124
        stdout = error.stdout or b""
        stderr = error.stderr or b""
    duration_ms = round((time.monotonic() - start) * 1000)
    after_state = inspect_git(target)
    after_worktree = worktree_fingerprint(target)
    mutation = before_worktree != after_worktree or before_status != after_state.status
    command_id = sha256_bytes(
        canonical_compact(
            {
                "name": command_name,
                "argv": argv,
                "startedAt": started_at,
                "cwd": command.get("cwd", "."),
            }
        ).encode("utf-8")
    )[:16]
    evidence_dir = run_dir / "evidence" / "commands"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = evidence_dir / f"{command_id}.stdout"
    stderr_path = evidence_dir / f"{command_id}.stderr"
    stdout_path.write_bytes(stdout)
    stderr_path.write_bytes(stderr)
    receipt = {
        "id": command_id,
        "name": command_name,
        "argv": argv,
        "cwd": command.get("cwd", "."),
        "startedAt": started_at,
        "durationMs": duration_ms,
        "exitCode": exit_code,
        "timedOut": timed_out,
        "stdoutArtifact": stdout_path.relative_to(run_dir).as_posix(),
        "stderrArtifact": stderr_path.relative_to(run_dir).as_posix(),
        "beforeStatusSha256": sha256_bytes(
            before_status.encode("utf-8", errors="surrogateescape")
        ),
        "afterStatusSha256": sha256_bytes(
            after_state.status.encode("utf-8", errors="surrogateescape")
        ),
        "repositoryMutationDetected": mutation,
    }
    commands_path = run_dir / ARTIFACT_PATHS["commands"]
    rows = read_jsonl(commands_path)
    rows.append(receipt)
    write_jsonl(commands_path, rows)
    if mutation and not manifest["configuration"]["policy"].get(
        "allowRepositoryMutation", False
    ):
        return 3, receipt
    return exit_code, receipt
