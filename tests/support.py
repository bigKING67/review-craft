from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_LIB = ROOT / "skills/review-craft/lib"
RUNTIME_SCRIPT = ROOT / "skills/review-craft/scripts/review_craft.py"
sys.path.insert(0, str(RUNTIME_LIB))

from review_craft.constants import (  # noqa: E402
    ARTIFACT_PATHS,
    REMEDIATION_PHASES,
    SCORE_DIMENSIONS,
)
from review_craft.jsonio import read_json, write_json, write_jsonl  # noqa: E402


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(RUNTIME_SCRIPT), *args],
        cwd=cwd or ROOT,
        env=environment,
        text=True,
        capture_output=True,
    )


def git_init(path: Path, *, commit: bool = False) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.name", "Review Craft Tests"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "review-craft-tests@example.invalid"],
        cwd=path,
        check=True,
    )
    if commit:
        subprocess.run(["git", "add", "--", "app.py"], cwd=path, check=True)
        subprocess.run(
            ["git", "commit", "-m", "fixture"],
            cwd=path,
            check=True,
            stdout=subprocess.DEVNULL,
        )


def make_target(
    *, git: bool = True, commit: bool = False
) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temporary = tempfile.TemporaryDirectory(prefix="review-craft-target-")
    target = Path(temporary.name)
    (target / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
    if git:
        git_init(target, commit=commit)
    return temporary, target


def create_run(target: Path, output_root: Path) -> Path:
    completed = run_cli(
        "preflight",
        "--target",
        str(target),
        "--output-root",
        str(output_root),
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    payload = json.loads(completed.stdout)
    return Path(payload["runDir"])


def _deductions(maximum: int, awarded: int, evidence: str) -> list[dict[str, Any]]:
    if awarded == maximum:
        return []
    return [
        {
            "points": maximum - awarded,
            "reason": "Validated fixture deduction",
            "evidenceRefs": [evidence],
        }
    ]


def populate_valid_run(run_dir: Path) -> None:
    quality_model = read_json(run_dir / ARTIFACT_PATHS["qualityModel"])
    quality_model.update(
        {
            "purpose": "Provide a deterministic fixture application.",
            "audience": "Review Craft contract tests.",
            "criticalPaths": ["Call answer and receive the expected integer."],
            "invariants": ["answer remains deterministic."],
            "nonGoals": ["No network or multi-user behavior."],
            "compatibility": ["Python 3.10 or later."],
            "performanceBudgets": ["No material runtime budget for this fixture."],
            "reliabilityRequirements": ["The function must not raise."],
            "authoritySources": ["app.py and the fixture tests."],
            "assumptions": ["The fixture represents a small library."],
            "unknowns": [],
        }
    )
    write_json(run_dir / ARTIFACT_PATHS["qualityModel"], quality_model)

    coverage = read_json(run_dir / ARTIFACT_PATHS["coverage"])
    for row in coverage["files"]:
        row["disposition"] = "REVIEWED"
        row["reason"] = "Reviewed by the valid-run fixture."
        row["evidenceRefs"] = [f"source:{row['path']}"]
    coverage["summary"]["reviewed"] = len(coverage["files"])
    coverage["summary"]["deferred"] = 0
    write_json(run_dir / ARTIFACT_PATHS["coverage"], coverage)

    location = {"path": "app.py", "lineStart": 1, "lineEnd": 2, "role": "primary"}
    candidate = {
        "id": "RC-CORR-001",
        "category": "correctness",
        "type": "incorrect_result",
        "title": "Fixture returns an outdated answer",
        "locations": [location],
        "evidence": [
            {
                "kind": "source_trace",
                "ref": "source:app.py:1-2",
                "summary": "The function returns 41 instead of the fixture contract value.",
            }
        ],
        "claimedImpact": ["incorrect result"],
        "confidence": "HIGH",
        "validation": {
            "status": "CONFIRMED",
            "method": "Direct source trace and fixture assertion.",
            "evidenceRefs": ["source:app.py:1-2"],
            "remainingUncertainty": "",
        },
    }
    write_jsonl(run_dir / ARTIFACT_PATHS["candidateLedger"], [candidate])

    finding = {
        "id": "RC-FINDING-001",
        "candidateId": "RC-CORR-001",
        "title": "Fixture returns an outdated answer",
        "category": "correctness",
        "locations": [location],
        "evidenceRefs": ["source:app.py:1-2"],
        "rootCause": "The fixture constant was not updated with its behavioral contract.",
        "currentImpact": "The returned value is wrong for the documented fixture behavior.",
        "longTermRisk": "Dependent tests and examples can encode the wrong result.",
        "validationStatus": "CONFIRMED",
        "confidence": "HIGH",
        "severity": "MEDIUM",
        "priority": "P1",
        "recommendation": "Update the constant and add a focused regression assertion.",
        "decisionId": "RC-DECISION-001",
        "modificationCost": "LOW",
        "modificationRisk": "LOW",
        "verification": ["Call answer and assert the corrected value."],
    }
    write_json(
        run_dir / ARTIFACT_PATHS["findings"],
        {
            "documentType": "review-craft.findings",
            "schemaVersion": "review-craft.run.v2",
            "findings": [finding],
        },
    )
    decision = {
        "id": "RC-DECISION-001",
        "subject": "app.answer constant",
        "findingRefs": ["RC-FINDING-001"],
        "decision": "CLEAN_UP",
        "rationale": "A local correction is sufficient; no boundary change is justified.",
        "alternatives": ["A rewrite was rejected because the function is already minimal."],
        "migration": "",
        "compatibilityRisks": [],
        "rollback": "",
        "verification": ["Run the focused fixture assertion."],
    }
    keep = {
        "id": "RC-DECISION-KEEP-001",
        "subject": "single-function module boundary",
        "findingRefs": [],
        "decision": "KEEP",
        "rationale": "The small module has one stable responsibility and needs no abstraction.",
        "alternatives": [],
        "migration": "",
        "compatibilityRisks": [],
        "rollback": "",
        "verification": ["Confirm the module retains one public behavior."],
    }
    write_json(
        run_dir / ARTIFACT_PATHS["decisions"],
        {
            "documentType": "review-craft.decisions",
            "schemaVersion": "review-craft.run.v2",
            "decisions": [decision, keep],
        },
    )

    awarded_by_id = {
        "correctness": 18,
        "architecture": 18,
        "maintainability": 12,
        "performance": 12,
        "codeQuality": 8,
        "testing": 8,
        "dependenciesSecurity": 4,
        "repositoryExperience": 5,
    }
    dimensions = []
    for identifier, label, maximum in SCORE_DIMENSIONS:
        awarded = awarded_by_id[identifier]
        dimensions.append(
            {
                "id": identifier,
                "label": label,
                "maximum": maximum,
                "awarded": awarded,
                "deductions": _deductions(maximum, awarded, "RC-FINDING-001"),
            }
        )
    write_json(
        run_dir / ARTIFACT_PATHS["scorecard"],
        {
            "documentType": "review-craft.scorecard",
            "schemaVersion": "review-craft.run.v2",
            "status": "final",
            "evidenceLevel": "E2",
            "confidence": "HIGH",
            "coveragePercent": 100.0,
            "unresolvedCandidates": 0,
            "dimensions": dimensions,
            "total": 85,
        },
    )

    target_architecture = {
        "overview": "Keep the fixture as a minimal single-module library.",
        "moduleBoundaries": ["Keep answer in the domain module."],
        "dependencyDirection": ["Tests depend on the public function only."],
        "coreDataFlow": ["Caller -> answer -> integer result."],
        "stateAndErrors": ["The function remains stateless and deterministic."],
        "directoryStructure": ["Keep implementation and tests directly discoverable."],
        "testingStructure": ["Use a focused behavioral assertion."],
        "deliveryFlow": ["Run tests before packaging."],
    }
    phases = []
    for identifier, title in REMEDIATION_PHASES:
        phases.append(
            {
                "id": identifier,
                "title": title,
                "modificationScope": ["The fixture module and its focused test."],
                "prerequisites": ["Preserve the current source fingerprint as a baseline."],
                "expectedBenefits": ["Keep behavior explicit and regression protected."],
                "risks": ["A consumer may have encoded the outdated value."],
                "acceptanceCriteria": ["The focused assertion passes without source drift."],
            }
        )
    write_json(
        run_dir / ARTIFACT_PATHS["remediationPlan"],
        {
            "documentType": "review-craft.remediation-plan",
            "schemaVersion": "review-craft.run.v2",
            "changeClass": "LOCAL_OPTIMIZATION",
            "targetScore": 92,
            "targetEvidenceLevel": "E3",
            "targetArchitecture": target_architecture,
            "phases": phases,
        },
    )
