"""Attribute boundaries for Planner programs and their string format fields."""

_EXECUTION_ATTRIBUTES = frozenset(
    {
        "gi_frame",
        "gi_code",
        "gi_yieldfrom",
        "cr_frame",
        "cr_code",
        "cr_await",
        "ag_frame",
        "ag_code",
        "ag_await",
        "f_back",
        "f_globals",
        "f_locals",
        "f_builtins",
        "f_code",
        "f_trace",
        "f_trace_lines",
        "f_trace_opcodes",
        "tb_frame",
        "tb_next",
        "co_code",
        "co_consts",
        "co_names",
        "co_varnames",
        "co_freevars",
        "co_cellvars",
        "func_code",
        "func_globals",
        "func_closure",
        "im_self",
        "im_func",
        "im_class",
    }
)


def validate_field_access(key, attribute=True):
    if not isinstance(key, str):
        return
    if key.startswith("_"):
        raise ValueError("Planner programs cannot access private fields")
    if attribute and key in _EXECUTION_ATTRIBUTES:
        raise ValueError("Planner programs cannot inspect execution state")
