from __future__ import annotations

SCHEMA_VERSION = "review-craft.run.v1"
VERSION = "0.1.0"

ARTIFACT_PATHS = {
    "qualityModel": "quality-model.json",
    "coverage": "coverage.json",
    "candidateLedger": "candidate-ledger.jsonl",
    "findings": "findings.json",
    "decisions": "decisions.json",
    "scorecard": "scorecard.json",
    "remediationPlan": "remediation-plan.json",
    "commands": "evidence/commands.jsonl",
    "report": "report.md",
}

COVERAGE_DISPOSITIONS = {
    "PENDING",
    "REVIEWED",
    "COVERED_BY_PARENT",
    "GENERATED",
    "VENDORED",
    "BINARY",
    "OUT_OF_SCOPE",
    "UNREADABLE",
    "DEFERRED",
}
FINAL_COVERAGE_DISPOSITIONS = COVERAGE_DISPOSITIONS - {"PENDING"}

VALIDATION_STATUSES = {
    "PENDING",
    "CONFIRMED",
    "LIKELY",
    "NEEDS_MEASUREMENT",
    "BLOCKED",
    "REJECTED",
    "NOT_APPLICABLE",
}

DECISIONS = {
    "KEEP",
    "CLEAN_UP",
    "MERGE",
    "REPLACE",
    "REWRITE",
    "DELETE",
    "DEFER",
    "MEASURE",
    "DOCUMENT",
}

SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
PRIORITIES = {"P0", "P1", "P2", "P3"}
EVIDENCE_LEVELS = {"E0": 0, "E1": 1, "E2": 2, "E3": 3, "E4": 4}
PERFORMANCE_CLASSES = {
    "MEASURED_REGRESSION",
    "ALGORITHMIC_RISK",
    "LIKELY_HOT_PATH",
    "UNVERIFIED_SUSPICION",
}

SCORE_DIMENSIONS = (
    ("correctness", "功能正确性与稳定性", 20),
    ("architecture", "架构与模块设计", 20),
    ("maintainability", "长期可维护性", 15),
    ("performance", "性能与资源效率", 15),
    ("codeQuality", "代码质量与一致性", 10),
    ("testing", "测试与工程体系", 10),
    ("dependenciesSecurity", "依赖与安全治理", 5),
    ("repositoryExperience", "目录结构、文档与开发体验", 5),
)

REMEDIATION_PHASES = (
    ("phase-0", "Phase 0：建立基准、补充测试与避免回归"),
    ("phase-1", "Phase 1：低风险高收益清理"),
    ("phase-2", "Phase 2：核心模块重构"),
    ("phase-3", "Phase 3：性能与稳定性优化"),
    ("phase-4", "Phase 4：目录、文档、依赖和工程治理"),
)

DEFAULT_EXCLUDES = (
    ".git/**",
    ".review-craft/**",
    ".venv/**",
    "**/__pycache__/**",
    ".pytest_cache/**",
    ".ruff_cache/**",
    "node_modules/**",
    "dist/**",
    "build/**",
    "coverage/**",
    "review-craft-runs/**",
)
