from __future__ import annotations

# tests.support adds the canonical runtime library to sys.path before product imports.
# ruff: noqa: I001

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from tests.support import ROOT, RUNTIME_SCRIPT, make_target, populate_valid_run, run_cli

from review_craft.evidence import run_configured_command
from review_craft.jsonio import read_json, read_jsonl, sha256_json, write_json, write_jsonl
from review_craft.repository import inventory
from review_craft.repository_analysis import build_dependency_map


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
                        },
                        "slow-check": {
                            "argv": [
                                sys.executable,
                                "-c",
                                (
                                    "import time; from pathlib import Path; "
                                    "time.sleep(0.5); "
                                    "assert 'return 42' in Path('app.py').read_text()"
                                ),
                            ]
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

    def test_tampered_command_argv_breaks_fix_validation(self) -> None:
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

        receipts = read_jsonl(fix_dir / "evidence/commands.jsonl")
        receipt = receipts[0]
        old_id = receipt["id"]
        receipt["argv"] = [sys.executable, "-c", "print('never executed')"]
        new_id = sha256_json(
            {
                "name": receipt["name"],
                "argv": receipt["argv"],
                "startedAt": receipt["startedAt"],
                "cwd": receipt["cwd"],
                "sequence": receipt["sequence"],
            }
        )[:16]
        receipt["id"] = new_id
        for suffix, field in (("stdout", "stdoutArtifact"), ("stderr", "stderrArtifact")):
            old_path = fix_dir / f"evidence/commands/{old_id}.{suffix}"
            new_path = fix_dir / f"evidence/commands/{new_id}.{suffix}"
            old_path.rename(new_path)
            receipt[field] = f"evidence/commands/{new_id}.{suffix}"
        write_jsonl(fix_dir / "evidence/commands.jsonl", receipts)

        result_path = fix_dir / "fix-verification.json"
        result = read_json(result_path)
        result["commands"][0]["receiptId"] = new_id
        result["commands"][0]["receiptSha256"] = sha256_json(receipt)
        write_json(result_path, result)

        validated = run_cli("validate-fix", "--fix-dir", str(fix_dir))
        self.assertEqual(validated.returncode, 2)
        self.assertIn("argv does not match configured command", validated.stderr)

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

    def test_concurrent_verify_allows_one_terminal_result(self) -> None:
        prepared = run_cli(
            "prepare-fix",
            "--run-dir",
            str(self.run_dir),
            "--finding",
            "RC-FINDING-001",
            "--command",
            "slow-check",
            "--output-root",
            self.fix_tmp.name,
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        fix_dir = Path(json.loads(prepared.stdout)["fixDir"])
        (self.target / "app.py").write_text(
            "def answer():\n    return 42\n", encoding="utf-8"
        )
        assessment = self._assessment(
            status="RESOLVED",
            evidence=["change:app.py", "command:slow-check"],
        )
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        argv = [
            sys.executable,
            str(RUNTIME_SCRIPT),
            "verify-fix",
            "--fix-dir",
            str(fix_dir),
            "--assessment",
            str(assessment),
        ]
        processes = [
            subprocess.Popen(
                argv,
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _ in range(2)
        ]
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            results.append((process.returncode, stdout, stderr))

        self.assertEqual(sorted(row[0] for row in results), [0, 2], results)
        success = next(row for row in results if row[0] == 0)
        rejected = next(row for row in results if row[0] == 2)
        self.assertEqual(json.loads(success[1])["status"], "VERIFIED")
        self.assertIn("fix session is already completed", rejected[2])
        receipts = read_jsonl(fix_dir / "evidence/commands.jsonl")
        verification = read_json(fix_dir / "fix-verification.json")
        self.assertEqual(len(receipts), 1)
        self.assertEqual(
            [row["receiptId"] for row in verification["commands"]],
            [receipts[0]["id"]],
        )
        validated = run_cli("validate-fix", "--fix-dir", str(fix_dir))
        self.assertEqual(validated.returncode, 0, validated.stderr)

    def test_incomplete_attempt_receipt_requires_new_fix_session(self) -> None:
        fix_dir = self._prepare()
        (self.target / "app.py").write_text(
            "def answer():\n    return 42\n", encoding="utf-8"
        )
        state = read_json(fix_dir / "fix-state.json")
        run_configured_command(
            session_dir=fix_dir,
            target=self.target,
            commands=state["commands"],
            command_name="check",
            allow_repository_mutation=False,
        )
        invalid_prepared = run_cli(
            "validate-fix", "--fix-dir", str(fix_dir), "--allow-prepared"
        )
        self.assertEqual(invalid_prepared.returncode, 2)
        self.assertIn(
            "prepared/incomplete fix session must not contain command receipts",
            invalid_prepared.stderr,
        )
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
        self.assertEqual(verified.returncode, 2)
        self.assertIn("contains prior command receipts", verified.stderr)
        self.assertEqual(len(read_jsonl(fix_dir / "evidence/commands.jsonl")), 1)

    def test_orphan_receipt_breaks_completed_fix_validation(self) -> None:
        fix_dir = self._prepare()
        (self.target / "app.py").write_text(
            "def answer():\n    return 42\n", encoding="utf-8"
        )
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
        state = read_json(fix_dir / "fix-state.json")
        run_configured_command(
            session_dir=fix_dir,
            target=self.target,
            commands=state["commands"],
            command_name="check",
            allow_repository_mutation=False,
        )

        validated = run_cli("validate-fix", "--fix-dir", str(fix_dir))
        self.assertEqual(validated.returncode, 2)
        self.assertIn("receipt ledger must exactly match", validated.stderr)

    def test_remediation_contract_dependencies_are_one_way(self) -> None:
        records, _ = inventory(ROOT)
        dependency_map = build_dependency_map(ROOT, records)
        edges = {(row["from"], row["to"]) for row in dependency_map["edges"]}
        runtime = "skills/review-craft/lib/review_craft"
        remediation = f"{runtime}/remediation.py"
        validation = f"{runtime}/remediation_validation.py"
        contract = f"{runtime}/remediation_contract.py"
        self.assertNotIn((validation, remediation), edges)
        self.assertIn((remediation, contract), edges)
        self.assertIn((validation, contract), edges)


if __name__ == "__main__":
    unittest.main()
