"""Import restricted pure Python functions into accepted native Sley graphs.

Import parses source as data. It never imports, compiles, evaluates, or calls
the submitted function. Expected valid cases come from the domain manifest.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROFILE = "python-pure-i64-v1"
AST_PYTHON = (3, 14)
MAX_SOURCE_BYTES = 32_768
MAX_DOMAIN_BYTES = 65_536
MAX_AST_NODES = 256
MAX_AST_DEPTH = 16
I64_MIN, I64_MAX = -(2**63), 2**63 - 1
LABEL = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
DECIMAL = re.compile(r"(?:0|-?[1-9][0-9]*)\Z")


class PythonImportError(ValueError):
    def __init__(self, message: str, node: ast.AST | None = None, code: str = "ST_IMPORT_UNSUPPORTED") -> None:
        super().__init__(message)
        self.code = code
        self.line = getattr(node, "lineno", None)
        self.column = getattr(node, "col_offset", None)


@dataclass(frozen=True)
class Domain:
    name: str
    kind: str
    minimum: int | None = None
    maximum: int | None = None


@dataclass(frozen=True)
class Info:
    kind: str
    minimum: int | None = None
    maximum: int | None = None


@dataclass(frozen=True)
class Binding:
    expression: ast.expr
    environment: dict[str, "Binding"]
    info: Info


def _regular_bytes(path: Path, maximum: int) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise PythonImportError("input must be a regular file", code="ST_INPUT_INVALID")
    if info.st_size > maximum:
        raise PythonImportError("input exceeds the importer byte limit", code="ST_RESOURCE_LIMIT")
    with path.open("rb") as source:
        data = source.read(maximum + 1)
    if len(data) > maximum:
        raise PythonImportError("input exceeds the importer byte limit", code="ST_RESOURCE_LIMIT")
    return data


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in items:
        if key in result:
            raise PythonImportError(f"duplicate domain manifest key {key!r}", code="ST_INPUT_INVALID")
        result[key] = value
    return result


def _decimal(value: object, where: str) -> int:
    if not isinstance(value, str) or len(value) > 20 or DECIMAL.fullmatch(value) is None:
        raise PythonImportError(f"{where} must be a canonical decimal string", code="ST_INPUT_INVALID")
    number = int(value)
    if not I64_MIN <= number <= I64_MAX:
        raise PythonImportError(f"{where} is outside signed 64 bit", code="ST_INPUT_INVALID")
    return number


def _domain(data: bytes) -> tuple[list[Domain], list[dict[str, object]]]:
    try:
        document = json.loads(data, object_pairs_hook=_pairs,
                              parse_constant=lambda token: (_ for _ in ()).throw(
                                  PythonImportError(f"non-JSON token {token!r}", code="ST_INPUT_INVALID")))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise PythonImportError(f"invalid domain JSON: {error}", code="ST_INPUT_INVALID") from error
    if not isinstance(document, dict) or set(document) != {"contract", "profile", "parameters", "cases"}:
        raise PythonImportError("domain manifest needs contract, profile, parameters, and cases", code="ST_INPUT_INVALID")
    if document["contract"] != "sley-tools.python-domain.v1" or document["profile"] != PROFILE:
        raise PythonImportError("domain manifest contract or profile differs", code="ST_INPUT_INVALID")
    rows = document["parameters"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 8:
        raise PythonImportError("domain needs one through eight parameters", code="ST_INPUT_INVALID")
    parameters: list[Domain] = []
    names: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("type") not in ("int", "bool"):
            raise PythonImportError("domain parameter needs int or bool type", code="ST_INPUT_INVALID")
        name = row.get("name")
        if not isinstance(name, str) or LABEL.fullmatch(name) is None or name in names:
            raise PythonImportError("domain parameter name is invalid or duplicated", code="ST_INPUT_INVALID")
        names.add(name)
        if row["type"] == "bool":
            if set(row) != {"name", "type"}:
                raise PythonImportError("Boolean domain has no numeric bounds", code="ST_INPUT_INVALID")
            parameters.append(Domain(name, "bool"))
        else:
            if set(row) != {"name", "type", "minimum", "maximum"}:
                raise PythonImportError("integer domain needs exact minimum and maximum", code="ST_INPUT_INVALID")
            minimum = _decimal(row["minimum"], f"{name}.minimum")
            maximum = _decimal(row["maximum"], f"{name}.maximum")
            if minimum > maximum:
                raise PythonImportError(f"{name} minimum exceeds maximum", code="ST_INPUT_INVALID")
            parameters.append(Domain(name, "int", minimum, maximum))
    cases = document["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 64:
        raise PythonImportError("domain needs one through 64 supplied valid cases", code="ST_INPUT_INVALID")
    ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {"id", "inputs", "expected"}:
            raise PythonImportError("case needs exactly id, inputs, expected", code="ST_INPUT_INVALID")
        case_id = case["id"]
        if not isinstance(case_id, str) or not 1 <= len(case_id) <= 64 or case_id in ids:
            raise PythonImportError("case ID is invalid or duplicated", code="ST_INPUT_INVALID")
        ids.add(case_id)
        if not isinstance(case["inputs"], dict) or set(case["inputs"]) != names:
            raise PythonImportError("case inputs must match every parameter", code="ST_INPUT_INVALID")
        for parameter in parameters:
            value = case["inputs"][parameter.name]
            if parameter.kind == "bool":
                if type(value) is not bool:
                    raise PythonImportError("case Boolean input has the wrong type", code="ST_INPUT_INVALID")
            else:
                number = _decimal(value, f"case {case_id} input {parameter.name}")
                if not parameter.minimum <= number <= parameter.maximum:
                    raise PythonImportError("supplied valid case is outside its declared domain", code="ST_INPUT_INVALID")
    return parameters, cases


def _ast_depth(node: ast.AST) -> int:
    pending = [(node, 1)]
    maximum = 0
    while pending:
        current, depth = pending.pop()
        maximum = max(maximum, depth)
        if maximum > MAX_AST_DEPTH:
            return maximum
        pending.extend((child, depth + 1) for child in ast.iter_child_nodes(current))
    return maximum


ALLOWED_AST = (ast.FunctionDef, ast.arguments, ast.arg, ast.Name, ast.Load, ast.Store,
               ast.Return, ast.Assign, ast.If, ast.Expr, ast.Constant, ast.UnaryOp,
               ast.BinOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.Not, ast.USub,
               ast.Add, ast.Sub, ast.Mult, ast.Eq, ast.NotEq, ast.Lt, ast.LtE,
               ast.Gt, ast.GtE, ast.And, ast.Or)


def _function(data: bytes, parameters: list[Domain]) -> tuple[ast.FunctionDef, str]:
    if sys.version_info[:2] != AST_PYTHON:
        raise PythonImportError("this profile pins the CPython 3.14 AST parser", code="ST_FEATURE_UNSUPPORTED")
    try:
        source = data.decode("utf-8")
        module = ast.parse(source, mode="exec", type_comments=False)
    except (UnicodeError, SyntaxError, RecursionError) as error:
        raise PythonImportError(f"invalid Python source: {error}", code="ST_INPUT_INVALID") from error
    if len(list(ast.walk(module))) > MAX_AST_NODES or _ast_depth(module) > MAX_AST_DEPTH:
        raise PythonImportError("Python AST exceeds node or depth limit", code="ST_RESOURCE_LIMIT")
    if len(module.body) != 1 or not isinstance(module.body[0], ast.FunctionDef):
        raise PythonImportError("source must contain exactly one ordinary function")
    function = module.body[0]
    for node in ast.walk(function):
        if not isinstance(node, ALLOWED_AST):
            raise PythonImportError(f"unsupported syntax {type(node).__name__}", node)
    if (function.decorator_list or function.type_params or function.args.posonlyargs
            or function.args.kwonlyargs or function.args.vararg or function.args.kwarg
            or function.args.defaults or any(default is not None for default in function.args.kw_defaults)):
        raise PythonImportError("decorators, generics, positional-only, keyword-only, variadic, and default arguments are unsupported", function)
    if not isinstance(function.returns, ast.Name) or function.returns.id not in ("int", "bool"):
        raise PythonImportError("return annotation must be int or bool", function)
    if len(function.args.args) != len(parameters):
        raise PythonImportError("function parameters differ from the explicit domain", function)
    for arg, domain in zip(function.args.args, parameters, strict=True):
        if (arg.arg != domain.name or LABEL.fullmatch(arg.arg) is None
                or not isinstance(arg.annotation, ast.Name) or arg.annotation.id != domain.kind):
            raise PythonImportError("parameter name or annotation differs from domain", arg)
    if not function.body:
        raise PythonImportError("function has no body", function)
    return function, function.returns.id


def _name(node: ast.Name, environment: dict[str, Binding], domains: dict[str, Domain]) -> tuple[ast.expr, dict[str, Binding]] | None:
    if node.id in domains:
        return None
    binding = environment.get(node.id)
    if binding is None:
        raise PythonImportError(f"unbound local name {node.id!r}", node)
    return binding.expression, binding.environment


def _info(node: ast.expr, environment: dict[str, Binding], domains: dict[str, Domain]) -> Info:
    if isinstance(node, ast.Name):
        if node.id in environment:
            return environment[node.id].info
        _name(node, environment, domains)
        parameter = domains[node.id]
        return Info(parameter.kind, parameter.minimum, parameter.maximum)
    if isinstance(node, ast.Constant):
        if type(node.value) is bool:
            return Info("bool")
        if type(node.value) is int and I64_MIN <= node.value <= I64_MAX:
            return Info("int", node.value, node.value)
        raise PythonImportError("literal must be Boolean or signed 64 bit integer", node)
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            if _info(node.operand, environment, domains).kind != "bool":
                raise PythonImportError("not requires a statically Boolean operand", node)
            return Info("bool")
        if isinstance(node.op, ast.USub):
            if isinstance(node.operand, ast.Constant) and type(node.operand.value) is int and node.operand.value == 2**63:
                return Info("int", I64_MIN, I64_MIN)
            operand = _info(node.operand, environment, domains)
            if operand.kind != "int":
                raise PythonImportError("unary minus requires an integer", node)
            low, high = -operand.maximum, -operand.minimum
            if low < I64_MIN or high > I64_MAX:
                raise PythonImportError("unary negation may overflow signed 64 bit", node)
            return Info("int", low, high)
        raise PythonImportError("unsupported unary operator", node)
    if isinstance(node, ast.BinOp):
        left, right = _info(node.left, environment, domains), _info(node.right, environment, domains)
        if left.kind != "int" or right.kind != "int":
            raise PythonImportError("arithmetic operands must be integers", node)
        if isinstance(node.op, ast.Add):
            low, high = left.minimum + right.minimum, left.maximum + right.maximum
        elif isinstance(node.op, ast.Sub):
            low, high = left.minimum - right.maximum, left.maximum - right.minimum
        elif isinstance(node.op, ast.Mult):
            products = [left.minimum * right.minimum, left.minimum * right.maximum,
                        left.maximum * right.minimum, left.maximum * right.maximum]
            low, high = min(products), max(products)
        else:
            raise PythonImportError("only addition, subtraction, and multiplication are supported", node)
        if low < I64_MIN or high > I64_MAX:
            raise PythonImportError("arithmetic intermediate may overflow signed 64 bit", node)
        return Info("int", low, high)
    if isinstance(node, ast.Compare):
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise PythonImportError("chained comparisons are unsupported", node)
        left, right = _info(node.left, environment, domains), _info(node.comparators[0], environment, domains)
        if left.kind != right.kind:
            raise PythonImportError("comparison operands have different types", node)
        if isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
            return Info("bool")
        if isinstance(node.ops[0], (ast.Lt, ast.LtE, ast.Gt, ast.GtE)) and left.kind == "int":
            return Info("bool")
        raise PythonImportError("ordered comparisons require integers", node)
    if isinstance(node, ast.BoolOp):
        if not isinstance(node.op, (ast.And, ast.Or)) or len(node.values) < 2:
            raise PythonImportError("unsupported Boolean operation", node)
        if any(_info(value, environment, domains).kind != "bool" for value in node.values):
            raise PythonImportError("and/or require statically Boolean operands", node)
        return Info("bool")
    if isinstance(node, ast.IfExp):
        if _info(node.test, environment, domains).kind != "bool":
            raise PythonImportError("conditional expression requires a Boolean test", node.test)
        yes, no = _info(node.body, environment, domains), _info(node.orelse, environment, domains)
        if yes.kind != no.kind:
            raise PythonImportError("conditional branches have different result types", node)
        if yes.kind == "bool":
            return Info("bool")
        return Info("int", min(yes.minimum, no.minimum), max(yes.maximum, no.maximum))
    raise PythonImportError(f"unsupported expression {type(node).__name__}", node)


class Graph:
    def __init__(self, program_id: str, parameters: list[Domain], result_kind: str) -> None:
        self.parameters = parameters
        self.result_kind = result_kind
        self.domains = {item.name: item for item in parameters}
        self.recipe: dict[str, Any] = {
            "contract": "sley-tools.graph-authoring.v0", "program_id": program_id,
            "title": "Imported restricted Python function", "purpose": "Execute a bounded pure function in native Sley",
            "parameters": [{"name": item.name, "value_type": "Bool" if item.kind == "bool" else "SInt64"}
                           for item in parameters],
            "constants": [], "result": {"kind": "record", "fields": [
                {"name": "in_domain", "value_type": "Bool"},
                {"name": "value", "value_type": "Bool" if result_kind == "bool" else "CheckedSInt64"},
            ]}, "entry_block": "entry", "blocks": [],
        }
        self.blocks: dict[str, dict[str, Any]] = {}
        self.operation_spans: list[dict[str, object]] = []
        self.constant_spans: list[dict[str, object]] = []
        self.next_block = 0
        self.next_operation = 0
        self.next_constant = 0
        self.new_block("entry")

    def new_block(self, name: str | None = None) -> str:
        if len(self.blocks) >= 16:
            raise PythonImportError("function exceeds native block limit", code="ST_RESOURCE_LIMIT")
        if name is None:
            name = f"b{self.next_block}"
            self.next_block += 1
        if name in self.blocks:
            raise PythonImportError("compiler created a duplicate native block")
        block = {"name": name, "operations": [], "terminator": None}
        self.blocks[name] = block
        self.recipe["blocks"].append(block)
        return name

    def op(self, block: str, kind: str, node: ast.AST | None = None, **parts: object) -> str:
        if self.next_operation >= 128:
            raise PythonImportError("function exceeds native operation limit", node, code="ST_RESOURCE_LIMIT")
        name = f"o{self.next_operation}"
        self.next_operation += 1
        self.blocks[block]["operations"].append({"op": kind, "name": name, **parts})
        if node is not None:
            self.operation_spans.append({"line": node.lineno, "column": node.col_offset,
                                         "end_line": node.end_lineno, "end_column": node.end_col_offset,
                                         "block": block, "operation": name})
        return "op." + name

    def constant(self, block: str, kind: str, value: bool | int, node: ast.AST | None = None) -> str:
        if self.next_constant >= 64:
            raise PythonImportError("function exceeds native constant limit", node, code="ST_RESOURCE_LIMIT")
        name = f"c{self.next_constant}"
        self.next_constant += 1
        self.recipe["constants"].append({"name": name, "value_type": "Bool" if kind == "bool" else "SInt64",
                                         "value": value if kind == "bool" else str(value)})
        if node is not None:
            self.constant_spans.append({"line": node.lineno, "column": node.col_offset,
                                        "end_line": node.end_lineno, "end_column": node.end_col_offset,
                                        "constant": name})
        return self.op(block, "constant_ref", node, constant=name)

    def terminate(self, block: str, terminator: dict[str, object]) -> None:
        if self.blocks[block]["terminator"] is not None:
            raise PythonImportError("compiler tried to terminate a block twice")
        self.blocks[block]["terminator"] = terminator

    def finish(self) -> dict[str, Any]:
        if (len(self.recipe["parameters"]) > 16 or len(self.recipe["constants"]) > 64
                or len(self.recipe["blocks"]) > 16 or self.next_operation > 128
                or any(block["terminator"] is None for block in self.recipe["blocks"])):
            raise PythonImportError("function exceeds native graph limits", code="ST_RESOURCE_LIMIT")
        if len(json.dumps(self.recipe).encode()) > 65_536:
            raise PythonImportError("generated recipe exceeds native graph byte limit", code="ST_RESOURCE_LIMIT")
        return self.recipe


def _resolve(node: ast.expr, environment: dict[str, Binding], domains: dict[str, Domain]) -> tuple[ast.expr, dict[str, Binding]]:
    while isinstance(node, ast.Name):
        bound = _name(node, environment, domains)
        if bound is None:
            break
        node, environment = bound
    return node, environment


class Compiler:
    def __init__(self, graph: Graph, result_kind: str) -> None:
        self.graph = graph
        self.result_kind = result_kind
        self.domains = graph.domains
        self.local_names: dict[str, ast.Assign] = {}

    def primitive(self, block: str, node: ast.expr, env: dict[str, Binding], kind: str) -> str:
        node, env = _resolve(node, env, self.domains)
        if _info(node, env, self.domains).kind != kind:
            raise PythonImportError(f"expected a {kind} expression", node)
        if isinstance(node, ast.Name):
            return "param." + node.id
        if isinstance(node, ast.Constant):
            return self.graph.constant(block, kind, node.value, node)
        if (kind == "int" and isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub)
                and isinstance(node.operand, ast.Constant) and type(node.operand.value) is int):
            return self.graph.constant(block, "int", -node.operand.value, node)
        raise PythonImportError("nested arithmetic needs native result extraction, which this graph profile lacks", node)

    def condition(self, block: str, node: ast.expr, env: dict[str, Binding], yes: str, no: str) -> None:
        node, env = _resolve(node, env, self.domains)
        if _info(node, env, self.domains).kind != "bool":
            raise PythonImportError("condition must be statically Boolean", node)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            self.condition(block, node.operand, env, no, yes)
            return
        if isinstance(node, ast.BoolOp):
            current = block
            for value in node.values[:-1]:
                following = self.graph.new_block()
                if isinstance(node.op, ast.And):
                    self.condition(current, value, env, following, no)
                else:
                    self.condition(current, value, env, yes, following)
                current = following
            self.condition(current, node.values[-1], env, yes, no)
            return
        if isinstance(node, ast.IfExp):
            branch_yes, branch_no = self.graph.new_block(), self.graph.new_block()
            self.condition(block, node.test, env, branch_yes, branch_no)
            self.condition(branch_yes, node.body, env, yes, no)
            self.condition(branch_no, node.orelse, env, yes, no)
            return
        if isinstance(node, ast.Compare):
            left_kind = _info(node.left, env, self.domains).kind
            left = self.primitive(block, node.left, env, left_kind)
            right = self.primitive(block, node.comparators[0], env, left_kind)
            opcode = {ast.Eq: "equal", ast.NotEq: "not_equal", ast.Lt: "less_than",
                      ast.LtE: "less_equal", ast.Gt: "greater_than", ast.GtE: "greater_equal"}[type(node.ops[0])]
            value = self.graph.op(block, opcode, node, left=left, right=right)
        else:
            value = self.primitive(block, node, env, "bool")
        self.graph.terminate(block, {"kind": "cond_branch", "condition": value,
                                     "if_true": yes, "if_false": no})

    def returned(self, block: str, expression: ast.expr, env: dict[str, Binding]) -> None:
        expression, env = _resolve(expression, env, self.domains)
        if _info(expression, env, self.domains).kind != self.result_kind:
            raise PythonImportError("return expression differs from function annotation", expression)
        if isinstance(expression, ast.IfExp):
            yes, no = self.graph.new_block(), self.graph.new_block()
            self.condition(block, expression.test, env, yes, no)
            self.returned(yes, expression.body, env)
            self.returned(no, expression.orelse, env)
            return
        if self.result_kind == "bool" and isinstance(expression, (ast.BoolOp, ast.UnaryOp)):
            yes, no = self.graph.new_block(), self.graph.new_block()
            self.condition(block, expression, env, yes, no)
            self._finish_return(yes, self.graph.constant(yes, "bool", True), expression)
            self._finish_return(no, self.graph.constant(no, "bool", False), expression)
            return
        if self.result_kind == "bool" and isinstance(expression, ast.Compare):
            yes, no = self.graph.new_block(), self.graph.new_block()
            self.condition(block, expression, env, yes, no)
            self._finish_return(yes, self.graph.constant(yes, "bool", True), expression)
            self._finish_return(no, self.graph.constant(no, "bool", False), expression)
            return
        if self.result_kind == "int" and isinstance(expression, ast.BinOp):
            left = self.primitive(block, expression.left, env, "int")
            right = self.primitive(block, expression.right, env, "int")
            opcode = {ast.Add: "int_add_checked", ast.Sub: "int_sub_checked",
                      ast.Mult: "int_mul_checked"}[type(expression.op)]
            value = self.graph.op(block, opcode, expression, left=left, right=right)
        elif self.result_kind == "int" and isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.USub):
            if isinstance(expression.operand, ast.Constant) and expression.operand.value == 2**63:
                plain = self.graph.constant(block, "int", I64_MIN, expression)
                value = self.graph.op(block, "result_ok", expression, operand=plain)
            else:
                operand = self.primitive(block, expression.operand, env, "int")
                value = self.graph.op(block, "int_neg_checked", expression, operand=operand)
        else:
            plain = self.primitive(block, expression, env, self.result_kind)
            value = plain if self.result_kind == "bool" else self.graph.op(
                block, "result_ok", expression, operand=plain)
        self._finish_return(block, value, expression)

    def _finish_return(self, block: str, value: str, node: ast.AST) -> None:
        valid = self.graph.constant(block, "bool", True)
        record = self.graph.op(block, "record_new", node, fields=[valid, value])
        self.graph.terminate(block, {"kind": "return", "value": record})

    def statements(self, block: str, statements: list[ast.stmt], env: dict[str, Binding]) -> None:
        if not statements:
            raise PythonImportError("every reachable path must return")
        head, rest = statements[0], statements[1:]
        if isinstance(head, ast.Return):
            if head.value is None:
                raise PythonImportError("return needs a value", head)
            self.returned(block, head.value, env)
            return
        if isinstance(head, ast.Assign):
            if len(head.targets) != 1 or not isinstance(head.targets[0], ast.Name):
                raise PythonImportError("only single-name local bindings are supported", head)
            name = head.targets[0].id
            previous = self.local_names.get(name)
            if name in self.domains or (previous is not None and previous is not head) or LABEL.fullmatch(name) is None:
                raise PythonImportError("local binding cannot reassign a name", head)
            info = _info(head.value, env, self.domains)
            self.local_names[name] = head
            next_env = dict(env)
            next_env[name] = Binding(head.value, dict(env), info)
            self.statements(block, rest, next_env)
            return
        if isinstance(head, ast.If):
            yes, no = self.graph.new_block(), self.graph.new_block()
            self.condition(block, head.test, env, yes, no)
            self.statements(yes, head.body + rest, dict(env))
            self.statements(no, head.orelse + rest, dict(env))
            return
        raise PythonImportError(f"unsupported statement {type(head).__name__}", head)

    def compile(self, function: ast.FunctionDef) -> dict[str, Any]:
        body = function.body
        if (isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]
        entry = "entry"
        integer_domains = [domain for domain in self.graph.parameters if domain.kind == "int"]
        if integer_domains:
            reject = self.graph.new_block()
            for domain in integer_domains:
                minimum = self.graph.constant(entry, "int", domain.minimum)
                maximum = self.graph.constant(entry, "int", domain.maximum)
                below = self.graph.op(entry, "less_than", left="param." + domain.name, right=minimum)
                above = self.graph.op(entry, "greater_than", left="param." + domain.name, right=maximum)
                outside = self.graph.op(entry, "bool_or", left=below, right=above)
                following = self.graph.new_block()
                self.graph.terminate(entry, {"kind": "cond_branch", "condition": outside,
                                             "if_true": reject, "if_false": following})
                entry = following
            false = self.graph.constant(reject, "bool", False)
            dummy = self.graph.constant(reject, self.result_kind, False if self.result_kind == "bool" else 0)
            value = dummy if self.result_kind == "bool" else self.graph.op(reject, "result_ok", operand=dummy)
            record = self.graph.op(reject, "record_new", fields=[false, value])
            self.graph.terminate(reject, {"kind": "return", "value": record})
        self.statements(entry, body, {})
        return self.graph.finish()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def _backend(command: list[str], action: str) -> dict[str, Any]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    except subprocess.TimeoutExpired as error:
        raise PythonImportError(f"{action} timed out", code="ST_TRANSPORT_FAILED") from error
    if result.returncode:
        try:
            failure = json.loads(result.stderr)
        except (ValueError, KeyError, TypeError):
            raise PythonImportError(f"{action}: native backend returned an unreadable error", code="ST_TRANSPORT_FAILED")
        if not isinstance(failure, dict) or not isinstance(failure.get("code"), str) or not isinstance(failure.get("message"), str):
            raise PythonImportError(f"{action}: native backend returned an unreadable error", code="ST_TRANSPORT_FAILED")
        raise PythonImportError(f"{action}: {failure['message']}", code=failure["code"])
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError) as error:
        raise PythonImportError(f"{action}: native backend returned unreadable JSON", code="ST_TRANSPORT_FAILED") from error


def _cases(parameters: list[Domain], result_kind: str, supplied: list[dict[str, object]]) -> dict[str, object]:
    cases = []
    for case in supplied:
        expected = case["expected"]
        if result_kind == "bool":
            if type(expected) is not bool:
                raise PythonImportError("Boolean expected value must be Boolean", code="ST_INPUT_INVALID")
            native_value: object = expected
        else:
            number = _decimal(expected, f"case {case['id']} expected")
            native_value = {"ok": str(number)}
        native_inputs = [case["inputs"][param.name] if param.kind == "bool"
                         else case["inputs"][param.name] for param in parameters]
        cases.append({"id": case["id"], "input": native_inputs,
                      "expected": {"in_domain": True, "value": native_value}})
    baseline = [supplied[0]["inputs"][param.name] for param in parameters]
    used_ids = {case["id"] for case in cases}
    for index, parameter in enumerate(parameters):
        if parameter.kind != "int":
            continue
        for direction, candidate in (("below", parameter.minimum - 1), ("above", parameter.maximum + 1)):
            if not I64_MIN <= candidate <= I64_MAX:
                continue
            case_id = f"domain-{index}-{direction}"
            suffix = 1
            while case_id in used_ids:
                case_id = f"domain-{index}-{direction}-{suffix}"
                suffix += 1
            used_ids.add(case_id)
            inputs = list(baseline)
            inputs[index] = str(candidate)
            cases.append({"id": case_id, "input": inputs,
                          "expected": {"in_domain": False,
                                       "value": False if result_kind == "bool" else {"ok": "0"}}})
    return {"contract": "sley-tools.graph-cases.v0", "cases": cases}


def import_project(source_path: Path, domain_path: Path, project: Path, tools: Path, core: Path) -> dict[str, object]:
    if project.exists() or project.is_symlink():
        raise PythonImportError("project destination already exists", code="ST_INPUT_INVALID")
    source = _regular_bytes(source_path, MAX_SOURCE_BYTES)
    domain_data = _regular_bytes(domain_path, MAX_DOMAIN_BYTES)
    parameters, supplied = _domain(domain_data)
    function, result_kind = _function(source, parameters)
    program_id = re.sub(r"[^a-z0-9-]", "-", project.name.lower()).strip("-")[:48]
    if not program_id:
        raise PythonImportError("project name cannot form a program ID", code="ST_INPUT_INVALID")
    graph = Graph(program_id, parameters, result_kind)
    recipe = Compiler(graph, result_kind).compile(function)
    case_doc = _cases(parameters, result_kind, supplied)
    graph_bytes = _json_bytes(recipe)
    case_bytes = _json_bytes(case_doc)
    with tempfile.TemporaryDirectory(prefix="sley-python-import-") as scratch:
        spec_file, cases_file = Path(scratch) / "graph.json", Path(scratch) / "cases.json"
        spec_file.write_bytes(graph_bytes)
        cases_file.write_bytes(case_bytes)
        native = _backend([str(tools), "init", str(project), "--graph", str(spec_file),
                           "--tests", str(cases_file), "--core", str(core)], "native graph initialization")
    artifact = project / "artifact/native-graph.json"
    manifest_file = project / "program.json"
    manifest = json.loads(manifest_file.read_text())
    manifest["native_logic"] = "Accepted Sley graph enforces parameter domains and computes the restricted pure function."
    manifest["host_responsibility"] = "Python is used only to parse/import source and to marshal named inputs; native execution does not require Python."
    manifest_file.write_bytes(_json_bytes(manifest))
    _backend([str(tools), "artifact", "export", "--project", str(project),
              "--output", str(artifact), "--bind-manifest"], "native artifact export")
    state = json.loads((project / ".sley-tools/state.json").read_text())
    source_dir = project / "python-source"
    source_dir.mkdir()
    (source_dir / "source.py").write_bytes(source)
    (source_dir / "domain.json").write_bytes(domain_data)
    mapping = {
        "contract": "sley-tools.python-source-map.v1", "profile": PROFILE,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "domain_sha256": hashlib.sha256(domain_data).hexdigest(),
        "initial_recipe_sha256": hashlib.sha256(graph_bytes).hexdigest(),
        "native_function_entity_id": state["function_id"],
        "native_result_type_entity_id": native["program"]["result_type_id"],
        "parameters": [{"name": item.name, "kind": item.kind, "minimum": item.minimum,
                         "maximum": item.maximum} for item in parameters],
        "return_kind": result_kind,
        "operation_spans": [{**span, "entity_id": state["operation_ids"][span["block"]][span["operation"]]}
                            for span in graph.operation_spans],
        "constant_spans": [{**span, "entity_id": state["constant_ids"][span["constant"]]}
                           for span in graph.constant_spans],
    }
    (source_dir / "mapping.json").write_bytes(_json_bytes(mapping))
    return {"contract": "sley-tools.python-import.v1", "profile": PROFILE,
            "project": str(project), "accepted_state_root": native["program"]["entrypoint"]["state_root"],
            "native_artifact": str(artifact), "native_artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "mapping": str(source_dir / "mapping.json"),
            "native_core_test_admission": "NOT_RUN: production native executor unavailable"}


def run_project(project: Path, input_path: Path, tools: Path) -> dict[str, object]:
    mapping = json.loads(_regular_bytes(project / "python-source/mapping.json", MAX_DOMAIN_BYTES))
    if (hashlib.sha256(_regular_bytes(project / "python-source/source.py", MAX_SOURCE_BYTES)).hexdigest()
            != mapping["source_sha256"] or
            hashlib.sha256(_regular_bytes(project / "python-source/domain.json", MAX_DOMAIN_BYTES)).hexdigest()
            != mapping["domain_sha256"]):
        raise PythonImportError("stored Python source or domain differs from its import binding", code="ST_STALE_BASE")
    incoming = json.loads(_regular_bytes(input_path, MAX_DOMAIN_BYTES), object_pairs_hook=_pairs)
    if not isinstance(incoming, dict) or set(incoming) != {entry["name"] for entry in mapping["parameters"]}:
        raise PythonImportError("named inputs must match the imported parameters", code="ST_INPUT_INVALID")
    typed = []
    for item in mapping["parameters"]:
        value = incoming[item["name"]]
        if item["kind"] == "bool":
            if type(value) is not bool:
                raise PythonImportError("Boolean input has wrong type", code="ST_INPUT_INVALID")
            typed.append(value)
        else:
            typed.append(str(_decimal(value, f"input {item['name']}")))
    with tempfile.TemporaryDirectory(prefix="sley-python-run-") as scratch:
        native_input = Path(scratch) / "input.json"
        native_input.write_bytes(_json_bytes(typed))
        native = _backend([str(tools), "run", "--project", str(project), "--input", str(native_input)],
                          "native function execution")
    record = native["result"]
    if type(record) is not dict or type(record.get("in_domain")) is not bool:
        raise PythonImportError("native function returned an invalid record", code="ST_VALIDATION_FAILED")
    if record["in_domain"]:
        if mapping["return_kind"] == "bool":
            if type(record.get("value")) is not bool:
                raise PythonImportError("native Boolean result differs", code="ST_VALIDATION_FAILED")
            value = record["value"]
        else:
            checked = record.get("value")
            if type(checked) is not dict or set(checked) != {"ok"}:
                raise PythonImportError("native checked arithmetic did not succeed", code="ST_VALIDATION_FAILED")
            value = checked["ok"]
    else:
        value = None
    digest = hashlib.sha256(_regular_bytes(project / "build/graph.json", MAX_DOMAIN_BYTES)).hexdigest()
    return {"contract": "sley-tools.python-run.v1", "profile": PROFILE,
            "in_domain": record["in_domain"], "value": value, "native_result": record,
            "accepted_state_root": native["state_root"],
            "source_alignment": "initial" if digest == mapping["initial_recipe_sha256"] else "native_edited"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import restricted pure Python functions as native Sley graphs")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import")
    importer.add_argument("source", type=Path)
    importer.add_argument("domain", type=Path)
    importer.add_argument("project", type=Path)
    importer.add_argument("--tools", required=True, type=Path)
    importer.add_argument("--core", required=True, type=Path)
    runner = commands.add_parser("run")
    runner.add_argument("--project", required=True, type=Path)
    runner.add_argument("--input", required=True, type=Path)
    runner.add_argument("--tools", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            report = import_project(args.source.absolute(), args.domain.absolute(), args.project.absolute(),
                                    args.tools.resolve(), args.core.resolve())
        else:
            report = run_project(args.project.resolve(), args.input.resolve(), args.tools.resolve())
    except (OSError, PythonImportError, ValueError, KeyError, TypeError) as error:
        code = error.code if isinstance(error, PythonImportError) else "ST_INPUT_INVALID"
        print(json.dumps({"code": code, "line": getattr(error, "line", None),
                          "column": getattr(error, "column", None), "message": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
