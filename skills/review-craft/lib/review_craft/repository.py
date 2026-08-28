from __future__ import annotations

import codecs
import fnmatch
import hashlib
import os
import re
import subprocess
from collections.abc import Iterable, Mapping
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
    if re.match(r"^[a-z][a-z0-9+.-]*://", value, flags=re.IGNORECASE):
        try:
            split = urlsplit(value)
            host = split.hostname or ""
        except ValueError:
            # A malformed remote is not safe identity material because userinfo
            # cannot be separated from the host reliably.
            return None
        if not host:
            return None
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        try:
            port = split.port
        except ValueError:
            return None
        if port:
            host = f"{host}:{port}"
        return urlunsplit((split.scheme, host, split.path, "", ""))
    scp_like = re.match(
        r"^[^@/\s]+@(?P<host>\[[^\]]+\]|[^:/\s]+):(?P<path>.+)$",
        value,
    )
    if scp_like:
        path = re.split(r"[?#]", scp_like.group("path"), maxsplit=1)[0]
        return f"{scp_like.group('host')}:{path}" if path else None
    if "@" in value:
        return None
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


def resolve_git_revision(root: Path, value: str) -> str:
    if not value or value.startswith("-") or any(character in value for character in "\0\r\n"):
        raise ValueError("diff base must be a non-option Git revision")
    state = inspect_git(root)
    if not state.is_repository:
        raise ValueError("diff mode requires a Git repository")
    result = run_git(state.root, "rev-parse", "--verify", f"{value}^{{commit}}")
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"invalid diff base {value!r}: {message or 'not a commit'}")
    return result.stdout.decode("ascii").strip()


def git_diff_changes(root: Path, base_revision: str) -> list[dict[str, Any]]:
    state = inspect_git(root)
    if not state.is_repository:
        raise ValueError("diff mode requires a Git repository")
    result = run_git(
        state.root,
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        base_revision,
        "--",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip())
    tokens = [
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    ]
    changes: dict[str, dict[str, Any]] = {}
    index = 0
    while index < len(tokens):
        status_token = tokens[index]
        index += 1
        status = status_token[0]
        if status in {"R", "C"}:
            if index + 1 >= len(tokens):
                raise RuntimeError("git diff returned a truncated rename/copy record")
            previous = tokens[index]
            path = tokens[index + 1]
            index += 2
            row = {
                "path": Path(path).as_posix(),
                "status": status,
                "statusDetail": status_token,
                "previousPath": Path(previous).as_posix(),
                "untracked": False,
            }
        else:
            if index >= len(tokens):
                raise RuntimeError("git diff returned a truncated path record")
            path = tokens[index]
            index += 1
            row = {
                "path": Path(path).as_posix(),
                "status": status,
                "statusDetail": status_token,
                "untracked": False,
            }
        changes[row["path"]] = row
    untracked = run_git(state.root, "ls-files", "--others", "--exclude-standard", "-z")
    if untracked.returncode != 0:
        raise RuntimeError(untracked.stderr.decode("utf-8", errors="replace").strip())
    for item in untracked.stdout.split(b"\0"):
        if not item:
            continue
        path = Path(item.decode("utf-8", errors="surrogateescape")).as_posix()
        changes.setdefault(
            path,
            {
                "path": path,
                "status": "A",
                "statusDetail": "untracked",
                "untracked": True,
            },
        )
    return [changes[path] for path in sorted(changes)]


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
    paths = {
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    }
    # Full-review identity follows the current filesystem. Diff mode supplies
    # deleted paths explicitly so it can preserve their base-revision content.
    return sorted(
        path
        for path in paths
        if (root / path).exists() or (root / path).is_symlink()
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


def repository_paths(root: Path) -> list[str]:
    root = root.resolve(strict=True)
    return _git_paths(root) if inspect_git(root).is_repository else _filesystem_paths(root)


def _binary_preview(preview: bytes, *, complete: bool) -> bool:
    if b"\0" in preview:
        return True
    try:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        decoder.decode(preview, final=complete)
    except UnicodeDecodeError:
        return True
    return False


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
    return {
        "path": relative,
        "kind": "file",
        "sizeBytes": size,
        "sha256": digest.hexdigest(),
        "binary": _binary_preview(preview, complete=size == len(preview)),
    }


def _payload_at_revision(root: Path, revision: str, relative: str) -> bytes:
    result = run_git(root, "show", f"{revision}:{relative}")
    if result.returncode != 0:
        raise OSError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def _file_record_at_revision(root: Path, revision: str, relative: str) -> dict[str, Any]:
    payload = _payload_at_revision(root, revision, relative)
    preview = payload[:8192]
    return {
        "path": relative,
        "kind": "deleted",
        "sizeBytes": len(payload),
        "sha256": sha256_bytes(payload),
        "binary": _binary_preview(preview, complete=len(payload) == len(preview)),
    }


def source_payload(
    root: Path, record: Mapping[str, Any], *, diff_base: str | None
) -> bytes:
    """Read the exact source side represented by one canonical inventory record."""
    root = root.resolve(strict=True)
    relative = record.get("path")
    if not isinstance(relative, str) or not relative:
        raise ValueError("source record path is invalid")
    if record.get("kind") == "deleted":
        if not diff_base:
            raise ValueError("deleted source requires an immutable diff base")
        payload = _payload_at_revision(root, diff_base, relative)
    else:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"source path is not a regular current file: {relative}")
        payload = path.read_bytes()
    if sha256_bytes(payload) != record.get("sha256"):
        raise ValueError(f"source content no longer matches the inventory: {relative}")
    return payload


