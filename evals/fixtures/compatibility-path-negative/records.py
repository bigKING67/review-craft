__all__ = ["load_record"]

SUPPORTED_VERSIONS = {1, 2, 3}


def _decode_v1(payload):
    return {"name": payload["legacy_name"]}


def _decode_v2(payload):
    return {"name": payload["name"]}


def load_record(record):
    if record["version"] not in SUPPORTED_VERSIONS:
        raise ValueError("unsupported record version")
    if record["version"] == 1:
        return _decode_v1(record["payload"])
    return _decode_v2(record["payload"])
