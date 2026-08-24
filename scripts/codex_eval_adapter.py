#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_LIB = ROOT / "skills/review-craft/lib"
sys.path.insert(0, str(RUNTIME_LIB))

from review_craft.process_lifecycle import (  # noqa: E402
    finalize_process_tree,
    open_process_tree,
    terminate_process_tree,
)

ADAPTER_VERSION = "0.6.4"
PROVIDER_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
USAGE_OUTPUT_ENV = "REVIEW_CRAFT_EVAL_USAGE_OUTPUT"
TOOL_TRACE_OUTPUT_ENV = "REVIEW_CRAFT_EVAL_TOOL_TRACE_OUTPUT"
PROGRESS_OUTPUT_ENV = "REVIEW_CRAFT_EVAL_PROGRESS_OUTPUT"
ISOLATION_OUTPUT_ENV = "REVIEW_CRAFT_EVAL_ISOLATION_OUTPUT"
ISOLATION_PREPARATION_PROTOCOL = "review-craft.eval-isolation-preparation.v1"
INACTIVITY_WARNING_ENV = "REVIEW_CRAFT_EVAL_INACTIVITY_WARNING_SECONDS"
INACTIVITY_DIAGNOSTIC_ENV = "REVIEW_CRAFT_EVAL_INACTIVITY_DIAGNOSTIC_SECONDS"
FIRST_ITEM_TIMEOUT_ENV = "REVIEW_CRAFT_EVAL_FIRST_ITEM_TIMEOUT_SECONDS"
SAMPLE_TIMEOUT_ENV = "REVIEW_CRAFT_EVAL_SAMPLE_TIMEOUT_SECONDS"
TIMEOUT_CONTROL_PROTOCOL = "review-craft.eval-timeout-control.v2"
TIMEOUT_EXIT_CODE = 124
TIMEOUT_FINALIZATION_GRACE_SECONDS = 30
PROCESS_TERMINATION_GRACE_SECONDS = 5
INACTIVITY_POLL_SECONDS = 0.25
TIMEOUT_RACE_SETTLE_SECONDS = 0.1
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
        "terminationReason": None,
        "timeoutPhase": None,
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
    timeout_seconds: int | None = None,
) -> int:
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    reader_errors: list[BaseException] = []
    progress = new_progress_receipt(utc_now())
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

    def finalize_execution(
        *,
        termination_reason: str,
        timeout_phase: str | None,
        cleanup: str,
    ) -> str:
        monitor_stop.set()
        monitor_thread.join(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
        stdout_thread.join(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
        stderr_thread.join(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
        if monitor_thread.is_alive() or stdout_thread.is_alive() or stderr_thread.is_alive():
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
        persist_stdout()
        return cleanup

    timed_out = False
    timeout_phase: str | None = None
    try:
        while True:
            observed_returncode = process.poll()
            if observed_returncode is not None:
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

        if timed_out:
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
    args = parse_args(argv)
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
                    "timeoutControl": {
                        "protocol": TIMEOUT_CONTROL_PROTOCOL,
                        "transport": "ENV_VALUE",
                        "environmentVariable": SAMPLE_TIMEOUT_ENV,
                        "firstItemEnvironmentVariable": FIRST_ITEM_TIMEOUT_ENV,
                        "timeoutExitCode": TIMEOUT_EXIT_CODE,
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
        timeout_seconds=sample_timeout_seconds(),
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AdapterError, OSError, ValueError) as error:
        print(f"codex eval adapter: {error}", file=sys.stderr)
        raise SystemExit(2) from None
