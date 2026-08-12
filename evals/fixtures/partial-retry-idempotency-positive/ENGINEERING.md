# Checkout Completion Contract

`complete_checkout(store, notifier, request, attempts=2)` coordinates two externally
visible effects:

1. `store.create_receipt(request)` creates the durable receipt for the request.
2. `notifier.deliver(receipt_id, email)` delivers a notification using that receipt
   identity.

One function invocation represents one logical checkout request, so it must create exactly
one receipt. A `TimeoutError` from `notifier.deliver` can mean the notification was delivered
but its response was lost. Retry that notification up to `attempts` times with the same
receipt identifier. Return the receipt identifier only after delivery is confirmed, and
re-raise the final timeout when all attempts are exhausted.
