"""Execute the Planner's Python commands against its task interface."""

import ast
import builtins
import io
import json
import re
import sys
import traceback
from collections.abc import Mapping
from dataclasses import dataclass
from .access import validate_field_access
from .formatting import format_method


class _GuardFormatMethods(ast.NodeTransformer):
    def visit_Attribute(self, node):
        self.generic_visit(node)
        if isinstance(node.ctx, ast.Load) and node.attr in ("format", "format_map"):
            guarded = ast.Call(
                func=ast.Name(id="__planner_format", ctx=ast.Load()),
                args=[node.value, ast.Constant(node.attr)],
                keywords=[],
            )
            return ast.copy_location(guarded, node)
        return node


@dataclass
class ExecutionResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    result: object = None
    error: str | None = None
    traceback: str | None = None
    executed_code: str | None = None

    def to_error_feedback(self):
        if self.success:
            return ""
        feedback = f"Code execution failed: `{self.error or ''}`\n\n"
        if self.traceback:
            lines = self.traceback.strip().splitlines()
            if len(lines) > 8:
                lines = ["  ..."] + lines[-5:]
            feedback += "Traceback:\n```\n" + "\n".join(lines) + "\n```\n\n"
        return feedback + "Fix the error and provide corrected code."


class CodeExtractor:
    PATTERNS = (r"```python\s*\n(.*?)```", r"```\s*\n(.*?)```", r"`([^`]+)`")

    @classmethod
    def extract(cls, text):
        for pattern in cls.PATTERNS:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                return match.group(1).strip()
        return None


class PlannerProgram:
    """Execute research commands with baseline builtins and optional isolation checks."""

    _BUILTIN_NAMES = (
        "bool int float str list dict set tuple frozenset bytes bytearray abs all any "
        "bin callable chr divmod enumerate filter format getattr hasattr hash hex id isinstance issubclass "
        "iter len map max min next oct ord pow range repr reversed round setattr slice sorted sum type zip "
        "Exception ValueError TypeError KeyError IndexError AttributeError RuntimeError StopIteration"
    ).split()

    _ISOLATED_BUILTINS = frozenset({"getattr", "hasattr", "id", "setattr", "type"})
    _RESERVED_NAMES = frozenset({"__builtins__", "__planner_format", "graph", "question",
                                 "add_task", "finish", "propose_restructure"})

    def __init__(self, cancelled=None, *, isolated: bool = False):
        self._cancelled = cancelled
        self.isolated = isolated
        self._namespace = {}
        self._stdout = None
        self.reset()

    def _print(self, *args, **kwargs):
        kwargs["file"] = self._stdout
        print(*args, **kwargs)

    def inject(self, name, value):
        if name in ("add_task", "finish", "propose_restructure"):

            def command(*args, **kwargs):
                result = value(*args, **kwargs)
                if isinstance(result, str) and ("[ERROR]" in result or "[WARNING]" in result):
                    self._print(result)
                return result

            self._namespace[name] = command
        else:
            self._namespace[name] = value

    def reset(self):
        safe = {name: getattr(builtins, name) for name in self._BUILTIN_NAMES
                if not self.isolated or name not in self._ISOLATED_BUILTINS}
        safe["print"] = self._print
        self._namespace = {"__builtins__": safe}
        if self.isolated:
            self._namespace["__planner_format"] = format_method

    def snapshot(self):
        def mapping_value(value):
            if isinstance(value, Mapping):
                return dict(value)
            raise TypeError("Planner value cannot be saved as JSON")

        values = {}
        for name, value in self._namespace.items():
            if name in self._RESERVED_NAMES or self.isolated and name.startswith("_"):
                continue
            try:
                values[name] = json.loads(json.dumps(value, default=mapping_value))
            except (TypeError, ValueError):
                continue
        return values

    def restore(self, values):
        for name, value in (values or {}).items():
            if (name.isidentifier() and name not in self._RESERVED_NAMES
                    and (not self.isolated or not name.startswith("_"))):
                self._namespace[name] = json.loads(json.dumps(value))

    @staticmethod
    def _validate(tree):
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                raise ValueError("Planner programs cannot import modules")
            if isinstance(node, ast.Attribute):
                validate_field_access(node.attr)
            if isinstance(node, ast.Name) and node.id.startswith("__"):
                raise ValueError("Planner programs cannot access private names")

    def execute(self, code):
        # Backticks inside string arguments must remain untouched.
        if code.lstrip().startswith("```"):
            code = CodeExtractor.extract(code) or code
        output = io.StringIO()
        self._stdout = output
        previous_trace = sys.gettrace()

        def check_cancel(frame, event, arg):
            if frame.f_code.co_filename == "<planner>" and self._cancelled is not None and self._cancelled.is_set():
                raise RuntimeError("Research cancelled")
            return check_cancel

        try:
            tree = ast.parse(code, filename="<planner>", mode="exec")
            if self.isolated:
                self._validate(tree)
                tree = _GuardFormatMethods().visit(tree)
            ast.fix_missing_locations(tree)
            expression = None
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                expression = ast.Expression(tree.body.pop().value)
                ast.fix_missing_locations(expression)
            if self._cancelled is not None:
                sys.settrace(check_cancel)
            exec(compile(tree, "<planner>", "exec"), self._namespace, self._namespace)
            result = (
                eval(compile(expression, "<planner>", "eval"), self._namespace, self._namespace) if expression else None
            )
            return ExecutionResult(True, stdout=output.getvalue(), result=result)
        except Exception as exc:
            return ExecutionResult(
                False, stdout=output.getvalue(), error=str(exc), traceback=traceback.format_exc(), executed_code=code
            )
        finally:
            sys.settrace(previous_trace)
            self._stdout = None
