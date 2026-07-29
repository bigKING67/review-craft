import json


def decode_record(payload):
    record = json.loads(payload)
    if record["version"] == 1:
        # Persisted v1 records remain supported until the offline migration completes.
        return {"name": record["display_name"], "version": 1}
    return {"name": record["name"], "version": record["version"]}
