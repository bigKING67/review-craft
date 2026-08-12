def consume_delivery(store, broker, message):
    try:
        created = store.save_once(message["id"], message["payload"])
        return "CREATED" if created else "DUPLICATE"
    finally:
        broker.acknowledge(message["delivery_tag"])
