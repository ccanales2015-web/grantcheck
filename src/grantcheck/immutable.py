"""Deep, detached, immutable copies of JSON-like data.

deep_freeze() turns dicts into read-only MappingProxyType views and lists or
tuples into tuples, recursively; strings, numbers, booleans, and None are kept.
Keys that are not strings become their repr(), and values of any other type
become a short placeholder naming the type, so a frozen value can always be
turned back into JSON-compatible data with deep_thaw().

Callers that need detachment from mutable inputs should freeze a deep copy.
"""

from types import MappingProxyType


def deep_freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key if isinstance(key, str) else repr(key): deep_freeze(item)
                                 for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze(item) for item in value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return f"<unsupported value of type {type(value).__name__}>"


def deep_thaw(value):
    if isinstance(value, MappingProxyType):
        return {key: deep_thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [deep_thaw(item) for item in value]
    return value
