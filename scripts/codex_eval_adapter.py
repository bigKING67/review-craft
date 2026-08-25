#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_LIB = ROOT / "skills/review-craft/lib"
sys.path.insert(0, str(RUNTIME_LIB))

from review_craft.locking import exclusive_file_lock  # noqa: E402
from review_craft.process_lifecycle import (  # noqa: E402
    finalize_process_tree,
    open_process_tree,
    terminate_process_tree,
)

ADAPTER_VERSION = "0.6.13"
PROVIDER_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
PROVIDER_REQUEST_MAX_RETRIES = 0
PROVIDER_STREAM_MAX_RETRIES = 0
PROVIDER_STREAM_IDLE_TIMEOUT_MS = 120_000
PROVIDER_WEBSOCKET_CONNECT_TIMEOUT_MS = 10_000
CODEX_TRANSPORT_LOG_FILTER = (
    "codex_api=debug,codex_api::responses_websocket_timing=off"
)
WEBSOCKET_CONNECTING = "connecting to websocket:"
WEBSOCKET_CONNECTED = "successfully connected to websocket:"
WEBSOCKET_CONNECT_FAILED = "failed to connect to websocket:"
WEBSOCKET_HTTPS_FALLBACK = "Falling back from WebSockets to HTTPS transport."
WEBSOCKET_ACCEPT_PATTERN = re.compile(
    r'("sec-websocket-accept"\s*:\s*)"[^"]*"', re.IGNORECASE
)
USAGE_OUTPUT_ENV = "REVIEW_CRAFT_EVAL_USAGE_OUTPUT"
TOOL_TRACE_OUTPUT_ENV = "REVIEW_CRAFT_EVAL_TOOL_TRACE_OUTPUT"
PROGRESS_OUTPUT_ENV = "REVIEW_CRAFT_EVAL_PROGRESS_OUTPUT"
ISOLATION_OUTPUT_ENV = "REVIEW_CRAFT_EVAL_ISOLATION_OUTPUT"
ISOLATION_PREPARATION_PROTOCOL = "review-craft.eval-isolation-preparation.v1"
INACTIVITY_WARNING_ENV = "REVIEW_CRAFT_EVAL_INACTIVITY_WARNING_SECONDS"
INACTIVITY_DIAGNOSTIC_ENV = "REVIEW_CRAFT_EVAL_INACTIVITY_DIAGNOSTIC_SECONDS"
FIRST_ITEM_TIMEOUT_ENV = "REVIEW_CRAFT_EVAL_FIRST_ITEM_TIMEOUT_SECONDS"
SAMPLE_TIMEOUT_ENV = "REVIEW_CRAFT_EVAL_SAMPLE_TIMEOUT_SECONDS"
REPOSITORY_TOOL_CALL_LIMIT_ENV = (
    "REVIEW_CRAFT_EVAL_REPOSITORY_TOOL_CALL_LIMIT"
)
SKILL_BOOTSTRAP_TOOL_CALL_LIMIT_ENV = (
    "REVIEW_CRAFT_EVAL_SKILL_BOOTSTRAP_TOOL_CALL_LIMIT"
)
TOOL_BUDGET_STATE_ENV = "REVIEW_CRAFT_EVAL_TOOL_BUDGET_STATE"
TOOL_BUDGET_SKILL_ROOT_ENV = "REVIEW_CRAFT_EVAL_TOOL_BUDGET_SKILL_ROOT"
TOOL_BUDGET_STATE_PROTOCOL = "review-craft.eval-tool-budget-state.v2"
TOOL_BUDGET_CONTROL_PROTOCOL = "review-craft.eval-tool-budget-control.v3"
TIMEOUT_CONTROL_PROTOCOL = "review-craft.eval-timeout-control.v2"
TIMEOUT_EXIT_CODE = 124
TOOL_BUDGET_EXIT_CODE = 125
TIMEOUT_FINALIZATION_GRACE_SECONDS = 30
PROCESS_TERMINATION_GRACE_SECONDS = 5
INACTIVITY_POLL_SECONDS = 0.25
TIMEOUT_RACE_SETTLE_SECONDS = 0.1
TOOL_BUDGET_HOOK_TIMEOUT_SECONDS = 10
MAX_RECOVERABLE_BOOTSTRAP_PREREQUISITE_BLOCKS = 1
TOOL_BUDGET_HOOK_LOCK = ".review-craft-eval-tool-budget-hook.lock"
USAGE_COLLECTOR = {
    "name": "codex-cli",
    "version": ADAPTER_VERSION,
    "format": "codex-exec-jsonl-v1",
}
SKILL_BOOTSTRAP_MAX_OUTPUT_LINES = 120
SKILL_BOOTSTRAP_ENTRYPOINT = "ENTRYPOINT"
SKILL_BOOTSTRAP_REFERENCE = "REFERENCE"

_SKILL_PATH_PREFIXES = ("$SKILL/", "${SKILL}/")
_BOOTSTRAP_READERS = {"head", "rg", "sed", "tail"}
_BOOTSTRAP_SHELL_WRAPPERS = {
    "sh",
    "bash",
    "zsh",
    "/bin/sh",
    "/bin/bash",
    "/bin/zsh",
}
_RG_FLAGS = {
    "-F",
    "-i",
    "-n",
    "--fixed-strings",
    "--heading",
    "--ignore-case",
    "--line-number",
    "--no-heading",
}
_RG_INTEGER_OPTIONS = {
    "-A",
    "-B",
    "-C",
    "-m",
    "--after-context",
    "--before-context",
    "--context",
    "--max-count",
}


def _skill_bootstrap_target_kind(
    target: str, skill_root: Path | None
) -> str | None:
    relative: str | None = None
    for prefix in _SKILL_PATH_PREFIXES:
        if target.startswith(prefix):
            relative = target[len(prefix) :]
            break
    if relative is None and skill_root is not None:
        candidate = Path(target).expanduser()
        if candidate.is_absolute():
            try:
                relative = candidate.resolve(strict=True).relative_to(
                    skill_root.resolve(strict=True)
                ).as_posix()
            except (OSError, ValueError):
                return None
    if relative is None:
        return None
    if any(character in relative for character in "*?[]{}$"):
        return None
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(
        part in {".", ".."} for part in path.parts
    ):
        return None
    if path.as_posix() == "SKILL.md":
        return SKILL_BOOTSTRAP_ENTRYPOINT
    if (
        len(path.parts) >= 2
        and path.parts[0] == "references"
        and path.suffix == ".md"
    ):
        return SKILL_BOOTSTRAP_REFERENCE
    return None


def _bounded_skill_output_count(value: str) -> bool:
    return (
        value.isdigit()
        and 1 <= int(value) <= SKILL_BOOTSTRAP_MAX_OUTPUT_LINES
    )


def _is_bounded_rg_read(arguments: list[str]) -> bool:
    patterns = 0
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in _RG_FLAGS:
            index += 1
            continue
        if argument in _RG_INTEGER_OPTIONS:
            if (
                index + 1 >= len(arguments)
                or not _bounded_skill_output_count(arguments[index + 1])
            ):
                return False
            index += 2
            continue
        if argument.startswith("--context=") or argument.startswith("--max-count="):
            if not _bounded_skill_output_count(argument.split("=", 1)[1]):
                return False
            index += 1
            continue
        if argument in {"-e", "--regexp"}:
            if index + 1 >= len(arguments):
                return False
            patterns += 1
            index += 2
            continue
        if argument.startswith("-"):
            return False
        patterns += 1
        index += 1
    return patterns == 1


def classify_skill_bootstrap_command(
    command: str, *, skill_root: Path | None = None
) -> str | None:
    """Recognize one bounded Skill-only read and reject ambiguous shell composition."""
    if (
        not command
        or "\n" in command
        or "\r" in command
        or "$(" in command
        or "`" in command
    ):
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;()<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return None
    if (
        len(tokens) == 3
        and tokens[0] in _BOOTSTRAP_SHELL_WRAPPERS
        and tokens[1] == "-lc"
    ):
        # Codex can preserve its strict login-shell wrapper in the completed trace
        # even though PreToolUse receives the inner command. Re-parse only that one
        # exact wrapper so hook enforcement and post-run validation classify alike.
        command = tokens[2]
        if not command or "\n" in command or "\r" in command:
            return None
        try:
            lexer = shlex.shlex(
                command, posix=True, punctuation_chars="|&;()<>"
            )
            lexer.whitespace_split = True
            lexer.commenters = ""
            tokens = list(lexer)
        except ValueError:
            return None
    if not tokens or any(
        token != "|" and token and all(character in "|&;()<>" for character in token)
        for token in tokens
    ):
        return None
    if tokens.count("|") > 1:
        return None
    if "|" in tokens:
        has_output_filter = True
        separator = tokens.index("|")
        primary = tokens[:separator]
        output_filter = tokens[separator + 1 :]
        if (
            len(output_filter) != 3
            or output_filter[0] != "head"
            or output_filter[1] != "-n"
            or not _bounded_skill_output_count(output_filter[2])
        ):
            return None
    else:
        has_output_filter = False
        primary = tokens
    if len(primary) < 2:
        return None
    reader = primary[0]
    if reader not in _BOOTSTRAP_READERS:
        return None
    target_kind = _skill_bootstrap_target_kind(primary[-1], skill_root)
    if target_kind is None:
        return None
    arguments = primary[1:-1]
    if reader == "sed":
        if len(arguments) != 2 or arguments[0] != "-n":
            return None
        match = re.fullmatch(r"([1-9][0-9]*)(?:,([1-9][0-9]*))?p", arguments[1])
        if match is None:
            return None
        start = int(match.group(1))
        end = int(match.group(2) or match.group(1))
        if end < start or end - start + 1 > SKILL_BOOTSTRAP_MAX_OUTPUT_LINES:
            return None
    elif reader == "rg":
        if not has_output_filter or not _is_bounded_rg_read(arguments):
            return None
    elif (
        len(arguments) != 2
        or arguments[0] != "-n"
        or not _bounded_skill_output_count(arguments[1])
    ):
        return None
    return target_kind
