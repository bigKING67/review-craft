from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_LIB = ROOT / "skills/review-craft/lib"
sys.path.insert(0, str(RUNTIME_LIB))

from review_craft.process_lifecycle import run_process  # noqa: E402


class ProcessLifecycleTests(unittest.TestCase):
    def test_timeout_preserves_partial_output_and_explicit_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_process(
                [
                    sys.executable,
                    "-c",
                    (
                        "import os, time; "
                        "print(os.environ['REVIEW_CRAFT_PROCESS_TEST'], flush=True); "
                        "time.sleep(30)"
                    ),
                ],
                cwd=Path(directory),
                timeout=1,
                env={**os.environ, "REVIEW_CRAFT_PROCESS_TEST": "streamed"},
            )

        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"streamed\n")


if __name__ == "__main__":
    unittest.main()
