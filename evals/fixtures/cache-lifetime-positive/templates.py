_CACHE = {}


def render(template_id, source, values):
    compiled = _CACHE.get(template_id)
    if compiled is None:
        compiled = source.format
        _CACHE[template_id] = compiled
    return compiled(**values)
