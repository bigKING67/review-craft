from __future__ import annotations

import json
import subprocess
import sys
import unittest

from tests.support import ROOT


class ComplexityBudgetTests(unittest.TestCase):
    def test_complexity_budget_is_enforced_for_core_functions(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/complexity_budget.py"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["valid"])
        self.assertGreaterEqual(len(payload["functions"]), 9)


if __name__ == "__main__":
    unittest.main()
