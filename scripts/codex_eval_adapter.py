#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ADAPTER_VERSION = "0.6.4"
PROVIDER_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
USAGE_OUTPUT_ENV = "REVIEW_CRAFT_EVAL_USAGE_OUTPUT"
TOOL_TRACE_OUTPUT_ENV = "REVIEW_CRAFT_EVAL_TOOL_TRACE_OUTPUT"
PROGRESS_OUTPUT_ENV = "REVIEW_CRAFT_EVAL_PROGRESS_OUTPUT"
ISOLATION_OUTPUT_ENV = "REVIEW_CRAFT_EVAL_ISOLATION_OUTPUT"
INACTIVITY_WARNING_ENV = "REVIEW_CRAFT_EVAL_INACTIVITY_WARNING_SECONDS"
INACTIVITY_DIAGNOSTIC_ENV = "REVIEW_CRAFT_EVAL_INACTIVITY_DIAGNOSTIC_SECONDS"
INACTIVITY_POLL_SECONDS = 0.25
USAGE_COLLECTOR = {
    "name": "codex-cli",
    "version": ADAPTER_VERSION,
    "format": "codex-exec-jsonl-v1",
}
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
    parser.add_argument("--allow-codex-home-extensions", action="store_true")
    parser.add_argument("--describe", action="store_true")
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
            exit_code = item.get("exit_code")
            row.update(
                {
                    "command": command or "<unknown>",
                    "exitCode": exit_code if isinstance(exit_code, int) else None,
                    "outputBytes": len(output),
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


def new_progress_receipt(started_at: str) -> dict[str, Any]:
    return {
        "schema": "review-craft.eval-progress.v1",
        "availability": "UNAVAILABLE",
        "startedAt": started_at,
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
        "terminationReason": None,
        "processTreeCleanup": "NOT_VERIFIED",
        "unavailableReason": "HOST_OUTPUT_EMPTY",
    }


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
    return {
        "name": args.provider_name,
        "baseUrl": base_url,
        "wireApi": args.provider_wire_api,
        "requiresOpenAIAuth": args.provider_requires_openai_auth,
        "supportsWebsockets": args.provider_supports_websockets,
    }


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def provider_config_args(provider: dict[str, Any]) -> list[str]:
    if provider["baseUrl"] is None:
        return []
    name = provider["name"]
    values = {
        "model_provider": _toml_string(name),
        f"model_providers.{name}.name": _toml_string(name),
        f"model_providers.{name}.base_url": _toml_string(provider["baseUrl"]),
        f"model_providers.{name}.wire_api": _toml_string(provider["wireApi"]),
        f"model_providers.{name}.requires_openai_auth": str(
            provider["requiresOpenAIAuth"]
        ).lower(),
        f"model_providers.{name}.supports_websockets": str(
            provider["supportsWebsockets"]
        ).lower(),
    }
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
) -> int:
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    reader_errors: list[BaseException] = []
    progress = new_progress_receipt(utc_now())
    progress_started = time.monotonic()
    turn_started: float | None = None
    progress_invalid = False
    warning_seconds, diagnostic_seconds = inactivity_thresholds()
    progress["inactivityWarningSeconds"] = warning_seconds
    progress["inactivityDiagnosticSeconds"] = diagnostic_seconds
    if warning_seconds is not None:
        progress["inactivityState"] = "NORMAL"
    progress_lock = threading.Lock()
    sidecar_lock = threading.Lock()
    monitor_stop = threading.Event()
    isolation_receipt: dict[str, Any] | None = None
    if os.environ.get(ISOLATION_OUTPUT_ENV) is not None:
        if pre_run_isolation is None:
            pre_run_isolation = codex_home_extension_state(allow_extensions=False)
        isolation_receipt = new_isolation_receipt(pre_run_isolation)
        write_isolation_output(isolation_receipt)

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
                progress["semanticProgressEventCount"] += 1
                progress["lastSemanticProgressAt"] = observed_at
                item = event.get("item") if isinstance(event, dict) else None
                item_type = item.get("type") if isinstance(item, dict) else None
                progress["lastSemanticProgressType"] = (
                    f"{event_type}:{item_type}" if isinstance(item_type, str) else event_type
                )
                baseline = turn_started or progress_started
                if progress["firstItemAt"] is None:
                    elapsed = max(0.0, round(observed_monotonic - baseline, 3))
                    progress["firstItemAt"] = observed_at
                    progress["timeToFirstItemSeconds"] = elapsed
                    progress["maximumPreItemInactivitySeconds"] = elapsed
                    progress["inactivityAgeSeconds"] = 0.0
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

    def drain(
        stream: Any,
        sink: Any,
        chunks: list[str],
        *,
        persist: bool,
    ) -> None:
        try:
            for line in iter(stream.readline, ""):
                chunks.append(line)
                sink.write(line)
                sink.flush()
                if persist:
                    record_progress(line)
                    persist_stdout()
        except BaseException as error:  # pragma: no cover - defensive thread boundary.
            reader_errors.append(error)
        finally:
            stream.close()

    write_usage_output(unavailable_usage("HOST_OUTPUT_EMPTY"))
    write_progress_output(progress)
    write_tool_trace_output({"schema": "review-craft.eval-tool-trace.v1", "items": []})
    process = subprocess.Popen(
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
        kwargs={"persist": True},
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=drain,
        args=(process.stderr, sys.stderr, stderr_lines),
        kwargs={"persist": False},
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    monitor_thread = threading.Thread(target=monitor_inactivity, daemon=True)
    monitor_thread.start()
    try:
        process.stdin.write(prompt)
        process.stdin.flush()
    except BrokenPipeError:
        pass
    finally:
        process.stdin.close()

    returncode = process.wait()
    monitor_stop.set()
    monitor_thread.join()
    stdout_thread.join()
    stderr_thread.join()
    if reader_errors:
        raise AdapterError(f"codex stream reader failed: {reader_errors[0]}")
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
        if (
            warning_seconds is not None
            and progress["firstItemAt"] is None
        ):
            progress["inactivityState"] = "NO_ITEM_BEFORE_EXIT"
        progress["terminationReason"] = "PROCESS_EXIT"
        progress["processTreeCleanup"] = "NOT_REQUIRED"
    persist_stdout()
    return returncode


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    provider = provider_metadata(args)
    isolation = codex_home_extension_state(
        allow_extensions=args.allow_codex_home_extensions
    )
    if args.describe:
        print(
            json.dumps(
                {
                    "schema": "review-craft.eval-adapter.v6",
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
    command = build_codex_command(
        executable=executable,
        args=args,
        fixture_root=fixture_root,
        skill_root=skill_root,
        evidence_root=evidence_root,
        output_schema=Path(args.output_schema).resolve(strict=True),
        output_file=Path(args.output_file).resolve(),
        provider=provider,
    )
    command_env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    if evidence_root is not None:
        command_env["REVIEW_CRAFT_EVAL_EVIDENCE_ROOT"] = str(evidence_root)
    replacements = {
        str(fixture_root): "$FIXTURE",
        str(skill_root): "$SKILL",
    }
    if evidence_root is not None:
        replacements[str(evidence_root)] = "$EVIDENCE"
    return run_codex_process(
        command,
        prompt=prompt,
        command_env=command_env,
        replacements=replacements,
        pre_run_isolation=isolation,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AdapterError, OSError, ValueError) as error:
        print(f"codex eval adapter: {error}", file=sys.stderr)
        raise SystemExit(2) from None
