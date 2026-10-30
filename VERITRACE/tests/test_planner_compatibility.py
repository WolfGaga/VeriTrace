"""Planner language and restricted-namespace contract coverage."""

import ast
import builtins
import io
import sys
import threading
from contextlib import redirect_stdout
from types import SimpleNamespace

import pytest

from veritrace.agent.runtime.program import PlannerProgram


# Builtins supported by the default Planner interpreter.
_BASELINE_BUILTINS = (
    "bool int float str list dict set tuple frozenset bytes bytearray abs all any "
    "bin callable chr divmod enumerate filter format getattr hasattr hash hex id "
    "isinstance issubclass iter len map max min next oct ord pow print range repr "
    "reversed round setattr slice sorted sum type zip Exception ValueError TypeError "
    "KeyError IndexError AttributeError RuntimeError StopIteration"
).split()


class BaselineRestrictedProgram:
    """Stdlib-only oracle for the supported namespace and exec/eval behavior."""

    def __init__(self):
        self.globals = {"__builtins__": {name: getattr(builtins, name) for name in _BASELINE_BUILTINS}}
        self.locals = {}

    def inject(self, name, value):
        self.locals[name] = value

    def execute(self, code):
        output = io.StringIO()
        try:
            tree = ast.parse(code)
            expression = None
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                expression = ast.get_source_segment(code, tree.body[-1])
            result = None
            with redirect_stdout(output):
                exec(code, self.globals, self.locals)
                if expression:
                    try:
                        result = eval(expression, self.globals, self.locals)
                    except Exception:
                        pass
            return SimpleNamespace(success=True, result=result, stdout=output.getvalue(), error=None)
        except Exception as error:
            return SimpleNamespace(success=False, result=None, stdout=output.getvalue(), error=str(error))


def make_programs():
    programs = [BaselineRestrictedProgram(), PlannerProgram()]
    for program in programs:
        program.inject("item", SimpleNamespace(
            name="task_a", _id="synthetic-id", f_globals="fixture-data",
            format=lambda value: f"custom {value}", format_map=lambda values: values["label"],
        ))
        program.inject("record", {"_id": "record-id", "count": 3})
    return programs


@pytest.mark.parametrize(
    "code,expected",
    [
        ('(getattr(item, "name"), hasattr(item, "name"), id(item) == id(item), type(item).__name__)',
         ("task_a", True, True, "SimpleNamespace")),
        ('setattr(item, "name", "renamed")\n(item.name, getattr(item, "missing", "fallback"))',
         ("renamed", "fallback")),
        ('item._id', "synthetic-id"),
        ('getattr(item, "_id")', "synthetic-id"),
        ('item.__class__.__name__', "SimpleNamespace"),
        ('__planner_value = "ordinary variable"\n__planner_value', "ordinary variable"),
        ('"{0._id}:{record[_id]}".format(item, record=record)', "synthetic-id:record-id"),
        ('render = "{value._id}/{record[_id]}".format_map\nrender({"value": item, "record": record})',
         "synthetic-id/record-id"),
        ('render = str.format\nrender("{0._id}", item)', "synthetic-id"),
        ('item.format("value")', "custom value"),
        ('item.format_map({"label": "custom map"})', "custom map"),
        ('item.f_globals', "fixture-data"),
        ('"{0.f_globals}".format(item)', "fixture-data"),
        ('"{value:{width}.{precision}f}".format(value=2.5, width=6, precision=2)', "  2.50"),
        ('[value * value for value in range(4)]', [0, 1, 4, 9]),
    ],
)
def test_default_language_matches_restricted_baseline_on_synthetic_fixtures(code, expected):
    baseline, current = [program.execute(code) for program in make_programs()]
    assert baseline.success, baseline.error
    assert current.success, current.error
    assert current.result == baseline.result == expected
    assert current.stdout == baseline.stdout


def test_default_preserves_variables_and_stdout_across_actions_like_baseline():
    for program in make_programs():
        result = program.execute('values = [3, 1, 2]\nif values:\n    print("stored", len(values))')
        assert result.success, result.error
        assert result.stdout == "stored 3\n"
        result = program.execute('for value in sorted(values):\n    print(value)\nsum(values)')
        assert result.success, result.error
        assert result.stdout == "1\n2\n3\n"
        assert result.result == 6


def test_default_checkpoint_preserves_underscore_variables_and_copies_serialized_values():
    first = PlannerProgram()
    first.inject("question", "injected question")
    first.inject("graph", {"injected": "graph"})
    assert first.execute('_cache = {"answers": [1]}\n__local = "kept"\npublic_value = 7').success
    checkpoint = first.snapshot()
    assert checkpoint == {"_cache": {"answers": [1]}, "__local": "kept", "public_value": 7}

    second = PlannerProgram()
    second.restore(checkpoint)
    checkpoint["_cache"]["answers"].append(2)
    for program in (first, second):
        result = program.execute("(_cache, __local, public_value)")
        assert result.success, result.error
        assert result.result == ({"answers": [1]}, "kept", 7)


def test_isolated_checkpoints_continue_excluding_private_variables():
    program = PlannerProgram(isolated=True)
    assert program.execute('_cache = {"answers": [1]}\npublic_value = 7').success
    assert program.snapshot() == {"public_value": 7}
    program.restore({"_cache": "replacement", "__local": "private", "public_value": 8})
    assert program.snapshot() == {"public_value": 8}
    assert program.execute("_cache").result == {"answers": [1]}


@pytest.mark.parametrize(
    "code",
    ['import math', 'from math import sqrt', '__import__("math")', 'callable(open)', 'eval("1 + 1")',
     'exec("value = 1")'],
)
def test_default_retains_original_restricted_import_and_builtin_failures(code):
    for program in make_programs():
        assert program.execute("kept = 7").success
        result = program.execute(code)
        assert not result.success
        assert result.error
        assert program.execute("kept").result == 7


@pytest.mark.parametrize("isolated", [False, True])
@pytest.mark.parametrize("code", ["if", 'raise ValueError("synthetic failure")', 'print("done")\n42'])
def test_planner_restores_external_trace_after_each_execution(isolated, code):
    cancelled = threading.Event()
    program = PlannerProgram(cancelled, isolated=isolated)
    previous_trace = sys.gettrace()

    def external_trace(frame, event, arg):
        return external_trace

    try:
        sys.settrace(external_trace)
        program.execute(code)
        assert sys.gettrace() is external_trace
        result = program.execute('print("next action")\n42')
        assert result.success and result.result == 42
        assert result.stdout == "next action\n"
        assert sys.gettrace() is external_trace
    finally:
        sys.settrace(previous_trace)


@pytest.mark.parametrize("isolated", [False, True])
def test_cancelled_planner_restores_trace_and_can_run_after_cancellation_clears(isolated):
    cancelled = threading.Event()
    program = PlannerProgram(cancelled, isolated=isolated)
    program.inject("stop", cancelled.set)
    previous_trace = sys.gettrace()
    result = program.execute("stop()\nwhile True:\n    pass")
    assert not result.success and result.error == "Research cancelled"
    assert sys.gettrace() is previous_trace

    cancelled.clear()
    result = program.execute('print("resumed")\n42')
    assert result.success and result.result == 42
    assert result.stdout == "resumed\n"
    assert sys.gettrace() is previous_trace
