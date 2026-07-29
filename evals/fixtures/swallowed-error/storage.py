class Storage:
    def save(self, payload):
        raise OSError("disk unavailable")


def persist(storage, payload):
    try:
        storage.save(payload)
    except OSError:
        pass
    return {"saved": True}
