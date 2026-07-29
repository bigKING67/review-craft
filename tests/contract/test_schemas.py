from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from tests.support import ROOT, create_run, make_target, populate_valid_run, run_cli


class SchemaTests(unittest.TestCase):
    def test_declarative_policy_fields_disclose_their_enforcement_boundary(self) -> None:
        schema_path = ROOT / "skills/review-craft/schemas/config.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        policy = schema["properties"]["policy"]["properties"]
        for field in ("allowNetwork", "allowInstall"):
            description = policy[field].get("description", "")
            self.assertIn("declarative", description)
            self.assertIn("does not enforce", description)

    def test_final_artifacts_match_public_schemas(self) -> None:
        target_tmp, target = make_target()
        self.addCleanup(target_tmp.cleanup)
        with tempfile.TemporaryDirectory() as output:
            run_dir = create_run(target, Path(output))
            populate_valid_run(run_dir)
            finalized = run_cli("finalize", "--run-dir", str(run_dir))
            self.assertEqual(finalized.returncode, 0, finalized.stderr)
            pairs = (
                ("review-manifest.json", "review-manifest.schema.json"),
                ("review-scope.json", "review-scope.schema.json"),
                ("quality-model.json", "quality-model.schema.json"),
                ("coverage.json", "coverage.schema.json"),
                ("module-map.json", "module-map.schema.json"),
                ("dependency-map.json", "dependency-map.schema.json"),
                ("findings.json", "findings.schema.json"),
                ("decisions.json", "decisions.schema.json"),
                ("scorecard.json", "scorecard.schema.json"),
                ("remediation-plan.json", "remediation-plan.schema.json"),
            )
            schema_root = ROOT / "skills/review-craft/schemas"
            for artifact, schema_name in pairs:
                schema = json.loads((schema_root / schema_name).read_text(encoding="utf-8"))
                instance = json.loads((run_dir / artifact).read_text(encoding="utf-8"))
                errors = list(
                    Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
                        instance
                    )
                )
                self.assertEqual(errors, [], f"{artifact}: {errors}")

    def test_tracked_golden_snapshots_are_content_bound_and_sanitized(self) -> None:
        schema_path = ROOT / "evals/schemas/eval-golden-snapshot.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        snapshots = sorted((ROOT / "evals/golden-results").glob("*/snapshot.json"))
        self.assertTrue(snapshots)
        forbidden_keys = {
            "adaptercommand",
            "apikey",
            "baseurl",
            "credential",
            "credentials",
            "password",
            "prompt",
            "promptartifact",
            "prompttemplate",
            "secret",
            "stderr",
            "stdout",
        }
        absolute_paths = (
            re.compile(r"/Users/[A-Za-z0-9._-]+/"),
            re.compile(r"/home/[A-Za-z0-9._-]+/"),
            re.compile(r"[A-Za-z]:\\\\Users\\\\[^\\\\]+\\\\"),
        )

        def keys(value: object) -> set[str]:
            if isinstance(value, dict):
                return {
                    str(key).lower()
                    for key in value
                } | set().union(*(keys(child) for child in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(child) for child in value))
            return set()

        for snapshot_path in snapshots:
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            errors = list(Draft202012Validator(schema).iter_errors(payload))
            self.assertEqual(errors, [], f"{snapshot_path}: {errors}")
            canonical = json.dumps(
                {
                    key: value
                    for key, value in payload.items()
                    if key != "contentSha256"
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            self.assertEqual(payload["contentSha256"], hashlib.sha256(canonical).hexdigest())
            self.assertEqual(keys(payload) & forbidden_keys, set())
            rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            self.assertFalse(any(pattern.search(rendered) for pattern in absolute_paths))

            leaked = json.loads(json.dumps(payload))
            leaked["host"]["provider"]["baseUrl"] = "https://provider.invalid"
            self.assertTrue(list(Draft202012Validator(schema).iter_errors(leaked)))


if __name__ == "__main__":
    unittest.main()
