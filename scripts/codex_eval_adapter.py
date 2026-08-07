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
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ADAPTER_VERSION = "0.6.0"
PROVIDER_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
USAGE_OUTPUT_ENV = "REVIEW_CRAFT_EVAL_USAGE_OUTPUT"
TOOL_TRACE_OUTPUT_ENV = "REVIEW_CRAFT_EVAL_TOOL_TRACE_OUTPUT"
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
    "ADVERSARIAL_PROMPT",
    "RISK_LENS_ADVERSARIAL",
    "REVIEW_CRAFT_EVIDENCE_LOOP",
}
SKILL_TREATMENTS = {"REVIEW_CRAFT", "REVIEW_CRAFT_EVIDENCE_LOOP"}


class AdapterError(RuntimeError):
    pass


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
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_tool_trace_output(payload: dict[str, Any]) -> None:
    output = os.environ.get(TOOL_TRACE_OUTPUT_ENV)
    if output is None:
        return
    path = Path(output).expanduser()
    if not path.parent.is_dir():
        raise AdapterError("tool trace output parent directory does not exist")
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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


def codex_home_extension_state(*, allow_extensions: bool) -> dict[str, Any]:
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
    if extension_paths and not allow_extensions:
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
        "read-only",
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
                    "schema": "review-craft.eval-adapter.v4",
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
    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        env=command_env,
    )
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    write_usage_output(parse_codex_jsonl(completed.stdout))
    replacements = {
        str(fixture_root): "$FIXTURE",
        str(skill_root): "$SKILL",
    }
    if evidence_root is not None:
        replacements[str(evidence_root)] = "$EVIDENCE"
    write_tool_trace_output(parse_tool_trace(completed.stdout, replacements))
    return completed.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AdapterError, OSError, ValueError) as error:
        print(f"codex eval adapter: {error}", file=sys.stderr)
        raise SystemExit(2) from None
