from __future__ import annotations

# tests.support initializes the canonical runtime import path.
# ruff: noqa: I001

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import make_target, run_cli

from review_craft.configuration import default_config
from review_craft.delivery import collect_delivery_evidence
from review_craft.remediation_contract import current_source
from review_craft.repository import inspect_git, inventory, run_git
from review_craft.repository_analysis import detect_profile


class RepositoryBoundaryTests(unittest.TestCase):
    def test_failed_git_status_cannot_report_clean_or_create_a_review(self) -> None:
        temporary, target = make_target(commit=True)
        self.addCleanup(temporary.cleanup)
        (target / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        self.assertTrue(inspect_git(target).status)
        run_git(target, "config", "status.showUntrackedFiles", "invalid", check=True)
        self.assertNotEqual(run_git(target, "status", "--porcelain=v1", "-z").returncode, 0)

        with self.assertRaisesRegex(RuntimeError, "git status failed"):
            inspect_git(target)
        with tempfile.TemporaryDirectory() as output:
            completed = run_cli("preflight", "--target", str(target), "--output-root", output)
            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertIn("git status failed", completed.stderr)
            self.assertEqual(list(Path(output).iterdir()), [])

    def test_failed_git_status_cannot_verify_delivery_local_source(self) -> None:
        temporary, target = make_target(commit=True)
        self.addCleanup(temporary.cleanup)
        (target / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        config = default_config()
        _, source = current_source(target, config)
        run_git(target, "config", "status.showUntrackedFiles", "invalid", check=True)

        with self.assertRaisesRegex(RuntimeError, "git status failed"):
            collect_delivery_evidence(
                target,
                source_configuration=config,
                expected_source_fingerprint=source["sourceFingerprint"],
                verify_push=False,
                github_run=None,
            )

    def test_auto_profile_never_reads_external_manifest_symlinks(self) -> None:
        for name, content in (
            ("package.json", '{"dependencies":{"next":"1"}}'),
            ("pyproject.toml", "[project.scripts]\ntool = 'app:main'\n"),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                target = base / "target"
                target.mkdir()
                external = base / "external"
                external.write_text(content, encoding="utf-8")
                try:
                    (target / name).symlink_to(external)
                except OSError as error:
                    self.skipTest(f"symlink creation unavailable: {error}")
                records, _ = inventory(target)
                self.assertEqual(records[0]["kind"], "symlink")
                with (
                    patch.object(Path, "read_bytes", side_effect=AssertionError("unexpected read")),
                    patch.object(Path, "read_text", side_effect=AssertionError("unexpected read")),
                ):
                    before = detect_profile(target, records, "auto")
                external.write_text("", encoding="utf-8")
                after = detect_profile(target, records, "auto")
                self.assertEqual(before, after)
                self.assertEqual(before["resolved"], "application")
                self.assertTrue(
                    any(name in signal and "symlink" in signal for signal in before["signals"])
                )

    def test_auto_profile_handles_binary_and_invalid_utf8_manifests(self) -> None:
        for name in ("package.json", "pyproject.toml"):
            for payload in (b"\xff", b"#" + b"a" * 8192 + b"\xff"):
                with self.subTest(name=name, size=len(payload)):
                    temporary, target = make_target(git=False)
                    self.addCleanup(temporary.cleanup)
                    (target / name).write_bytes(payload)
                    with tempfile.TemporaryDirectory() as output:
                        completed = run_cli(
                            "preflight", "--target", str(target), "--output-root", output
                        )
                        self.assertEqual(completed.returncode, 0, completed.stderr)
                        run_dir = Path(json.loads(completed.stdout)["runDir"])
                        scope = json.loads((run_dir / "review-scope.json").read_text())
                        signals = scope["profile"]["signals"]
                        self.assertTrue(
                            any(name in signal and "skipped" in signal for signal in signals)
                        )
                        validated = run_cli("validate", "--run-dir", str(run_dir), "--allow-draft")
                        self.assertEqual(validated.returncode, 0, validated.stderr)

    def test_auto_profile_skips_changed_or_missing_inventory_content(self) -> None:
        for replacement in ('{"dependencies":{"next":"1"}}', None):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = root / "package.json"
                manifest.write_text("{}", encoding="utf-8")
                records, _ = inventory(root)
                if replacement is None:
                    manifest.unlink()
                else:
                    manifest.write_text(replacement, encoding="utf-8")
                profile = detect_profile(root, records, "auto")
                self.assertEqual(profile["resolved"], "application")
                self.assertTrue(
                    any(
                        "package.json" in signal and "skipped" in signal
                        for signal in profile["signals"]
                    )
                )

    def test_auto_profile_reports_invalid_package_json(self) -> None:
        for content in ("", " ", "{"):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "package.json").write_text(content, encoding="utf-8")
                records, _ = inventory(root)
                profile = detect_profile(root, records, "auto")
                self.assertEqual(profile["resolved"], "application")
                self.assertTrue(
                    any(
                        "package.json" in signal and "JSONDecodeError" in signal
                        for signal in profile["signals"]
                    )
                )

    def test_explicit_profile_does_not_read_manifest_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_bytes(b"\xff")
            records, _ = inventory(root)
            with patch.object(Path, "read_bytes", side_effect=AssertionError("unexpected read")):
                profile = detect_profile(root, records, "library")
            self.assertEqual(profile["resolved"], "library")
            self.assertEqual(profile["confidence"], "HIGH")
