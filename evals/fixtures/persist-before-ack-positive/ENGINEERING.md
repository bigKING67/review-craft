# Delivery Consumption Contract

`consume_delivery(store, broker, message)` must durably classify a delivery before
acknowledging it:

- `store.save_once(message_id, payload)` returns `True` for a new record and `False` for an
  already-persisted duplicate.
- If persistence raises, leave the delivery unacknowledged and preserve the exception for
  the caller so the broker can retry it.
- A new or duplicate delivery that reaches a durable decision is acknowledged exactly once,
  after `save_once` returns.
- Return `"CREATED"` for a new record and `"DUPLICATE"` for an existing record.
