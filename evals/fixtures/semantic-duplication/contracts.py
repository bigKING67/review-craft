def retail_price(subtotal):
    """Retail contract includes sales tax and rounds each order."""
    return round(subtotal * 1.08, 2)


def wholesale_price(subtotal):
    """Wholesale contract excludes tax and applies a volume discount."""
    return round(subtotal * 0.92, 2)
