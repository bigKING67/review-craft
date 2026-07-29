from __future__ import annotations

import hashlib
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
