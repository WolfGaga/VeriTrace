"""String formatting without private object traversal."""

import _string
from string import Formatter
from .access import validate_field_access


class _PublicFieldFormatter(Formatter):
    def __init__(self, positional=True):
        self.positional = positional

    def get_field(self, field_name, args, kwargs):
        _, traversal = _string.formatter_field_name_split(field_name)
        for attribute, key in traversal:
            validate_field_access(key, attribute)
        return super().get_field(field_name, args, kwargs)

    def get_value(self, key, args, kwargs):
        if isinstance(key, int) and not self.positional:
            raise ValueError("Format string contains positional fields")
        return super().get_value(key, args, kwargs)


def format_method(value, name):
    """Bind guarded string methods, including the unbound str.format forms."""
    if name not in ("format", "format_map"):
        raise ValueError("Only string formatting methods can be bound")
    if value is str:

        def unbound(template, /, *args, **kwargs):
            if not isinstance(template, str):
                raise TypeError("String formatting requires a string template")
            return format_method(template, name)(*args, **kwargs)

        return unbound

    if isinstance(value, str):
        if name == "format":

            def bound(*args, **kwargs):
                return _PublicFieldFormatter().vformat(value, args, kwargs)

            return bound

        def mapped(mapping, /):
            return _PublicFieldFormatter(positional=False).vformat(value, (), mapping)

        return mapped

    return getattr(value, name)
