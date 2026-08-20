from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

TERMINATION_GRACE_SECONDS = 1.0
FORCE_WAIT_SECONDS = 5.0


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool


def _popen(
    argv: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    options: dict[str, object] = {
        "cwd": cwd,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        options["start_new_session"] = True
    return subprocess.Popen(argv, **options)  # type: ignore[arg-type]


def _posix_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_posix_group(process: subprocess.Popen[bytes]) -> None:
    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + TERMINATION_GRACE_SECONDS
    while time.monotonic() < deadline and _posix_group_exists(process_group):
        time.sleep(0.02)
    if _posix_group_exists(process_group):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process_group, signal.SIGKILL)


def _terminate_windows_tree(process: subprocess.Popen[bytes]) -> None:
    completed = subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=FORCE_WAIT_SECONDS,
        check=False,
        shell=False,
    )
    if completed.returncode != 0 and process.poll() is None:
        process.kill()


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        _terminate_windows_tree(process)
    else:
        _terminate_posix_group(process)
    try:
        process.wait(timeout=FORCE_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=FORCE_WAIT_SECONDS)


def run_process(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
) -> ProcessResult:
    """Run a fixed argv and terminate its inherited process tree on timeout."""
    process = _popen(argv, cwd, env)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return ProcessResult(process.returncode, stdout, stderr, False)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        stdout, stderr = process.communicate()
        return ProcessResult(124, stdout, stderr, True)
