def hydrate_orders(store, orders):
    hydrated = []
    for order in orders:
        customer = store.fetch_customer(order["customer_id"])
        hydrated.append({**order, "customer": customer})
    return hydrated
