from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import RUNTIME_LIB

sys.path.insert(0, str(RUNTIME_LIB))

from review_craft.repository import inventory
from review_craft.repository_analysis import build_dependency_map


class DependencyEvidenceTests(unittest.TestCase):
    def _map(self, root: Path) -> dict:
        records, _ = inventory(root)
        return build_dependency_map(root, records)

    def test_unrelated_declarations_preserve_import_facts_after_line_rebinding(self) -> None:
        variants = (
            "import service\n",
            "class Other:\n    def service(self):\n        return 0\nimport service\n",
            "def unrelated():\n    service = 0\n    return service\nimport service\n",
            "\n\nimport service\nclass Renamed:\n    pass\n",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text("VALUE = 42\n", encoding="utf-8")
            for source in variants:
                with self.subTest(source=source):
                    (root / "app.py").write_text(source, encoding="utf-8")
                    result = self._map(root)
                    self.assertEqual(result["filesSkipped"], [])
                    self.assertEqual(result["edges"], [{
                        "from": "app.py", "to": "service.py", "kind": "python-import",
                        "line": source.splitlines().index("import service") + 1,
                    }])

    def test_parse_gap_does_not_erase_independent_consumer_or_masquerade_as_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text("VALUE = 42\n", encoding="utf-8")
            (root / "app.py").write_text("import service\n", encoding="utf-8")
            broken = root / "other.py"
            for source, skipped in (("def broken(:\n", True), ("VALUE = 0\n", False)):
                with self.subTest(skipped=skipped):
                    broken.write_text(source, encoding="utf-8")
                    result = self._map(root)
                    self.assertEqual(len(result["edges"]), 1)
                    self.assertEqual(result["edges"][0]["from"], "app.py")
                    self.assertEqual(result["filesAnalyzed"], 2 if skipped else 3)
                    self.assertEqual(result["filesSkipped"], [
                        {"path": "other.py", "reason": "Python syntax error"}
                    ] if skipped else [])

    def test_empty_static_edges_do_not_prove_absence_of_runtime_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text("VALUE = 42\n", encoding="utf-8")
            app = root / "app.py"
            app.write_text('print(__import__("service").VALUE)\n', encoding="utf-8")
            result = self._map(root)
            self.assertEqual(result["edges"], [])
            self.assertEqual(result["filesSkipped"], [])
            # Execute only this authored fixture, never arbitrary review-target source.
            execution = subprocess.run(
                [sys.executable, "-B", str(app)], cwd=root, capture_output=True,
                text=True, check=False, timeout=10,
            )
            self.assertEqual(execution.returncode, 0, execution.stderr)
            self.assertEqual(execution.stdout.strip(), "42")
            app.write_text("print(0)\n", encoding="utf-8")
            self.assertEqual(self._map(root)["edges"], result["edges"])
            execution = subprocess.run(
                [sys.executable, "-B", str(app)], cwd=root, capture_output=True,
                text=True, check=False, timeout=10,
            )
            self.assertEqual(execution.returncode, 0, execution.stderr)
            self.assertEqual(execution.stdout.strip(), "0")
