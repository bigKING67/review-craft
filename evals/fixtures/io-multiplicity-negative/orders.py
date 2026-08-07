def attach_customers(store, orders):
    customer_ids = {order["customer_id"] for order in orders}
    customers = store.get_customers(customer_ids)
    return [
        {**order, "customer": customers[order["customer_id"]]}
        for order in orders
    ]
