from decimal import Decimal, ROUND_HALF_UP


def legacy_total(subtotal):
    return (Decimal(subtotal) * Decimal("1.05")).quantize(Decimal("0.01"))


def current_total(subtotal):
    fee = Decimal("0.30")
    return (Decimal(subtotal) * Decimal("1.05") + fee).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
