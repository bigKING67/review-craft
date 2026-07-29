from accounts import current_account


def current_order():
    return {"account": current_account()}
