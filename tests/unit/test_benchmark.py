from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import ROOT

BENCHMARK = ROOT / "scripts/benchmark_runtime.py"


class BenchmarkTests(unittest.TestCase):
    def test_malformed_benchmark_document_fails_without_a_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "result.json"
            result_path.write_text("[]\n", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(BENCHMARK),
                    "validate",
                    "--result",
                    str(result_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("validation failed", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)

    def test_small_runtime_benchmark_is_schema_valid_without_threshold_claims(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "result.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(BENCHMARK),
                    "run",
                    "--sizes",
                    "20",
                    "--repetitions",
                    "1",
                    "--warmups",
                    "0",
                    "--no-git",
                    "--output",
                    str(result_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["parameters"]["sizes"], [20])
            self.assertEqual(payload["measurements"][0]["fileCount"], 20)
            self.assertEqual(
                set(payload["measurements"][0]["operations"]),
                set(json.loads((ROOT / "benchmarks/specs/runtime.json").read_text())["operations"]),
            )
            self.assertEqual(
                payload["measurements"][0]["operations"]["preflight"]["memoryScope"],
                "NOT_CAPTURED_FOR_SUBPROCESS",
            )
            validated = subprocess.run(
                [
                    sys.executable,
                    str(BENCHMARK),
                    "validate",
                    "--result",
                    str(result_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(validated.returncode, 0, validated.stderr)


if __name__ == "__main__":
    unittest.main()
