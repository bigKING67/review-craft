#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "skills" / "review-craft" / "lib"
sys.path.insert(0, str(RUNTIME))

from review_craft import __version__  # noqa: E402
from review_craft.constants import VERSION as CONSTANT_VERSION  # noqa: E402

REQUIRED_FILES = (
    ".codex-plugin/plugin.json",
    ".github/workflows/validate.yml",
    ".gitignore",
    "AGENTS.md",
    "benchmarks/schemas/runtime-result.schema.json",
    "benchmarks/specs/runtime.json",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
    "VERSION",
    "contracts/evidence-policy.json",
    "contracts/package-boundary.json",
    "contracts/release-policy.json",
    "package.json",
    "pyproject.toml",
    "skills/review-craft/SKILL.md",
    "skills/review-craft/VERSION",
    "skills/review-craft/agents/openai.yaml",
    "skills/review-craft/lib/review_craft/configuration.py",
    "skills/review-craft/lib/review_craft/evidence.py",
    "skills/review-craft/lib/review_craft/locking.py",
    "skills/review-craft/lib/review_craft/remediation.py",
    "skills/review-craft/lib/review_craft/remediation_contract.py",
    "skills/review-craft/lib/review_craft/remediation_validation.py",
    "skills/review-craft/lib/review_craft/repository_analysis.py",
    "skills/review-craft/references/modes-and-profiles.md",
    "skills/review-craft/references/remediation.md",
    "skills/review-craft/schemas/dependency-map.schema.json",
    "skills/review-craft/schemas/fix-assessment.schema.json",
    "skills/review-craft/schemas/fix-plan.schema.json",
    "skills/review-craft/schemas/fix-verification.schema.json",
    "skills/review-craft/schemas/module-map.schema.json",
    "skills/review-craft/schemas/review-scope.schema.json",
    "skills/review-craft/scripts/review_craft.py",
    "evals/prompts/ordinary-review.md",
    "evals/prompts/review-craft.md",
    "evals/schemas/eval-adapter.schema.json",
    "evals/schemas/eval-adjudication-result.schema.json",
    "evals/schemas/eval-adjudication.schema.json",
    "evals/schemas/eval-cases.schema.json",
    "evals/schemas/eval-comparison.schema.json",
    "evals/schemas/eval-golden-snapshot.schema.json",
    "evals/schemas/eval-host-output.schema.json",
    "evals/schemas/eval-run.schema.json",
    "evals/schemas/eval-usage.schema.json",
    "evals/golden-results/705dbac-gpt-5.6-sol/README.md",
    "evals/golden-results/705dbac-gpt-5.6-sol/snapshot.json",
    "scripts/codex_eval_adapter.py",
    "scripts/benchmark_runtime.py",
    "scripts/eval_contracts.py",
    "scripts/run_evals.py",
)

FORBIDDEN_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "node_modules"}
TEXT_SUFFIXES = {".md", ".py", ".json", ".toml", ".yaml", ".yml", ".txt"}
ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    re.compile(r"/home/[A-Za-z0-9._-]+/"),
    re.compile(r"[A-Za-z]:\\\\Users\\\\[^\\\\]+\\\\"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the Review Craft source tree")
    parser.add_argument("--schemas-only", action="store_true")
    return parser.parse_args()


def source_files() -> list[Path]:
    if (ROOT / ".git").exists():
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(ROOT),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            check=True,
            capture_output=True,
        )
        relative_paths = (
            item.decode("utf-8", errors="surrogateescape")
            for item in completed.stdout.split(b"\0")
            if item
        )
        return sorted(path for item in relative_paths if (path := ROOT / item).is_file())
    return sorted(
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and not any(part in FORBIDDEN_PARTS or part == ".venv" for part in path.parts)
    )


def json_files() -> list[Path]:
    return [path for path in source_files() if path.suffix == ".json"]


