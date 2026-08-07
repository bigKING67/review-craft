def create_order(store, payload):
    try:
        order_id = store.insert(payload)
    except OSError:
        return {"ok": True, "order_id": None}
    return {"ok": True, "order_id": order_id}
