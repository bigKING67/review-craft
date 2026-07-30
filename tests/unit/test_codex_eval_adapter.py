from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import ROOT

sys.path.insert(0, str(ROOT / "scripts"))

import codex_eval_adapter as adapter


class CodexEvalAdapterTests(unittest.TestCase):
    def test_codex_command_enables_jsonl_for_structured_usage(self) -> None:
        args = adapter.parse_args(
            ["--model", "gpt-test", "--reasoning", "high"]
        )
        command = adapter.build_codex_command(
            executable="codex",
            args=args,
            fixture_root=Path("/tmp/fixture"),
            skill_root=Path("/tmp/skill"),
            output_schema=Path("/tmp/output.schema.json"),
            output_file=Path("/tmp/output.json"),
            provider=adapter.provider_metadata(args),
        )
        self.assertIn("--json", command)

    def test_codex_jsonl_usage_and_tool_calls_are_structured(self) -> None:
        events = [
            {"type": "thread.started", "thread_id": "thread-1"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {
                    "id": "command-1",
                    "type": "command_execution",
                    "command": "rg --files",
                    "aggregated_output": "file.py",
                    "exit_code": 0,
                    "status": "completed",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "mcp-1",
                    "type": "mcp_tool_call",
                    "server": "fixture",
                    "tool": "inspect",
                    "arguments": {},
                    "result": None,
                    "error": None,
                    "status": "completed",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "message-1",
                    "type": "agent_message",
                    "text": "done",
                },
            },
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 1000,
                    "cached_input_tokens": 400,
                    "cache_write_input_tokens": 50,
                    "output_tokens": 250,
                    "reasoning_output_tokens": 100,
                },
            },
        ]
        payload = adapter.parse_codex_jsonl(
            "\n".join(json.dumps(event) for event in events) + "\n"
        )
        self.assertEqual(payload["availability"], "AVAILABLE")
        self.assertEqual(payload["collector"]["format"], "codex-exec-jsonl-v1")
        self.assertEqual(payload["inputTokens"], 1000)
        self.assertEqual(payload["cachedInputTokens"], 400)
        self.assertEqual(payload["cacheWriteInputTokens"], 50)
        self.assertEqual(payload["outputTokens"], 250)
        self.assertEqual(payload["reasoningOutputTokens"], 100)
        self.assertEqual(payload["totalTokens"], 1250)
        self.assertEqual(payload["turnCount"], 1)
        self.assertEqual(payload["toolCalls"]["total"], 2)
        self.assertEqual(payload["toolCalls"]["byType"]["commandExecution"], 1)
        self.assertEqual(payload["toolCalls"]["byType"]["mcpToolCall"], 1)

    def test_codex_jsonl_format_drift_is_explicitly_unavailable(self) -> None:
        cases = (
            ("", "HOST_OUTPUT_EMPTY"),
            ("not-json\n", "HOST_OUTPUT_INVALID"),
            (json.dumps({"type": "future.event"}), "HOST_FORMAT_UNSUPPORTED"),
            (
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"id": "future", "type": "future_tool"},
                    }
                ),
                "HOST_FORMAT_UNSUPPORTED",
            ),
            (
                json.dumps({"type": "turn.started"}),
                "HOST_USAGE_MISSING",
            ),
            (
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": -1,
                            "cached_input_tokens": 0,
                            "cache_write_input_tokens": 0,
                            "output_tokens": 1,
                            "reasoning_output_tokens": 0,
                        },
                    }
                ),
                "HOST_USAGE_INVALID",
            ),
        )
        for value, expected in cases:
            with self.subTest(expected=expected):
                payload = adapter.parse_codex_jsonl(value)
                self.assertEqual(payload["availability"], "UNAVAILABLE")
                self.assertEqual(payload["unavailableReason"], expected)
                self.assertIsNone(payload["totalTokens"])
                self.assertIsNone(payload["toolCalls"])

    def test_usage_sidecar_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "usage.json"
            payload = adapter.unavailable_usage("HOST_USAGE_MISSING")
            with patch.dict(os.environ, {adapter.USAGE_OUTPUT_ENV: str(output)}):
                adapter.write_usage_output(payload)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), payload)

    def test_explicit_provider_is_validated_and_rendered_as_codex_config(self) -> None:
        args = adapter.parse_args(
            [
                "--model",
                "gpt-test",
                "--reasoning",
                "high",
                "--provider-name",
                "local_proxy",
                "--provider-base-url",
                "http://127.0.0.1:8317/v1",
                "--provider-wire-api",
                "responses",
                "--provider-supports-websockets",
            ]
        )
        provider = adapter.provider_metadata(args)
        rendered = adapter.provider_config_args(provider)
        self.assertEqual(provider["name"], "local_proxy")
        self.assertEqual(provider["baseUrl"], "http://127.0.0.1:8317/v1")
        self.assertIn('model_provider="local_proxy"', rendered)
        self.assertIn(
            'model_providers.local_proxy.base_url="http://127.0.0.1:8317/v1"',
            rendered,
        )
        self.assertIn("model_providers.local_proxy.supports_websockets=true", rendered)

    def test_provider_base_url_rejects_credentials(self) -> None:
        args = adapter.parse_args(
            [
                "--model",
                "gpt-test",
                "--reasoning",
                "high",
                "--provider-name",
                "local",
                "--provider-base-url",
                "http://user:secret@127.0.0.1:8317/v1",
            ]
        )
        with self.assertRaisesRegex(adapter.AdapterError, "credential-free"):
            adapter.provider_metadata(args)

    def test_codex_home_extensions_fail_closed_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            skill = home / "skills/demo/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("# Demo\n", encoding="utf-8")
            with patch.dict(os.environ, {"CODEX_HOME": str(home)}):
                with self.assertRaisesRegex(adapter.AdapterError, "isolated auth-only"):
                    adapter.codex_home_extension_state(allow_extensions=False)
                state = adapter.codex_home_extension_state(allow_extensions=True)
            self.assertEqual(state["codexHomeExtensionFileCount"], 1)
            self.assertNotEqual(state["codexHomeExtensionTreeSha256"], "0" * 64)

    def test_codex_managed_system_skills_are_allowed_and_fingerprinted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            system_skill = home / "skills/.system/review-agent/SKILL.md"
            system_skill.parent.mkdir(parents=True)
            system_skill.write_text("# Managed system skill\n", encoding="utf-8")
            with patch.dict(os.environ, {"CODEX_HOME": str(home)}):
                state = adapter.codex_home_extension_state(allow_extensions=False)
            self.assertEqual(state["codexHomeSystemFileCount"], 1)
            self.assertNotEqual(state["codexHomeSystemTreeSha256"], "0" * 64)
            self.assertEqual(state["codexHomeExtensionFileCount"], 0)
            self.assertEqual(
                state["codexHomeExtensionTreeSha256"],
                hashlib.sha256(b"[]").hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
