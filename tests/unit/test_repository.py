from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import RUNTIME_LIB

sys.path.insert(0, str(RUNTIME_LIB))

from review_craft.repository import fingerprint_inventory, inventory, safe_remote
from review_craft.repository_analysis import (
    build_dependency_map,
    build_module_map,
    detect_profile,
)


class RepositoryTests(unittest.TestCase):
    def test_safe_remote_strips_https_userinfo(self) -> None:
        self.assertEqual(
            safe_remote("https://user:secret@example.invalid/owner/repo.git"),
            "https://example.invalid/owner/repo.git",
        )

    def test_inventory_is_sorted_and_excludes_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "z.py").write_text("z = 1\n", encoding="utf-8")
            (root / "a.py").write_text("a = 1\n", encoding="utf-8")
            (root / "dist").mkdir()
            (root / "dist/out.js").write_text("generated\n", encoding="utf-8")
            records, excluded = inventory(root)
            self.assertEqual([row["path"] for row in records], ["a.py", "z.py"])
            self.assertEqual([row["path"] for row in excluded], ["dist/out.js"])

    def test_inventory_records_symlink_without_following_it(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            target = Path(outside) / "secret.txt"
            target.write_text("outside\n", encoding="utf-8")
            try:
                (root / "link").symlink_to(target)
            except OSError as error:
                self.skipTest(f"symlink creation unavailable: {error}")
            records, _ = inventory(root)
            self.assertEqual(records[0]["kind"], "symlink")
            self.assertEqual(records[0]["linkTarget"], os.readlink(root / "link"))

    def test_fingerprint_ignores_input_order(self) -> None:
        rows = [
            {"path": "b", "kind": "file", "sha256": "b" * 64, "classification": "source"},
            {"path": "a", "kind": "file", "sha256": "a" * 64, "classification": "source"},
        ]
        self.assertEqual(fingerprint_inventory(rows), fingerprint_inventory(list(reversed(rows))))

    def test_repository_maps_capture_local_python_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src/a.py").write_text("from . import b\n", encoding="utf-8")
            (root / "src/b.py").write_text("VALUE = 1\n", encoding="utf-8")
            records, _ = inventory(root)
            module_map = build_module_map(records)
            dependency_map = build_dependency_map(root, records)
            self.assertEqual(module_map["modules"][0]["fileCount"], 2)
            self.assertEqual(
                dependency_map["edges"],
                [
                    {
                        "from": "src/a.py",
                        "to": "src/b.py",
                        "kind": "python-import",
                        "line": 1,
                    }
                ],
            )

    def test_auto_profile_uses_repository_signals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(
                '{"dependencies":{"react":"1"}}\n', encoding="utf-8"
            )
            (root / "index.html").write_text("<main></main>\n", encoding="utf-8")
            records, _ = inventory(root)
            profile = detect_profile(root, records, "auto")
            self.assertEqual(profile["resolved"], "frontend")
            self.assertEqual(profile["confidence"], "MEDIUM")


if __name__ == "__main__":
    unittest.main()
