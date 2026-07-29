from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

if os.name == "nt":
    import msvcrt

    fcntl = None
else:
    import fcntl

    msvcrt = None

from .constants import ARTIFACT_PATHS
from .jsonio import (
    canonical_compact,
    read_json,
    read_jsonl,
    sha256_bytes,
    write_jsonl,
)
from .repository import inspect_git, worktree_fingerprint

LOCK_NAME = ".evidence-command.lock"
LOCK_POLL_SECONDS = 0.05


def _try_lock(handle: BinaryIO) -> bool:
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except (BlockingIOError, OSError):
        return False
    return True


def _unlock(handle: BinaryIO) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    else:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def evidence_run_lock(run_dir: Path, *, lease_seconds: int) -> Iterator[None]:
    lock_path = run_dir / LOCK_NAME
    wait_deadline = time.monotonic() + lease_seconds
    lock_path.touch(mode=0o600, exist_ok=True)
    with lock_path.open("r+b") as handle:
        if lock_path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        while not _try_lock(handle):
            if time.monotonic() >= wait_deadline:
                raise TimeoutError(
                    "timed out waiting for another evidence command to finish"
                ) from None
            time.sleep(LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            _unlock(handle)


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
    started_at: str | None = None,
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
    # A run owns one receipt stream. Serialize the complete command lifecycle so
    # sequence assignment and before/after mutation evidence remain attributable.
    with evidence_run_lock(run_dir, lease_seconds=timeout + 30):
        return _run_evidence_command_locked(
            run_dir=run_dir,
            target=target,
            command_name=command_name,
            command=command,
            cwd=cwd,
            argv=argv,
            timeout=timeout,
            started_at=started_at or _utc_now(),
            allow_repository_mutation=manifest["configuration"]["policy"].get(
                "allowRepositoryMutation", False
            ),
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_evidence_command_locked(
    *,
    run_dir: Path,
    target: Path,
    command_name: str,
    command: dict[str, Any],
    cwd: Path,
    argv: list[str],
    timeout: int,
    started_at: str,
    allow_repository_mutation: bool,
) -> tuple[int, dict[str, Any]]:
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
    commands_path = run_dir / ARTIFACT_PATHS["commands"]
    rows = read_jsonl(commands_path)
    existing_sequences = [
        value
        for index, row in enumerate(rows)
        for value in [row.get("sequence", index)]
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
    ]
    sequence = max(existing_sequences, default=-1) + 1
    command_id = sha256_bytes(
        canonical_compact(
            {
                "name": command_name,
                "argv": argv,
                "startedAt": started_at,
                "cwd": command.get("cwd", "."),
                "sequence": sequence,
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
        "sequence": sequence,
        "name": command_name,
        "argv": argv,
        "cwd": command.get("cwd", "."),
        "startedAt": started_at,
        "durationMs": duration_ms,
        "exitCode": exit_code,
        "timedOut": timed_out,
        "stdoutArtifact": stdout_path.relative_to(run_dir).as_posix(),
        "stderrArtifact": stderr_path.relative_to(run_dir).as_posix(),
        "stdoutSha256": sha256_bytes(stdout),
        "stderrSha256": sha256_bytes(stderr),
        "beforeStatusSha256": sha256_bytes(
            before_status.encode("utf-8", errors="surrogateescape")
        ),
        "afterStatusSha256": sha256_bytes(
            after_state.status.encode("utf-8", errors="surrogateescape")
        ),
        "repositoryMutationDetected": mutation,
    }
    rows.append(receipt)
    write_jsonl(commands_path, rows)
    if mutation and not allow_repository_mutation:
        return 3, receipt
    return exit_code, receipt
