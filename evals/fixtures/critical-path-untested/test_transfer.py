from transfer import format_amount


def test_format_amount():
    assert format_amount(123) == "1.23"
