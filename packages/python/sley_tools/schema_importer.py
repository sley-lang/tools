"""Restricted JSON Schema importer backed by accepted native Sley graphs.

The parser reports mechanical JSON facts. The generated graph decides whether
those facts satisfy the selected schema. This is the json-schema-records-v1
profile, not a general JSON Schema implementation.
"""

from __future__ import annotations

import argparse
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


MAX_JSON_BYTES = 65_536
MAX_DEPTH = 16
MAX_OBJECT_KEYS = 64
MAX_ARRAY_ITEMS = 64
MAX_PROPERTIES = 3  # 16 native parameters and 16 native result fields.
I64_MIN, I64_MAX = -(2**63), 2**63 - 1
DRAFT = "https://json-schema.org/draft/2020-12/schema"
ANNOTATIONS = {"title", "description", "$id", "$schema"}
NUMBER = re.compile(r"(-?)(0|[1-9][0-9]*)(?:\.([0-9]+))?(?:[eE]([+-]?[0-9]+))?\Z")


@dataclass(frozen=True)
class NumberToken:
    text: str


class SchemaImportError(ValueError):
    def __init__(self, message: str, pointer: str = "") -> None:
        super().__init__(message)
        self.pointer = pointer


class ResourceLimitError(SchemaImportError):
    pass


