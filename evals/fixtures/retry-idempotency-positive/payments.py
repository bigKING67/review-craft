import uuid


def charge_with_retry(gateway, request, attempts=2):
    for attempt in range(attempts):
        key = str(uuid.uuid4())
        try:
            return gateway.charge(request["amount"], idempotency_key=key)
        except TimeoutError:
            if attempt + 1 == attempts:
                raise
