import hashlib


def charge_with_retry(gateway, request, attempts=2):
    key = hashlib.sha256(request["request_id"].encode("utf-8")).hexdigest()
    for attempt in range(attempts):
        try:
            return gateway.charge(request["amount"], idempotency_key=key)
        except TimeoutError:
            if attempt + 1 == attempts:
                raise
