from records import decode_record


def test_persisted_v1_record():
    assert decode_record('{"version": 1, "display_name": "Ada"}')["name"] == "Ada"
