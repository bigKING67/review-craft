def create_order(store, metrics, payload):
    order_id = store.insert(payload)
    try:
        metrics.emit("order_created", order_id)
    except OSError:
        pass
    return {"ok": True, "order_id": order_id}
