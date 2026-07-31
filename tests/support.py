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
    SCHEMA_VERSION,
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
            "reason": "已验证的夹具扣分项",
            "evidenceRefs": [evidence],
        }
    ]


def populate_valid_run(run_dir: Path) -> None:
    quality_model = read_json(run_dir / ARTIFACT_PATHS["qualityModel"])
    quality_model.update(
        {
            "purpose": "提供一个确定性的夹具应用。",
            "audience": "Review Craft 契约测试。",
            "criticalPaths": ["调用 answer 并获得预期整数。"],
            "invariants": ["answer 始终保持确定性。"],
            "nonGoals": ["不包含网络或多用户行为。"],
            "compatibility": ["兼容 Python 3.10 及以上版本。"],
            "performanceBudgets": ["该夹具没有实质性的运行时预算。"],
            "reliabilityRequirements": ["函数不得抛出异常。"],
            "authoritySources": ["app.py 与夹具测试。"],
            "assumptions": ["该夹具代表一个小型库。"],
            "unknowns": [],
        }
    )
    write_json(run_dir / ARTIFACT_PATHS["qualityModel"], quality_model)

    coverage = read_json(run_dir / ARTIFACT_PATHS["coverage"])
    for row in coverage["files"]:
        row["disposition"] = "REVIEWED"
        row["reason"] = "已由有效运行夹具完成审查。"
        row["evidenceRefs"] = [f"source:{row['path']}"]
    coverage["summary"]["reviewed"] = len(coverage["files"])
    coverage["summary"]["deferred"] = 0
    write_json(run_dir / ARTIFACT_PATHS["coverage"], coverage)

    location = {"path": "app.py", "lineStart": 1, "lineEnd": 2, "role": "primary"}
    candidate = {
        "id": "RC-CORR-001",
        "category": "correctness",
        "type": "incorrect_result",
        "title": "夹具返回了过期结果",
        "locations": [location],
        "evidence": [
            {
                "kind": "source_trace",
                "ref": "source:app.py:1-2",
                "summary": "函数返回 41，而不是夹具契约要求的值。",
            }
        ],
        "claimedImpact": ["返回结果错误"],
        "confidence": "HIGH",
        "validation": {
            "status": "CONFIRMED",
            "method": "直接源码追踪与夹具断言。",
            "evidenceRefs": ["source:app.py:1-2"],
            "remainingUncertainty": "",
        },
    }
    write_jsonl(run_dir / ARTIFACT_PATHS["candidateLedger"], [candidate])

    finding = {
        "id": "RC-FINDING-001",
        "candidateId": "RC-CORR-001",
        "title": "夹具返回了过期结果",
        "category": "correctness",
        "locations": [location],
        "evidenceRefs": ["source:app.py:1-2"],
        "rootCause": "夹具常量没有随行为契约一起更新。",
        "currentImpact": "返回值不符合夹具记录的行为。",
        "longTermRisk": "依赖它的测试和示例可能继续固化错误结果。",
        "validationStatus": "CONFIRMED",
        "confidence": "HIGH",
        "severity": "MEDIUM",
        "priority": "P1",
        "recommendation": "更新常量，并补充聚焦的回归断言。",
        "decisionId": "RC-DECISION-001",
        "modificationCost": "LOW",
        "modificationRisk": "LOW",
        "verification": ["调用 answer 并断言修正后的值。"],
    }
    write_json(
        run_dir / ARTIFACT_PATHS["findings"],
        {
            "documentType": "review-craft.findings",
            "schemaVersion": SCHEMA_VERSION,
            "findings": [finding],
        },
    )
    decision = {
        "id": "RC-DECISION-001",
        "subject": "app.answer 常量",
        "findingRefs": ["RC-FINDING-001"],
        "decision": "CLEAN_UP",
        "rationale": "局部修正已经足够，没有理由改变模块边界。",
        "alternatives": ["该函数已经足够精简，因此不采用重写。"],
        "migration": "",
        "compatibilityRisks": [],
        "rollback": "",
        "verification": ["运行聚焦的夹具断言。"],
    }
    keep = {
        "id": "RC-DECISION-KEEP-001",
        "subject": "单函数模块边界",
        "findingRefs": [],
        "decision": "KEEP",
        "rationale": "该小模块只有一个稳定职责，不需要增加抽象。",
        "alternatives": [],
        "migration": "",
        "compatibilityRisks": [],
        "rollback": "",
        "verification": ["确认模块继续只保留一个公开行为。"],
    }
    write_json(
        run_dir / ARTIFACT_PATHS["decisions"],
        {
            "documentType": "review-craft.decisions",
            "schemaVersion": SCHEMA_VERSION,
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
            "schemaVersion": SCHEMA_VERSION,
            "status": "final",
            "evidenceLevel": "E1",
            "confidence": "HIGH",
            "coveragePercent": 100.0,
            "accountedPercent": 100.0,
            "reviewedPercent": 100.0,
            "unresolvedCandidates": 0,
            "dimensions": dimensions,
            "total": 85,
        },
    )

    target_architecture = {
        "overview": "将夹具保留为最小单模块库。",
        "moduleBoundaries": ["answer 继续归属领域模块。"],
        "dependencyDirection": ["测试只依赖公开函数。"],
        "coreDataFlow": ["调用方 -> answer -> 整数结果。"],
        "stateAndErrors": ["函数保持无状态和确定性。"],
        "directoryStructure": ["让实现和测试保持可直接发现。"],
        "testingStructure": ["使用聚焦的行为断言。"],
        "deliveryFlow": ["打包前运行测试。"],
    }
    phases = []
    for identifier, title in REMEDIATION_PHASES:
        phases.append(
            {
                "id": identifier,
                "title": title,
                "modificationScope": ["夹具模块及其聚焦测试。"],
                "prerequisites": ["保留当前源码指纹作为基线。"],
                "expectedBenefits": ["让行为保持明确并获得回归保护。"],
                "risks": ["某个调用方可能已经固化过期值。"],
                "acceptanceCriteria": ["聚焦断言通过，且源码没有发生额外漂移。"],
            }
        )
    write_json(
        run_dir / ARTIFACT_PATHS["remediationPlan"],
        {
            "documentType": "review-craft.remediation-plan",
            "schemaVersion": SCHEMA_VERSION,
            "changeClass": "LOCAL_OPTIMIZATION",
            "targetScore": 92,
            "targetEvidenceLevel": "E3",
            "targetArchitecture": target_architecture,
            "phases": phases,
        },
    )
