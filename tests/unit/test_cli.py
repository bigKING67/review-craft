from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import create_run, make_target, run_cli


class CliTests(unittest.TestCase):
    def test_doctor_reports_ready(self) -> None:
        completed = run_cli("doctor", "--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(json.loads(completed.stdout)["ready"])

    def test_preflight_writes_outside_target(self) -> None:
        temporary, target = make_target()
        self.addCleanup(temporary.cleanup)
        with tempfile.TemporaryDirectory() as output:
            run_dir = create_run(target, Path(output))
            self.assertTrue((run_dir / "review-manifest.json").is_file())
            self.assertFalse(str(run_dir).startswith(str(target)))

    def test_preflight_draft_passes_draft_validation(self) -> None:
        temporary, target = make_target()
        self.addCleanup(temporary.cleanup)
        with tempfile.TemporaryDirectory() as output:
            run_dir = create_run(target, Path(output))
            completed = run_cli(
                "validate",
                "--run-dir",
                str(run_dir),
                "--allow-draft",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(json.loads(completed.stdout)["final"])

    def test_focus_preflight_records_dimensions_and_detected_profile(self) -> None:
        temporary, target = make_target()
        self.addCleanup(temporary.cleanup)
        with tempfile.TemporaryDirectory() as output:
            completed = run_cli(
                "preflight",
                "--target",
                str(target),
                "--output-root",
                output,
                "--mode",
                "focus",
                "--focus",
                "architecture,performance",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            run_dir = Path(payload["runDir"])
            scope = json.loads((run_dir / "review-scope.json").read_text(encoding="utf-8"))
            self.assertEqual(scope["mode"], "focus")
            self.assertEqual(scope["dimensions"], ["architecture", "performance"])
            self.assertEqual(scope["profile"]["resolved"], "application")
            self.assertIsNone(scope["diff"])

    def test_diff_preflight_covers_modified_added_and_deleted_files(self) -> None:
        temporary, target = make_target(commit=True)
        self.addCleanup(temporary.cleanup)
        (target / "gone.py").write_text("VALUE = 1\n", encoding="utf-8")
        (target / "SKILL.md").write_text("---\nname: fixture\n---\n", encoding="utf-8")
        subprocess.run(["git", "add", "--", "gone.py", "SKILL.md"], cwd=target, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add deleted fixture"],
            cwd=target,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=target,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        (target / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        (target / "added.py").write_text("from app import answer\n", encoding="utf-8")
        (target / "gone.py").unlink()
        with tempfile.TemporaryDirectory() as output:
            completed = run_cli(
                "preflight",
                "--target",
                str(target),
                "--output-root",
                output,
                "--mode",
                "diff",
                "--base",
                base,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            run_dir = Path(json.loads(completed.stdout)["runDir"])
            scope = json.loads((run_dir / "review-scope.json").read_text(encoding="utf-8"))
            coverage = json.loads((run_dir / "coverage.json").read_text(encoding="utf-8"))
            by_path = {row["path"]: row for row in coverage["files"]}
            self.assertEqual(set(by_path), {"added.py", "app.py", "gone.py"})
            self.assertEqual(by_path["added.py"]["diffStatus"], "A")
            self.assertTrue(by_path["added.py"]["untracked"])
            self.assertEqual(by_path["app.py"]["diffStatus"], "M")
            self.assertEqual(by_path["gone.py"]["kind"], "deleted")
            self.assertEqual(scope["diff"]["baseRevision"], base)
            self.assertEqual(scope["profile"]["resolved"], "agent-project")
            validated = run_cli("validate", "--run-dir", str(run_dir), "--allow-draft")
            self.assertEqual(validated.returncode, 0, validated.stderr)

    def test_focus_mode_requires_dimensions(self) -> None:
        temporary, target = make_target()
        self.addCleanup(temporary.cleanup)
        completed = run_cli("preflight", "--target", str(target), "--mode", "focus")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("focus mode requires", completed.stderr)

    def test_diff_mode_rejects_invalid_base(self) -> None:
        temporary, target = make_target(commit=True)
        self.addCleanup(temporary.cleanup)
        completed = run_cli(
            "preflight",
            "--target",
            str(target),
            "--mode",
            "diff",
            "--base",
            "missing-revision",
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("invalid diff base", completed.stderr)

    def test_diff_focus_records_rename_and_excluded_change(self) -> None:
        temporary, target = make_target(commit=True)
        self.addCleanup(temporary.cleanup)
        (target / "excluded").mkdir()
        (target / "excluded/skip.py").write_text("VALUE = 1\n", encoding="utf-8")
        config = target / ".review-craft.json"
        config.write_text(json.dumps({"exclude": ["excluded/**"]}), encoding="utf-8")
        subprocess.run(
            ["git", "add", "--", ".review-craft.json", "excluded/skip.py"],
            cwd=target,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add diff fixtures"],
            cwd=target,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=target,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(["git", "mv", "app.py", "renamed.py"], cwd=target, check=True)
        (target / "excluded/skip.py").write_text("VALUE = 2\n", encoding="utf-8")
        with tempfile.TemporaryDirectory() as output:
            completed = run_cli(
                "preflight",
                "--target",
                str(target),
                "--config",
                str(config),
                "--output-root",
                output,
                "--mode",
                "diff",
                "--base",
                base,
                "--focus",
                "architecture,testing",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            run_dir = Path(json.loads(completed.stdout)["runDir"])
            scope = json.loads((run_dir / "review-scope.json").read_text(encoding="utf-8"))
            coverage = json.loads((run_dir / "coverage.json").read_text(encoding="utf-8"))
            changes = {row["path"]: row for row in scope["diff"]["changes"]}
            self.assertEqual(scope["mode"], "diff")
            self.assertEqual(scope["dimensions"], ["architecture", "testing"])
            self.assertEqual({row["path"] for row in coverage["files"]}, {"renamed.py"})
            self.assertEqual(changes["renamed.py"]["status"], "R")
            self.assertEqual(changes["renamed.py"]["previousPath"], "app.py")
            self.assertTrue(changes["renamed.py"]["inScope"])
            self.assertFalse(changes["excluded/skip.py"]["inScope"])
            self.assertEqual(changes["excluded/skip.py"]["reason"], "matched exclude pattern")

    def test_diff_scope_drift_blocks_draft_validation(self) -> None:
        temporary, target = make_target(commit=True)
        self.addCleanup(temporary.cleanup)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=target,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        (target / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        with tempfile.TemporaryDirectory() as output:
            created = run_cli(
                "preflight",
                "--target",
                str(target),
                "--output-root",
                output,
                "--mode",
                "diff",
                "--base",
                base,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            run_dir = Path(json.loads(created.stdout)["runDir"])
            (target / "later.py").write_text("VALUE = 1\n", encoding="utf-8")
            validated = run_cli(
                "validate",
                "--run-dir",
                str(run_dir),
                "--allow-draft",
            )
            self.assertEqual(validated.returncode, 2)
            self.assertIn("diff scope changed after preflight", validated.stderr)

    def test_preflight_rejects_output_path_that_resolves_inside_target(self) -> None:
        temporary, target = make_target()
        self.addCleanup(temporary.cleanup)
        completed = run_cli(
            "preflight",
            "--target",
            str(target),
            "--output-root",
            str(target.parent),
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("resolves inside the target repository", completed.stderr)

    def test_preflight_rejects_unknown_policy_field(self) -> None:
        temporary, target = make_target()
        self.addCleanup(temporary.cleanup)
        config = target / ".review-craft.json"
        config.write_text(
            json.dumps({"policy": {"pretendSafe": True}}),
            encoding="utf-8",
        )
        with tempfile.TemporaryDirectory() as output:
            completed = run_cli(
                "preflight",
                "--target",
                str(target),
                "--config",
                str(config),
                "--output-root",
                output,
            )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("unsupported fields pretendSafe", completed.stderr)

    def test_evidence_command_captures_output_without_shell(self) -> None:
        temporary, target = make_target(commit=True)
        self.addCleanup(temporary.cleanup)
        config = target / ".review-craft.json"
        config.write_text(
            json.dumps(
                {
                    "commands": {
                        "literal": {
                            "argv": [
                                sys.executable,
                                "-c",
                                "import sys; print(sys.argv[1])",
                                "; touch nope",
                            ],
                            "cwd": ".",
                            "timeoutSeconds": 30,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        with tempfile.TemporaryDirectory() as output:
            completed = run_cli(
                "preflight",
                "--target",
                str(target),
                "--config",
                str(config),
                "--output-root",
                output,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            run_dir = Path(json.loads(completed.stdout)["runDir"])
            evidence = run_cli("run-evidence", "--run-dir", str(run_dir), "--command", "literal")
            self.assertEqual(evidence.returncode, 0, evidence.stderr)
            receipt = json.loads(evidence.stdout)
            stdout = (run_dir / receipt["stdoutArtifact"]).read_text(encoding="utf-8")
            self.assertEqual(stdout.strip(), "; touch nope")
            self.assertFalse((target / "nope").exists())

    def test_evidence_detects_tracked_mutation(self) -> None:
        temporary, target = make_target(commit=True)
        self.addCleanup(temporary.cleanup)
        config = target / ".review-craft.json"
        config.write_text(
            json.dumps(
                {
                    "commands": {
                        "mutate": {
                            "argv": [
                                sys.executable,
                                "-c",
                                (
                                    "from pathlib import Path; "
                                    "Path('app.py').write_text('changed')"
                                ),
                            ],
                            "cwd": ".",
                            "timeoutSeconds": 30,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        with tempfile.TemporaryDirectory() as output:
            created = run_cli(
                "preflight",
                "--target",
                str(target),
                "--config",
                str(config),
                "--output-root",
                output,
            )
            run_dir = Path(json.loads(created.stdout)["runDir"])
            evidence = run_cli("run-evidence", "--run-dir", str(run_dir), "--command", "mutate")
            self.assertEqual(evidence.returncode, 3)
            self.assertTrue(json.loads(evidence.stdout)["repositoryMutationDetected"])

    def test_evidence_detects_new_untracked_source(self) -> None:
        temporary, target = make_target(commit=True)
        self.addCleanup(temporary.cleanup)
        config = target / ".review-craft.json"
        config.write_text(
            json.dumps(
                {
                    "commands": {
                        "mutate": {
                            "argv": [
                                sys.executable,
                                "-c",
                                "from pathlib import Path; Path('new.py').write_text('VALUE = 1')",
                            ],
                            "timeoutSeconds": 30,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        with tempfile.TemporaryDirectory() as output:
            created = run_cli(
                "preflight",
                "--target",
                str(target),
                "--config",
                str(config),
                "--output-root",
                output,
            )
            run_dir = Path(json.loads(created.stdout)["runDir"])
            evidence = run_cli("run-evidence", "--run-dir", str(run_dir), "--command", "mutate")
            self.assertEqual(evidence.returncode, 3)
            self.assertTrue(json.loads(evidence.stdout)["repositoryMutationDetected"])

    def test_evidence_all_runs_configured_commands_in_stable_order(self) -> None:
        temporary, target = make_target(commit=True)
        self.addCleanup(temporary.cleanup)
        config = target / ".review-craft.json"
        config.write_text(
            json.dumps(
                {
                    "commands": {
                        "second": {"argv": [sys.executable, "-c", "print('second')"]},
                        "first": {"argv": [sys.executable, "-c", "print('first')"]},
                    }
                }
            ),
            encoding="utf-8",
        )
        with tempfile.TemporaryDirectory() as output:
            created = run_cli(
                "preflight",
                "--target",
                str(target),
                "--config",
                str(config),
                "--output-root",
                output,
            )
            run_dir = Path(json.loads(created.stdout)["runDir"])
            evidence = run_cli("run-evidence", "--run-dir", str(run_dir), "--all")
            self.assertEqual(evidence.returncode, 0, evidence.stderr)
            receipts = json.loads(evidence.stdout)["commands"]
            self.assertEqual([row["name"] for row in receipts], ["first", "second"])


if __name__ == "__main__":
    unittest.main()
