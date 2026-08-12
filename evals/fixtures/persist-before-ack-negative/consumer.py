def consume_delivery(store, broker, message):
    created = store.save_once(message["id"], message["payload"])
    broker.acknowledge(message["delivery_tag"])
    return "CREATED" if created else "DUPLICATE"
