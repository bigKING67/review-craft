from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from tests.support import ROOT, make_target, populate_valid_run, run_cli


class RemediationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target_tmp, self.target = make_target(commit=True)
        self.output_tmp = tempfile.TemporaryDirectory(prefix="review-craft-runs-")
        self.fix_tmp = tempfile.TemporaryDirectory(prefix="review-craft-fixes-")
        config = self.target / ".review-craft.json"
        config.write_text(
            json.dumps(
                {
                    "commands": {
                        "check": {
                            "argv": [
                                sys.executable,
                                "-c",
                                (
                                    "from pathlib import Path; "
                                    "assert 'return 42' in Path('app.py').read_text()"
                                ),
                            ]
                        },
                        "mutate": {
                            "argv": [
                                sys.executable,
                                "-c",
                                (
                                    "from pathlib import Path; "
                                    "Path('app.py').write_text('def answer():\\n    return 99\\n')"
                                ),
                            ]
                        },
                        "after": {
                            "argv": [sys.executable, "-c", "print('must be skipped')"]
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        created = run_cli(
            "preflight",
            "--target",
            str(self.target),
            "--config",
            str(config),
            "--output-root",
            self.output_tmp.name,
        )
        if created.returncode != 0:
            raise AssertionError(created.stderr)
        self.run_dir = Path(json.loads(created.stdout)["runDir"])
        populate_valid_run(self.run_dir)
        finalized = run_cli("finalize", "--run-dir", str(self.run_dir))
        if finalized.returncode != 0:
            raise AssertionError(finalized.stderr)

    def tearDown(self) -> None:
        self.fix_tmp.cleanup()
        self.output_tmp.cleanup()
        self.target_tmp.cleanup()

    def _prepare(self) -> Path:
        prepared = run_cli(
            "prepare-fix",
            "--run-dir",
            str(self.run_dir),
            "--finding",
            "RC-FINDING-001",
            "--command",
            "check",
            "--output-root",
            self.fix_tmp.name,
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        return Path(json.loads(prepared.stdout)["fixDir"])

    def _assessment(
        self, *, status: str, evidence: list[str], kind: str = "AGENT_ASSISTED"
    ) -> Path:
        path = Path(self.fix_tmp.name) / f"assessment-{status.lower()}.json"
        path.write_text(
            json.dumps(
                {
                    "documentType": "review-craft.fix-assessment",
                    "schemaVersion": "review-craft.fix.v1",
                    "kind": kind,
                    "assessor": "Review Craft test",
                    "assessedAt": "2026-07-30T03:00:00Z",
                    "findings": [
                        {
                            "findingId": "RC-FINDING-001",
                            "status": status,
                            "rationale": "The focused source correction matches the contract.",
                            "evidenceRefs": evidence,
                        }
                    ],
                    "remainingRisks": [],
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_prepare_is_read_only_and_binds_selected_finding(self) -> None:
        before = (self.target / "app.py").read_bytes()
        fix_dir = self._prepare()
        self.assertEqual((self.target / "app.py").read_bytes(), before)
        plan = json.loads((fix_dir / "fix-plan.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["authorization"]["sourceMutation"], "EXPLICIT_USER_REQUIRED")
        self.assertFalse(plan["authorization"]["runtimeMutatesSource"])
        self.assertEqual(
            [row["findingId"] for row in plan["selections"]], ["RC-FINDING-001"]
        )
        validated = run_cli("validate-fix", "--fix-dir", str(fix_dir), "--allow-prepared")
        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.assertEqual(json.loads(validated.stdout)["status"], "PREPARED")

    def test_verify_binds_changes_commands_and_assessment(self) -> None:
        fix_dir = self._prepare()
        (self.target / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        assessment = self._assessment(
            status="RESOLVED",
            evidence=["change:app.py", "command:check"],
        )
        verified = run_cli(
            "verify-fix",
            "--fix-dir",
            str(fix_dir),
            "--assessment",
            str(assessment),
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        result = json.loads(verified.stdout)
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["changes"][0]["path"], "app.py")
        self.assertEqual(result["commands"][0]["exitCode"], 0)
        self.assertEqual(result["findingResults"][0]["locationPathsChanged"], ["app.py"])
        schema_root = ROOT / "skills/review-craft/schemas"
        for artifact, schema_name in (
            ("fix-plan.json", "fix-plan.schema.json"),
            ("fix-assessment.json", "fix-assessment.schema.json"),
            ("fix-verification.json", "fix-verification.schema.json"),
        ):
            schema = json.loads((schema_root / schema_name).read_text(encoding="utf-8"))
            instance = json.loads((fix_dir / artifact).read_text(encoding="utf-8"))
            errors = list(
                Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
                    instance
                )
            )
            self.assertEqual(errors, [], f"{artifact}: {errors}")
        validated = run_cli("validate-fix", "--fix-dir", str(fix_dir))
        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.assertEqual(json.loads(validated.stdout)["status"], "VERIFIED")

    def test_verify_without_source_change_is_not_success(self) -> None:
        fix_dir = self._prepare()
        assessment = self._assessment(
            status="UNRESOLVED",
            evidence=["manual:no source modification was applied"],
            kind="HUMAN",
        )
        verified = run_cli(
            "verify-fix",
            "--fix-dir",
            str(fix_dir),
            "--assessment",
            str(assessment),
        )
        self.assertEqual(verified.returncode, 5, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)["status"], "NO_CHANGES")

    def test_tampered_command_output_breaks_fix_validation(self) -> None:
        fix_dir = self._prepare()
        (self.target / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        assessment = self._assessment(
            status="RESOLVED",
            evidence=["change:app.py", "command:check"],
        )
        verified = run_cli(
            "verify-fix",
            "--fix-dir",
            str(fix_dir),
            "--assessment",
            str(assessment),
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        result = json.loads(verified.stdout)
        receipt_id = result["commands"][0]["receiptId"]
        (fix_dir / f"evidence/commands/{receipt_id}.stdout").write_text(
            "tampered\n", encoding="utf-8"
        )
        validated = run_cli("validate-fix", "--fix-dir", str(fix_dir))
        self.assertEqual(validated.returncode, 2)
        self.assertIn("stdoutSha256 mismatch", validated.stderr)

    def test_failed_verification_command_cannot_produce_verified_status(self) -> None:
        fix_dir = self._prepare()
        (self.target / "app.py").write_text("def answer():\n    return 43\n", encoding="utf-8")
        assessment = self._assessment(
            status="RESOLVED",
            evidence=["change:app.py", "command:check"],
        )
        verified = run_cli(
            "verify-fix",
            "--fix-dir",
            str(fix_dir),
            "--assessment",
            str(assessment),
        )
        self.assertEqual(verified.returncode, 4, verified.stderr)
        result = json.loads(verified.stdout)
        self.assertEqual(result["status"], "FAILED")
        self.assertNotEqual(result["commands"][0]["exitCode"], 0)
        validated = run_cli("validate-fix", "--fix-dir", str(fix_dir))
        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.assertEqual(json.loads(validated.stdout)["status"], "FAILED")

    def test_source_drift_after_verification_breaks_validation(self) -> None:
        fix_dir = self._prepare()
        (self.target / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        assessment = self._assessment(
            status="RESOLVED",
            evidence=["change:app.py", "command:check"],
        )
        verified = run_cli(
            "verify-fix",
            "--fix-dir",
            str(fix_dir),
            "--assessment",
            str(assessment),
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        (self.target / "app.py").write_text("def answer():\n    return 44\n", encoding="utf-8")
        validated = run_cli("validate-fix", "--fix-dir", str(fix_dir))
        self.assertEqual(validated.returncode, 2)
        self.assertIn("target source changed after verification", validated.stderr)

    def test_assessment_must_bind_real_post_fix_evidence(self) -> None:
        fix_dir = self._prepare()
        (self.target / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        assessment = self._assessment(
            status="RESOLVED",
            evidence=["change:not-changed.py", "command:check"],
        )
        verified = run_cli(
            "verify-fix",
            "--fix-dir",
            str(fix_dir),
            "--assessment",
            str(assessment),
        )
        self.assertEqual(verified.returncode, 2)
        self.assertIn("change evidence is not present", verified.stderr)

    def test_mutating_verification_command_stops_later_commands(self) -> None:
        prepared = run_cli(
            "prepare-fix",
            "--run-dir",
            str(self.run_dir),
            "--finding",
            "RC-FINDING-001",
            "--command",
            "mutate",
            "--command",
            "after",
            "--output-root",
            self.fix_tmp.name,
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        fix_dir = Path(json.loads(prepared.stdout)["fixDir"])
        (self.target / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        assessment = self._assessment(
            status="REGRESSED",
            evidence=["change:app.py", "command:mutate"],
        )
        verified = run_cli(
            "verify-fix",
            "--fix-dir",
            str(fix_dir),
            "--assessment",
            str(assessment),
        )
        self.assertEqual(verified.returncode, 4, verified.stderr)
        result = json.loads(verified.stdout)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual([row["name"] for row in result["commands"]], ["mutate"])
        self.assertEqual(result["skippedCommands"], ["after"])
        self.assertTrue(result["commands"][0]["repositoryMutationDetected"])
        validated = run_cli("validate-fix", "--fix-dir", str(fix_dir))
        self.assertEqual(validated.returncode, 0, validated.stderr)


if __name__ == "__main__":
    unittest.main()
