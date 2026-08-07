#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import ModuleType


def load_module(target: Path, filename: str) -> ModuleType:
    path = target / filename
    spec = importlib.util.spec_from_file_location(f"eval_{target.name}_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def failure_truthfulness(target: Path, *, negative: bool) -> dict:
    service = load_module(target, "service.py")

    class Store:
        def __init__(self, fail: bool) -> None:
            self.fail = fail
            self.saved = []

        def insert(self, payload):
            if self.fail:
                raise OSError("durable write failed")
            self.saved.append(payload)
            return "order-1"

    if not negative:
        store = Store(fail=True)
        result = service.create_order(store, {"sku": "A"})
        return {"returned": result, "durableRecordCount": len(store.saved)}

    class Metrics:
        def emit(self, name, order_id):
            raise OSError("telemetry unavailable")

    store = Store(fail=False)
    result = service.create_order(store, Metrics(), {"sku": "A"})
    return {"returned": result, "durableRecordCount": len(store.saved)}


def retry_idempotency(target: Path) -> dict:
    payments = load_module(target, "payments.py")

    class Gateway:
        def __init__(self) -> None:
            self.keys = []
            self.charges = {}
            self.first = True

        def charge(self, amount, *, idempotency_key):
            self.keys.append(idempotency_key)
            if idempotency_key not in self.charges:
                self.charges[idempotency_key] = amount
            if self.first:
                self.first = False
                raise TimeoutError("response lost after commit")
            return {"charged": self.charges[idempotency_key]}

    gateway = Gateway()
    result = payments.charge_with_retry(
        gateway,
        {"request_id": "request-7", "amount": 25},
    )
    return {
        "returned": result,
        "attemptCount": len(gateway.keys),
        "distinctKeyCount": len(set(gateway.keys)),
        "chargeCount": len(gateway.charges),
    }


def ack_order(target: Path) -> dict:
    jobs = load_module(target, "jobs.py")
    events = []

    class Store:
        def save(self, job):
            events.append("save-attempt")
            raise OSError("disk unavailable")

    class Queue:
        def acknowledge(self, job_id):
            events.append("acknowledge")

    error = None
    try:
        jobs.submit_job(Store(), Queue(), {"id": "job-1"})
    except OSError as caught:
        error = type(caught).__name__
    return {"events": events, "error": error}


def cache_lifetime(target: Path) -> dict:
    templates = load_module(target, "templates.py")
    for index in range(100):
        templates.render(f"template-{index}", "Hello {name}", {"name": "Ada"})
    return {"cacheSize": len(templates._CACHE), "inputKeyCount": 100}


def compatibility_path(target: Path) -> dict:
    records = load_module(target, "records.py")
    error = None
    decoded = None
    try:
        decoded = records.load_record(
            {"version": 1, "payload": {"legacy_name": "Ada"}}
        )
    except ValueError as caught:
        error = str(caught)
    return {
        "supportedVersions": sorted(records.SUPPORTED_VERSIONS),
        "publicExports": list(records.__all__),
        "version1Result": decoded,
        "version1Error": error,
    }


def io_multiplicity(target: Path) -> dict:
    orders_module = load_module(target, "orders.py")

    class Store:
        def __init__(self) -> None:
            self.single_reads = 0
            self.batch_reads = 0

        def get_customer(self, customer_id):
            self.single_reads += 1
            return {"id": customer_id}

        def get_customers(self, customer_ids):
            self.batch_reads += 1
            return {customer_id: {"id": customer_id} for customer_id in customer_ids}

    store = Store()
    orders = [
        {"id": f"order-{index}", "customer_id": f"customer-{index % 10}"}
        for index in range(50)
    ]
    result = orders_module.attach_customers(store, orders)
    return {
        "resultCount": len(result),
        "singleReadCount": store.single_reads,
        "batchReadCount": store.batch_reads,
    }


def observe(case_id: str, target: Path) -> dict:
    if case_id == "failure-truthfulness-positive":
        return failure_truthfulness(target, negative=False)
    if case_id == "failure-truthfulness-negative":
        return failure_truthfulness(target, negative=True)
    if case_id.startswith("retry-idempotency-"):
        return retry_idempotency(target)
    if case_id.startswith("ack-order-"):
        return ack_order(target)
    if case_id.startswith("cache-lifetime-"):
        return cache_lifetime(target)
    if case_id.startswith("compatibility-path-"):
        return compatibility_path(target)
    if case_id.startswith("io-multiplicity-"):
        return io_multiplicity(target)
    raise ValueError(f"unsupported case: {case_id}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    payload = {
        "schema": "review-craft.eval-observation.v1",
        "caseId": args.case,
        "observation": observe(args.case, Path(args.target).resolve(strict=True)),
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