EVENT_TYPES = {
    "thread.started",
    "turn.started",
    "turn.completed",
    "turn.failed",
    "item.started",
    "item.updated",
    "item.completed",
    "error",
}
ITEM_TYPES = {
    "agent_message",
    "reasoning",
    "command_execution",
    "file_change",
    "mcp_tool_call",
    "collab_tool_call",
    "web_search",
    "todo_list",
    "error",
}
SEMANTIC_ITEM_TYPES = ITEM_TYPES - {"error"}
TOOL_ITEM_TYPES = {
    "command_execution": "commandExecution",
    "file_change": "fileChange",
    "mcp_tool_call": "mcpToolCall",
    "collab_tool_call": "collabToolCall",
    "web_search": "webSearch",
}
TOKEN_FIELDS = {
    "input_tokens": "inputTokens",
    "cached_input_tokens": "cachedInputTokens",
    "cache_write_input_tokens": "cacheWriteInputTokens",
    "output_tokens": "outputTokens",
    "reasoning_output_tokens": "reasoningOutputTokens",
}
ABLATION_TREATMENTS = {
    "ORDINARY_PROMPT",
    "RISK_LENS_REVIEW",
    "REVIEW_CRAFT_EVIDENCE_LOOP",
}
REMEDIATION_TREATMENTS = {
    "ORDINARY_NAIVE_LOOP",
    "REVIEW_CRAFT_UNGATED_LOOP",
    "REVIEW_CRAFT_EVIDENCE_GATED_LOOP",
}
SKILL_TREATMENTS = {
    "REVIEW_CRAFT",
    "REVIEW_CRAFT_EVIDENCE_LOOP",
    "REVIEW_CRAFT_UNGATED_LOOP",
    "REVIEW_CRAFT_EVIDENCE_GATED_LOOP",
    "ROUTING_DECISION",
}


class AdapterError(RuntimeError):
    pass


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Codex CLI adapter for Review Craft evals")
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning", required=True)
    parser.add_argument("--provider-name", default="openai")
    parser.add_argument("--provider-base-url")
    parser.add_argument("--provider-wire-api", choices=("responses", "chat"), default="responses")
    parser.add_argument(
        "--provider-requires-openai-auth",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--provider-supports-websockets",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--provider-request-max-retries",
        type=int,
        default=PROVIDER_REQUEST_MAX_RETRIES,
    )
    parser.add_argument(
        "--provider-stream-max-retries",
        type=int,
        default=PROVIDER_STREAM_MAX_RETRIES,
    )
    parser.add_argument(
        "--provider-stream-idle-timeout-ms",
        type=int,
        default=PROVIDER_STREAM_IDLE_TIMEOUT_MS,
    )
    parser.add_argument(
        "--provider-websocket-connect-timeout-ms",
        type=int,
        default=PROVIDER_WEBSOCKET_CONNECT_TIMEOUT_MS,
    )
    parser.add_argument("--allow-codex-home-extensions", action="store_true")
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--prepare-isolation", action="store_true")
    parser.add_argument("--fixture-root")
    parser.add_argument("--skill-root")
    parser.add_argument("--evidence-root")
    parser.add_argument("--prompt-file")
    parser.add_argument("--output-schema")
    parser.add_argument("--output-file")
    parser.add_argument("--treatment")
    parser.add_argument("--case-id")
    parser.add_argument("--operation", choices=("review", "repair"), default="review")
    parser.add_argument("--workspace-marker")
    parser.add_argument("--workspace-key")
    parser.add_argument("--round-number", type=int)
    return parser.parse_args(argv)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def unavailable_usage(reason: str) -> dict[str, Any]:
    return {
        "schema": "review-craft.eval-usage.v1",
        "availability": "UNAVAILABLE",
        "collector": USAGE_COLLECTOR,
        "inputTokens": None,
        "cachedInputTokens": None,
        "cacheWriteInputTokens": None,
        "outputTokens": None,
        "reasoningOutputTokens": None,
        "totalTokens": None,
        "turnCount": None,
        "toolCalls": None,
        "unavailableReason": reason,
    }


def parse_codex_jsonl(value: str) -> dict[str, Any]:
    lines = [line for line in value.splitlines() if line.strip()]
    if not lines:
        return unavailable_usage("HOST_OUTPUT_EMPTY")
    token_totals = {field: 0 for field in TOKEN_FIELDS.values()}
    tool_counts = {field: 0 for field in TOOL_ITEM_TYPES.values()}
    turn_count = 0
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return unavailable_usage("HOST_OUTPUT_INVALID")
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            return unavailable_usage("HOST_OUTPUT_INVALID")
        event_type = event["type"]
        if event_type not in EVENT_TYPES:
            return unavailable_usage("HOST_FORMAT_UNSUPPORTED")
        if event_type.startswith("item."):
            item = event.get("item")
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                return unavailable_usage("HOST_OUTPUT_INVALID")
            item_type = item["type"]
            if item_type not in ITEM_TYPES:
                return unavailable_usage("HOST_FORMAT_UNSUPPORTED")
            if event_type == "item.completed" and item_type in TOOL_ITEM_TYPES:
                tool_counts[TOOL_ITEM_TYPES[item_type]] += 1
        if event_type != "turn.completed":
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            return unavailable_usage("HOST_USAGE_INVALID")
        parsed_usage = {}
        for source, target in TOKEN_FIELDS.items():
            count = usage.get(source)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                return unavailable_usage("HOST_USAGE_INVALID")
            parsed_usage[target] = count
        for field, count in parsed_usage.items():
            token_totals[field] += count
        turn_count += 1
    if turn_count == 0:
        return unavailable_usage("HOST_USAGE_MISSING")
    tool_total = sum(tool_counts.values())
    return {
        "schema": "review-craft.eval-usage.v1",
        "availability": "AVAILABLE",
        "collector": USAGE_COLLECTOR,
        **token_totals,
        "totalTokens": token_totals["inputTokens"] + token_totals["outputTokens"],
        "turnCount": turn_count,
        "toolCalls": {
            "total": tool_total,
            "byType": tool_counts,
        },
        "unavailableReason": None,
    }


def parse_tool_trace(value: str, replacements: dict[str, str]) -> dict[str, Any]:
    items = []
    for line in (line for line in value.splitlines() if line.strip()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") not in TOOL_ITEM_TYPES:
            continue
        item_type = item["type"]
        row: dict[str, Any] = {
            "sequence": len(items),
            "type": TOOL_ITEM_TYPES[item_type],
            "status": str(item.get("status") or "completed"),
        }
        if item_type == "command_execution":
            command = str(item.get("command") or "")
            for source, replacement in sorted(
                replacements.items(), key=lambda pair: len(pair[0]), reverse=True
            ):
                command = command.replace(source, replacement)
            output = str(item.get("aggregated_output") or "").encode(
                "utf-8", errors="surrogateescape"
            )
            output_lines = output.count(b"\n")
            if output and not output.endswith(b"\n"):
                output_lines += 1
            exit_code = item.get("exit_code")
            row.update(
                {
                    "command": command or "<unknown>",
                    "exitCode": exit_code if isinstance(exit_code, int) else None,
                    "outputBytes": len(output),
                    "outputLines": output_lines,
                    "outputSha256": hashlib.sha256(output).hexdigest(),
                }
            )
        items.append(row)
    return {"schema": "review-craft.eval-tool-trace.v1", "items": items}


def write_usage_output(payload: dict[str, Any]) -> None:
    output = os.environ.get(USAGE_OUTPUT_ENV)
    if output is None:
        return
    path = Path(output).expanduser()
    if not path.parent.is_dir():
        raise AdapterError("usage output parent directory does not exist")
    _write_json_sidecar(path, payload)


def _write_json_sidecar(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def write_tool_trace_output(payload: dict[str, Any]) -> None:
    output = os.environ.get(TOOL_TRACE_OUTPUT_ENV)
    if output is None:
        return
    path = Path(output).expanduser()
    if not path.parent.is_dir():
        raise AdapterError("tool trace output parent directory does not exist")
    _write_json_sidecar(path, payload)


def write_progress_output(payload: dict[str, Any]) -> None:
    output = os.environ.get(PROGRESS_OUTPUT_ENV)
    if output is None:
        return
    path = Path(output).expanduser()
    if not path.parent.is_dir():
        raise AdapterError("progress output parent directory does not exist")
    _write_json_sidecar(path, payload)


def write_isolation_output(payload: dict[str, Any]) -> None:
    output = os.environ.get(ISOLATION_OUTPUT_ENV)
    if output is None:
        return
    path = Path(output).expanduser()
    if not path.parent.is_dir():
        raise AdapterError("isolation output parent directory does not exist")
    _write_json_sidecar(path, payload)


def new_progress_receipt(
    started_at: str, tool_budget: dict[str, int] | None = None
) -> dict[str, Any]:
    return {
        "schema": "review-craft.eval-progress.v1",
        "availability": "UNAVAILABLE",
        "startedAt": started_at,
        "completedAt": None,
        "threadStartedAt": None,
        "turnStartedAt": None,
        "firstItemAt": None,
        "lastEventAt": None,
        "lastEventType": None,
        "eventCount": 0,
        "itemEventCount": 0,
        "timeToFirstItemSeconds": None,
        "timeToThreadStartedSeconds": None,
        "timeToTurnStartedSeconds": None,
        "firstToolCallAt": None,
        "timeToFirstToolCallSeconds": None,
        "lastSemanticProgressAt": None,
        "lastSemanticProgressType": None,
        "semanticProgressEventCount": 0,
        "inactivityWarningSeconds": None,
        "inactivityDiagnosticSeconds": None,
        "inactivityState": "NOT_CONFIGURED",
        "inactivityAgeSeconds": None,
        "maximumPreItemInactivitySeconds": None,
        "diagnosticCapturedAt": None,
        "processAliveWhenDiagnosticCaptured": None,
        "providerTransportState": "NOT_OBSERVED",
        "providerTransportStartedAt": None,
        "providerTransportConnectedAt": None,
        "providerTransportFailedAt": None,
        "providerTransportFallbackAt": None,
        "terminationReason": None,
        "timeoutPhase": None,
        "toolBudgetState": "ACTIVE" if tool_budget is not None else "NOT_CONFIGURED",
        "repositoryToolCallsStarted": 0,
        "skillBootstrapToolCallsStarted": 0,
        "repositoryToolCallsApproved": 0,
        "skillBootstrapToolCallsApproved": 0,
        "preExecutionCommandsBlocked": 0,
        "skillBootstrapPrerequisiteState": (
            "REQUIRED"
            if tool_budget is not None
            and tool_budget["skillBootstrapToolCallLimit"] > 0
            else "NOT_REQUIRED"
        ),
        "skillBootstrapPrerequisiteBlocks": 0,
        "skillBootstrapPrerequisiteBlockedAt": None,
        "skillBootstrapPrerequisiteBlockedCommandSha256": None,
        "toolBudgetExceededAt": None,
        "toolBudgetExceededKind": None,
        "processTreeCleanup": "NOT_VERIFIED",
        "unavailableReason": "HOST_OUTPUT_EMPTY",
    }


def tool_budget_limits() -> dict[str, int] | None:
    raw_repository = os.environ.get(REPOSITORY_TOOL_CALL_LIMIT_ENV)
    raw_bootstrap = os.environ.get(SKILL_BOOTSTRAP_TOOL_CALL_LIMIT_ENV)
    if raw_repository is None and raw_bootstrap is None:
        return None
    if raw_repository is None or raw_bootstrap is None:
        raise AdapterError("tool budget limits must be configured together")
    try:
        repository_limit = int(raw_repository)
        bootstrap_limit = int(raw_bootstrap)
    except ValueError as error:
        raise AdapterError("tool budget limits must be integers") from error
    if repository_limit < 1 or bootstrap_limit < 0:
        raise AdapterError(
            "repository tool limit must be positive and bootstrap limit non-negative"
        )
    return {
        "repositoryToolCallLimit": repository_limit,
        "skillBootstrapToolCallLimit": bootstrap_limit,
    }


def _tool_budget_hook_command() -> str:
    argv = [sys.executable, str(Path(__file__).resolve()), "--tool-budget-hook"]
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def tool_budget_hook_configuration() -> dict[str, Any]:
    return {
        "description": "Review Craft pre-execution evaluation tool budget",
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": ".*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": _tool_budget_hook_command(),
                            "statusMessage": "Checking evaluation tool budget",
                            "timeout": TOOL_BUDGET_HOOK_TIMEOUT_SECONDS,
                        }
                    ],
                }
            ]
        },
    }