class NativeBackendError(SchemaImportError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def _backend_failure(action: str, stderr: str) -> NativeBackendError:
    try:
        document = json.loads(stderr)
        code = document["code"]
        detail = document["message"]
        if not isinstance(code, str) or not isinstance(detail, str):
            raise ValueError("backend error fields differ")
        return NativeBackendError(f"{action}: {detail}", code)
    except (ValueError, KeyError, json.JSONDecodeError):
        return NativeBackendError(f"{action}: backend returned an unreadable error", "ST_TRANSPORT_FAILED")


def _pointer(parent: str, part: str) -> str:
    return parent + "/" + part.replace("~", "~0").replace("/", "~1")


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise SchemaImportError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _finite_tree(value: object, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise ResourceLimitError("JSON nesting exceeds the input limit")
    if isinstance(value, dict):
        if len(value) > MAX_OBJECT_KEYS:
            raise ResourceLimitError("JSON object exceeds the key limit")
        for key, child in value.items():
            if any(0xD800 <= ord(char) <= 0xDFFF for char in key):
                raise SchemaImportError("JSON object key contains an unpaired surrogate")
            _finite_tree(child, depth + 1)
    elif isinstance(value, list):
        if len(value) > MAX_ARRAY_ITEMS:
            raise ResourceLimitError("JSON array exceeds the item limit")
        for child in value:
            _finite_tree(child, depth + 1)


def parse_json(data: bytes) -> object:
    if len(data) > MAX_JSON_BYTES:
        raise ResourceLimitError("JSON input exceeds 65536 bytes")
    try:
        value = json.loads(
            data.decode("utf-8"), object_pairs_hook=_pairs,
            parse_int=NumberToken, parse_float=NumberToken,
            parse_constant=lambda token: (_ for _ in ()).throw(
                SchemaImportError(f"non-JSON numeric token {token!r}")),
        )
        _finite_tree(value)
        return value
    except RecursionError as error:
        raise ResourceLimitError("JSON nesting exceeds parser capacity") from error
    except (UnicodeError, json.JSONDecodeError) as error:
        raise SchemaImportError(f"invalid JSON input: {error}") from error


def read_json_file(path: Path) -> bytes:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise SchemaImportError("JSON source must be a regular file")
    if metadata.st_size > MAX_JSON_BYTES:
        raise ResourceLimitError("JSON input exceeds 65536 bytes")
    with path.open("rb") as source:
        data = source.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise ResourceLimitError("JSON input exceeds 65536 bytes")
    return data


def exact_i64(number: NumberToken) -> tuple[bool, bool, int]:
    """Return mathematical integrality, i64 fit, and a safe placeholder/value."""
    match = NUMBER.fullmatch(number.text)
    if match is None:
        raise SchemaImportError("JSON parser returned an invalid number token")
    sign, integer, fraction, exponent = match.groups()
    digits = (integer + (fraction or "")).lstrip("0")
    if not digits:
        return True, True, 0
    exponent = exponent or "0"
    negative_exponent = exponent.startswith("-")
    exponent_digits = exponent.lstrip("+-").lstrip("0") or "0"
    if len(exponent_digits) > 6:
        scale = -1_000_000 if negative_exponent else 1_000_000
    else:
        scale = int(exponent)
    scale -= len(fraction or "")
    if scale < 0:
        removed = -scale
        if removed > len(digits) or digits[-removed:] != "0" * removed:
            return False, False, 0
        digits = digits[:-removed]
    elif len(digits) + scale > 19:
        return True, False, 0
    else:
        digits += "0" * scale
    if len(digits) > 19:
        return True, False, 0
    magnitude = int(digits or "0")
    value = -magnitude if sign else magnitude
    if I64_MIN <= value <= I64_MAX:
        return True, True, value
    return True, False, 0


def _integer_schema(schema: object, pointer: str) -> dict[str, object]:
    if not isinstance(schema, dict):
        raise SchemaImportError("schema must be an object", pointer)
    allowed = ANNOTATIONS | {"type", "minimum", "maximum"}
    for key in schema:
        if key not in allowed:
            raise SchemaImportError(f"unsupported schema keyword {key!r}", _pointer(pointer, key))
    if schema.get("type") != "integer":
        raise SchemaImportError("expected type 'integer'", _pointer(pointer, "type"))
    bounds = []
    for key in ("minimum", "maximum"):
        raw = schema.get(key)
        if not isinstance(raw, NumberToken):
            raise SchemaImportError("integer bound is required as a JSON number", _pointer(pointer, key))
        integral, fits, value = exact_i64(raw)
        if not integral or not fits:
            raise SchemaImportError("integer bound must be integral and fit signed 64 bits", _pointer(pointer, key))
        bounds.append(value)
    if bounds[0] > bounds[1]:
        raise SchemaImportError("minimum exceeds maximum", _pointer(pointer, "minimum"))
    _annotations(schema, pointer)
    return {"type": "integer", "minimum": bounds[0], "maximum": bounds[1]}


def _annotations(schema: dict[str, object], pointer: str) -> None:
    for key in ANNOTATIONS & schema.keys():
        if not isinstance(schema[key], str):
            raise SchemaImportError("annotation must be a string", _pointer(pointer, key))
    if "$schema" in schema and schema["$schema"] != DRAFT:
        raise SchemaImportError("only JSON Schema draft 2020-12 is supported", _pointer(pointer, "$schema"))


def _boolean_schema(schema: object, pointer: str) -> dict[str, object]:
    if not isinstance(schema, dict):
        raise SchemaImportError("schema must be an object; literal true/false schemas are unsupported", pointer)
    for key in schema:
        if key not in ANNOTATIONS | {"type"}:
            raise SchemaImportError(f"unsupported schema keyword {key!r}", _pointer(pointer, key))
    if schema.get("type") != "boolean":
        raise SchemaImportError("expected type 'boolean'", _pointer(pointer, "type"))
    _annotations(schema, pointer)
    return {"type": "boolean"}


def select_schema(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise SchemaImportError("root schema must be an object; literal true/false schemas are unsupported")
    kind = raw.get("type")
    if kind == "integer":
        return _integer_schema(raw, "")
    if kind == "boolean":
        return _boolean_schema(raw, "")
    if kind != "object":
        raise SchemaImportError("root type must be boolean, integer, or object", "/type")
    allowed = ANNOTATIONS | {"type", "properties", "required", "additionalProperties"}
    for key in raw:
        if key not in allowed:
            raise SchemaImportError(f"unsupported schema keyword {key!r}", _pointer("", key))
    _annotations(raw, "")
    properties = raw.get("properties")
    if not isinstance(properties, dict):
        raise SchemaImportError("properties must be an explicit object", "/properties")
    if len(properties) > MAX_PROPERTIES:
        raise SchemaImportError(f"at most {MAX_PROPERTIES} properties fit this native profile", "/properties")
    required = raw.get("required")
    if not isinstance(required, list) or any(not isinstance(name, str) for name in required):
        raise SchemaImportError("required must explicitly list every property", "/required")
    if len(required) != len(properties) or set(required) != set(properties):
        raise SchemaImportError("required must list exactly every property once", "/required")
    if raw.get("additionalProperties") is not False:
        raise SchemaImportError("additionalProperties must explicitly be false", "/additionalProperties")
    selected = {}
    for name in sorted(properties):
        pointer = _pointer("/properties", name)
        item = properties[name]
        if not isinstance(item, dict):
            raise SchemaImportError("property schema must be an object", pointer)
        if item.get("type") == "integer":
            selected[name] = _integer_schema(item, pointer)
        elif item.get("type") == "boolean":
            selected[name] = _boolean_schema(item, pointer)
        else:
            raise SchemaImportError("property type must be boolean or integer", _pointer(pointer, "type"))
    return {"type": "object", "properties": selected}


class Graph:
    def __init__(self, program_id: str) -> None:
        self.recipe: dict[str, Any] = {
            "contract": "sley-tools.graph-authoring.v0", "program_id": program_id,
            "title": "Imported schema validator", "purpose": "Validate a mechanical JSON envelope using native Sley rules",
            "parameters": [], "constants": [], "result": {"kind": "record", "fields": []},
            "entry_block": "entry", "blocks": [{"name": "entry", "operations": [], "terminator": {}}],
        }
        self.ops = self.recipe["blocks"][0]["operations"]
        self.outputs: list[tuple[str, str]] = []

    def param(self, name: str, kind: str) -> str:
        self.recipe["parameters"].append({"name": name, "value_type": kind})
        return "param." + name

    def constant(self, name: str, kind: str, value: str | bool) -> str:
        self.recipe["constants"].append({"name": name, "value_type": kind, "value": value})
        return self.op("constant_ref", name + "_ref", constant=name)

    def op(self, kind: str, name: str, **args: str) -> str:
        self.ops.append({"op": kind, "name": name, **args})
        return "op." + name

    def unary(self, kind: str, name: str, operand: str) -> str:
        return self.op(kind, name, operand=operand)

    def binary(self, kind: str, name: str, left: str, right: str) -> str:
        return self.op(kind, name, left=left, right=right)

    def output(self, name: str, ref: str) -> None:
        self.recipe["result"]["fields"].append({"name": name, "value_type": "Bool"})
        self.outputs.append((name, ref))

    def finish(self) -> dict[str, Any]:
        result = self.op("record_new", "validation_result", fields=[ref for _, ref in self.outputs])
        self.recipe["blocks"][0]["terminator"] = {"kind": "return", "value": result}
        return self.recipe


def compile_graph(selected: dict[str, object], program_id: str) -> tuple[dict[str, Any], dict[str, object]]:
    graph = Graph(program_id)
    mapping: dict[str, object] = {"contract": "sley-tools.schema-mapping.v1", "profile": "json-schema-records-v1", "root_type": selected["type"], "root_schema_pointer": "", "properties": []}
    if selected["type"] == "boolean":
        kind = graph.param("root_is_boolean", "Bool")
        graph.param("boolean_value", "Bool")
        graph.output("valid", kind)
        graph.output("wrong_type", graph.unary("bool_not", "wrong_type", kind))
    elif selected["type"] == "integer":
        _integer_checks(graph, selected, "root", "", mapping)
    else:
        root = graph.param("root_is_object", "Bool")
        extra = graph.param("extra_count", "UInt32")
        zero = graph.constant("zero", "UInt32", "0")
        extra_zero = graph.binary("equal", "extra_zero", extra, zero)
        valid = graph.binary("bool_and", "root_and_extra", root, extra_zero)
        graph.output("wrong_root", graph.unary("bool_not", "wrong_root", root))
        extra_present = graph.unary("bool_not", "extra_present", extra_zero)
        graph.output("extra_properties", graph.binary("bool_and", "extra_properties", root, extra_present))
        for index, (name, item) in enumerate(selected["properties"].items()):
            prefix = f"p{index}"
            pointer = _pointer("/properties", name)
            entry: dict[str, object] = {"name": name, "schema_pointer": pointer, "index": index, "type": item["type"]}
            entry["envelope_parameters"] = ([prefix + "_present", prefix + "_kind", prefix + "_value"]
                                             if item["type"] == "boolean" else
                                             [prefix + "_present", prefix + "_kind", prefix + "_fits", prefix + "_value"])
            entry["reason_flags"] = ([prefix + "_missing", prefix + "_wrong_type"]
                                     if item["type"] == "boolean" else
                                     [prefix + "_missing", prefix + "_wrong_type",
                                      prefix + "_outside_native_range", prefix + "_out_of_bounds"])
            mapping["properties"].append(entry)
            present = graph.param(prefix + "_present", "Bool")
            kind = graph.param(prefix + "_kind", "Bool")
            if item["type"] == "boolean":
                graph.param(prefix + "_value", "Bool")
                good = graph.binary("bool_and", prefix + "_good", present, kind)
                missing = graph.unary("bool_not", prefix + "_absent", present)
                graph.output(prefix + "_missing", graph.binary("bool_and", prefix + "_missing", root, missing))
                wrong = graph.unary("bool_not", prefix + "_not_bool", kind)
                present_wrong = graph.binary("bool_and", prefix + "_present_wrong", present, wrong)
                graph.output(prefix + "_wrong_type", graph.binary("bool_and", prefix + "_wrong_type", root, present_wrong))
            else:
                good = _integer_checks(graph, item, prefix, root, entry, present=present, kind=kind)
            valid = graph.binary("bool_and", prefix + "_all_valid", valid, good)
        graph.outputs.insert(0, ("valid", valid))
        graph.recipe["result"]["fields"].insert(0, {"name": "valid", "value_type": "Bool"})
    recipe = graph.finish()
    mapping["envelope_parameters"] = recipe["parameters"]
    mapping["native_result_flags"] = [field["name"] for field in recipe["result"]["fields"]]
    return recipe, mapping


def _integer_checks(graph: Graph, selected: dict[str, object], prefix: str,
                    root: str, mapping: dict[str, object], *, present: str | None = None,
                    kind: str | None = None) -> str:
    kind = kind or graph.param(prefix + "_kind", "Bool")
    fits = graph.param(prefix + "_fits", "Bool")
    value = graph.param(prefix + "_value", "SInt64")
    lower = graph.constant(prefix + "_minimum", "SInt64", str(selected["minimum"]))
    upper = graph.constant(prefix + "_maximum", "SInt64", str(selected["maximum"]))
    above = graph.binary("greater_equal", prefix + "_above_min", value, lower)
    below = graph.binary("less_equal", prefix + "_below_max", value, upper)
    bounds = graph.binary("bool_and", prefix + "_in_bounds", above, below)
    representable = graph.binary("bool_and", prefix + "_representable", kind, fits)
    good = graph.binary("bool_and", prefix + "_good", representable, bounds)
    mapping["minimum_constant"] = prefix + "_minimum"
    mapping["maximum_constant"] = prefix + "_maximum"
    if present is None:
        graph.output("valid", good)
        graph.output("wrong_type", graph.unary("bool_not", prefix + "_wrong_type", kind))
        no_fit = graph.unary("bool_not", prefix + "_no_fit", fits)
        graph.output("outside_native_range", graph.binary("bool_and", prefix + "_outside", kind, no_fit))
        not_bounds = graph.unary("bool_not", prefix + "_not_bounds", bounds)
        graph.output("out_of_bounds", graph.binary("bool_and", prefix + "_out_of_bounds", representable, not_bounds))
        return good
    good = graph.binary("bool_and", prefix + "_present_good", present, good)
    missing = graph.unary("bool_not", prefix + "_absent", present)
    graph.output(prefix + "_missing", graph.binary("bool_and", prefix + "_missing", root, missing))
    wrong = graph.unary("bool_not", prefix + "_not_integer", kind)
    present_wrong = graph.binary("bool_and", prefix + "_present_wrong", present, wrong)
    graph.output(prefix + "_wrong_type", graph.binary("bool_and", prefix + "_wrong_type", root, present_wrong))
    no_fit = graph.unary("bool_not", prefix + "_no_fit", fits)
    wide = graph.binary("bool_and", prefix + "_wide", kind, no_fit)
    wide = graph.binary("bool_and", prefix + "_wide_present", present, wide)
    graph.output(prefix + "_outside_native_range", graph.binary("bool_and", prefix + "_wide_root", root, wide))
    not_bounds = graph.unary("bool_not", prefix + "_not_bounds", bounds)
    bad_bounds = graph.binary("bool_and", prefix + "_bad_bounds", representable, not_bounds)
    bad_bounds = graph.binary("bool_and", prefix + "_bad_bounds_present", present, bad_bounds)
    graph.output(prefix + "_out_of_bounds", graph.binary("bool_and", prefix + "_bad_bounds_root", root, bad_bounds))
    return good


def envelope(selected: dict[str, object], raw: object) -> tuple[list[bool | str], object | None]:
    if selected["type"] == "boolean":
        correct = type(raw) is bool
        return [correct, raw if correct else False], raw if correct else None
    if selected["type"] == "integer":
        integral, fits, value = exact_i64(raw) if isinstance(raw, NumberToken) else (False, False, 0)
        return [integral, fits, str(value)], value if integral and fits else None
    is_object = isinstance(raw, dict)
    source = raw if is_object else {}
    names = selected["properties"]
    result: list[bool | str] = [is_object, str(len(source.keys() - names.keys()))]
    payload: dict[str, bool | int] = {}
    for name, item in names.items():
        present = name in source
        value = source.get(name)
        if item["type"] == "boolean":
            correct = type(value) is bool
            result.extend((present, correct, value if correct else False))
            if correct:
                payload[name] = value
        else:
            integral, fits, integer = exact_i64(value) if isinstance(value, NumberToken) else (False, False, 0)
            result.extend((present, integral, fits, str(integer)))
            if integral and fits:
                payload[name] = integer
    return result, payload if is_object else None


def _expected(selected: dict[str, object], raw: object, mapping: dict[str, object]) -> dict[str, bool]:
    """Seed public cases only; independent reference verification is separate."""
    names = ["valid"]
    if selected["type"] == "object":
        names += ["wrong_root", "extra_properties"]
        for entry in mapping["properties"]:
            prefix = f"p{entry['index']}"
            names += [prefix + "_missing", prefix + "_wrong_type"]
            if entry["type"] == "integer":
                names += [prefix + "_outside_native_range", prefix + "_out_of_bounds"]
    elif selected["type"] == "boolean":
        names += ["wrong_type"]
    else:
        names += ["wrong_type", "outside_native_range", "out_of_bounds"]
    flags = {name: False for name in names}
    if selected["type"] == "object":
        if not isinstance(raw, dict):
            flags["wrong_root"] = True
        else:
            flags["extra_properties"] = bool(raw.keys() - selected["properties"].keys())
            for entry in mapping["properties"]:
                index, name = entry["index"], entry["name"]
                prefix = f"p{index}"
                if name not in raw:
                    flags[prefix + "_missing"] = True
                elif entry["type"] == "boolean":
                    flags[prefix + "_wrong_type"] = type(raw[name]) is not bool
                else:
                    integral, fits, value = exact_i64(raw[name]) if isinstance(raw[name], NumberToken) else (False, False, 0)
                    flags[prefix + "_wrong_type"] = not integral
                    flags[prefix + "_outside_native_range"] = integral and not fits
                    bounds = selected["properties"][name]
                    flags[prefix + "_out_of_bounds"] = integral and fits and not bounds["minimum"] <= value <= bounds["maximum"]
    elif selected["type"] == "boolean":
        flags["wrong_type"] = type(raw) is not bool
    else:
        integral, fits, value = exact_i64(raw) if isinstance(raw, NumberToken) else (False, False, 0)
        flags["wrong_type"] = not integral
        flags["outside_native_range"] = integral and not fits
        flags["out_of_bounds"] = integral and fits and not selected["minimum"] <= value <= selected["maximum"]
    flags["valid"] = not any(value for name, value in flags.items() if name != "valid")
    return flags


def _cases(selected: dict[str, object], mapping: dict[str, object]) -> dict[str, object]:
    if selected["type"] == "boolean":
        raw_cases = [("true", b"true"), ("false", b"false"), ("wrong-type", b"1")]
    elif selected["type"] == "integer":
        minimum, maximum = selected["minimum"], selected["maximum"]
        raw_cases = [("minimum", str(minimum).encode()), ("maximum", str(maximum).encode()),
                     ("fractional", b"1.5"), ("boolean", b"true")]
    else:
        nominal = {name: item["minimum"] if item["type"] == "integer" else False
                   for name, item in selected["properties"].items()}
        extra_name = "__extra__"
        while extra_name in nominal:
            extra_name += "_"
        raw_cases = [("valid", json.dumps(nominal).encode()), ("extra", json.dumps({**nominal, extra_name: 1}).encode()),
                     ("wrong-root", b"[]")]
        if nominal:
            first = next(iter(nominal))
            raw_cases.append(("missing", json.dumps({key: value for key, value in nominal.items() if key != first}).encode()))
            raw_cases.append(("wrong-type", json.dumps({**nominal, first: "wrong"}).encode()))
    return {"contract": "sley-tools.graph-cases.v0", "cases": [
        {"id": name, "input": envelope(selected, parsed := parse_json(raw))[0],
         "expected": _expected(selected, parsed, mapping)} for name, raw in raw_cases
    ]}


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def import_project(schema_path: Path, project: Path, tools: Path, core: Path) -> dict[str, object]:
    if project.exists() or project.is_symlink():
        raise SchemaImportError("project destination already exists")
    source = read_json_file(schema_path)
    selected = select_schema(parse_json(source))
    program_id = re.sub(r"[^a-z0-9-]", "-", project.name.lower()).strip("-")[:48]
    if not program_id:
        raise SchemaImportError("project name cannot form a program ID")
    graph, mapping = compile_graph(selected, program_id)
    cases = _cases(selected, mapping)
    graph_bytes, case_bytes = _json_bytes(graph), _json_bytes(cases)
    if len(graph_bytes) > MAX_JSON_BYTES:
        raise SchemaImportError("generated graph exceeds the native recipe limit")
    with tempfile.TemporaryDirectory(prefix="sley-schema-import-") as scratch:
        spec_path, cases_path = Path(scratch) / "graph.json", Path(scratch) / "cases.json"
        spec_path.write_bytes(graph_bytes)
        cases_path.write_bytes(case_bytes)
        command = [str(tools), "init", str(project), "--graph", str(spec_path),
                   "--tests", str(cases_path), "--core", str(core)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode:
        raise _backend_failure("native graph initialization failed", result.stderr)
    artifact_path = project / "artifact/native-graph.json"
    exported = subprocess.run(
        [str(tools), "artifact", "export", "--project", str(project),
         "--output", str(artifact_path), "--bind-manifest"],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if exported.returncode:
        raise _backend_failure("native artifact export failed", exported.stderr)
    manifest_path = project / "program.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["native_logic"] = (
        "Accepted Sley graph decides root and property type, requiredness, extra-property, "
        "native integer representability, and declared numeric-bound validity from a typed JSON envelope."
    )
    manifest["host_responsibility"] = (
        "Optional Python Schema Importer parses exact bounded JSON and projects root kind, property presence, "
        "raw kind, extra count, integrality, representability, and safe values; it does not decide schema validity. "
        "Original schema and source-to-entity mapping are stored in schema/."
    )
    manifest_path.write_bytes(_json_bytes(manifest))
    schema_dir = project / "schema"
    schema_dir.mkdir()
    (schema_dir / "source.json").write_bytes(source)
    native = json.loads(result.stdout)
    state = json.loads((project / ".sley-tools/state.json").read_text())
    constant_ids = state["constant_ids"]
    mapping["native_function_entity_id"] = state["function_id"]
    mapping["native_result_type_entity_id"] = native["program"]["result_type_id"]
    mapping["native_constant_entity_ids"] = constant_ids
    for entry in mapping["properties"]:
        if entry["type"] == "integer":
            entry["native_bound_entities"] = {
                "minimum": constant_ids[entry["minimum_constant"]],
                "maximum": constant_ids[entry["maximum_constant"]],
            }
    mapping.update({
        "source_schema_sha256": hashlib.sha256(source).hexdigest(),
        "initial_recipe_sha256": hashlib.sha256(graph_bytes).hexdigest(),
        "host_native_responsibility": "Host parses exact bounded JSON and projects root kind, presence, raw kinds, extra count, integrality and representability; the accepted Sley graph decides requiredness, type, extra-property and numeric-bound validity.",
    })
    (schema_dir / "mapping.json").write_bytes(_json_bytes(mapping))
    return {"contract": "sley-tools.schema-import.v1", "profile": "json-schema-records-v1",
            "project": str(project), "accepted_state_root": native["program"]["entrypoint"]["state_root"],
            "native_artifact": str(artifact_path), "native_artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
            "source_schema_sha256": mapping["source_schema_sha256"],
            "mapping": str(schema_dir / "mapping.json"),
            "native_core_test_admission": "NOT_RUN: production native executor unavailable"}


def validate_project(project: Path, raw_input: bytes, tools: Path) -> dict[str, object]:
    mapping_path, source_path = project / "schema/mapping.json", project / "schema/source.json"
    mapping = json.loads(read_json_file(mapping_path))
    source = read_json_file(source_path)
    if hashlib.sha256(source).hexdigest() != mapping["source_schema_sha256"]:
        raise NativeBackendError("stored source schema differs from its import binding", "ST_STALE_BASE")
    selected = select_schema(parse_json(source))
    raw = parse_json(raw_input)
    arguments, payload = envelope(selected, raw)
    with tempfile.TemporaryDirectory(prefix="sley-schema-run-") as scratch:
        input_path = Path(scratch) / "input.json"
        input_path.write_bytes(_json_bytes(arguments))
        result = subprocess.run([str(tools), "run", "--project", str(project), "--input", str(input_path)],
                                capture_output=True, text=True, timeout=120, check=False)
    if result.returncode:
        raise _backend_failure("native validator execution failed", result.stderr)
    native = json.loads(result.stdout)
    flags = native["result"]
    if type(flags) is not dict or type(flags.get("valid")) is not bool or any(type(value) is not bool for value in flags.values()):
        raise SchemaImportError("native validator returned an invalid result shape")
    recipe_digest = hashlib.sha256(read_json_file(project / "build/graph.json")).hexdigest()
    return {"contract": "sley-tools.schema-validation.v1", "profile": "json-schema-records-v1",
            "valid": flags["valid"], "reasons": [key for key, value in flags.items() if key != "valid" and value],
            "typed_payload": payload if flags["valid"] else None,
            "accepted_state_root": native["state_root"], "native_value_hash": native["value_hash"],
            "source_schema_alignment": "initial" if recipe_digest == mapping["initial_recipe_sha256"] else "native_edited"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import restricted JSON Schema as a native Sley validator; literal true/false schemas are unsupported")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import")
    importer.add_argument("schema", type=Path)
    importer.add_argument("project", type=Path)
    importer.add_argument("--tools", required=True, type=Path)
    importer.add_argument("--core", required=True, type=Path)
    validator = commands.add_parser("validate")
    validator.add_argument("--project", required=True, type=Path)
    validator.add_argument("--input", required=True, type=Path)
    validator.add_argument("--tools", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            report = import_project(args.schema.resolve(), args.project.absolute(), args.tools.resolve(), args.core.resolve())
        else:
            report = validate_project(args.project.resolve(), read_json_file(args.input), args.tools.resolve())
    except (OSError, SchemaImportError, subprocess.TimeoutExpired, ValueError, KeyError) as error:
        pointer = error.pointer if isinstance(error, SchemaImportError) else ""
        code = (error.code if isinstance(error, NativeBackendError)
                else "ST_TRANSPORT_FAILED" if isinstance(error, subprocess.TimeoutExpired)
                else "ST_RESOURCE_LIMIT" if isinstance(error, ResourceLimitError)
                else "ST_SCHEMA_UNSUPPORTED" if args.command == "import" else "ST_INPUT_INVALID")
        print(json.dumps({"code": code,
                          "schema_pointer": pointer, "message": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
