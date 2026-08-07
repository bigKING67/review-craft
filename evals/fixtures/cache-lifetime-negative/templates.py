from collections import OrderedDict

_MAX_CACHE_ENTRIES = 16
_CACHE = OrderedDict()


def render(template_id, source, values):
    compiled = _CACHE.get(template_id)
    if compiled is None:
        compiled = source.format
        _CACHE[template_id] = compiled
        if len(_CACHE) > _MAX_CACHE_ENTRIES:
            _CACHE.popitem(last=False)
    else:
        _CACHE.move_to_end(template_id)
    return compiled(**values)
