from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .constants import DEFAULT_EXCLUDES
from .jsonio import canonical_compact, sha256_bytes


@dataclass(frozen=True)
class GitState:
    is_repository: bool
    root: Path
    revision: str | None
    branch: str | None
    remote: str | None
    status: str
    unborn: bool


def run_git(root: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        check=check,
    )


def safe_remote(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if re.match(r"^https?://", value, flags=re.IGNORECASE):
        split = urlsplit(value)
        host = split.hostname or ""
        if split.port:
            host = f"{host}:{split.port}"
        return urlunsplit((split.scheme, host, split.path, split.query, split.fragment))
    return value


def inspect_git(target: Path) -> GitState:
    target = target.resolve(strict=True)
    probe = run_git(target, "rev-parse", "--show-toplevel")
    if probe.returncode != 0:
        return GitState(False, target, None, None, None, "", False)
    root = Path(probe.stdout.decode("utf-8", errors="replace").strip()).resolve()
    revision_result = run_git(root, "rev-parse", "HEAD")
    revision = (
        revision_result.stdout.decode("utf-8", errors="replace").strip()
        if revision_result.returncode == 0
        else None
    )
    branch_result = run_git(root, "symbolic-ref", "--short", "-q", "HEAD")
    branch = (
        branch_result.stdout.decode("utf-8", errors="replace").strip()
        if branch_result.returncode == 0
        else None
    )
    remote_result = run_git(root, "remote", "get-url", "origin")
    remote = (
        safe_remote(remote_result.stdout.decode("utf-8", errors="replace"))
        if remote_result.returncode == 0
        else None
    )
    status = run_git(root, "status", "--porcelain=v1", "-z").stdout.decode(
        "utf-8", errors="surrogateescape"
    )
    return GitState(True, root, revision, branch, remote, status, revision is None)


def _matches(path: str, patterns: Iterable[str]) -> bool:
    candidates = {path, f"{path}/"}
    for pattern in patterns:
        normalized = pattern.removeprefix("./")
        if any(fnmatch.fnmatch(candidate, normalized) for candidate in candidates):
            return True
        if normalized.endswith("/**"):
            prefix = normalized[:-3].rstrip("/")
            if path == prefix or path.startswith(prefix + "/"):
                return True
    return False


def _is_in_scope(path: str, scopes: Iterable[str]) -> bool:
    for scope in scopes:
        normalized = scope.removeprefix("./").rstrip("/")
        if not normalized or normalized == ".":
            return True
        if path == normalized or path.startswith(normalized + "/"):
            return True
    return False


def _git_paths(root: Path) -> list[str]:
    result = run_git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip())
    return sorted(
        {
            item.decode("utf-8", errors="surrogateescape")
            for item in result.stdout.split(b"\0")
            if item
        }
    )


def _filesystem_paths(root: Path) -> list[str]:
    paths: list[str] = []
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = sorted(name for name in names if name != ".git")
        for name in sorted(files):
            paths.append((Path(directory) / name).relative_to(root).as_posix())
        for name in names:
            candidate = Path(directory) / name
            if candidate.is_symlink():
                paths.append(candidate.relative_to(root).as_posix())
    return sorted(set(paths))


def _file_record(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    stat = path.lstat()
    if path.is_symlink():
        target = os.readlink(path)
        return {
            "path": relative,
            "kind": "symlink",
            "sizeBytes": stat.st_size,
            "sha256": sha256_bytes(target.encode("utf-8", errors="surrogateescape")),
            "linkTarget": target,
            "binary": False,
        }
    if not path.is_file():
        return {
            "path": relative,
            "kind": "other",
            "sizeBytes": stat.st_size,
            "sha256": sha256_bytes(b""),
            "binary": True,
        }
    digest = hashlib.sha256()
    preview = b""
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            if len(preview) < 8192:
                preview += chunk[: 8192 - len(preview)]
            digest.update(chunk)
            size += len(chunk)
    try:
        preview.decode("utf-8")
        invalid_utf8 = False
    except UnicodeDecodeError:
        invalid_utf8 = True
    return {
        "path": relative,
        "kind": "file",
        "sizeBytes": size,
        "sha256": digest.hexdigest(),
        "binary": b"\0" in preview or invalid_utf8,
    }


def inventory(
    root: Path,
    *,
    scopes: Iterable[str] = (".",),
    excludes: Iterable[str] = (),
    generated: Iterable[str] = (),
    vendored: Iterable[str] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    root = root.resolve(strict=True)
    git_state = inspect_git(root)
    paths = _git_paths(root) if git_state.is_repository else _filesystem_paths(root)
    exclude_patterns = tuple(DEFAULT_EXCLUDES) + tuple(excludes)
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for relative in paths:
        relative = Path(relative).as_posix()
        if not _is_in_scope(relative, scopes):
            excluded.append({"path": relative, "reason": "outside configured scope"})
            continue
        if _matches(relative, exclude_patterns):
            excluded.append({"path": relative, "reason": "matched exclude pattern"})
            continue
        try:
            record = _file_record(root, relative)
        except OSError as error:
            record = {
                "path": relative,
                "kind": "unreadable",
                "sizeBytes": 0,
                "sha256": sha256_bytes(str(error).encode("utf-8")),
                "binary": True,
                "readError": type(error).__name__,
            }
        if _matches(relative, generated):
            record["classification"] = "generated"
        elif _matches(relative, vendored):
            record["classification"] = "vendored"
        else:
            record["classification"] = "source"
        included.append(record)
    return sorted(included, key=lambda item: item["path"]), sorted(
        excluded, key=lambda item: item["path"]
    )


def fingerprint_inventory(records: list[dict[str, Any]]) -> str:
    stable = [
        {
            "path": row["path"],
            "kind": row["kind"],
            "sha256": row["sha256"],
            "classification": row["classification"],
        }
        for row in sorted(records, key=lambda item: item["path"])
    ]
    return sha256_bytes(canonical_compact(stable).encode("utf-8"))


def tracked_fingerprint(root: Path) -> str | None:
    state = inspect_git(root)
    if not state.is_repository:
        return None
    result = run_git(root, "ls-files", "-z")
    records: list[dict[str, str]] = []
    for item in result.stdout.split(b"\0"):
        if not item:
            continue
        relative = item.decode("utf-8", errors="surrogateescape")
        path = root / relative
        if path.is_symlink():
            payload = os.readlink(path).encode("utf-8", errors="surrogateescape")
        elif path.is_file():
            payload = path.read_bytes()
        else:
            payload = b"<missing>"
        records.append({"path": relative, "sha256": sha256_bytes(payload)})
    return sha256_bytes(canonical_compact(sorted(records, key=lambda row: row["path"])).encode())


def repository_identity(state: GitState, records: list[dict[str, Any]]) -> str:
    seed = {
        "remote": state.remote,
        "revision": state.revision,
        "branch": state.branch,
        "sourceFingerprint": fingerprint_inventory(records),
    }
    return sha256_bytes(canonical_compact(seed).encode("utf-8"))
