from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from tests.support import ROOT

sys.path.insert(0, str(ROOT / "scripts"))

import codex_eval_adapter as adapter


class CodexEvalAdapterTests(unittest.TestCase):
    def test_adapter_version_identifies_the_three_arm_protocol(self) -> None:
        self.assertEqual(adapter.ADAPTER_VERSION, "0.6.4")
        self.assertEqual(adapter.USAGE_COLLECTOR["version"], adapter.ADAPTER_VERSION)
        self.assertEqual(
            adapter.ABLATION_TREATMENTS,
            {
                "ORDINARY_PROMPT",
                "RISK_LENS_REVIEW",
                "REVIEW_CRAFT_EVIDENCE_LOOP",
            },
        )
        isolation = {
            "homeMatchesCodexHome": True,
            "ignoreUserConfig": True,
            "ignoreRules": True,
            "allowCodexHomeExtensions": False,
            "codexHomeSystemFileCount": 0,
            "codexHomeSystemTreeSha256": hashlib.sha256(b"[]").hexdigest(),
            "codexHomeExtensionFileCount": 0,
            "codexHomeExtensionTreeSha256": hashlib.sha256(b"[]").hexdigest(),
        }
        with (
            patch.object(adapter, "codex_version", return_value="codex-cli test"),
            patch.object(
                adapter,
                "codex_home_extension_state",
                return_value=isolation,
            ),
            patch("builtins.print") as print_mock,
        ):
            status = adapter.main(
                ["--model", "gpt-test", "--reasoning", "high", "--describe"]
            )
        self.assertEqual(status, 0)
        description = json.loads(print_mock.call_args.args[0])
        self.assertEqual(description["adapterVersion"], "0.6.4")
        self.assertEqual(description["schema"], "review-craft.eval-adapter.v6")
        self.assertEqual(
            description["progress"],
            {
                "protocol": "review-craft.eval-progress.v1",
                "transport": "ENV_PATH",
                "environmentVariable": adapter.PROGRESS_OUTPUT_ENV,
            },
        )
        self.assertEqual(
            description["isolationReceipt"],
            {
                "protocol": "review-craft.eval-isolation-receipt.v1",
                "transport": "ENV_PATH",
                "environmentVariable": adapter.ISOLATION_OUTPUT_ENV,
            },
        )
        self.assertEqual(
            description["timeoutControl"],
            {
                "protocol": "review-craft.eval-timeout-control.v1",
                "transport": "ENV_VALUE",
                "environmentVariable": adapter.SAMPLE_TIMEOUT_ENV,
                "timeoutExitCode": 124,
                "finalizationGraceSeconds": 30,
            },
        )
        self.assertEqual(
            description["isolationPreparation"],
            {
                "protocol": "review-craft.eval-isolation-preparation.v1",
                "invocation": "APPEND_FLAG",
                "flag": "--prepare-isolation",
                "requiredWhenSystemTreeEmpty": True,
                "networkBoundary": "OWNED_LOOPBACK_BLACKHOLE",
            },
        )
        self.assertEqual(
            description["capabilities"],
            {
                "operations": ["REVIEW", "REPAIR"],
                "reviewSandbox": "read-only",
                "repairSandbox": "workspace-write",
                "fixtureMutationBoundary": "RUNNER_STAGED_ROOT",
            },
        )
        schema = json.loads(
            (ROOT / "evals/schemas/eval-adapter.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(description)), [])

    def test_codex_command_enables_jsonl_for_structured_usage(self) -> None:
        args = adapter.parse_args(
            ["--model", "gpt-test", "--reasoning", "high"]
        )
        command = adapter.build_codex_command(
            executable="codex",
            args=args,
            fixture_root=Path("/tmp/fixture"),
            skill_root=Path("/tmp/skill"),
            evidence_root=None,
            output_schema=Path("/tmp/output.schema.json"),
            output_file=Path("/tmp/output.json"),
            provider=adapter.provider_metadata(args),
        )
        self.assertIn("--json", command)
        sandbox_index = command.index("--sandbox")
        self.assertEqual(command[sandbox_index + 1], "read-only")

        args.operation = "repair"
        repair_command = adapter.build_codex_command(
            executable="codex",
            args=args,
            fixture_root=Path("/tmp/fixture"),
            skill_root=Path("/tmp/skill"),
            evidence_root=None,
            output_schema=Path("/tmp/output.schema.json"),
            output_file=Path("/tmp/output.json"),
            provider=adapter.provider_metadata(args),
        )
        sandbox_index = repair_command.index("--sandbox")
        self.assertEqual(repair_command[sandbox_index + 1], "workspace-write")

    def test_repair_workspace_marker_binds_target_case_arm_and_round(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = workspace / "target"
            target.mkdir()
            marker = workspace / ".review-craft-remediation-workspace.json"
            marker.write_text(
                json.dumps(
                    {
                        "schema": "review-craft.eval-remediation-workspace.v1",
                        "caseId": "bounded-saturating-add-positive",
                        "arm": "REVIEW_CRAFT_EVIDENCE_GATED_LOOP",
                        "round": 2,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            key = hashlib.sha256(marker.read_bytes()).hexdigest()
            adapter.validate_repair_workspace(
                fixture_root=target,
                marker_path=marker,
                workspace_key=key,
                case_id="bounded-saturating-add-positive",
                treatment="REVIEW_CRAFT_EVIDENCE_GATED_LOOP",
                round_number=2,
            )
            with self.assertRaisesRegex(adapter.AdapterError, "hash mismatch"):
                adapter.validate_repair_workspace(
                    fixture_root=target,
                    marker_path=marker,
                    workspace_key="0" * 64,
                    case_id="bounded-saturating-add-positive",
                    treatment="REVIEW_CRAFT_EVIDENCE_GATED_LOOP",
                    round_number=2,
                )
            with self.assertRaisesRegex(adapter.AdapterError, "does not match"):
                adapter.validate_repair_workspace(
                    fixture_root=target,
                    marker_path=marker,
                    workspace_key=key,
                    case_id="bounded-saturating-add-positive",
                    treatment="REVIEW_CRAFT_EVIDENCE_GATED_LOOP",
                    round_number=1,
                )

    def test_repair_rejects_missing_marker_and_repository_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            skill = root / "skill"
            target.mkdir()
            skill.mkdir()
            prompt = root / "prompt.md"
            schema = root / "schema.json"
            prompt.write_text("repair\n", encoding="utf-8")
            schema.write_text("{}\n", encoding="utf-8")
            argv = [
                "--model",
                "gpt-test",
                "--reasoning",
                "high",
                "--fixture-root",
                str(target),
                "--skill-root",
                str(skill),
                "--prompt-file",
                str(prompt),
                "--output-schema",
                str(schema),
                "--output-file",
                str(root / "output.json"),
                "--treatment",
                "ORDINARY_NAIVE_LOOP",
                "--case-id",
                "bounded-saturating-add-positive",
                "--operation",
                "repair",
            ]
            with (
                patch.object(adapter, "codex_home_extension_state", return_value={}),
                patch.object(adapter.shutil, "which", return_value="/usr/bin/codex"),
                self.assertRaisesRegex(adapter.AdapterError, "requires a runner workspace"),
            ):
                adapter.main(argv)

        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            workspace = Path(directory)
            target = workspace / "target"
            target.mkdir()
            marker = workspace / ".review-craft-remediation-workspace.json"
            marker.write_text(
                json.dumps(
                    {
                        "schema": "review-craft.eval-remediation-workspace.v1",
                        "caseId": "bounded-saturating-add-positive",
                        "arm": "ORDINARY_NAIVE_LOOP",
                        "round": 1,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(adapter.AdapterError, "must not be inside"):
                adapter.validate_repair_workspace(
                    fixture_root=target,
                    marker_path=marker,
                    workspace_key=hashlib.sha256(marker.read_bytes()).hexdigest(),
                    case_id="bounded-saturating-add-positive",
                    treatment="ORDINARY_NAIVE_LOOP",
                    round_number=1,
                )

    def test_ablation_resources_are_exposed_only_to_the_evidence_loop(self) -> None:
        fixture_root = Path("/tmp/fixture")
        skill_root = Path("/tmp/skill")
        evidence_root = Path("/tmp/evidence")
        output_schema = Path("/tmp/output.schema.json")
        output_file = Path("/tmp/output.json")
        ordinary = adapter.parse_args(
            [
                "--model",
                "gpt-test",
                "--reasoning",
                "high",
                "--treatment",
                "ORDINARY_PROMPT",
            ]
        )
        ordinary_command = adapter.build_codex_command(
            executable="codex",
            args=ordinary,
            fixture_root=fixture_root,
            skill_root=skill_root,
            evidence_root=None,
            output_schema=output_schema,
            output_file=output_file,
            provider=adapter.provider_metadata(ordinary),
        )
        self.assertNotIn(str(skill_root), ordinary_command)
        self.assertNotIn(str(evidence_root), ordinary_command)
        adapter.validate_treatment_resources("ORDINARY_PROMPT", None)
        with self.assertRaisesRegex(adapter.AdapterError, "cannot access verifiers"):
            adapter.validate_treatment_resources("ORDINARY_PROMPT", evidence_root)

        evidence_loop = adapter.parse_args(
            [
                "--model",
                "gpt-test",
                "--reasoning",
                "high",
                "--treatment",
                "REVIEW_CRAFT_EVIDENCE_LOOP",
            ]
        )
        evidence_command = adapter.build_codex_command(
            executable="codex",
            args=evidence_loop,
            fixture_root=fixture_root,
            skill_root=skill_root,
            evidence_root=evidence_root,
            output_schema=output_schema,
            output_file=output_file,
            provider=adapter.provider_metadata(evidence_loop),
        )
        self.assertIn(str(skill_root), evidence_command)
        self.assertIn(str(evidence_root), evidence_command)
        adapter.validate_treatment_resources("REVIEW_CRAFT_EVIDENCE_LOOP", evidence_root)
        with self.assertRaisesRegex(adapter.AdapterError, "requires verifier access"):
            adapter.validate_treatment_resources("REVIEW_CRAFT_EVIDENCE_LOOP", None)

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

    def test_codex_process_streams_timeout_sidecars_before_child_exit(self) -> None:
        thread_event = {"type": "thread.started"}
        turn_event = {"type": "turn.started"}
        command_event = {
            "type": "item.completed",
            "item": {
                "id": "command-1",
                "type": "command_execution",
                "command": "rg --files",
                "aggregated_output": "file.py\n",
                "exit_code": 0,
                "status": "completed",
            },
        }
        usage_event = {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 100,
                "cached_input_tokens": 0,
                "cache_write_input_tokens": 0,
                "output_tokens": 20,
                "reasoning_output_tokens": 10,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            usage_path = Path(directory) / "usage.json"
            trace_path = Path(directory) / "tool-trace.json"
            progress_path = Path(directory) / "progress.json"
            release_path = Path(directory) / "release-child"
            child = "\n".join(
                (
                    "import json, os, sys, time",
                    "sys.stdin.read()",
                    f"print(json.dumps({thread_event!r}), flush=True)",
                    f"print(json.dumps({turn_event!r}), flush=True)",
                    f"print(json.dumps({command_event!r}), flush=True)",
                    "deadline = time.monotonic() + 10",
                    "while not os.path.exists(os.environ['REVIEW_CRAFT_TEST_RELEASE']) "
                    "and time.monotonic() < deadline:",
                    "    time.sleep(0.01)",
                    f"print(json.dumps({usage_event!r}), flush=True)",
                )
            )
            result: list[int] = []
            stdout = io.StringIO()
            stderr = io.StringIO()

            def invoke() -> None:
                result.append(
                    adapter.run_codex_process(
                        [sys.executable, "-c", child],
                        prompt="review\n",
                        command_env={
                            **os.environ,
                            "PYTHONDONTWRITEBYTECODE": "1",
                            "REVIEW_CRAFT_TEST_RELEASE": str(release_path),
                        },
                        replacements={},
                    )
                )

            with (
                patch.dict(
                    os.environ,
                    {
                        adapter.USAGE_OUTPUT_ENV: str(usage_path),
                        adapter.TOOL_TRACE_OUTPUT_ENV: str(trace_path),
                        adapter.PROGRESS_OUTPUT_ENV: str(progress_path),
                    },
                ),
                patch.object(adapter.sys, "stdout", stdout),
                patch.object(adapter.sys, "stderr", stderr),
            ):
                thread = threading.Thread(target=invoke)
                thread.start()
                try:
                    deadline = time.monotonic() + 2
                    partial_trace = None
                    while time.monotonic() < deadline and thread.is_alive():
                        if trace_path.is_file():
                            candidate = json.loads(
                                trace_path.read_text(encoding="utf-8")
                            )
                            if candidate["items"]:
                                partial_trace = candidate
                                break
                        time.sleep(0.01)
                    self.assertTrue(thread.is_alive())
                    self.assertIsNotNone(partial_trace)
                    self.assertEqual(len(partial_trace["items"]), 1)
                    partial_usage = json.loads(
                        usage_path.read_text(encoding="utf-8")
                    )
                    self.assertEqual(partial_usage["availability"], "UNAVAILABLE")
                    self.assertEqual(
                        partial_usage["unavailableReason"], "HOST_USAGE_MISSING"
                    )
                    partial_progress = json.loads(
                        progress_path.read_text(encoding="utf-8")
                    )
                    self.assertEqual(partial_progress["availability"], "AVAILABLE")
                    self.assertEqual(
                        partial_progress["lastEventType"], "item.completed"
                    )
                    self.assertEqual(partial_progress["eventCount"], 3)
                    self.assertEqual(partial_progress["itemEventCount"], 1)
                    self.assertIsNotNone(
                        partial_progress["timeToFirstItemSeconds"]
                    )
                    self.assertIsNotNone(
                        partial_progress["timeToThreadStartedSeconds"]
                    )
                    self.assertIsNotNone(
                        partial_progress["timeToTurnStartedSeconds"]
                    )
                    self.assertIsNotNone(partial_progress["firstToolCallAt"])
                    self.assertIsNotNone(
                        partial_progress["timeToFirstToolCallSeconds"]
                    )
                finally:
                    release_path.touch()
                    thread.join(timeout=3)

            self.assertFalse(thread.is_alive())
            self.assertEqual(result, [0])
            final_usage = json.loads(usage_path.read_text(encoding="utf-8"))
            self.assertEqual(final_usage["availability"], "AVAILABLE")
            self.assertEqual(final_usage["totalTokens"], 120)
            final_progress = json.loads(progress_path.read_text(encoding="utf-8"))
            self.assertEqual(final_progress["eventCount"], 4)
            self.assertEqual(final_progress["lastEventType"], "turn.completed")
            self.assertEqual(final_progress["terminationReason"], "PROCESS_EXIT")
            self.assertEqual(final_progress["processTreeCleanup"], "NOT_REQUIRED")
            self.assertIn('"type": "item.completed"', stdout.getvalue())
            self.assertEqual(stderr.getvalue(), "")

    def test_codex_process_captures_and_recovers_live_inactivity_diagnostic(
        self,
    ) -> None:
        events = [
            {"type": "thread.started"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {
                    "id": "command-1",
                    "type": "command_execution",
                    "command": "rg --files",
                    "aggregated_output": "file.py\n",
                    "exit_code": 0,
                    "status": "completed",
                },
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            progress_path = root / "progress.json"
            release_path = root / "release-child"
            child = "\n".join(
                (
                    "import json, os, sys, time",
                    "sys.stdin.read()",
                    f"print(json.dumps({events[0]!r}), flush=True)",
                    f"print(json.dumps({events[1]!r}), flush=True)",
                    "while not os.path.exists(os.environ['REVIEW_CRAFT_TEST_RELEASE']):",
                    "    time.sleep(0.01)",
                    f"print(json.dumps({events[2]!r}), flush=True)",
                )
            )
            result: list[int] = []
            stdout = io.StringIO()
            stderr = io.StringIO()

            def invoke() -> None:
                result.append(
                    adapter.run_codex_process(
                        [sys.executable, "-c", child],
                        prompt="review\n",
                        command_env={
                            **os.environ,
                            "PYTHONDONTWRITEBYTECODE": "1",
                            "REVIEW_CRAFT_TEST_RELEASE": str(release_path),
                        },
                        replacements={},
                    )
                )

            with (
                patch.dict(
                    os.environ,
                    {
                        adapter.PROGRESS_OUTPUT_ENV: str(progress_path),
                        adapter.INACTIVITY_WARNING_ENV: "1",
                        adapter.INACTIVITY_DIAGNOSTIC_ENV: "2",
                    },
                ),
                patch.object(adapter.sys, "stdout", stdout),
                patch.object(adapter.sys, "stderr", stderr),
            ):
                thread = threading.Thread(target=invoke)
                thread.start()
                try:
                    deadline = time.monotonic() + 4
                    diagnostic = None
                    while time.monotonic() < deadline and thread.is_alive():
                        if progress_path.is_file():
                            candidate = json.loads(
                                progress_path.read_text(encoding="utf-8")
                            )
                            if candidate["inactivityState"] == "DIAGNOSTIC":
                                diagnostic = candidate
                                break
                        time.sleep(0.05)
                    self.assertIsNotNone(diagnostic)
                    assert diagnostic is not None
                    self.assertTrue(diagnostic["processAliveWhenDiagnosticCaptured"])
                    self.assertIsNotNone(diagnostic["diagnosticCapturedAt"])
                finally:
                    release_path.touch()
                    thread.join(timeout=3)

            self.assertFalse(thread.is_alive())
            self.assertEqual(result, [0])
            final = json.loads(progress_path.read_text(encoding="utf-8"))
            self.assertEqual(final["inactivityState"], "RECOVERED_DIAGNOSTIC")
            self.assertGreaterEqual(final["maximumPreItemInactivitySeconds"], 2)

    def test_inactivity_thresholds_fail_closed(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    adapter.INACTIVITY_WARNING_ENV: "600",
                    adapter.INACTIVITY_DIAGNOSTIC_ENV: "300",
                },
            ),
            self.assertRaisesRegex(
                adapter.AdapterError,
                "warning threshold must be below diagnostic",
            ),
        ):
            adapter.inactivity_thresholds()

    def test_sample_timeout_fails_closed(self) -> None:
        for raw_timeout in ("0", "-1", "not-an-integer"):
            with self.subTest(raw_timeout=raw_timeout), patch.dict(
                os.environ,
                {adapter.SAMPLE_TIMEOUT_ENV: raw_timeout},
            ), self.assertRaisesRegex(
                adapter.AdapterError,
                "sample timeout must be a positive integer",
            ):
                adapter.sample_timeout_seconds()

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
            with patch.dict(
                os.environ,
                {"CODEX_HOME": str(home), "HOME": str(home)},
            ), patch.object(Path, "home", return_value=home.parent / "ambient-home"):
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
            with patch.dict(
                os.environ,
                {"CODEX_HOME": str(home), "HOME": str(home)},
            ):
                state = adapter.codex_home_extension_state(allow_extensions=False)
            self.assertTrue(state["homeMatchesCodexHome"])
            self.assertEqual(state["codexHomeSystemFileCount"], 1)
            self.assertNotEqual(state["codexHomeSystemTreeSha256"], "0" * 64)
            self.assertEqual(state["codexHomeExtensionFileCount"], 0)
            self.assertEqual(
                state["codexHomeExtensionTreeSha256"],
                hashlib.sha256(b"[]").hexdigest(),
            )

    def test_isolation_preparation_is_noop_when_system_tree_exists(self) -> None:
        empty_hash = hashlib.sha256(b"[]").hexdigest()
        initial = {
            "homeMatchesCodexHome": True,
            "ignoreUserConfig": True,
            "ignoreRules": True,
            "allowCodexHomeExtensions": False,
            "codexHomeSystemFileCount": 1,
            "codexHomeSystemTreeSha256": "1" * 64,
            "codexHomeExtensionFileCount": 0,
            "codexHomeExtensionTreeSha256": empty_hash,
        }
        with (
            patch.object(adapter, "codex_version", return_value="codex-cli test"),
            patch.object(adapter.subprocess, "Popen") as popen,
        ):
            receipt = adapter.prepare_codex_home_isolation(
                executable="codex",
                model="gpt-test",
                reasoning="high",
                initial_state=initial,
            )
        popen.assert_not_called()
        self.assertEqual(receipt["status"], "ALREADY_PREPARED")
        self.assertEqual(receipt["networkBoundary"], "NOT_USED")
        self.assertEqual(receipt["before"], receipt["after"])

    def test_isolation_preparation_materializes_before_any_provider_response(
        self,
    ) -> None:
        empty_hash = hashlib.sha256(b"[]").hexdigest()
        initial = {
            "homeMatchesCodexHome": True,
            "ignoreUserConfig": True,
            "ignoreRules": True,
            "allowCodexHomeExtensions": False,
            "codexHomeSystemFileCount": 0,
            "codexHomeSystemTreeSha256": empty_hash,
            "codexHomeExtensionFileCount": 0,
            "codexHomeExtensionTreeSha256": empty_hash,
        }
        prepared = {
            **initial,
            "codexHomeSystemFileCount": 60,
            "codexHomeSystemTreeSha256": "2" * 64,
        }

        class FakeStdin:
            def __init__(self) -> None:
                self.value = ""

            def write(self, value: str) -> None:
                self.value += value

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                self.stdin = FakeStdin()
                self.pid = 12345

            def poll(self) -> None:
                return None

        process = FakeProcess()
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"HOME": directory, "CODEX_HOME": directory},
        ), patch.object(
            adapter.subprocess, "Popen", return_value=process
        ) as popen, patch.object(
            adapter,
            "codex_home_extension_state",
            side_effect=[prepared, prepared, prepared, prepared],
        ), patch.object(
            adapter,
            "_terminate_preparation_process",
            return_value="TERMINATED_AFTER_MATERIALIZATION",
        ), patch.object(
            adapter, "codex_version", return_value="codex-cli test"
        ):
            receipt = adapter.prepare_codex_home_isolation(
                executable="codex",
                model="gpt-test",
                reasoning="high",
                initial_state=initial,
            )
        command = popen.call_args.args[0]
        self.assertIn('model_provider="review_craft_bootstrap"', command)
        self.assertTrue(
            any("http://127.0.0.1:" in argument for argument in command)
        )
        self.assertEqual(receipt["status"], "MATERIALIZED")
        self.assertEqual(receipt["after"]["systemFileCount"], 60)
        self.assertEqual(
            receipt["networkBoundary"], "OWNED_LOOPBACK_BLACKHOLE"
        )
        schema = json.loads(
            (
                ROOT / "evals/schemas/eval-isolation-preparation.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            list(Draft202012Validator(schema).iter_errors(receipt)), []
        )

    def test_codex_home_isolation_requires_home_and_scans_dot_agents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch.dict(
                os.environ,
                {"CODEX_HOME": str(home), "HOME": "/tmp/different-home"},
            ), self.assertRaisesRegex(adapter.AdapterError, "HOME and CODEX_HOME"):
                adapter.codex_home_extension_state(allow_extensions=False)

            extension = home / ".agents/skills/demo/SKILL.md"
            extension.parent.mkdir(parents=True)
            extension.write_text("# Demo\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {"CODEX_HOME": str(home), "HOME": str(home)},
            ), self.assertRaisesRegex(adapter.AdapterError, "isolated auth-only"):
                adapter.codex_home_extension_state(allow_extensions=False)

    def test_isolation_receipt_separates_system_and_user_extension_drift(self) -> None:
        empty_hash = hashlib.sha256(b"[]").hexdigest()
        pre = {
            "homeMatchesCodexHome": True,
            "ignoreUserConfig": True,
            "ignoreRules": True,
            "allowCodexHomeExtensions": False,
            "codexHomeSystemFileCount": 0,
            "codexHomeSystemTreeSha256": empty_hash,
            "codexHomeExtensionFileCount": 0,
            "codexHomeExtensionTreeSha256": empty_hash,
        }
        receipt = adapter.new_isolation_receipt(pre)
        adapter.update_isolation_receipt(receipt, phase="postStart", state=pre)
        self.assertEqual(receipt["availability"], "UNAVAILABLE")
        self.assertEqual(receipt["comparison"]["overall"], "CAPTURE_UNAVAILABLE")
        self.assertEqual(receipt["unavailableReason"], "POST_EXIT_NOT_CAPTURED")
        drifted = dict(pre)
        drifted["codexHomeExtensionFileCount"] = 1
        drifted["codexHomeExtensionTreeSha256"] = "1" * 64
        adapter.update_isolation_receipt(receipt, phase="postExit", state=drifted)
        self.assertEqual(receipt["comparison"]["postStartSystemState"], "MATCHED")
        self.assertEqual(
            receipt["comparison"]["postExitUserExtensionState"], "DRIFTED"
        )
        self.assertEqual(receipt["comparison"]["overall"], "USER_EXTENSION_DRIFT")
        schema = json.loads(
            (ROOT / "evals/schemas/eval-isolation-receipt.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(receipt)), [])

    def test_codex_process_writes_matched_isolation_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            isolation_path = home / "isolation.json"
            environment = {
                **os.environ,
                "HOME": str(home),
                "CODEX_HOME": str(home),
                adapter.ISOLATION_OUTPUT_ENV: str(isolation_path),
            }
            with patch.dict(os.environ, environment, clear=True):
                pre = adapter.codex_home_extension_state(allow_extensions=False)
                status = adapter.run_codex_process(
                    [sys.executable, "-c", "import sys; sys.stdin.read()"],
                    prompt="review\n",
                    command_env=environment,
                    replacements={},
                    pre_run_isolation=pre,
                )
            self.assertEqual(status, 0)
            receipt = json.loads(isolation_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["availability"], "AVAILABLE")
            self.assertEqual(receipt["comparison"]["overall"], "MATCHED")
            self.assertIsNotNone(receipt["postStart"])
            self.assertIsNotNone(receipt["postExit"])

    def test_codex_process_owns_timeout_and_finalizes_isolation(self) -> None:
        events = [
            {"type": "thread.started"},
            {"type": "turn.started"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            late_path = home / "late-descendant"
            descendant = (
                "from pathlib import Path; import time; time.sleep(2); "
                f"Path({str(late_path)!r}).write_text('late')"
            )
            child = "\n".join(
                (
                    "import json, subprocess, sys, time",
                    "sys.stdin.read()",
                    f"subprocess.Popen([sys.executable, '-c', {descendant!r}])",
                    f"print(json.dumps({events[0]!r}), flush=True)",
                    f"print(json.dumps({events[1]!r}), flush=True)",
                    "time.sleep(60)",
                )
            )
            isolation_path = home / "isolation.json"
            usage_path = home / "usage.json"
            trace_path = home / "tool-trace.json"
            progress_path = home / "progress.json"
            environment = {
                **os.environ,
                "HOME": str(home),
                "CODEX_HOME": str(home),
                adapter.ISOLATION_OUTPUT_ENV: str(isolation_path),
                adapter.USAGE_OUTPUT_ENV: str(usage_path),
                adapter.TOOL_TRACE_OUTPUT_ENV: str(trace_path),
                adapter.PROGRESS_OUTPUT_ENV: str(progress_path),
            }
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(adapter.sys, "stdout", stdout),
                patch.object(adapter.sys, "stderr", stderr),
            ):
                pre = adapter.codex_home_extension_state(allow_extensions=False)
                started = time.monotonic()
                status = adapter.run_codex_process(
                    [sys.executable, "-c", child],
                    prompt="review\n",
                    command_env=environment,
                    replacements={},
                    pre_run_isolation=pre,
                    timeout_seconds=1,
                )
                elapsed = time.monotonic() - started

            self.assertEqual(status, adapter.TIMEOUT_EXIT_CODE)
            self.assertLess(elapsed, 10)
            receipt = json.loads(isolation_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["availability"], "AVAILABLE")
            self.assertEqual(receipt["comparison"]["overall"], "MATCHED")
            self.assertIsNotNone(receipt["postExit"])
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
            self.assertEqual(progress["terminationReason"], "TIMEOUT")
            self.assertEqual(progress["processTreeCleanup"], "COMPLETED")
            self.assertEqual(
                progress["inactivityState"], "TIMED_OUT_BEFORE_FIRST_ITEM"
            )
            self.assertGreater(progress["maximumPreItemInactivitySeconds"], 0.9)
            usage = json.loads(usage_path.read_text(encoding="utf-8"))
            self.assertEqual(usage["availability"], "UNAVAILABLE")
            self.assertEqual(usage["unavailableReason"], "HOST_USAGE_MISSING")
            time.sleep(2.5)
            self.assertFalse(late_path.exists())

    def test_tool_trace_normalizes_paths_and_hashes_without_raw_output(self) -> None:
        output = "verification result\n"
        events = [
            {
                "type": "item.completed",
                "item": {
                    "id": "command-1",
                    "type": "command_execution",
                    "command": (
                        "python3 /private/evidence/verify_case.py --target "
                        "/private/fixture"
                    ),
                    "aggregated_output": output,
                    "exit_code": 7,
                    "status": "failed",
                },
            }
        ]
        trace = adapter.parse_tool_trace(
            "\n".join(json.dumps(event) for event in events) + "\n",
            {
                "/private/evidence": "$EVIDENCE",
                "/private/fixture": "$FIXTURE",
            },
        )
        self.assertEqual(trace["schema"], "review-craft.eval-tool-trace.v1")
        self.assertEqual(
            trace["items"][0]["command"],
            "python3 $EVIDENCE/verify_case.py --target $FIXTURE",
        )
        self.assertEqual(trace["items"][0]["exitCode"], 7)
        self.assertEqual(trace["items"][0]["outputBytes"], len(output.encode("utf-8")))
        self.assertEqual(
            trace["items"][0]["outputSha256"],
            hashlib.sha256(output.encode("utf-8")).hexdigest(),
        )
        self.assertNotIn(output, json.dumps(trace))


if __name__ == "__main__":
    unittest.main()