def inventory(
    root: Path,
    *,
    scopes: Iterable[str] = (".",),
    excludes: Iterable[str] = (),
    generated: Iterable[str] = (),
    vendored: Iterable[str] = (),
    paths: Iterable[str] | None = None,
    deleted_revision: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    root = root.resolve(strict=True)
    git_state = inspect_git(root)
    candidate_paths = (
        sorted(set(paths))
        if paths is not None
        else (_git_paths(root) if git_state.is_repository else _filesystem_paths(root))
    )
    exclude_patterns = tuple(DEFAULT_EXCLUDES) + tuple(excludes)
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for relative in candidate_paths:
        relative = Path(relative).as_posix()
        if not _is_in_scope(relative, scopes):
            excluded.append({"path": relative, "reason": "outside configured scope"})
            continue
        if _matches(relative, exclude_patterns):
            excluded.append({"path": relative, "reason": "matched exclude pattern"})
            continue
        try:
            if (root / relative).exists() or (root / relative).is_symlink():
                record = _file_record(root, relative)
            elif deleted_revision is not None:
                record = _file_record_at_revision(root, deleted_revision, relative)
            else:
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


def inventory_for_mode(
    root: Path,
    *,
    mode: str,
    scopes: Iterable[str] = (".",),
    excludes: Iterable[str] = (),
    generated: Iterable[str] = (),
    vendored: Iterable[str] = (),
    diff_base: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, Any] | None]:
    if mode != "diff":
        records, excluded = inventory(
            root,
            scopes=scopes,
            excludes=excludes,
            generated=generated,
            vendored=vendored,
        )
        return records, excluded, None
    if not diff_base:
        raise ValueError("diff mode requires a diff base")
    base_revision = resolve_git_revision(root, diff_base)
    changes = git_diff_changes(root, base_revision)
    records, excluded = inventory(
        root,
        scopes=scopes,
        excludes=excludes,
        generated=generated,
        vendored=vendored,
        paths=[row["path"] for row in changes],
        deleted_revision=base_revision,
    )
    changes_by_path = {row["path"]: row for row in changes}
    for record in records:
        change = changes_by_path[record["path"]]
        record["diffStatus"] = change["status"]
        if change.get("previousPath"):
            record["previousPath"] = change["previousPath"]
        if change["untracked"]:
            record["untracked"] = True
    included_paths = {row["path"] for row in records}
    excluded_reasons = {row["path"]: row["reason"] for row in excluded}
    scoped_changes = []
    for change in changes:
        row = dict(change)
        row["inScope"] = row["path"] in included_paths
        row["reason"] = "in configured scope" if row["inScope"] else excluded_reasons.get(
            row["path"], "not present in the selected inventory"
        )
        scoped_changes.append(row)
    return records, excluded, {"baseRevision": base_revision, "changes": scoped_changes}


def source_inventory_configuration(
    configuration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the only configuration fields allowed to influence source identity."""
    value = configuration or {}
    return {
        "mode": value.get("mode", "review"),
        "scope": list(value.get("scope", ["."])),
        "exclude": list(value.get("exclude", [])),
        "generated": list(value.get("generated", [])),
        "vendored": list(value.get("vendored", [])),
        "diffBase": value.get("diffBase"),
    }


def inventory_for_configuration(
    root: Path,
    configuration: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, Any] | None]:
    value = source_inventory_configuration(configuration)
    return inventory_for_mode(
        root,
        mode=value["mode"],
        scopes=value["scope"],
        excludes=value["exclude"],
        generated=value["generated"],
        vendored=value["vendored"],
        diff_base=value["diffBase"],
    )


def fingerprint_inventory(records: list[dict[str, Any]]) -> str:
    stable = []
    for row in sorted(records, key=lambda item: item["path"]):
        value = {
            "path": row["path"],
            "kind": row["kind"],
            "sha256": row["sha256"],
            "classification": row["classification"],
        }
        for field in ("diffStatus", "previousPath", "untracked"):
            if field in row:
                value[field] = row[field]
        stable.append(value)
    return sha256_bytes(canonical_compact(stable).encode("utf-8"))


def worktree_fingerprint(
    root: Path,
    *,
    records: Iterable[dict[str, Any]] | None = None,
    configuration: Mapping[str, Any] | None = None,
) -> str:
    """Hash the canonical source projection without reopening excluded content."""
    root = root.resolve(strict=True)
    if records is not None and configuration is not None:
        raise ValueError("worktree fingerprint accepts records or configuration, not both")
    if records is None:
        records, _, _ = inventory_for_configuration(root, configuration)
    return fingerprint_inventory(list(records))


def repository_identity(state: GitState, records: list[dict[str, Any]]) -> str:
    seed = {
        "remote": state.remote,
        "revision": state.revision,
        "branch": state.branch,
        "sourceFingerprint": fingerprint_inventory(records),
    }
    return sha256_bytes(canonical_compact(seed).encode("utf-8"))
