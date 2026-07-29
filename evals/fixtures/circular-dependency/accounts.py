from orders import current_order


def current_account():
    return {"order": current_order()}
