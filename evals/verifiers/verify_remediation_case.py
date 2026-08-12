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
    "partial-retry-idempotency-positive": (
        ("single-receipt-per-request", "DEFECT"),
        ("response-lost-delivery-recovery", "PRESERVATION"),
    ),
    "partial-retry-idempotency-negative": (
        ("single-receipt-per-request", "DEFECT"),
        ("response-lost-delivery-recovery", "PRESERVATION"),
    ),
    "persist-before-ack-positive": (
        ("failed-persistence-remains-retryable", "DEFECT"),
        ("created-and-duplicate-flow", "PRESERVATION"),
    ),
    "persist-before-ack-negative": (
        ("failed-persistence-remains-retryable", "DEFECT"),
        ("created-and-duplicate-flow", "PRESERVATION"),
    ),
    "stable-operation-fresh-lease-positive": (
        ("stable-operation-identity", "DEFECT"),
        ("fresh-lease-per-attempt", "PRESERVATION"),
        ("timeout-recovery-preserved", "PRESERVATION"),
    ),
    "stable-operation-fresh-lease-negative": (
        ("stable-operation-identity", "DEFECT"),
        ("fresh-lease-per-attempt", "PRESERVATION"),
        ("timeout-recovery-preserved", "PRESERVATION"),
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


def partial_retry_idempotency(target: Path) -> list[dict[str, Any]]:
    checkout = load_module(target, "checkout.py")

    class Store:
        def __init__(self) -> None:
            self.receipts: list[dict[str, Any]] = []

        def create_receipt(self, request: dict[str, Any]) -> str:
            receipt_id = f"receipt-{len(self.receipts) + 1}"
            self.receipts.append({"id": receipt_id, "request": dict(request)})
            return receipt_id

    class Notifier:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []
            self.deliveries: set[str] = set()
            self.first = True

        def deliver(self, receipt_id: str, email: str) -> None:
            self.calls.append({"receiptId": receipt_id, "email": email})
            self.deliveries.add(receipt_id)
            if self.first:
                self.first = False
                raise TimeoutError("response lost after notification delivery")

    store = Store()
    notifier = Notifier()
    result: Any = None
    delivery_error: str | None = None
    try:
        result = checkout.complete_checkout(
            store,
            notifier,
            {"request_id": "checkout-9", "email": "buyer@example.test"},
        )
    except Exception as error:  # Recovery must remain caller-observable when exhausted.
        delivery_error = type(error).__name__
    receipt_ids = [row["id"] for row in store.receipts]
    notification_ids = [row["receiptId"] for row in notifier.calls]
    observation = {
        "receiptCount": len(receipt_ids),
        "receiptIds": receipt_ids,
        "notificationAttemptCount": len(notification_ids),
        "notificationReceiptIds": notification_ids,
        "deliveryCount": len(notifier.deliveries),
        "returned": result,
        "error": delivery_error,
    }
    return [
        claim(
            "single-receipt-per-request",
            "DEFECT",
            len(receipt_ids) == 1
            and len(set(notification_ids)) == 1
            and notifier.deliveries == set(receipt_ids),
            observation,
        ),
        claim(
            "response-lost-delivery-recovery",
            "PRESERVATION",
            delivery_error is None
            and len(notification_ids) == 2
            and result == notification_ids[-1]
            and result in receipt_ids,
            observation,
        ),
    ]


def persist_before_ack(target: Path) -> list[dict[str, Any]]:
    consumer = load_module(target, "consumer.py")
    message = {"id": "message-3", "payload": {"sku": "A"}, "delivery_tag": "tag-3"}
    failed_events: list[str] = []

    class FailingStore:
        def save_once(self, message_id: str, payload: dict[str, Any]) -> bool:
            failed_events.append("save-attempt")
            raise OSError("durable store unavailable")

    class FailedBroker:
        def acknowledge(self, delivery_tag: str) -> None:
            failed_events.append("acknowledge")

    failure_error: str | None = None
    try:
        consumer.consume_delivery(FailingStore(), FailedBroker(), message)
    except Exception as error:  # Caller-visible failure is part of the contract.
        failure_error = type(error).__name__

    def observe_success(created: bool, expected: str) -> dict[str, Any]:
        label = expected.lower()
        events: list[str] = []

        class Store:
            def save_once(self, message_id: str, payload: dict[str, Any]) -> bool:
                events.append(f"save-{label}")
                return created

        class Broker:
            def acknowledge(self, delivery_tag: str) -> None:
                events.append(f"acknowledge-{label}")

        returned = consumer.consume_delivery(Store(), Broker(), message)
        return {
            "classification": expected,
            "events": events,
            "returned": returned,
            "passed": events == [f"save-{label}", f"acknowledge-{label}"]
            and returned == expected,
        }

    success_observations = [
        observe_success(True, "CREATED"),
        observe_success(False, "DUPLICATE"),
    ]

    return [
        claim(
            "failed-persistence-remains-retryable",
            "DEFECT",
            failure_error == "OSError" and failed_events == ["save-attempt"],
            {"events": failed_events, "error": failure_error},
        ),
        claim(
            "created-and-duplicate-flow",
            "PRESERVATION",
            all(row["passed"] for row in success_observations),
            {"flows": success_observations},
        ),
    ]


def stable_operation_fresh_lease(target: Path) -> list[dict[str, Any]]:
    operations = load_module(target, "operations.py")
    request = {"request_id": "operation-request-4", "payload": {"sku": "A"}}

    class Store:
        def __init__(self) -> None:
            self.operations: list[dict[str, Any]] = []
            self.leases: list[dict[str, str]] = []

        def create_operation(self, value: dict[str, Any]) -> str:
            operation_id = f"operation-{len(self.operations) + 1}"
            self.operations.append({"id": operation_id, "request": dict(value)})
            return operation_id

        def issue_lease(self, operation_id: str) -> str:
            lease = f"lease-{len(self.leases) + 1}"
            self.leases.append({"operationId": operation_id, "lease": lease})
            return lease

    class RecoveringWorker:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def execute(self, operation_id: str, lease: str) -> dict[str, str]:
            self.calls.append({"operationId": operation_id, "lease": lease})
            if len(self.calls) == 1:
                raise TimeoutError("response lost after execution")
            return {"operation_id": operation_id, "status": "completed"}

    recovery_store = Store()
    recovery_worker = RecoveringWorker()
    recovery_result: Any = None
    recovery_error: str | None = None
    try:
        recovery_result = operations.execute_with_retry(
            recovery_store, recovery_worker, request
        )
    except Exception as error:  # Claims remain distinguishable after a bad hoist.
        recovery_error = type(error).__name__

    operation_ids = [row["id"] for row in recovery_store.operations]
    worker_operation_ids = [row["operationId"] for row in recovery_worker.calls]
    worker_leases = [row["lease"] for row in recovery_worker.calls]
    issued_pairs = {
        (row["operationId"], row["lease"]) for row in recovery_store.leases
    }
    stable_identity = (
        len(operation_ids) == 1
        and len(worker_operation_ids) == 2
        and set(worker_operation_ids) == set(operation_ids)
    )
    fresh_leases = (
        len(worker_leases) == 2
        and len(set(worker_leases)) == 2
        and len(recovery_store.leases) == 2
        and all(
            (row["operationId"], row["lease"]) in issued_pairs
            for row in recovery_worker.calls
        )
    )
    recovered = (
        recovery_error is None
        and len(recovery_worker.calls) == 2
        and recovery_result
        == {"operation_id": worker_operation_ids[-1], "status": "completed"}
    )

    class ExhaustingWorker:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def execute(self, operation_id: str, lease: str) -> None:
            self.calls.append({"operationId": operation_id, "lease": lease})
            raise TimeoutError("worker remained unavailable")

    exhaustion_store = Store()
    exhaustion_worker = ExhaustingWorker()
    exhaustion_error: str | None = None
    try:
        operations.execute_with_retry(exhaustion_store, exhaustion_worker, request)
    except Exception as error:  # The final timeout must remain caller-visible.
        exhaustion_error = type(error).__name__
    exhausted_truthfully = (
        exhaustion_error == "TimeoutError" and len(exhaustion_worker.calls) == 2
    )

    recovery_observation = {
        "operationIds": operation_ids,
        "issuedLeases": recovery_store.leases,
        "workerCalls": recovery_worker.calls,
        "returned": recovery_result,
        "error": recovery_error,
    }
    timeout_observation = {
        "recovery": recovery_observation,
        "exhaustionAttemptCount": len(exhaustion_worker.calls),
        "exhaustionError": exhaustion_error,
    }
    return [
        claim(
            "stable-operation-identity",
            "DEFECT",
            stable_identity,
            recovery_observation,
        ),
        claim(
            "fresh-lease-per-attempt",
            "PRESERVATION",
            fresh_leases,
            recovery_observation,
        ),
        claim(
            "timeout-recovery-preserved",
            "PRESERVATION",
            recovered and exhausted_truthfully,
            timeout_observation,
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
    if case_id.startswith("partial-retry-idempotency-"):
        return partial_retry_idempotency(target)
    if case_id.startswith("persist-before-ack-"):
        return persist_before_ack(target)
    if case_id.startswith("stable-operation-fresh-lease-"):
        return stable_operation_fresh_lease(target)
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
