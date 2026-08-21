from __future__ import annotations

import json
import re
from typing import Any

INCORRECT_API_KEY_PATTERN = re.compile(
    r"(?i)(Incorrect API key provided:\s*(?:\\?[\"'])?)[^\s,\\\"']+"
)
LABELED_CREDENTIAL_PATTERN = re.compile(
    r"(?i)((?:\\?[\"'])?"
    r"(?:api[-_]?key|password|secret|access[-_]?token|refresh[-_]?token)"
    r"(?:\\?[\"'])?\s*(?::(?![=])|=(?![=>~]))\s*"
    r"(?:\\?[\"'])?)[^\s,\\\"']+"
)
AUTHORIZATION_VALUE_PATTERN = re.compile(
    r"(?i)((?:\\?[\"'])?authorization(?:\\?[\"'])?\s*"
    r"(?::(?![=])|=(?![=>~]))\s*(?:\\?[\"'])?"
    r"(?:bearer\s+)?)[^\s,\\\"']+"
)
BEARER_AUTHORIZATION_PATTERN = re.compile(
    r"(?i)((?:\\?[\"'])?authorization(?:\\?[\"'])?\s*"
    r"(?::(?![=])|=(?![=>~]))\s*(?:\\?[\"'])?bearer\s+)"
    r"[^\s,\\\"']+"
)
UNLABELED_SECRET_PATTERN = re.compile(
    r"(?i)(?:\bsk-[A-Za-z0-9_-]{16,}\b|\bgh[pousr]_[A-Za-z0-9]{20,}\b|"
    r"\bAKIA[0-9A-Z]{16}\b|\bxox[baprs]-[A-Za-z0-9-]{20,}\b)"
)
PRIVATE_KEY_BLOCK_PATTERN = re.compile(
    r"(?is)-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
    r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)
LABELED_SENSITIVE_OUTPUT_PATTERNS = (
    INCORRECT_API_KEY_PATTERN,
    LABELED_CREDENTIAL_PATTERN,
    AUTHORIZATION_VALUE_PATTERN,
)
HIGH_CONFIDENCE_SENSITIVE_OUTPUT_PATTERNS = (
    INCORRECT_API_KEY_PATTERN,
    BEARER_AUTHORIZATION_PATTERN,
    UNLABELED_SECRET_PATTERN,
    PRIVATE_KEY_BLOCK_PATTERN,
)
SENSITIVE_OUTPUT_PATTERNS = (
    *LABELED_SENSITIVE_OUTPUT_PATTERNS,
    UNLABELED_SECRET_PATTERN,
    PRIVATE_KEY_BLOCK_PATTERN,
)
CREDENTIAL_SOURCE_COMMAND = re.compile(
    r"(?i)(?:(?:^|&&|\|\||;|\s-lc\s+[\"']?)\s*(?:env|printenv)\b|"
    r"\b(?:python(?:3)?|node)\b[^\n]*(?:os\.environ|process\.env)|"
    r"(?:~|\$HOME)/(?:\.codex/)?auth\.json\b|"
    r"\bsecurity\s+find-(?:generic|internet)-password\b)"
)


def redact_output(payload: bytes) -> bytes:
    rendered = payload.decode("utf-8", errors="replace")
    for pattern in LABELED_SENSITIVE_OUTPUT_PATTERNS:
        rendered = pattern.sub(r"\1[REDACTED]", rendered)
    for pattern in (UNLABELED_SECRET_PATTERN, PRIVATE_KEY_BLOCK_PATTERN):
        rendered = pattern.sub("[REDACTED]", rendered)
    return rendered.encode("utf-8")


def _matches(rendered: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(rendered) for pattern in patterns)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in _strings(child)]
    if isinstance(value, list):
        return [text for child in value for text in _strings(child)]
    return []


def _command_item_contains_sensitive_output(item: dict[str, Any]) -> bool:
    command = str(item.get("command") or "")
    if _matches(command, SENSITIVE_OUTPUT_PATTERNS):
        return True
    # Generic labels in source excerpts are not host-secret evidence by themselves.
    output_patterns = (
        SENSITIVE_OUTPUT_PATTERNS
        if CREDENTIAL_SOURCE_COMMAND.search(command)
        else HIGH_CONFIDENCE_SENSITIVE_OUTPUT_PATTERNS
    )
    if _matches(str(item.get("aggregated_output") or ""), output_patterns):
        return True
    remaining = {
        key: value for key, value in item.items() if key not in {"command", "aggregated_output"}
    }
    return any(
        _matches(text, HIGH_CONFIDENCE_SENSITIVE_OUTPUT_PATTERNS) for text in _strings(remaining)
    )


def _event_contains_sensitive_output(event: dict[str, Any]) -> bool | None:
    event_type = event["type"]
    if event_type in {"error", "turn.failed"}:
        return any(_matches(text, SENSITIVE_OUTPUT_PATTERNS) for text in _strings(event))
    if event_type.startswith("item."):
        item = event.get("item")
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            return None
        if item["type"] == "command_execution":
            return _command_item_contains_sensitive_output(item)
        patterns = (
            HIGH_CONFIDENCE_SENSITIVE_OUTPUT_PATTERNS
            if item["type"] in {"agent_message", "reasoning", "file_change"}
            else SENSITIVE_OUTPUT_PATTERNS
        )
        return any(_matches(text, patterns) for text in _strings(item))
    if event_type not in {"thread.started", "turn.started", "turn.completed"}:
        return None
    return any(
        _matches(text, HIGH_CONFIDENCE_SENSITIVE_OUTPUT_PATTERNS) for text in _strings(event)
    )


def _jsonl_contains_sensitive_output(payload: bytes) -> bool | None:
    lines = [
        line for line in payload.decode("utf-8", errors="replace").splitlines() if line.strip()
    ]
    if not lines:
        return False
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            return None
        events.append(event)

    for event in events:
        result = _event_contains_sensitive_output(event)
        if result is None:
            return None
        if result:
            return True
    return False


def contains_sensitive_output(stdout: bytes, stderr: bytes = b"") -> bool:
    rendered_stderr = stderr.decode("utf-8", errors="replace")
    if _matches(rendered_stderr, SENSITIVE_OUTPUT_PATTERNS):
        return True
    jsonl_result = _jsonl_contains_sensitive_output(stdout)
    if jsonl_result is not None:
        return jsonl_result
    return _matches(stdout.decode("utf-8", errors="replace"), SENSITIVE_OUTPUT_PATTERNS)