def tool_budget_hook_configuration_sha256() -> str:
    return hashlib.sha256(
        _canonical_bytes(tool_budget_hook_configuration())
    ).hexdigest()


def tool_budget_hook_implementation_sha256() -> str:
    digest = hashlib.sha256()
    for path in (
        Path(__file__).resolve(),
        ROOT / "scripts/real_repository_campaign.py",
    ):
        label = path.relative_to(ROOT).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(label).to_bytes(8, "big"))
        digest.update(label)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _tool_budget_state_path() -> Path:
    raw_state = os.environ.get(TOOL_BUDGET_STATE_ENV)
    raw_home = os.environ.get("CODEX_HOME")
    if raw_state is None or raw_home is None:
        raise AdapterError("pre-execution tool budget state is not configured")
    home = Path(raw_home).expanduser().resolve(strict=True)
    state = Path(raw_state).expanduser()
    if state.is_symlink():
        raise AdapterError("pre-execution tool budget state must not be a symlink")
    if state.parent.resolve(strict=True) != home:
        raise AdapterError("pre-execution tool budget state must stay inside CODEX_HOME")
    return state


def _new_tool_budget_state(limits: dict[str, int]) -> dict[str, Any]:
    return {
        "schema": TOOL_BUDGET_STATE_PROTOCOL,
        "status": "ACTIVE",
        **limits,
        "repositoryToolCallsApproved": 0,
        "skillBootstrapToolCallsApproved": 0,
        "preExecutionCommandsBlocked": 0,
        "skillBootstrapPrerequisiteState": (
            "REQUIRED"
            if limits["skillBootstrapToolCallLimit"] > 0
            else "NOT_REQUIRED"
        ),
        "skillBootstrapPrerequisiteBlocks": 0,
        "skillBootstrapPrerequisiteBlockedAt": None,
        "skillBootstrapPrerequisiteBlockedCommandSha256": None,
        "blockedAt": None,
        "blockedKind": None,
        "blockedCommandSha256": None,
    }


