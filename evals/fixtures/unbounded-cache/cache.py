_CACHE = {}


def render_for_user(user_id, template):
    key = f"{user_id}:{template}"
    if key not in _CACHE:
        _CACHE[key] = template.format(user_id=user_id)
    return _CACHE[key]
