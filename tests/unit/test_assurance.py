from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import make_target, populate_valid_run, run_cli


class AssuranceTests(unittest.TestCase):
    def _preflight(
        self,
        target: Path,
        output: str,
        level: str,
        config: Path | None = None,
    ) -> Path:
        args = [
            "preflight",
            "--target",
            str(target),
            "--output-root",
            output,
            "--assurance",
            level,
        ]
        if config is not None:
            args.extend(["--config", str(config)])
        completed = run_cli(*args)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return Path(json.loads(completed.stdout)["runDir"])

    def test_fast_assurance_is_budgeted_and_never_final_score(self) -> None:
        temporary, target = make_target()
        self.addCleanup(temporary.cleanup)
        with tempfile.TemporaryDirectory() as output:
            run_dir = self._preflight(target, output, "fast")
            manifest = json.loads(
                (run_dir / "review-manifest.json").read_text(encoding="utf-8")
            )
            scope = json.loads(
                (run_dir / "review-scope.json").read_text(encoding="utf-8")
            )
            scorecard = json.loads(
                (run_dir / "scorecard.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["configuration"]["assuranceLevel"], "fast")
            self.assertEqual(scope["assuranceLevel"], "fast")
            self.assertEqual(scorecard["assurance"]["level"], "fast")
            self.assertEqual(
                scorecard["assurance"]["budget"]["maxEvidenceCommands"], 3
            )

            populate_valid_run(run_dir)
            rejected = run_cli("finalize", "--run-dir", str(run_dir))
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("fast assurance must remain provisional", rejected.stderr)

            scorecard = json.loads(
                (run_dir / "scorecard.json").read_text(encoding="utf-8")
            )
            scorecard["status"] = "provisional"
            (run_dir / "scorecard.json").write_text(
                json.dumps(scorecard, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            finalized = run_cli("finalize", "--run-dir", str(run_dir))
            self.assertEqual(finalized.returncode, 0, finalized.stderr)
            report = (run_dir / "report.md").read_text(encoding="utf-8")
            self.assertIn("Assurance: `FAST`", report)
            self.assertIn("FAST 保证等级不形成最终评分", report)
            sealed_scorecard = json.loads(
                (run_dir / "scorecard.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                sealed_scorecard["assurance"]["completionStatus"], "PARTIAL"
            )

    def test_assured_finalization_requires_bound_independent_verification(self) -> None:
        temporary, target = make_target()
        self.addCleanup(temporary.cleanup)
        with tempfile.TemporaryDirectory() as output:
            config = target / "assured.json"
            config.write_text(
                json.dumps(
                    {
                        "commands": {
                            "runtime-proof": {
                                "argv": [
                                    sys.executable,
                                    "-c",
                                    "import json; print(json.dumps({'ok': True}))",
                                ],
                                "evidenceClaims": [
                                    {
                                        "id": "runtime-proof",
                                        "kind": "runtime",
                                        "jsonPointer": "/ok",
                                        "equals": True,
                                    }
                                ],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            run_dir = self._preflight(target, output, "assured", config)
            evidence = run_cli(
                "run-evidence",
                "--run-dir",
                str(run_dir),
                "--command",
                "runtime-proof",
            )
            self.assertEqual(evidence.returncode, 0, evidence.stderr)
            populate_valid_run(run_dir)
            scorecard_path = run_dir / "scorecard.json"
            scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
            scorecard["evidenceLevel"] = "E3"
            scorecard_path.write_text(
                json.dumps(scorecard, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            missing = run_cli("finalize", "--run-dir", str(run_dir))
            self.assertEqual(missing.returncode, 2)
            self.assertIn("requires one registered verification artifact", missing.stderr)

            manifest = json.loads(
                (run_dir / "review-manifest.json").read_text(encoding="utf-8")
            )
            verifier = Path(output) / "verifier.json"
            verifier.write_text(
                json.dumps(
                    {
                        "schema": "review-craft.assurance-verification.v1",
                        "reviewRunId": manifest["runId"],
                        "sourceFingerprint": manifest["target"]["sourceFingerprint"],
                        "createdAt": "2026-08-20T00:00:00Z",
                        "verifier": {
                            "kind": "HUMAN",
                            "identifier": "independent-fixture-reviewer",
                            "independent": True,
                        },
                        "assessments": [
                            {
                                "findingId": "RC-FINDING-001",
                                "disposition": "AGREED",
                                "rationale": "The independent fixture trace agrees.",
                                "evidenceRefs": ["source:app.py:1-2"],
                            }
                        ],
                        "unverifiedClaims": [],
                    }
                ),
                encoding="utf-8",
            )
            registered = run_cli(
                "register-evidence",
                "--run-dir",
                str(run_dir),
                "--id",
                "assured-verifier",
                "--source",
                str(verifier),
                "--kind",
                "verification",
                "--producer",
                "independent-fixture-reviewer",
                "--description",
                "Independent assured review fixture.",
                "--media-type",
                "application/json",
            )
            self.assertEqual(registered.returncode, 0, registered.stderr)
            finalized = run_cli("finalize", "--run-dir", str(run_dir))
            self.assertEqual(finalized.returncode, 0, finalized.stderr)
            sealed = json.loads(scorecard_path.read_text(encoding="utf-8"))
            self.assertEqual(sealed["assurance"]["completionStatus"], "COMPLETE")
            self.assertEqual(sealed["assurance"]["verifier"]["status"], "VERIFIED")
            self.assertEqual(
                sealed["assurance"]["verifier"]["evidenceRef"],
                "artifact:assured-verifier",
            )


if __name__ == "__main__":
    unittest.main()
