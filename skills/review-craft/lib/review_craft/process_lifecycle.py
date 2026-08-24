from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TERMINATION_GRACE_SECONDS = 5.0
FORCE_WAIT_SECONDS = 5.0
_WINDOWS_JOB_ATTRIBUTE = "_review_craft_windows_job_handle"
_WINDOWS_JOB_LOCK = threading.Lock()


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool
    process_tree_cleanup: str = "NOT_REQUIRED"


def _assign_windows_kill_job(process: subprocess.Popen[Any]) -> None:
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    information = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    information.BasicLimitInformation.LimitFlags = 0x00002000
    try:
        if not kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(information), ctypes.sizeof(information)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel32.AssignProcessToJobObject(job, process._handle):  # type: ignore[attr-defined]
            raise ctypes.WinError(ctypes.get_last_error())
    except BaseException:
        kernel32.CloseHandle(job)
        raise
    setattr(process, _WINDOWS_JOB_ATTRIBUTE, int(job))


def open_process_tree(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdin: Any = None,
    stdout: Any = subprocess.PIPE,
    stderr: Any = subprocess.PIPE,
    text: bool = False,
    bufsize: int = -1,
) -> subprocess.Popen[Any]:
    options: dict[str, object] = {
        "cwd": cwd,
        "env": env,
        "stdin": stdin,
        "stdout": stdout,
        "stderr": stderr,
        "text": text,
        "bufsize": bufsize,
        "shell": False,
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(argv, **options)  # type: ignore[arg-type]
    # Some managed Windows hosts prohibit nested Job Objects. Keep the
    # documented taskkill fallback instead of rejecting valid commands.
    with contextlib.suppress(OSError):
        _assign_windows_kill_job(process)
    return process


def _posix_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_posix_group(process: subprocess.Popen[Any]) -> str:
    process_group = process.pid
    existed = _posix_group_exists(process_group)
    if not existed:
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=FORCE_WAIT_SECONDS)
        return "NOT_REQUIRED" if process.poll() is not None else "FAILED"
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return "NOT_REQUIRED"
    deadline = time.monotonic() + TERMINATION_GRACE_SECONDS
    while time.monotonic() < deadline and _posix_group_exists(process_group):
        process.poll()
        time.sleep(0.02)
    if _posix_group_exists(process_group):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process_group, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=FORCE_WAIT_SECONDS)
    deadline = time.monotonic() + FORCE_WAIT_SECONDS
    while time.monotonic() < deadline and _posix_group_exists(process_group):
        process.poll()
        time.sleep(0.02)
    return (
        "CONFIRMED"
        if process.poll() is not None and not _posix_group_exists(process_group)
        else "FAILED"
    )


def _close_windows_job(process: subprocess.Popen[Any]) -> bool:
    handle = getattr(process, _WINDOWS_JOB_ATTRIBUTE, None)
    if handle is None:
        return False
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    with _WINDOWS_JOB_LOCK:
        handle = getattr(process, _WINDOWS_JOB_ATTRIBUTE, None)
        if handle is None:
            return True
        closed = bool(kernel32.CloseHandle(wintypes.HANDLE(handle)))
        if closed:
            delattr(process, _WINDOWS_JOB_ATTRIBUTE)
        return closed


def _terminate_windows_tree(process: subprocess.Popen[Any]) -> str:
    had_job = getattr(process, _WINDOWS_JOB_ATTRIBUTE, None) is not None
    closed = _close_windows_job(process) if had_job else False
    taskkill_confirmed = False
    if not had_job:
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=FORCE_WAIT_SECONDS,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            completed = None
        taskkill_confirmed = completed is not None and completed.returncode == 0
        if (completed is None or completed.returncode != 0) and process.poll() is None:
            process.kill()
    try:
        process.wait(timeout=FORCE_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=FORCE_WAIT_SECONDS)
    return (
        "CONFIRMED"
        if process.poll() is not None and (closed or taskkill_confirmed)
        else "FAILED"
    )


def terminate_process_tree(process: subprocess.Popen[Any]) -> str:
    if os.name == "nt":
        return _terminate_windows_tree(process)
    return _terminate_posix_group(process)


def finalize_process_tree(process: subprocess.Popen[Any]) -> str:
    if os.name == "nt":
        had_job = getattr(process, _WINDOWS_JOB_ATTRIBUTE, None) is not None
        closed = _close_windows_job(process) if had_job else False
        return "CONFIRMED" if had_job and closed else "NOT_REQUIRED"
    if _posix_group_exists(process.pid):
        return terminate_process_tree(process)
    return "NOT_REQUIRED"


def _communicate_process(
    process: subprocess.Popen[Any], timeout: int
) -> ProcessResult:
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        cleanup = finalize_process_tree(process)
        return ProcessResult(process.returncode, stdout, stderr, False, cleanup)
    except subprocess.TimeoutExpired:
        cleanup = terminate_process_tree(process)
        stdout, stderr = process.communicate()
        return ProcessResult(124, stdout, stderr, True, cleanup)


def run_process(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
) -> ProcessResult:
    """Run fixed argv and clean its inherited process tree on every escape path."""
    process = open_process_tree(argv, cwd=cwd, env=env)
    try:
        return _communicate_process(process, timeout)
    except BaseException:
        terminate_process_tree(process)
        with contextlib.suppress(BaseException):
            process.communicate(timeout=FORCE_WAIT_SECONDS)
        raise
