#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
from contextlib import suppress
from pathlib import Path
from types import ModuleType
from typing import Any

CLAIMS = {
    "failure-truthfulness-positive": (
        ("durable-failure-visible", "DEFECT"),
        ("successful-write-preserved", "PRESERVATION"),
    ),
    "failure-truthfulness-negative": (
        ("durable-failure-visible", "DEFECT"),
        ("best-effort-telemetry-preserved", "PRESERVATION"),
    ),
    "retry-idempotency-positive": (
        ("stable-retry-identity", "DEFECT"),
        ("timeout-recovery-preserved", "PRESERVATION"),
    ),
    "retry-idempotency-negative": (
        ("stable-retry-identity", "DEFECT"),
        ("timeout-recovery-preserved", "PRESERVATION"),
    ),
    "ack-order-positive": (
        ("no-ack-before-persistence", "DEFECT"),
        ("successful-order-preserved", "PRESERVATION"),
    ),
    "ack-order-negative": (
        ("no-ack-before-persistence", "DEFECT"),
        ("successful-order-preserved", "PRESERVATION"),
    ),
    "bounded-saturating-add-positive": (
        ("exhaustive-saturation", "DEFECT"),
        ("branchless-contract", "PRESERVATION"),
    ),
    "bounded-saturating-add-negative": (
        ("exhaustive-saturation", "DEFECT"),
        ("branchless-contract", "PRESERVATION"),
    ),
}


