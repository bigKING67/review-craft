def complete_checkout(store, notifier, request, attempts=2):
    for attempt in range(attempts):
        receipt_id = store.create_receipt(request)
        try:
            notifier.deliver(receipt_id, request["email"])
            return receipt_id
        except TimeoutError:
            if attempt + 1 == attempts:
                raise
