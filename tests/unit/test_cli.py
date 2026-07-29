from __future__ import annotations

import json
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


if __name__ == "__main__":
    unittest.main()