def load_module(target: Path, filename: str) -> ModuleType:
    path = target / filename
    spec = importlib.util.spec_from_file_location(f"remediation_{target.name}_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def claim(claim_id: str, role: str, passed: bool, observation: Any) -> dict[str, Any]:
    return {
        "id": claim_id,
        "role": role,
        "status": "PASS" if passed else "FAIL",
        "observation": observation,
    }


def failure_truthfulness(target: Path, *, negative: bool) -> list[dict[str, Any]]:
    service = load_module(target, "service.py")

    class Store:
        def __init__(self, fail: bool) -> None:
            self.fail = fail
            self.saved: list[dict[str, Any]] = []

        def insert(self, payload: dict[str, Any]) -> str:
            if self.fail:
                raise OSError("durable write failed")
            self.saved.append(payload)
            return "order-1"

    class Metrics:
        def emit(self, name: str, order_id: str) -> None:
            raise OSError("telemetry unavailable")

    failure_result: Any = None
    failure_error: str | None = None
    try:
        failure_result = (
            service.create_order(Store(True), Metrics(), {"sku": "A"})
            if negative
            else service.create_order(Store(True), {"sku": "A"})
        )
    except Exception as error:  # The oracle records caller-visible failure semantics.
        failure_error = type(error).__name__
    failure_visible = failure_error is not None or not (
        isinstance(failure_result, dict) and failure_result.get("ok") is True
    )

    success_store = Store(False)
    success_result = (
        service.create_order(success_store, Metrics(), {"sku": "A"})
        if negative
        else service.create_order(success_store, {"sku": "A"})
    )
    preservation_id = (
        "best-effort-telemetry-preserved" if negative else "successful-write-preserved"
    )
    return [
        claim(
            "durable-failure-visible",
            "DEFECT",
            failure_visible,
            {"returned": failure_result, "error": failure_error},
        ),
        claim(
            preservation_id,
            "PRESERVATION",
            success_result == {"ok": True, "order_id": "order-1"}
            and len(success_store.saved) == 1,
            {"returned": success_result, "durableRecordCount": len(success_store.saved)},
        ),
    ]


def retry_idempotency(target: Path) -> list[dict[str, Any]]:
    payments = load_module(target, "payments.py")

    class Gateway:
        def __init__(self) -> None:
            self.keys: list[str] = []
            self.charges: dict[str, int] = {}
            self.first = True

        def charge(self, amount: int, *, idempotency_key: str) -> dict[str, int]:
            self.keys.append(idempotency_key)
            self.charges.setdefault(idempotency_key, amount)
            if self.first:
                self.first = False
                raise TimeoutError("response lost after commit")
            return {"charged": self.charges[idempotency_key]}

    gateway = Gateway()
    result = payments.charge_with_retry(
        gateway,
        {"request_id": "request-7", "amount": 25},
    )
    stable = len(set(gateway.keys)) == 1 and len(gateway.charges) == 1
    recovered = result == {"charged": 25} and len(gateway.keys) == 2
    observation = {
        "attemptCount": len(gateway.keys),
        "distinctKeyCount": len(set(gateway.keys)),
        "chargeCount": len(gateway.charges),
        "returned": result,
    }
    return [
        claim("stable-retry-identity", "DEFECT", stable, observation),
        claim("timeout-recovery-preserved", "PRESERVATION", recovered, observation),
    ]


def ack_order(target: Path) -> list[dict[str, Any]]:
    jobs = load_module(target, "jobs.py")
    failed_events: list[str] = []

    class FailingStore:
        def save(self, job: dict[str, str]) -> None:
            failed_events.append("save-attempt")
            raise OSError("disk unavailable")

    class FailedQueue:
        def acknowledge(self, job_id: str) -> None:
            failed_events.append("acknowledge")

    with suppress(OSError):
        jobs.submit_job(FailingStore(), FailedQueue(), {"id": "job-1"})

    success_events: list[str] = []

    class Store:
        def save(self, job: dict[str, str]) -> None:
            success_events.append("save")

    class Queue:
        def acknowledge(self, job_id: str) -> None:
            success_events.append("acknowledge")

    returned = jobs.submit_job(Store(), Queue(), {"id": "job-1"})
    return [
        claim(
            "no-ack-before-persistence",
            "DEFECT",
            "acknowledge" not in failed_events,
            {"failedEvents": failed_events},
        ),
        claim(
            "successful-order-preserved",
            "PRESERVATION",
            sorted(success_events) == ["acknowledge", "save"] and returned == "job-1",
            {"successEvents": success_events, "returned": returned},
        ),
    ]


def bounded_saturating_add(target: Path) -> list[dict[str, Any]]:
    module = load_module(target, "bounded_add.py")
    mismatches = []
    for left in range(256):
        for right in range(256):
            actual = module.saturating_add(left, right)
            expected = min(255, left + right)
            if actual != expected and len(mismatches) < 8:
                mismatches.append(
                    {"left": left, "right": right, "expected": expected, "actual": actual}
                )

    tree = ast.parse((target / "bounded_add.py").read_text(encoding="utf-8"))
    forbidden_nodes = [
        type(node).__name__
        for node in ast.walk(tree)
        if isinstance(node, (ast.If, ast.IfExp))
        or (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"min", "max"}
        )
    ]
    return [
        claim(
            "exhaustive-saturation",
            "DEFECT",
            not mismatches,
            {"inputPairCount": 65536, "sampleMismatches": mismatches},
        ),
        claim(
            "branchless-contract",
            "PRESERVATION",
            not forbidden_nodes,
            {"forbiddenNodes": forbidden_nodes},
        ),
    ]


def observe(case_id: str, target: Path) -> list[dict[str, Any]]:
    if case_id == "failure-truthfulness-positive":
        return failure_truthfulness(target, negative=False)
    if case_id == "failure-truthfulness-negative":
        return failure_truthfulness(target, negative=True)
    if case_id.startswith("retry-idempotency-"):
        return retry_idempotency(target)
    if case_id.startswith("ack-order-"):
        return ack_order(target)
    if case_id.startswith("bounded-saturating-add-"):
        return bounded_saturating_add(target)
    raise ValueError(f"unsupported remediation case: {case_id}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    if args.case not in CLAIMS:
        raise ValueError(f"unsupported remediation case: {args.case}")
    try:
        rows = observe(args.case, Path(args.target).resolve(strict=True))
    except Exception as error:
        # A model may leave the staged fixture unimportable or behaviorally explosive.
        # That is a code-quality outcome, not an oracle-process infrastructure failure.
        rows = [
            claim(
                claim_id,
                role,
                False,
                {"sourceEvaluationError": type(error).__name__},
            )
            for claim_id, role in CLAIMS[args.case]
        ]
    payload = {
        "schema": "review-craft.eval-remediation-oracle.v1",
        "caseId": args.case,
        "status": "PASS" if all(row["status"] == "PASS" for row in rows) else "FAIL",
        "claims": rows,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
