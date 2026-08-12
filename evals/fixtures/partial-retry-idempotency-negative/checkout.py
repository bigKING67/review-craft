def complete_checkout(store, notifier, request, attempts=2):
    receipt_id = store.create_receipt(request)
    for attempt in range(attempts):
        try:
            notifier.deliver(receipt_id, request["email"])
            return receipt_id
        except TimeoutError:
            if attempt + 1 == attempts:
                raise