def _validate_tool_budget_state(
    payload: Any, limits: dict[str, int]
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AdapterError("pre-execution tool budget state must be an object")
    required = {
        "schema",
        "status",
        "repositoryToolCallLimit",
        "skillBootstrapToolCallLimit",
        "repositoryToolCallsApproved",
        "skillBootstrapToolCallsApproved",
        "preExecutionCommandsBlocked",
        "skillBootstrapPrerequisiteState",
        "skillBootstrapPrerequisiteBlocks",
        "skillBootstrapPrerequisiteBlockedAt",
        "skillBootstrapPrerequisiteBlockedCommandSha256",
        "blockedAt",
        "blockedKind",
        "blockedCommandSha256",
    }
    if set(payload) != required or payload.get("schema") != TOOL_BUDGET_STATE_PROTOCOL:
        raise AdapterError("pre-execution tool budget state has an invalid shape")
    if payload.get("status") not in {"ACTIVE", "BLOCKED"}:
        raise AdapterError("pre-execution tool budget state has an invalid status")
    for key in (
        "repositoryToolCallLimit",
        "skillBootstrapToolCallLimit",
        "repositoryToolCallsApproved",
        "skillBootstrapToolCallsApproved",
        "preExecutionCommandsBlocked",
        "skillBootstrapPrerequisiteBlocks",
    ):
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AdapterError("pre-execution tool budget state has invalid counters")
    if any(payload[key] != value for key, value in limits.items()):
        raise AdapterError("pre-execution tool budget state limit mismatch")
    if payload["repositoryToolCallsApproved"] > limits["repositoryToolCallLimit"]:
        raise AdapterError("repository tool budget state exceeded its sealed limit")
    if (
        payload["skillBootstrapToolCallsApproved"]
        > limits["skillBootstrapToolCallLimit"]
    ):
        raise AdapterError("Skill bootstrap budget state exceeded its sealed limit")
    prerequisite_state = payload.get("skillBootstrapPrerequisiteState")
    if prerequisite_state not in {
        "NOT_REQUIRED",
        "REQUIRED",
        "RECOVERY_USED",
        "SATISFIED",
        "FAILED",
    }:
        raise AdapterError("Skill bootstrap prerequisite state is invalid")
    prerequisite_blocks = payload["skillBootstrapPrerequisiteBlocks"]
    if prerequisite_blocks > MAX_RECOVERABLE_BOOTSTRAP_PREREQUISITE_BLOCKS:
        raise AdapterError("Skill bootstrap prerequisite recovery limit exceeded")
    prerequisite_metadata = (
        payload.get("skillBootstrapPrerequisiteBlockedAt"),
        payload.get("skillBootstrapPrerequisiteBlockedCommandSha256"),
    )
    if prerequisite_blocks:
        blocked_at, blocked_digest = prerequisite_metadata
        if not isinstance(blocked_at, str):
            raise AdapterError("Skill bootstrap prerequisite block time is missing")
        if (
            not isinstance(blocked_digest, str)
            or re.fullmatch(r"[a-f0-9]{64}", blocked_digest) is None
        ):
            raise AdapterError("Skill bootstrap prerequisite command digest is invalid")
    elif any(value is not None for value in prerequisite_metadata):
        raise AdapterError("Skill bootstrap prerequisite metadata has no block")
    bootstrap_limit = limits["skillBootstrapToolCallLimit"]
    bootstrap_approved = payload["skillBootstrapToolCallsApproved"]
    if bootstrap_limit == 0:
        if prerequisite_state != "NOT_REQUIRED" or prerequisite_blocks:
            raise AdapterError("unexpected Skill bootstrap prerequisite state")
    elif prerequisite_state == "NOT_REQUIRED":
        raise AdapterError("required Skill bootstrap prerequisite is disabled")
    if prerequisite_state in {"REQUIRED", "RECOVERY_USED", "FAILED"} and bootstrap_approved:
        raise AdapterError("unsatisfied Skill bootstrap prerequisite has approvals")
    if prerequisite_state == "REQUIRED" and prerequisite_blocks:
        raise AdapterError("unused Skill bootstrap recovery contains a block")
    if prerequisite_state == "RECOVERY_USED" and prerequisite_blocks != 1:
        raise AdapterError("Skill bootstrap recovery state is inconsistent")
    if prerequisite_state == "SATISFIED" and bootstrap_approved < 1:
        raise AdapterError("satisfied Skill bootstrap prerequisite has no approval")
    if prerequisite_state == "FAILED" and (
        payload.get("status") != "BLOCKED"
        or payload.get("blockedKind") != "SKILL_BOOTSTRAP_REQUIRED"
    ):
        raise AdapterError("failed Skill bootstrap prerequisite lacks terminal block")
    if payload["preExecutionCommandsBlocked"] < prerequisite_blocks:
        raise AdapterError("pre-execution block count is below prerequisite blocks")
    if payload["status"] == "BLOCKED":
        if payload.get("blockedKind") not in {
            "REPOSITORY_CALLS",
            "SKILL_BOOTSTRAP_CALLS",
            "SKILL_BOOTSTRAP_REQUIRED",
            "CONTROL_FAILURE",
        }:
            raise AdapterError("pre-execution tool budget block kind is invalid")
        if not isinstance(payload.get("blockedAt"), str):
            raise AdapterError("pre-execution tool budget block time is missing")
        digest = payload.get("blockedCommandSha256")
        if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
            raise AdapterError("pre-execution tool budget command digest is invalid")
    elif any(
        payload.get(key) is not None
        for key in ("blockedAt", "blockedKind", "blockedCommandSha256")
    ):
        raise AdapterError("active pre-execution tool budget state contains block data")
    return payload


def initialize_tool_budget_state(path: Path, limits: dict[str, int]) -> None:
    if path.exists() or path.is_symlink():
        raise AdapterError("pre-execution tool budget state already exists")
    _write_json_sidecar(path, _new_tool_budget_state(limits))


def read_tool_budget_state(path: Path, limits: dict[str, int]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AdapterError("pre-execution tool budget state is unreadable") from error
    return _validate_tool_budget_state(payload, limits)


def _tool_budget_state_lock_name(path: Path) -> str:
    return f".{path.name}.lock"


def _block_tool_budget_control_failure(path: Path, limits: dict[str, int]) -> None:
    with (
        contextlib.suppress(AdapterError, OSError, TimeoutError, ValueError),
        exclusive_file_lock(
            path.parent,
            name=_tool_budget_state_lock_name(path),
            wait_seconds=2,
            timeout_message="timed out waiting for the tool budget state lock",
        ),
    ):
        payload = read_tool_budget_state(path, limits)
        if payload["status"] != "BLOCKED":
            payload.update(
                {
                    "status": "BLOCKED",
                    "blockedAt": utc_now(),
                    "blockedKind": "CONTROL_FAILURE",
                    "blockedCommandSha256": hashlib.sha256(b"").hexdigest(),
                }
            )
            _write_json_sidecar(path, payload)


def run_tool_budget_hook() -> int:
    limits = tool_budget_limits()
    if limits is None:
        print(json.dumps({"decision": "approve"}, sort_keys=True))
        return 0
    state_path = _tool_budget_state_path()
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or payload.get("hook_event_name") != "PreToolUse":
            raise AdapterError("tool budget hook received an invalid event")
        tool_input = payload.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if not isinstance(command, str) or not command:
            raise AdapterError("tool budget hook received an unmetered command")
        skill_root = os.environ.get(TOOL_BUDGET_SKILL_ROOT_ENV)
        if not skill_root:
            raise AdapterError("tool budget Skill root is not configured")
        bootstrap_kind = classify_skill_bootstrap_command(
            command,
            skill_root=Path(skill_root),
        )
        is_bootstrap = bootstrap_kind is not None
        is_entrypoint = bootstrap_kind == SKILL_BOOTSTRAP_ENTRYPOINT
        command_sha256 = hashlib.sha256(
            command.encode("utf-8", errors="surrogateescape")
        ).hexdigest()
        with exclusive_file_lock(
            state_path.parent,
            name=_tool_budget_state_lock_name(state_path),
            wait_seconds=5,
            timeout_message="timed out waiting for the tool budget state lock",
        ):
            state = read_tool_budget_state(state_path, limits)
            if state["status"] == "BLOCKED":
                state["preExecutionCommandsBlocked"] += 1
                _write_json_sidecar(state_path, state)
                effective_kind = state["blockedKind"] or "CONTROL_FAILURE"
                reason = (
                    "Review Craft blocked this command before execution because the "
                    f"{effective_kind.lower().replace('_', ' ')} budget is exhausted."
                )
                print(
                    json.dumps(
                        {"decision": "block", "reason": reason},
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                return 0

            prerequisite_state = state["skillBootstrapPrerequisiteState"]
            if not is_entrypoint and prerequisite_state == "REQUIRED":
                state.update(
                    {
                        "skillBootstrapPrerequisiteState": "RECOVERY_USED",
                        "skillBootstrapPrerequisiteBlocks": 1,
                        "skillBootstrapPrerequisiteBlockedAt": utc_now(),
                        "skillBootstrapPrerequisiteBlockedCommandSha256": command_sha256,
                    }
                )
                state["preExecutionCommandsBlocked"] += 1
                _write_json_sidecar(state_path, state)
                print(
                    json.dumps(
                        {
                            "decision": "block",
                            "reason": (
                                "Review Craft blocked this repository command before "
                                "execution. Your next command must read the bound "
                                "$SKILL/SKILL.md entrypoint without touching repository "
                                "files; do not use a relative SKILL.md path."
                            ),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                return 0
            if not is_entrypoint and prerequisite_state == "RECOVERY_USED":
                state.update(
                    {
                        "status": "BLOCKED",
                        "skillBootstrapPrerequisiteState": "FAILED",
                        "blockedAt": utc_now(),
                        "blockedKind": "SKILL_BOOTSTRAP_REQUIRED",
                        "blockedCommandSha256": command_sha256,
                    }
                )
                state["preExecutionCommandsBlocked"] += 1
                _write_json_sidecar(state_path, state)
                print(
                    json.dumps(
                        {
                            "decision": "block",
                            "reason": (
                                "Review Craft blocked this repository command before "
                                "execution because the required SKILL.md bootstrap was "
                                "not completed after one recovery opportunity."
                            ),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                return 0

            counter = (
                "skillBootstrapToolCallsApproved"
                if is_bootstrap
                else "repositoryToolCallsApproved"
            )
            limit = (
                limits["skillBootstrapToolCallLimit"]
                if is_bootstrap
                else limits["repositoryToolCallLimit"]
            )
            blocked_kind = (
                "SKILL_BOOTSTRAP_CALLS" if is_bootstrap else "REPOSITORY_CALLS"
            )
            if state[counter] >= limit:
                state.update(
                    {
                        "status": "BLOCKED",
                        "blockedAt": utc_now(),
                        "blockedKind": blocked_kind,
                        "blockedCommandSha256": command_sha256,
                    }
                )
                state["preExecutionCommandsBlocked"] += 1
                _write_json_sidecar(state_path, state)
                reason = (
                    "Review Craft blocked this command before execution because the "
                    f"{blocked_kind.lower().replace('_', ' ')} budget is exhausted."
                )
                print(
                    json.dumps(
                        {"decision": "block", "reason": reason},
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                return 0
            state[counter] += 1
            if is_entrypoint and state["skillBootstrapPrerequisiteState"] != "NOT_REQUIRED":
                state["skillBootstrapPrerequisiteState"] = "SATISFIED"
            _write_json_sidecar(state_path, state)
        print(json.dumps({"decision": "approve"}, sort_keys=True))
        return 0
    except (
        AdapterError,
        OSError,
        TimeoutError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        _block_tool_budget_control_failure(state_path, limits)
        print(f"Review Craft tool budget control failed closed: {error}", file=sys.stderr)
        return 2


def install_tool_budget_hook(home: Path) -> Path:
    hook_path = home / "hooks.json"
    if hook_path.is_symlink():
        raise AdapterError("isolated Codex hook configuration must not be a symlink")
    expected = tool_budget_hook_configuration()
    if hook_path.exists():
        try:
            current = json.loads(hook_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AdapterError("isolated Codex hook configuration is invalid") from error
        if current != expected:
            raise AdapterError("isolated Codex home already contains a different hooks.json")
        return hook_path
    _write_json_sidecar(hook_path, expected)
    return hook_path


def remove_tool_budget_hook(hook_path: Path) -> None:
    if not hook_path.exists() or hook_path.is_symlink():
        return
    with contextlib.suppress(OSError, json.JSONDecodeError):
        if json.loads(hook_path.read_text(encoding="utf-8")) == tool_budget_hook_configuration():
            hook_path.unlink()


def inactivity_thresholds() -> tuple[int | None, int | None]:
    raw_warning = os.environ.get(INACTIVITY_WARNING_ENV)
    raw_diagnostic = os.environ.get(INACTIVITY_DIAGNOSTIC_ENV)
    if raw_warning is None and raw_diagnostic is None:
        return None, None
    if raw_warning is None or raw_diagnostic is None:
        raise AdapterError("inactivity warning and diagnostic thresholds must be set together")
    try:
        warning = int(raw_warning)
        diagnostic = int(raw_diagnostic)
    except ValueError as error:
        raise AdapterError("inactivity thresholds must be positive integers") from error
    if warning < 1 or diagnostic < 1:
        raise AdapterError("inactivity thresholds must be positive integers")
    if warning >= diagnostic:
        raise AdapterError("inactivity warning threshold must be below diagnostic threshold")
    return warning, diagnostic


def sample_timeout_seconds() -> int | None:
    raw_timeout = os.environ.get(SAMPLE_TIMEOUT_ENV)
    if raw_timeout is None:
        return None
    try:
        timeout = int(raw_timeout)
    except ValueError as error:
        raise AdapterError("sample timeout must be a positive integer") from error
    if timeout < 1:
        raise AdapterError("sample timeout must be a positive integer")
    return timeout


def first_item_timeout_seconds() -> int | None:
    raw_timeout = os.environ.get(FIRST_ITEM_TIMEOUT_ENV)
    if raw_timeout is None:
        return None
    try:
        timeout = int(raw_timeout)
    except ValueError as error:
        raise AdapterError("first-item timeout must be a positive integer") from error
    if timeout < 1:
        raise AdapterError("first-item timeout must be a positive integer")
    return timeout


def _terminate_codex_process_tree(process: subprocess.Popen[str]) -> str:
    return terminate_process_tree(process)


def _fingerprint_rows(paths: list[Path], *, home: Path) -> list[dict[str, Any]]:
    rows = []
    for path in paths:
        content = (
            os.readlink(path).encode("utf-8", errors="surrogateescape")
            if path.is_symlink()
            else path.read_bytes()
        )
        rows.append(
            {
                "path": path.relative_to(home).as_posix(),
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    return rows


def codex_home_extension_state(
    *, allow_extensions: bool, fail_on_extensions: bool = True
) -> dict[str, Any]:
    configured_process_home = os.environ.get("HOME")
    process_home = (
        Path(configured_process_home).expanduser()
        if configured_process_home is not None
        else Path.home().expanduser()
    )
    configured_codex_home = os.environ.get("CODEX_HOME")
    home = (
        Path(configured_codex_home).expanduser()
        if configured_codex_home is not None
        else process_home / ".codex"
    )
    if process_home.resolve() != home.resolve():
        raise AdapterError(
            "HOME and CODEX_HOME must point to the same isolated auth-only directory"
        )
    paths = sorted(
        path
        for root_name in ("skills", "plugins", ".agents/skills", ".agents/plugins")
        for root in [home / root_name]
        if root.is_dir()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    )
    system_paths = [
        path
        for path in paths
        if path.relative_to(home).parts[:2] == ("skills", ".system")
    ]
    extension_paths = [path for path in paths if path not in system_paths]
    if extension_paths and not allow_extensions and fail_on_extensions:
        raise AdapterError(
            "CODEX_HOME contains user skills or plugins; "
            "use an isolated auth-only CODEX_HOME"
        )
    system_rows = _fingerprint_rows(system_paths, home=home)
    extension_rows = _fingerprint_rows(extension_paths, home=home)
    return {
        "homeMatchesCodexHome": True,
        "ignoreUserConfig": True,
        "ignoreRules": True,
        "allowCodexHomeExtensions": allow_extensions,
        "codexHomeSystemFileCount": len(system_rows),
        "codexHomeSystemTreeSha256": hashlib.sha256(
            _canonical_bytes(system_rows)
        ).hexdigest(),
        "codexHomeExtensionFileCount": len(extension_rows),
        "codexHomeExtensionTreeSha256": hashlib.sha256(
            _canonical_bytes(extension_rows)
        ).hexdigest(),
    }


def _isolation_surface(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "capturedAt": utc_now(),
        "systemFileCount": state["codexHomeSystemFileCount"],
        "systemTreeSha256": state["codexHomeSystemTreeSha256"],
        "userExtensionFileCount": state["codexHomeExtensionFileCount"],
        "userExtensionTreeSha256": state["codexHomeExtensionTreeSha256"],
    }


def new_isolation_receipt(pre_run_state: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "review-craft.eval-isolation-receipt.v1",
        "availability": "UNAVAILABLE",
        "policy": {
            key: pre_run_state[key]
            for key in (
                "homeMatchesCodexHome",
                "ignoreUserConfig",
                "ignoreRules",
                "allowCodexHomeExtensions",
            )
        },
        "preRun": _isolation_surface(pre_run_state),
        "postStart": None,
        "postExit": None,
        "comparison": {
            "postStartSystemState": "NOT_CAPTURED",
            "postStartUserExtensionState": "NOT_CAPTURED",
            "postExitSystemState": "NOT_CAPTURED",
            "postExitUserExtensionState": "NOT_CAPTURED",
            "overall": "CAPTURE_UNAVAILABLE",
        },
        "unavailableReason": "POST_START_NOT_CAPTURED",
    }


def update_isolation_receipt(
    receipt: dict[str, Any], *, phase: str, state: dict[str, Any]
) -> None:
    if phase not in {"postStart", "postExit"}:
        raise AdapterError(f"unsupported isolation receipt phase: {phase}")
    current = _isolation_surface(state)
    receipt[phase] = current
    prefix = "postStart" if phase == "postStart" else "postExit"
    system_match = (
        current["systemFileCount"] == receipt["preRun"]["systemFileCount"]
        and current["systemTreeSha256"] == receipt["preRun"]["systemTreeSha256"]
    )
    extension_match = (
        current["userExtensionFileCount"]
        == receipt["preRun"]["userExtensionFileCount"]
        and current["userExtensionTreeSha256"]
        == receipt["preRun"]["userExtensionTreeSha256"]
    )
    receipt["comparison"][f"{prefix}SystemState"] = (
        "MATCHED" if system_match else "DRIFTED"
    )
    receipt["comparison"][f"{prefix}UserExtensionState"] = (
        "MATCHED" if extension_match else "DRIFTED"
    )
    comparisons = receipt["comparison"]
    comparison_keys = (
        "postStartSystemState",
        "postStartUserExtensionState",
        "postExitSystemState",
        "postExitUserExtensionState",
    )
    if any(comparisons[key] == "NOT_CAPTURED" for key in comparison_keys):
        receipt["availability"] = "UNAVAILABLE"
        receipt["unavailableReason"] = (
            "POST_START_NOT_CAPTURED"
            if any(
                comparisons[key] == "NOT_CAPTURED"
                for key in (
                    "postStartSystemState",
                    "postStartUserExtensionState",
                )
            )
            else "POST_EXIT_NOT_CAPTURED"
        )
        receipt["comparison"]["overall"] = "CAPTURE_UNAVAILABLE"
        return
    user_drift = any(
        comparisons[key] == "DRIFTED"
        for key in ("postStartUserExtensionState", "postExitUserExtensionState")
    )
    system_drift = any(
        comparisons[key] == "DRIFTED"
        for key in ("postStartSystemState", "postExitSystemState")
    )
    receipt["availability"] = "AVAILABLE"
    receipt["unavailableReason"] = None
    receipt["comparison"]["overall"] = (
        "USER_EXTENSION_DRIFT"
        if user_drift
        else "SYSTEM_STATE_DRIFT"
        if system_drift
        else "MATCHED"
    )


def mark_isolation_capture_unavailable(
    receipt: dict[str, Any], reason: str
) -> None:
    receipt["availability"] = "UNAVAILABLE"
    receipt["comparison"]["overall"] = "CAPTURE_UNAVAILABLE"
    receipt["unavailableReason"] = reason


def _preparation_surface(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "systemFileCount": state["codexHomeSystemFileCount"],
        "systemTreeSha256": state["codexHomeSystemTreeSha256"],
        "userExtensionFileCount": state["codexHomeExtensionFileCount"],
        "userExtensionTreeSha256": state["codexHomeExtensionTreeSha256"],
    }


def _seal_preparation_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    payload["contentSha256"] = hashlib.sha256(
        _canonical_bytes(
            {key: value for key, value in payload.items() if key != "contentSha256"}
        )
    ).hexdigest()
    return payload


def build_isolation_preparation_command(
    *, executable: str, model: str, reasoning: str, home: Path, port: int
) -> list[str]:
    provider = "review_craft_bootstrap"
    return [
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
        "--json",
        "--cd",
        str(home),
        "--model",
        model,
        "--config",
        f"model_reasoning_effort={_toml_string(reasoning)}",
        "--config",
        f'model_provider="{provider}"',
        "--config",
        f'model_providers.{provider}.name="{provider}"',
        "--config",
        f'model_providers.{provider}.base_url="http://127.0.0.1:{port}/v1"',
        "--config",
        f'model_providers.{provider}.wire_api="responses"',
        "--config",
        f"model_providers.{provider}.requires_openai_auth=false",
        "--config",
        f"model_providers.{provider}.supports_websockets=false",
        "--config",
        f"model_providers.{provider}.request_max_retries=0",
        "--config",
        f"model_providers.{provider}.stream_max_retries=0",
        "-",
    ]


def _terminate_preparation_process(process: subprocess.Popen[str]) -> str:
    if process.poll() is not None:
        return "PROCESS_EXITED_AFTER_MATERIALIZATION"
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:  # pragma: no cover - exercised by hosted Windows contract jobs.
            completed = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            if completed.returncode != 0 and process.poll() is None:
                process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover - exercised by hosted Windows contract jobs.
            process.kill()
        process.wait(timeout=5)
    return "TERMINATED_AFTER_MATERIALIZATION"


def prepare_codex_home_isolation(
    *,
    executable: str,
    model: str,
    reasoning: str,
    initial_state: dict[str, Any],
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    started_at = utc_now()
    started = time.monotonic()
    before = _preparation_surface(initial_state)
    if before["userExtensionFileCount"] != 0:
        raise AdapterError("isolation preparation requires an auth-only Codex home")
    if before["systemFileCount"] > 0:
        return _seal_preparation_receipt(
            {
                "schema": ISOLATION_PREPARATION_PROTOCOL,
                "status": "ALREADY_PREPARED",
                "hostVersion": codex_version(),
                "startedAt": started_at,
                "completedAt": utc_now(),
                "before": before,
                "after": before,
                "networkBoundary": "NOT_USED",
                "processTermination": "NOT_STARTED",
                "durationSeconds": round(time.monotonic() - started, 3),
                "contentSha256": "0" * 64,
            }
        )

    process: subprocess.Popen[str] | None = None
    termination = "NOT_STARTED"
    stable_state: dict[str, Any] | None = None
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = int(listener.getsockname()[1])
        command = build_isolation_preparation_command(
            executable=executable,
            model=model,
            reasoning=reasoning,
            home=Path(os.environ["CODEX_HOME"]).resolve(),
            port=port,
        )
        command_env = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
        popen_options: dict[str, Any] = {"start_new_session": os.name == "posix"}
        if os.name == "nt":  # pragma: no cover - exercised by hosted Windows jobs.
            popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            env=command_env,
            **popen_options,
        )
        if process.stdin is None:
            raise AdapterError("isolation preparation process stdin is unavailable")
        process.stdin.write("Initialize managed system skills only.\n")
        process.stdin.close()

        deadline = time.monotonic() + timeout_seconds
        stable_key: tuple[int, str] | None = None
        stable_observations = 0
        while time.monotonic() < deadline:
            try:
                current = codex_home_extension_state(allow_extensions=False)
            except OSError:
                time.sleep(0.1)
                continue
            current_surface = _preparation_surface(current)
            if current_surface["userExtensionFileCount"] != 0:
                raise AdapterError(
                    "user extension state appeared during isolation preparation"
                )
            key = (
                current_surface["systemFileCount"],
                current_surface["systemTreeSha256"],
            )
            if key[0] > 0:
                if key == stable_key:
                    stable_observations += 1
                else:
                    stable_key = key
                    stable_observations = 1
                if stable_observations >= 3:
                    stable_state = current
                    break
            if process.poll() is not None and key[0] == 0:
                raise AdapterError(
                    "Codex exited before managed system skills were materialized"
                )
            time.sleep(0.1)
        if stable_state is None:
            raise AdapterError(
                "timed out waiting for a stable managed system skill tree"
            )
    finally:
        if process is not None:
            termination = _terminate_preparation_process(process)
        listener.close()

    final_state = codex_home_extension_state(allow_extensions=False)
    after = _preparation_surface(final_state)
    if stable_state is None or after != _preparation_surface(stable_state):
        raise AdapterError("managed system skill tree changed during process cleanup")
    return _seal_preparation_receipt(
        {
            "schema": ISOLATION_PREPARATION_PROTOCOL,
            "status": "MATERIALIZED",
            "hostVersion": codex_version(),
            "startedAt": started_at,
            "completedAt": utc_now(),
            "before": before,
            "after": after,
            "networkBoundary": "OWNED_LOOPBACK_BLACKHOLE",
            "processTermination": termination,
            "durationSeconds": round(time.monotonic() - started, 3),
            "contentSha256": "0" * 64,
        }
    )


def provider_metadata(args: argparse.Namespace) -> dict[str, Any]:
    if PROVIDER_NAME.fullmatch(args.provider_name) is None:
        raise AdapterError(
            "provider name must contain only letters, digits, underscores, or hyphens"
        )
    base_url = args.provider_base_url
    if base_url is not None:
        try:
            parsed = urlsplit(base_url)
        except ValueError as error:
            raise AdapterError("provider base URL is invalid") from error
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise AdapterError(
                "provider base URL must be credential-free HTTP(S) without query or fragment"
            )
        base_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    elif args.provider_name != "openai":
        raise AdapterError("a non-default provider requires --provider-base-url")
    transport_values = {
        "requestMaxRetries": args.provider_request_max_retries,
        "streamMaxRetries": args.provider_stream_max_retries,
        "streamIdleTimeoutMs": args.provider_stream_idle_timeout_ms,
        "websocketConnectTimeoutMs": args.provider_websocket_connect_timeout_ms,
    }
    if (
        any(
            type(value) is not int or value < 0
            for key, value in transport_values.items()
            if key in {"requestMaxRetries", "streamMaxRetries"}
        )
        or type(transport_values["streamIdleTimeoutMs"]) is not int
        or transport_values["streamIdleTimeoutMs"] < 1
        or type(transport_values["websocketConnectTimeoutMs"]) is not int
        or transport_values["websocketConnectTimeoutMs"] < 1
    ):
        raise AdapterError("provider transport retry and timeout controls are invalid")
    return {
        "name": args.provider_name,
        "baseUrl": base_url,
        "wireApi": args.provider_wire_api,
        "requiresOpenAIAuth": args.provider_requires_openai_auth,
        "supportsWebsockets": args.provider_supports_websockets,
        **transport_values,
    }


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def provider_config_args(provider: dict[str, Any]) -> list[str]:
    name = provider["name"]
    values = {
        f"model_providers.{name}.request_max_retries": str(
            provider["requestMaxRetries"]
        ),
        f"model_providers.{name}.stream_max_retries": str(
            provider["streamMaxRetries"]
        ),
        f"model_providers.{name}.stream_idle_timeout_ms": str(
            provider["streamIdleTimeoutMs"]
        ),
        f"model_providers.{name}.websocket_connect_timeout_ms": str(
            provider["websocketConnectTimeoutMs"]
        ),
    }
    if provider["baseUrl"] is not None:
        values.update(
            {
                "model_provider": _toml_string(name),
                f"model_providers.{name}.name": _toml_string(name),
                f"model_providers.{name}.base_url": _toml_string(
                    provider["baseUrl"]
                ),
                f"model_providers.{name}.wire_api": _toml_string(
                    provider["wireApi"]
                ),
                f"model_providers.{name}.requires_openai_auth": str(
                    provider["requiresOpenAIAuth"]
                ).lower(),
                f"model_providers.{name}.supports_websockets": str(
                    provider["supportsWebsockets"]
                ).lower(),
            }
        )
    return [item for key, value in values.items() for item in ("--config", f"{key}={value}")]


def validate_treatment_resources(
    treatment: str | None, evidence_root: Path | None
) -> None:
    if treatment == "REVIEW_CRAFT_EVIDENCE_LOOP" and evidence_root is None:
        raise AdapterError("Review Craft evidence-loop treatment requires verifier access")
    if (
        treatment in ABLATION_TREATMENTS - {"REVIEW_CRAFT_EVIDENCE_LOOP"}
        and evidence_root is not None
    ):
        raise AdapterError("non-evidence ablation treatments cannot access verifiers")


def validate_repair_workspace(
    *,
    fixture_root: Path,
    marker_path: Path,
    workspace_key: str,
    case_id: str,
    treatment: str,
    round_number: int,
) -> None:
    if round_number < 1:
        raise AdapterError("repair workspace marker round is invalid")
    marker_path = marker_path.resolve(strict=True)
    fixture_root = fixture_root.resolve(strict=True)
    if marker_path.parent != fixture_root.parent or fixture_root.name != "target":
        raise AdapterError("repair target is not a runner-staged workspace")
    try:
        fixture_root.relative_to(Path(__file__).resolve().parents[1])
    except ValueError:
        pass
    else:
        raise AdapterError("repair target must not be inside the Review Craft repository")
    marker_bytes = marker_path.read_bytes()
    if hashlib.sha256(marker_bytes).hexdigest() != workspace_key:
        raise AdapterError("repair workspace marker hash mismatch")
    try:
        marker = json.loads(marker_bytes)
    except json.JSONDecodeError as error:
        raise AdapterError("repair workspace marker is invalid JSON") from error
    expected = {
        "schema": "review-craft.eval-remediation-workspace.v1",
        "caseId": case_id,
        "arm": treatment,
        "round": round_number,
    }
    if any(marker.get(field) != value for field, value in expected.items()):
        raise AdapterError("repair workspace marker does not match the invocation")


def build_codex_command(
    *,
    executable: str,
    args: argparse.Namespace,
    fixture_root: Path,
    skill_root: Path,
    evidence_root: Path | None,
    output_schema: Path,
    output_file: Path,
    provider: dict[str, Any],
    enable_tool_budget_hook: bool = False,
) -> list[str]:
    command = [
        executable,
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox",
        "workspace-write" if args.operation == "repair" else "read-only",
        "--skip-git-repo-check",
        "--color",
        "never",
        "--json",
        "--cd",
        str(fixture_root),
        "--model",
        args.model,
        "--config",
        f'model_reasoning_effort={_toml_string(args.reasoning)}',
        *provider_config_args(provider),
        "--output-schema",
        str(output_schema),
        "--output-last-message",
        str(output_file),
        "-",
    ]
    if enable_tool_budget_hook:
        insertion = command.index("--sandbox")
        command[insertion:insertion] = [
            "--enable",
            "hooks",
            "--dangerously-bypass-hook-trust",
        ]
    if args.treatment in SKILL_TREATMENTS:
        insertion = command.index("--model")
        command[insertion:insertion] = ["--add-dir", str(skill_root)]
    if evidence_root is not None:
        insertion = command.index("--model")
        command[insertion:insertion] = ["--add-dir", str(evidence_root)]
    return command


def run_codex_process(
    command: list[str],
    *,
    prompt: str,
    command_env: dict[str, str],
    replacements: dict[str, str],
    pre_run_isolation: dict[str, Any] | None = None,
    timeout_seconds: int | None = None,
    skill_root: Path | None = None,
    tool_budget_state_path: Path | None = None,
) -> int:
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    reader_errors: list[BaseException] = []
    tool_budget = tool_budget_limits()
    if (tool_budget is None) != (tool_budget_state_path is None):
        raise AdapterError(
            "pre-execution tool budget limits and state must be configured together"
        )
    progress = new_progress_receipt(utc_now(), tool_budget)
    progress_started = time.monotonic()
    turn_started: float | None = None
    progress_invalid = False
    warning_seconds, diagnostic_seconds = inactivity_thresholds()
    first_item_timeout = first_item_timeout_seconds()
    if (
        first_item_timeout is not None
        and timeout_seconds is not None
        and first_item_timeout > timeout_seconds
    ):
        raise AdapterError("first-item timeout must not exceed sample timeout")
    progress["inactivityWarningSeconds"] = warning_seconds
    progress["inactivityDiagnosticSeconds"] = diagnostic_seconds
    if warning_seconds is not None:
        progress["inactivityState"] = "NORMAL"
    progress_lock = threading.Lock()
    sidecar_lock = threading.Lock()
    monitor_stop = threading.Event()
    first_item_observed = threading.Event()
    tool_budget_exceeded = threading.Event()
    started_tool_ids: set[str] = set()
    isolation_receipt: dict[str, Any] | None = None
    if os.environ.get(ISOLATION_OUTPUT_ENV) is not None:
        if pre_run_isolation is None:
            pre_run_isolation = codex_home_extension_state(allow_extensions=False)
        isolation_receipt = new_isolation_receipt(pre_run_isolation)
        write_isolation_output(isolation_receipt)

    def sync_tool_budget_state() -> None:
        if tool_budget is None or tool_budget_state_path is None:
            return
        try:
            state = read_tool_budget_state(tool_budget_state_path, tool_budget)
        except AdapterError:
            with progress_lock:
                progress["toolBudgetState"] = "EXCEEDED"
                progress["toolBudgetExceededAt"] = utc_now()
                progress["toolBudgetExceededKind"] = "CONTROL_FAILURE"
                tool_budget_exceeded.set()
            return
        with progress_lock:
            progress["repositoryToolCallsApproved"] = state[
                "repositoryToolCallsApproved"
            ]
            progress["skillBootstrapToolCallsApproved"] = state[
                "skillBootstrapToolCallsApproved"
            ]
            progress["preExecutionCommandsBlocked"] = state[
                "preExecutionCommandsBlocked"
            ]
            progress["skillBootstrapPrerequisiteState"] = state[
                "skillBootstrapPrerequisiteState"
            ]
            progress["skillBootstrapPrerequisiteBlocks"] = state[
                "skillBootstrapPrerequisiteBlocks"
            ]
            progress["skillBootstrapPrerequisiteBlockedAt"] = state[
                "skillBootstrapPrerequisiteBlockedAt"
            ]
            progress["skillBootstrapPrerequisiteBlockedCommandSha256"] = state[
                "skillBootstrapPrerequisiteBlockedCommandSha256"
            ]
            if state["status"] == "BLOCKED":
                progress["toolBudgetState"] = "EXCEEDED"
                progress["toolBudgetExceededAt"] = state["blockedAt"]
                progress["toolBudgetExceededKind"] = state["blockedKind"]
                tool_budget_exceeded.set()

    def record_progress(line: str) -> None:
        nonlocal progress_invalid, turn_started
        observed_at = utc_now()
        observed_monotonic = time.monotonic()
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            event = None
        event_type = event.get("type") if isinstance(event, dict) else None
        if not isinstance(event_type, str) or event_type not in EVENT_TYPES:
            with progress_lock:
                progress_invalid = True
                progress["availability"] = "UNAVAILABLE"
                progress["unavailableReason"] = "HOST_OUTPUT_INVALID"
            return
        with progress_lock:
            if not progress_invalid:
                progress["availability"] = "AVAILABLE"
                progress["unavailableReason"] = None
            progress["eventCount"] += 1
            progress["lastEventAt"] = observed_at
            progress["lastEventType"] = event_type
            if event_type == "thread.started" and progress["threadStartedAt"] is None:
                progress["threadStartedAt"] = observed_at
                progress["timeToThreadStartedSeconds"] = max(
                    0.0, round(observed_monotonic - progress_started, 3)
                )
            if event_type == "turn.started" and progress["turnStartedAt"] is None:
                progress["turnStartedAt"] = observed_at
                progress["timeToTurnStartedSeconds"] = max(
                    0.0, round(observed_monotonic - progress_started, 3)
                )
                turn_started = observed_monotonic
            if event_type.startswith("item."):
                progress["itemEventCount"] += 1
                item = event.get("item") if isinstance(event, dict) else None
                item_type = item.get("type") if isinstance(item, dict) else None
                item_id = item.get("id") if isinstance(item, dict) else None
                semantic_item = item_type in SEMANTIC_ITEM_TYPES
                if semantic_item:
                    progress["semanticProgressEventCount"] += 1
                    progress["lastSemanticProgressAt"] = observed_at
                if (
                    tool_budget is not None
                    and event_type == "item.started"
                    and isinstance(item_id, str)
                    and item_id not in started_tool_ids
                    and item_type in TOOL_ITEM_TYPES
                ):
                    started_tool_ids.add(item_id)
                    if item_type != "command_execution":
                        progress["toolBudgetState"] = "EXCEEDED"
                        progress["toolBudgetExceededAt"] = observed_at
                        progress["toolBudgetExceededKind"] = "UNMETERED_TOOL"
                        tool_budget_exceeded.set()
                    else:
                        command = str(item.get("command") or "")
                        is_bootstrap = (
                            classify_skill_bootstrap_command(
                                command,
                                skill_root=skill_root,
                            )
                            is not None
                        )
                        counter = (
                            "skillBootstrapToolCallsStarted"
                            if is_bootstrap
                            else "repositoryToolCallsStarted"
                        )
                        progress[counter] += 1
                baseline = turn_started or progress_started
                if semantic_item:
                    progress["lastSemanticProgressType"] = (
                        f"{event_type}:{item_type}"
                        if isinstance(item_type, str)
                        else event_type
                    )
                    if progress["firstItemAt"] is None:
                        elapsed = max(0.0, round(observed_monotonic - baseline, 3))
                        progress["firstItemAt"] = observed_at
                        progress["timeToFirstItemSeconds"] = elapsed
                        progress["maximumPreItemInactivitySeconds"] = elapsed
                        progress["inactivityAgeSeconds"] = 0.0
                        first_item_observed.set()
                        if progress["inactivityState"] == "DIAGNOSTIC":
                            progress["inactivityState"] = "RECOVERED_DIAGNOSTIC"
                        elif progress["inactivityState"] == "WARNING":
                            progress["inactivityState"] = "RECOVERED_WARNING"
                if (
                    item_type in TOOL_ITEM_TYPES
                    and progress["firstToolCallAt"] is None
                ):
                    progress["firstToolCallAt"] = observed_at
                    progress["timeToFirstToolCallSeconds"] = max(
                        0.0, round(observed_monotonic - baseline, 3)
                    )

    def record_transport_progress(line: str) -> bool:
        observed_at = utc_now()
        state: str | None = None
        timestamp_field: str | None = None
        if WEBSOCKET_CONNECTED in line:
            state = "WEBSOCKET_CONNECTED"
            timestamp_field = "providerTransportConnectedAt"
        elif WEBSOCKET_CONNECT_FAILED in line:
            state = "WEBSOCKET_CONNECT_FAILED"
            timestamp_field = "providerTransportFailedAt"
        elif WEBSOCKET_HTTPS_FALLBACK in line:
            state = "HTTPS_FALLBACK"
            timestamp_field = "providerTransportFallbackAt"
        elif WEBSOCKET_CONNECTING in line:
            state = "WEBSOCKET_CONNECTING"
            timestamp_field = "providerTransportStartedAt"
        if state is None or timestamp_field is None:
            return False
        with progress_lock:
            progress["providerTransportState"] = state
            if progress[timestamp_field] is None:
                progress[timestamp_field] = observed_at
        return True

    def progress_snapshot() -> dict[str, Any]:
        with progress_lock:
            return dict(progress)

    def persist_stdout() -> None:
        with sidecar_lock:
            rendered = "".join(stdout_lines)
            write_usage_output(parse_codex_jsonl(rendered))
            write_progress_output(progress_snapshot())
            # A populated trace is the commit marker for matching usage and progress.
            write_tool_trace_output(parse_tool_trace(rendered, replacements))

    def persist_progress() -> None:
        with sidecar_lock:
            write_progress_output(progress_snapshot())

    def monitor_inactivity() -> None:
        if warning_seconds is None or diagnostic_seconds is None:
            return
        while not monitor_stop.wait(INACTIVITY_POLL_SECONDS):
            persist = False
            with progress_lock:
                if progress["firstItemAt"] is not None:
                    return
                baseline = turn_started or progress_started
                age = max(0.0, round(time.monotonic() - baseline, 3))
                progress["inactivityAgeSeconds"] = age
                if age >= diagnostic_seconds and progress["inactivityState"] != "DIAGNOSTIC":
                    progress["inactivityState"] = "DIAGNOSTIC"
                    progress["diagnosticCapturedAt"] = utc_now()
                    progress["processAliveWhenDiagnosticCaptured"] = (
                        process.poll() is None
                    )
                    persist = True
                elif age >= warning_seconds and progress["inactivityState"] == "NORMAL":
                    progress["inactivityState"] = "WARNING"
                    persist = True
            if persist:
                persist_progress()

    def monitor_tool_budget() -> None:
        if tool_budget_state_path is None:
            return
        while not monitor_stop.wait(INACTIVITY_POLL_SECONDS):
            sync_tool_budget_state()
            if tool_budget_exceeded.is_set():
                persist_progress()
                return

    def drain(
        stream: Any,
        sink: Any,
        chunks: list[str],
        *,
        observation: str | None,
    ) -> None:
        try:
            for line in iter(stream.readline, ""):
                if observation == "PROVIDER_TRANSPORT":
                    line = WEBSOCKET_ACCEPT_PATTERN.sub(
                        r'\1"[REDACTED_HANDSHAKE_PROOF]"', line
                    )
                chunks.append(line)
                sink.write(line)
                sink.flush()
                if observation == "HOST_EVENTS":
                    record_progress(line)
                    persist_stdout()
                elif observation == "PROVIDER_TRANSPORT" and record_transport_progress(
                    line
                ):
                    persist_progress()
        except BaseException as error:  # pragma: no cover - defensive thread boundary.
            reader_errors.append(error)
        finally:
            stream.close()

    write_usage_output(unavailable_usage("HOST_OUTPUT_EMPTY"))
    write_progress_output(progress)
    write_tool_trace_output({"schema": "review-craft.eval-tool-trace.v1", "items": []})
    process = open_process_tree(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=command_env,
    )
    if isolation_receipt is not None:
        try:
            post_start = codex_home_extension_state(
                allow_extensions=isolation_receipt["policy"][
                    "allowCodexHomeExtensions"
                ],
                fail_on_extensions=False,
            )
            update_isolation_receipt(
                isolation_receipt, phase="postStart", state=post_start
            )
        except (AdapterError, OSError):
            mark_isolation_capture_unavailable(
                isolation_receipt, "POST_START_CAPTURE_FAILED"
            )
        write_isolation_output(isolation_receipt)
    if process.stdin is None or process.stdout is None or process.stderr is None:
        process.kill()
        raise AdapterError("codex process pipes are unavailable")

    stdout_thread = threading.Thread(
        target=drain,
        args=(process.stdout, sys.stdout, stdout_lines),
        kwargs={"observation": "HOST_EVENTS"},
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=drain,
        args=(process.stderr, sys.stderr, stderr_lines),
        kwargs={"observation": "PROVIDER_TRANSPORT"},
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    monitor_thread = threading.Thread(target=monitor_inactivity, daemon=True)
    monitor_thread.start()
    tool_budget_thread = threading.Thread(target=monitor_tool_budget, daemon=True)
    tool_budget_thread.start()
    try:
        process.stdin.write(prompt)
        process.stdin.flush()
    except BrokenPipeError:
        pass
    finally:
        process.stdin.close()

    def finalize_execution(
        *,
        termination_reason: str,
        timeout_phase: str | None,
        cleanup: str,
    ) -> str:
        monitor_stop.set()
        monitor_thread.join(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
        tool_budget_thread.join(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
        stdout_thread.join(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
        stderr_thread.join(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
        if (
            monitor_thread.is_alive()
            or tool_budget_thread.is_alive()
            or stdout_thread.is_alive()
            or stderr_thread.is_alive()
        ):
            cleanup = "FAILED"
        if isolation_receipt is not None:
            try:
                post_exit = codex_home_extension_state(
                    allow_extensions=isolation_receipt["policy"][
                        "allowCodexHomeExtensions"
                    ],
                    fail_on_extensions=False,
                )
                update_isolation_receipt(
                    isolation_receipt, phase="postExit", state=post_exit
                )
            except (AdapterError, OSError):
                mark_isolation_capture_unavailable(
                    isolation_receipt, "POST_EXIT_CAPTURE_FAILED"
                )
            write_isolation_output(isolation_receipt)
        with progress_lock:
            if termination_reason == "TIMEOUT" and progress["firstItemAt"] is None:
                inactivity_age = max(
                    0.0, round(time.monotonic() - progress_started, 3)
                )
                progress["inactivityAgeSeconds"] = inactivity_age
                progress["maximumPreItemInactivitySeconds"] = max(
                    progress["maximumPreItemInactivitySeconds"] or 0.0,
                    inactivity_age,
                )
                progress["inactivityState"] = "TIMED_OUT_BEFORE_FIRST_ITEM"
            elif (
                termination_reason == "PROCESS_EXIT"
                and warning_seconds is not None
                and progress["firstItemAt"] is None
            ):
                progress["inactivityState"] = "NO_ITEM_BEFORE_EXIT"
            progress["completedAt"] = utc_now()
            progress["terminationReason"] = termination_reason
            progress["timeoutPhase"] = timeout_phase
            progress["processTreeCleanup"] = cleanup
        sync_tool_budget_state()
        if tool_budget is not None:
            completed_trace = parse_tool_trace("".join(stdout_lines), replacements)
            executed_commands = sum(
                1
                for item in completed_trace["items"]
                if item["type"] == "commandExecution"
                and isinstance(item.get("exitCode"), int)
            )
            with progress_lock:
                approved_commands = (
                    progress["repositoryToolCallsApproved"]
                    + progress["skillBootstrapToolCallsApproved"]
                )
                accounted_commands = (
                    approved_commands + progress["preExecutionCommandsBlocked"]
                )
                if executed_commands > accounted_commands:
                    progress["toolBudgetState"] = "EXCEEDED"
                    progress["toolBudgetExceededAt"] = utc_now()
                    progress["toolBudgetExceededKind"] = "CONTROL_FAILURE"
                    tool_budget_exceeded.set()
        persist_stdout()
        return cleanup

    timed_out = False
    budget_exceeded = False
    timeout_phase: str | None = None
    try:
        while True:
            if tool_budget_exceeded.is_set():
                budget_exceeded = True
                break
            observed_returncode = process.poll()
            if observed_returncode is not None:
                sync_tool_budget_state()
                if tool_budget_exceeded.wait(TIMEOUT_RACE_SETTLE_SECONDS):
                    budget_exceeded = True
                    break
                returncode = observed_returncode
                break
            elapsed = time.monotonic() - progress_started
            with progress_lock:
                has_first_item = progress["firstItemAt"] is not None
            if (
                first_item_timeout is not None
                and not has_first_item
                and elapsed >= first_item_timeout
            ):
                first_item_observed.wait(TIMEOUT_RACE_SETTLE_SECONDS)
                observed_returncode = process.poll()
                with progress_lock:
                    has_first_item = progress["firstItemAt"] is not None
                if observed_returncode is not None:
                    returncode = observed_returncode
                    break
                if has_first_item:
                    continue
                timed_out = True
                timeout_phase = "BEFORE_FIRST_ITEM"
                break
            if timeout_seconds is not None and elapsed >= timeout_seconds:
                timed_out = True
                timeout_phase = (
                    "AFTER_FIRST_ITEM" if has_first_item else "BEFORE_FIRST_ITEM"
                )
                break
            time.sleep(0.05)

        if budget_exceeded:
            cleanup = _terminate_codex_process_tree(process)
            returncode = TOOL_BUDGET_EXIT_CODE
            finalize_execution(
                termination_reason="TOOL_BUDGET",
                timeout_phase=None,
                cleanup=cleanup,
            )
        elif timed_out:
            cleanup = _terminate_codex_process_tree(process)
            returncode = TIMEOUT_EXIT_CODE
            finalize_execution(
                termination_reason="TIMEOUT",
                timeout_phase=timeout_phase,
                cleanup=cleanup,
            )
        else:
            cleanup = finalize_process_tree(process)
            finalize_execution(
                termination_reason="PROCESS_EXIT",
                timeout_phase=None,
                cleanup=cleanup,
            )
            if tool_budget_exceeded.is_set():
                returncode = TOOL_BUDGET_EXIT_CODE
                with progress_lock:
                    progress["terminationReason"] = "TOOL_BUDGET"
                    progress["timeoutPhase"] = None
                persist_progress()
    except BaseException as error:
        cleanup = _terminate_codex_process_tree(process)
        reason = "INTERRUPTED" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "ERROR"
        with contextlib.suppress(BaseException):
            finalize_execution(
                termination_reason=reason,
                timeout_phase=None,
                cleanup=cleanup,
            )
        raise

    if reader_errors:
        with progress_lock:
            progress["terminationReason"] = "ERROR"
        persist_progress()
        raise AdapterError(f"codex stream reader failed: {reader_errors[0]}")
    if cleanup == "FAILED":
        raise AdapterError("codex process-tree cleanup could not be confirmed")
    return returncode


def main(argv: list[str] | None = None) -> int:
    effective_argv = sys.argv[1:] if argv is None else argv
    if effective_argv == ["--tool-budget-hook"]:
        return run_tool_budget_hook()
    args = parse_args(effective_argv)
    if args.describe and args.prepare_isolation:
        raise AdapterError("--describe and --prepare-isolation are mutually exclusive")
    provider = provider_metadata(args)
    isolation = codex_home_extension_state(
        allow_extensions=args.allow_codex_home_extensions
    )
    if args.prepare_isolation:
        if args.allow_codex_home_extensions:
            raise AdapterError(
                "isolation preparation does not allow Codex home extensions"
            )
        executable = shutil.which("codex")
        if executable is None:
            raise AdapterError("codex executable is unavailable")
        print(
            json.dumps(
                prepare_codex_home_isolation(
                    executable=executable,
                    model=args.model,
                    reasoning=args.reasoning,
                    initial_state=isolation,
                ),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    if args.describe:
        print(
            json.dumps(
                {
                    "schema": "review-craft.eval-adapter.v10",
                    "name": "codex-cli",
                    "version": codex_version(),
                    "model": args.model,
                    "reasoning": args.reasoning,
                    "adapterVersion": ADAPTER_VERSION,
                    "evidenceKind": "REAL_HOST",
                    "provider": provider,
                    "isolation": isolation,
                    "usage": {
                        "protocol": "review-craft.eval-usage.v1",
                        "transport": "ENV_PATH",
                        "environmentVariable": USAGE_OUTPUT_ENV,
                    },
                    "toolTrace": {
                        "protocol": "review-craft.eval-tool-trace.v1",
                        "transport": "ENV_PATH",
                        "environmentVariable": TOOL_TRACE_OUTPUT_ENV,
                    },
                    "progress": {
                        "protocol": "review-craft.eval-progress.v1",
                        "transport": "ENV_PATH",
                        "environmentVariable": PROGRESS_OUTPUT_ENV,
                    },
                    "isolationReceipt": {
                        "protocol": "review-craft.eval-isolation-receipt.v1",
                        "transport": "ENV_PATH",
                        "environmentVariable": ISOLATION_OUTPUT_ENV,
                    },
                    "timeoutControl": {
                        "protocol": TIMEOUT_CONTROL_PROTOCOL,
                        "transport": "ENV_VALUE",
                        "environmentVariable": SAMPLE_TIMEOUT_ENV,
                        "firstItemEnvironmentVariable": FIRST_ITEM_TIMEOUT_ENV,
                        "timeoutExitCode": TIMEOUT_EXIT_CODE,
                        "finalizationGraceSeconds": TIMEOUT_FINALIZATION_GRACE_SECONDS,
                    },
                    "toolBudgetControl": {
                        "protocol": TOOL_BUDGET_CONTROL_PROTOCOL,
                        "transport": "ENV_VALUE",
                        "repositoryLimitEnvironmentVariable": (
                            REPOSITORY_TOOL_CALL_LIMIT_ENV
                        ),
                        "skillBootstrapLimitEnvironmentVariable": (
                            SKILL_BOOTSTRAP_TOOL_CALL_LIMIT_ENV
                        ),
                        "enforcementEvent": "PreToolUse",
                        "commandEnforcement": "PRE_EXECUTION_BLOCK",
                        "nonCommandEnforcement": "ITEM_STARTED_EARLY_TERMINATION",
                        "hookDecision": "block",
                        "hookTrustMode": "AUTOMATION_BYPASS",
                        "stateTransport": "ADAPTER_MANAGED_PATH",
                        "skillBootstrapPrerequisite": (
                            "REQUIRED_WHEN_LIMIT_POSITIVE"
                        ),
                        "prerequisiteEnforcement": (
                            "RECOVERABLE_PRE_EXECUTION_BLOCK"
                        ),
                        "maxRecoverablePrerequisiteBlocks": (
                            MAX_RECOVERABLE_BOOTSTRAP_PREREQUISITE_BLOCKS
                        ),
                        "prerequisiteFailureKind": "SKILL_BOOTSTRAP_REQUIRED",
                        "bootstrapCommandPolicy": "DEDICATED_BOUND_SKILL_READ_V1",
                        "hookConfigurationSha256": (
                            tool_budget_hook_configuration_sha256()
                        ),
                        "hookImplementationSha256": (
                            tool_budget_hook_implementation_sha256()
                        ),
                        "budgetExitCode": TOOL_BUDGET_EXIT_CODE,
                        "finalizationGraceSeconds": TIMEOUT_FINALIZATION_GRACE_SECONDS,
                    },
                    "isolationPreparation": {
                        "protocol": ISOLATION_PREPARATION_PROTOCOL,
                        "invocation": "APPEND_FLAG",
                        "flag": "--prepare-isolation",
                        "requiredWhenSystemTreeEmpty": True,
                        "networkBoundary": "OWNED_LOOPBACK_BLACKHOLE",
                    },
                    "capabilities": {
                        "operations": ["REVIEW", "REPAIR"],
                        "reviewSandbox": "read-only",
                        "repairSandbox": "workspace-write",
                        "fixtureMutationBoundary": "RUNNER_STAGED_ROOT",
                    },
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
        "case-id": args.case_id,
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
    evidence_root = (
        Path(args.evidence_root).resolve(strict=True) if args.evidence_root else None
    )
    validate_treatment_resources(args.treatment, evidence_root)
    if args.operation == "repair":
        if args.treatment not in REMEDIATION_TREATMENTS:
            raise AdapterError("repair operation requires a remediation-safety treatment")
        if not args.workspace_marker or not args.workspace_key or not args.round_number:
            raise AdapterError("repair operation requires a runner workspace marker")
        validate_repair_workspace(
            fixture_root=fixture_root,
            marker_path=Path(args.workspace_marker),
            workspace_key=args.workspace_key,
            case_id=args.case_id,
            treatment=args.treatment,
            round_number=args.round_number,
        )
    elif args.workspace_marker or args.workspace_key or args.round_number is not None:
        raise AdapterError("review operation must not receive a repair workspace marker")
    prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    limits = tool_budget_limits()
    command = build_codex_command(
        executable=executable,
        args=args,
        fixture_root=fixture_root,
        skill_root=skill_root,
        evidence_root=evidence_root,
        output_schema=Path(args.output_schema).resolve(strict=True),
        output_file=Path(args.output_file).resolve(),
        provider=provider,
        enable_tool_budget_hook=limits is not None,
    )
    command_env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "RUST_LOG": CODEX_TRANSPORT_LOG_FILTER,
    }
    if args.treatment in SKILL_TREATMENTS:
        command_env["SKILL"] = str(skill_root)
    if evidence_root is not None:
        command_env["REVIEW_CRAFT_EVAL_EVIDENCE_ROOT"] = str(evidence_root)
    replacements = {
        str(fixture_root): "$FIXTURE",
        str(skill_root): "$SKILL",
    }
    if evidence_root is not None:
        replacements[str(evidence_root)] = "$EVIDENCE"
    configured_timeout = sample_timeout_seconds()

    if limits is None:
        return run_codex_process(
            command,
            prompt=prompt,
            command_env=command_env,
            replacements=replacements,
            pre_run_isolation=isolation,
            timeout_seconds=configured_timeout,
            skill_root=skill_root,
        )

    home = Path(os.environ["CODEX_HOME"]).expanduser().resolve(strict=True)
    state_path = home / f".review-craft-tool-budget-state-{os.getpid()}.json"
    wait_seconds = (configured_timeout or 900) + TIMEOUT_FINALIZATION_GRACE_SECONDS
    with exclusive_file_lock(
        home,
        name=TOOL_BUDGET_HOOK_LOCK,
        wait_seconds=wait_seconds,
        timeout_message="timed out waiting for another isolated Codex evaluation",
    ):
        hook_path = install_tool_budget_hook(home)
        initialize_tool_budget_state(state_path, limits)
        command_env[TOOL_BUDGET_STATE_ENV] = str(state_path)
        command_env[TOOL_BUDGET_SKILL_ROOT_ENV] = str(skill_root)
        try:
            return run_codex_process(
                command,
                prompt=prompt,
                command_env=command_env,
                replacements=replacements,
                pre_run_isolation=isolation,
                timeout_seconds=configured_timeout,
                skill_root=skill_root,
                tool_budget_state_path=state_path,
            )
        finally:
            remove_tool_budget_hook(hook_path)
            with contextlib.suppress(OSError):
                state_path.unlink()
            with contextlib.suppress(OSError):
                (home / _tool_budget_state_lock_name(state_path)).unlink()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AdapterError, OSError, TimeoutError, ValueError) as error:
        print(f"codex eval adapter: {error}", file=sys.stderr)
        raise SystemExit(2) from None
