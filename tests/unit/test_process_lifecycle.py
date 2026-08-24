from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_LIB = ROOT / "skills/review-craft/lib"
sys.path.insert(0, str(RUNTIME_LIB))

from review_craft import process_lifecycle  # noqa: E402
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
        self.assertEqual(result.stdout.replace(b"\r\n", b"\n"), b"streamed\n")
        self.assertEqual(result.process_tree_cleanup, "CONFIRMED")

    @unittest.skipIf(os.name == "nt", "POSIX process-group assertion")
    def test_timeout_terminates_spawned_child_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child_pid_path = root / "child.pid"
            result = run_process(
                [
                    sys.executable,
                    "-c",
                    (
                        "import pathlib, subprocess, sys, time; "
                        "child=subprocess.Popen([sys.executable, '-c', "
                        "'import time; time.sleep(30)']); "
                        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid)); "
                        "print('ready', flush=True); time.sleep(30)"
                    ),
                ],
                cwd=root,
                timeout=1,
            )
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))

            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.02)
            else:
                self.fail("spawned child survived process-tree cleanup")

        self.assertTrue(result.timed_out)
        self.assertEqual(result.process_tree_cleanup, "CONFIRMED")

    def test_keyboard_interrupt_cleans_process_tree_and_reraises(self) -> None:
        class InterruptedProcess:
            def communicate(self, timeout: int | None = None) -> tuple[bytes, bytes]:
                raise KeyboardInterrupt

        process = InterruptedProcess()
        with (
            patch.object(
                process_lifecycle,
                "open_process_tree",
                return_value=process,
            ),
            patch.object(
                process_lifecycle,
                "terminate_process_tree",
                return_value="CONFIRMED",
            ) as terminate,
            self.assertRaises(KeyboardInterrupt),
        ):
            run_process(["fixture"], cwd=Path.cwd(), timeout=1)

        terminate.assert_called_once_with(process)


if __name__ == "__main__":
    unittest.main()
