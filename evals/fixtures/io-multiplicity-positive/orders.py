def attach_customers(store, orders):
    result = []
    for order in orders:
        customer = store.get_customer(order["customer_id"])
        result.append({**order, "customer": customer})
    return result
