"""Fluent host recipe authoring; the Rust builder and Sley core own semantics."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import Any

from .runtime import _graph_raw, _input_error


_LABEL = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_PROGRAM_ID = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
_INPUT_TYPES = {"Bool", "UInt32", "SInt64"}
_RESULT_TYPES = _INPUT_TYPES | {"CheckedSInt64"}
_UNARY = {"bool_not", "int_neg_checked", "result_ok"}
_BINARY = {"bool_and", "bool_or", "equal", "not_equal", "less_than", "less_equal",
           "greater_than", "greater_equal", "int_add_checked", "int_sub_checked", "int_mul_checked"}


def _label(name: str) -> str:
    if type(name) is not str or _LABEL.fullmatch(name) is None:
        raise _input_error("graph label must start with a letter and contain at most 64 ASCII letters, digits, or underscores")
    return name


def _reference(value: str) -> str:
    if type(value) is not str:
        raise _input_error("graph value reference must be a string")
    prefix, separator, name = value.partition(".")
    if separator != "." or prefix not in {"param", "op"}:
        raise _input_error("graph value reference must be param.NAME or op.NAME")
    _label(name)
    return value


class GraphBuilder:
    """Build one bounded recipe; submit its result with Runtime.create_graph."""

    def __init__(self, *, program_id: str, title: str, purpose: str) -> None:
        if (type(program_id) is not str or len(program_id) > 64
                or _PROGRAM_ID.fullmatch(program_id) is None):
            raise _input_error("graph program ID must be lowercase words separated by single hyphens")
        if type(title) is not str or not title.strip() or len(title) > 120:
            raise _input_error("graph title must be 1 through 120 characters")
        if type(purpose) is not str or not purpose.strip() or len(purpose) > 500:
            raise _input_error("graph purpose must be 1 through 500 characters")
        self.program_id = program_id
        self.title = title
        self.purpose = purpose
        self._parameters: list[dict[str, str]] = []
        self._constants: list[dict[str, Any]] = []
        self._blocks: list[dict[str, Any]] = []
        self._named: set[str] = set()
        self._operations: set[str] = set()
        self._result: dict[str, Any] | None = None
        self._entry: str | None = None

    @staticmethod
    def param(name: str) -> str:
        return f"param.{_label(name)}"

    @staticmethod
    def op(name: str) -> str:
        return f"op.{_label(name)}"

    def _named_label(self, name: str) -> None:
        _label(name)
        if name in self._named:
            raise _input_error(f"duplicate graph label: {name}")
        self._named.add(name)

    def parameter(self, name: str, value_type: str) -> GraphBuilder:
        if type(value_type) is not str or value_type not in _INPUT_TYPES:
            raise _input_error("graph parameter type must be Bool, UInt32, or SInt64")
        self._named_label(name)
        self._parameters.append({"name": name, "value_type": value_type})
        return self

    def constant(self, name: str, value_type: str, value: bool | int) -> GraphBuilder:
        if type(value_type) is not str or value_type not in _INPUT_TYPES:
            raise _input_error("graph constant type must be Bool, UInt32, or SInt64")
        _graph_raw(value, value_type)
        self._named_label(name)
        self._constants.append({"name": name, "value_type": value_type, "value": value})
        return self

    def primitive_result(self, value_type: str) -> GraphBuilder:
        if type(value_type) is not str or value_type not in _RESULT_TYPES:
            raise _input_error("unsupported graph primitive result type")
        self._result = {"kind": "primitive", "value_type": value_type}
        return self

    def record_result(self, fields: Mapping[str, str]) -> GraphBuilder:
        if not isinstance(fields, Mapping) or not 1 <= len(fields) <= 16:
            raise _input_error("graph record result needs 1 through 16 named fields")
        result = []
        for name, value_type in fields.items():
            _label(name)
            if type(value_type) is not str or value_type not in _RESULT_TYPES:
                raise _input_error("unsupported graph result field type")
            result.append({"name": name, "value_type": value_type})
        self._result = {"kind": "record", "fields": result}
        return self

    def block(self, name: str) -> GraphBlock:
        self._named_label(name)
        block: dict[str, Any] = {"name": name, "operations": []}
        self._blocks.append(block)
        if self._entry is None:
            self._entry = name
        return GraphBlock(self, block)

    def entry(self, name: str) -> GraphBuilder:
        self._entry = _label(name)
        return self

    def build(self) -> dict[str, Any]:
        if self._result is None or not self._blocks or self._entry not in {block["name"] for block in self._blocks}:
            raise _input_error("graph needs a result and an existing entry block")
        if (len(self._parameters) > 16 or len(self._constants) > 64 or len(self._blocks) > 16
                or sum(len(block["operations"]) for block in self._blocks) > 128):
            raise _input_error("graph exceeds bounded authoring limits")
        if any("terminator" not in block for block in self._blocks):
            raise _input_error("every graph block needs a terminator")
        return copy.deepcopy({
            "contract": "sley-tools.graph-authoring.v0", "program_id": self.program_id,
            "title": self.title, "purpose": self.purpose,
            "parameters": self._parameters, "constants": self._constants,
            "result": self._result, "entry_block": self._entry, "blocks": self._blocks,
        })


class GraphBlock:
    def __init__(self, builder: GraphBuilder, block: dict[str, Any]) -> None:
        self._builder = builder
        self._block = block

    def _operation(self, operation: dict[str, Any]) -> GraphBlock:
        if "terminator" in self._block:
            raise _input_error("cannot add an operation after a block terminator")
        name = _label(operation["name"])
        if name in self._builder._operations:
            raise _input_error(f"duplicate graph operation label: {name}")
        self._builder._operations.add(name)
        self._block["operations"].append(operation)
        return self

    def constant_ref(self, name: str, constant: str) -> GraphBlock:
        if constant not in {item["name"] for item in self._builder._constants}:
            raise _input_error(f"unknown graph constant: {constant}")
        return self._operation({"op": "constant_ref", "name": name, "constant": constant})

    def unary(self, opcode: str, name: str, operand: str) -> GraphBlock:
        if type(opcode) is not str or opcode not in _UNARY:
            raise _input_error("unsupported unary graph operation")
        return self._operation({"op": opcode, "name": name, "operand": _reference(operand)})

    def binary(self, opcode: str, name: str, left: str, right: str) -> GraphBlock:
        if type(opcode) is not str or opcode not in _BINARY:
            raise _input_error("unsupported binary graph operation")
        return self._operation({"op": opcode, "name": name,
                                "left": _reference(left), "right": _reference(right)})

    def record(self, name: str, fields: list[str]) -> GraphBlock:
        if not isinstance(fields, list) or not 1 <= len(fields) <= 16:
            raise _input_error("graph record operation needs 1 through 16 fields")
        return self._operation({"op": "record_new", "name": name,
                                "fields": [_reference(field) for field in fields]})

    def _terminate(self, value: dict[str, Any]) -> GraphBlock:
        if "terminator" in self._block:
            raise _input_error("graph block already has a terminator")
        self._block["terminator"] = value
        return self

    def return_value(self, value: str) -> GraphBlock:
        return self._terminate({"kind": "return", "value": _reference(value)})

    def jump(self, target: str) -> GraphBlock:
        return self._terminate({"kind": "branch", "target": _label(target)})

    def conditional(self, condition: str, if_true: str, if_false: str) -> GraphBlock:
        return self._terminate({"kind": "cond_branch", "condition": _reference(condition),
                                "if_true": _label(if_true), "if_false": _label(if_false)})

    def end(self) -> GraphBuilder:
        return self._builder
