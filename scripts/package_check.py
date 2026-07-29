#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    contract = json.loads(
        (ROOT / "contracts/package-boundary.json").read_text(encoding="utf-8")
    )
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="review-craft-package-") as directory:
        completed = subprocess.run(
            [
                "npm",
                "pack",
                "--ignore-scripts",
                "--pack-destination",
                directory,
                "--json",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            print(completed.stderr, file=sys.stderr)
            return completed.returncode
        payload = json.loads(completed.stdout)
        tarball = Path(directory) / payload[0]["filename"]
        with tarfile.open(tarball, "r:gz") as archive:
            names = sorted(member.name for member in archive.getmembers() if member.isfile())
        install_root = Path(directory) / "installed"
        installed = subprocess.run(
            [
                "npm",
                "install",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                "--prefix",
                str(install_root),
                str(tarball),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if installed.returncode != 0:
            errors.append(
                "packed artifact installation failed: "
                + (installed.stderr.strip() or installed.stdout.strip() or "no output")
            )
        else:
            package_root = install_root / "node_modules/@bigking67/review-craft"
            doctor = subprocess.run(
                [
                    sys.executable,
                    str(package_root / "skills/review-craft/scripts/review_craft.py"),
                    "doctor",
                    "--json",
                ],
                cwd=package_root,
                capture_output=True,
                text=True,
            )
            if doctor.returncode != 0:
                errors.append(
                    "packaged runtime doctor failed: "
                    + (doctor.stderr.strip() or doctor.stdout.strip() or "no output")
                )
            else:
                try:
                    doctor_payload = json.loads(doctor.stdout)
                except json.JSONDecodeError as error:
                    errors.append(f"packaged runtime doctor returned invalid JSON: {error}")
                else:
                    if doctor_payload.get("ready") is not True:
                        errors.append("packaged runtime doctor did not report ready=true")
                    if doctor_payload.get("version") != payload[0]["version"]:
                        errors.append("packaged runtime version does not match package version")
    allowed_roots = set(contract["allowedRoots"])
    for name in names:
        relative = PurePosixPath(name).relative_to("package")
        allowed = any(
            relative == PurePosixPath(root) or PurePosixPath(root) in relative.parents
            for root in allowed_roots
        )
        if not allowed:
            errors.append(f"unexpected packaged path: {name}")
        for segment in contract["forbiddenSegments"]:
            if segment in name:
                errors.append(f"forbidden packaged segment {segment!r}: {name}")
    missing = sorted(set(contract["requiredPackageFiles"]) - set(names))
    errors.extend(f"missing packaged file: {name}" for name in missing)
    if errors:
        print("review-craft package check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"review-craft package check passed ({len(names)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
