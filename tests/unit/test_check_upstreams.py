from __future__ import annotations

import json
import subprocess
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.check_upstreams import UpstreamContractError, evaluate, load_contract
from tests.support import ROOT


class UpstreamCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_contract(ROOT / "contracts/upstreams.json")

    def test_repository_contract_passes_offline_without_network(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/check_upstreams.py"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["mode"], "offline")
        self.assertEqual(payload["sources"][0]["status"], "NOT_CHECKED")

    def test_contract_rejects_non_full_revision(self) -> None:
        payload = deepcopy(self.contract)
        payload["sources"][0]["reviewedRevision"] = "add872f"
        with TemporaryDirectory(prefix="review-craft-upstream-") as directory:
            contract_path = Path(directory) / "upstreams.json"
            contract_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(UpstreamContractError):
                load_contract(contract_path)

    def test_contract_rejects_unsafe_repository_and_unknown_fields(self) -> None:
        for repository, extra in (
            ("file:///tmp/upstream", {}),
            ("https://github.com:invalid/tt-a1i/simplify-codebase", {}),
            (
                "https://github.com/tt-a1i/simplify-codebase",
                {"unreviewedField": True},
            ),
        ):
            payload = deepcopy(self.contract)
            payload["sources"][0]["repository"] = repository
            payload["sources"][0].update(extra)
            with TemporaryDirectory(prefix="review-craft-upstream-") as directory:
                contract_path = Path(directory) / "upstreams.json"
                contract_path.write_text(json.dumps(payload), encoding="utf-8")
                with self.subTest(repository=repository, extra=extra), self.assertRaises(
                    UpstreamContractError
                ):
                    load_contract(contract_path)

    def test_remote_comparison_distinguishes_current_and_updated(self) -> None:
        current = self.contract["sources"][0]["reviewedRevision"]
        for remote_revision, status, code in (
            (current, "CURRENT", 0),
            ("f" * 40, "UPDATED", 1),
        ):
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=f"{remote_revision}\trefs/heads/main\n",
                stderr="",
            )
            with self.subTest(status=status), patch(
                "scripts.check_upstreams.subprocess.run", return_value=completed
            ):
                payload, actual_code = evaluate(deepcopy(self.contract), remote=True)
                self.assertEqual(payload["sources"][0]["status"], status)
                self.assertEqual(actual_code, code)

    def test_remote_failure_is_explicit_and_fails_closed(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="fixture failure"
        )
        with patch("scripts.check_upstreams.subprocess.run", return_value=completed):
            payload, code = evaluate(deepcopy(self.contract), remote=True)

        self.assertEqual(code, 2)
        self.assertEqual(payload["sources"][0]["status"], "UNREACHABLE")
        self.assertNotIn("fixture failure", payload["sources"][0]["error"])


if __name__ == "__main__":
    unittest.main()