def validate_schemas(errors: list[str]) -> None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        errors.append("jsonschema is required for schema validation; run `uv sync --group dev`")
        return
    schemas: dict[str, dict] = {}
    schema_roots = (
        ROOT / "skills" / "review-craft" / "schemas",
        ROOT / "evals" / "schemas",
        ROOT / "benchmarks" / "schemas",
    )
    schema_paths = sorted(path for root in schema_roots for path in root.glob("*.schema.json"))
    for path in schema_paths:
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            schemas[path.name] = schema
        except (json.JSONDecodeError, ValueError) as error:
            errors.append(f"{path.relative_to(ROOT)}: invalid schema: {error}")
    config_path = ROOT / ".review-craft.example.json"
    if "config.schema.json" in schemas and config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        schema_errors = sorted(
            Draft202012Validator(schemas["config.schema.json"]).iter_errors(config),
            key=lambda item: list(item.path),
        )
        for error in schema_errors:
            location = ".".join(str(part) for part in error.path) or "<root>"
            errors.append(f".review-craft.example.json:{location}: {error.message}")
    cases_path = ROOT / "evals/specs/cases.json"
    cases_schema_path = ROOT / "evals/schemas/eval-cases.schema.json"
    if cases_path.exists() and cases_schema_path.exists():
        cases = json.loads(cases_path.read_text(encoding="utf-8"))
        schema = schemas.get("eval-cases.schema.json")
        if schema is not None:
            case_errors = sorted(
                Draft202012Validator(schema).iter_errors(cases),
                key=lambda item: list(item.path),
            )
            for error in case_errors:
                location = ".".join(str(part) for part in error.path) or "<root>"
                errors.append(f"evals/specs/cases.json:{location}: {error.message}")
    from eval_contracts import validate_golden_snapshot

    golden_root = ROOT / "evals/golden-results"
    for snapshot_path in sorted(golden_root.glob("*/snapshot.json")):
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for error in validate_golden_snapshot(snapshot):
            errors.append(f"{snapshot_path.relative_to(ROOT)}:{error}")


def validate_required_files(errors: list[str]) -> None:
    for relative in REQUIRED_FILES:
        if not (ROOT / relative).is_file():
            errors.append(f"missing required file: {relative}")


def validate_versions(errors: list[str]) -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    pyproject_version = re.search(r'^version = "([0-9]+\.[0-9]+\.[0-9]+)"$', pyproject, re.M)
    values = {
        "VERSION": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
        "skill VERSION": (ROOT / "skills/review-craft/VERSION").read_text(
            encoding="utf-8"
        ).strip(),
        "package.json": json.loads((ROOT / "package.json").read_text(encoding="utf-8"))[
            "version"
        ],
        "plugin.json": json.loads(
            (ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )["version"],
        "pyproject.toml": pyproject_version.group(1) if pyproject_version else None,
        "runtime": __version__,
        "runtime constants": CONSTANT_VERSION,
    }
    if len(set(values.values())) != 1:
        errors.append(f"version mismatch: {values}")
    version = next(iter(values.values()))
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        errors.append(f"version is not strict semver: {version}")


def validate_skill(errors: list[str]) -> None:
    path = ROOT / "skills/review-craft/SKILL.md"
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        errors.append("SKILL.md: invalid YAML frontmatter boundary")
        return
    frontmatter = text.split("---\n", 2)[1]
    keys = [line.split(":", 1)[0] for line in frontmatter.splitlines() if ":" in line]
    if keys != ["name", "description"]:
        errors.append(f"SKILL.md: frontmatter must contain only name and description, got {keys}")
    if "name: review-craft" not in frontmatter:
        errors.append("SKILL.md: canonical name is missing")
    if "[TODO" in text or "[TODO" in (ROOT / ".codex-plugin/plugin.json").read_text():
        errors.append("skill or plugin contains a TODO placeholder")
    if len(text.splitlines()) > 500:
        errors.append("SKILL.md exceeds the 500-line progressive-disclosure budget")


def validate_tree(errors: list[str]) -> None:
    for path in source_files():
        relative = path.relative_to(ROOT)
        if any(part in FORBIDDEN_PARTS for part in relative.parts):
            errors.append(f"forbidden generated path: {relative}")
        if path.suffix not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.name != "validate.py":
            for pattern in ABSOLUTE_PATH_PATTERNS:
                if pattern.search(text):
                    errors.append(f"absolute user path leaked into {relative}")
            if "BEGIN OPENSSH PRIVATE KEY" in text or "BEGIN RSA PRIVATE KEY" in text:
                errors.append(f"private key material detected in {relative}")


def validate_json_syntax(errors: list[str]) -> None:
    for path in json_files():
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            errors.append(f"{path.relative_to(ROOT)}: invalid JSON: {error}")


def main() -> int:
    args = parse_args()
    errors: list[str] = []
    validate_json_syntax(errors)
    validate_schemas(errors)
    if not args.schemas_only:
        validate_required_files(errors)
        validate_versions(errors)
        validate_skill(errors)
        validate_tree(errors)
    if errors:
        print("review-craft validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("review-craft validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
