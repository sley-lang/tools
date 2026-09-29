#!/usr/bin/env python3
"""Reference-check the restricted Schema Importer against accepted native graphs."""

from __future__ import annotations

import argparse
import decimal
import hashlib
import importlib.metadata
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.validators import extend

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages/python"))
from sley_tools import schema_importer as importer  # noqa: E402
from validate_program_manifest import validate  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(*args: str) -> dict:
    environment = os.environ.copy()
    package_root = str(Path(importer.__file__).resolve().parent.parent)
    environment["PYTHONPATH"] = package_root + os.pathsep + environment.get("PYTHONPATH", "")
    result = subprocess.run(args, text=True, capture_output=True, timeout=120, check=False, env=environment)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args[:3])} failed: {result.stderr[-600:]}")
    return json.loads(result.stdout)


def reference(schema: dict, raw: bytes) -> bool:
    """Independent draft-2020-12 oracle with exact Decimal JSON numbers."""
    checker = Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda _checker, value: type(value) is int or (
            isinstance(value, decimal.Decimal) and value.is_finite()
            and value == value.to_integral_value()
        ),
    )
    exact_validator = extend(Draft202012Validator, type_checker=checker)
    value = json.loads(raw, parse_int=decimal.Decimal, parse_float=decimal.Decimal)
    return exact_validator(schema).is_valid(value)


def compare(project: Path, schema: dict, cases: dict[str, bytes], tools: Path) -> list[dict]:
    observations = []
    root = None
    for name, raw in cases.items():
        native = importer.validate_project(project, raw, tools)
        expected = reference(schema, raw)
        if native["valid"] != expected or (native["typed_payload"] is None) == expected:
            raise RuntimeError(f"native/schema reference mismatch: {name}: {native} vs {expected}")
        if root is None:
            root = native["accepted_state_root"]
        elif root != native["accepted_state_root"]:
            raise RuntimeError("schema validator changed accepted root during read-only cases")
        observations.append({"case": name, "valid": expected, "reasons": native["reasons"],
                             "native_value_hash": native["native_value_hash"]})
    return observations


def unsupported() -> list[dict]:
    examples = [
        ({"type": "string"}, "/type"),
        ({"type": "integer", "minimum": 0, "maximum": 10, "pattern": "x"}, "/pattern"),
        ({"type": "integer", "minimum": 1.5, "maximum": 10}, "/minimum"),
        ({"type": "integer", "minimum": 0, "maximum": 2**63}, "/maximum"),
        ({"type": "object", "properties": {"x": {"type": "boolean"}}, "required": [], "additionalProperties": False}, "/required"),
        ({"type": "object", "properties": {"x": {"type": "boolean"}}, "required": ["x"]}, "/additionalProperties"),
        ({"type": "object", "properties": {"x": {"type": "array"}}, "required": ["x"], "additionalProperties": False}, "/properties/x/type"),
        ({"type": "boolean", "$ref": "https://example.test/schema"}, "/$ref"),
        ({"type": "boolean", "$schema": "https://json-schema.org/draft/2019-09/schema"}, "/$schema"),
        (True, ""),
    ]
    reports = []
    for source, pointer in examples:
        try:
            importer.select_schema(importer.parse_json(json.dumps(source).encode()))
        except importer.SchemaImportError as error:
            if error.pointer != pointer:
                raise RuntimeError(f"unsupported schema location differs: {error.pointer} != {pointer}") from error
            reports.append({"pointer": pointer, "message": str(error)})
        else:
            raise RuntimeError(f"unsupported schema was accepted at {pointer}")
    return reports


def parser_failures() -> list[str]:
    examples = {
        "duplicate-key": b'{"count": 1, "count": 2}',
        "malformed": b'{"count":',
        "oversized": b'"' + b'x' * importer.MAX_JSON_BYTES + b'"',
        "too-deep": b'[' * 25 + b'0' + b']' * 25,
        "too-many-keys": json.dumps({str(index): 0 for index in range(65)}).encode(),
    }
    for name, raw in examples.items():
        try:
            importer.parse_json(raw)
        except importer.SchemaImportError:
            continue
        raise RuntimeError(f"external parser accepted {name}")
    for text, expected in {
        "1": (True, True, 1), "1.0": (True, True, 1), "1e0": (True, True, 1),
        "10e-1": (True, True, 1), "1.5": (False, False, 0),
        "9223372036854775808": (True, False, 0),
        "-9223372036854775808": (True, True, -(2**63)),
        "1e1000000": (True, False, 0), "0e-1000000": (True, True, 0),
    }.items():
        if importer.exact_i64(importer.parse_json(text.encode())) != expected:
            raise RuntimeError(f"exact JSON number classification differs for {text}")
    long_integral = ("1" + "0" * 5000 + "e-1").encode()
    if importer.exact_i64(importer.parse_json(long_integral)) != (True, False, 0):
        raise RuntimeError("long integral JSON number was truncated or rejected by host integer conversion")
    return list(examples)


def exact_number_sweep() -> int:
    rng = random.Random(20260927)
    for _ in range(512):
        sign = "-" if rng.randrange(2) else ""
        token = sign + str(rng.randrange(10_000))
        if rng.randrange(2):
            token += "." + str(rng.randrange(1_000_000)).zfill(6)
        if rng.randrange(2):
            token += "e" + str(rng.randrange(-10, 11))
        decimal_value = decimal.Decimal(token)
        integral = decimal_value == decimal_value.to_integral_value()
        fits = integral and importer.I64_MIN <= decimal_value <= importer.I64_MAX
        expected = (integral, fits, int(decimal_value) if fits else 0)
        observed = importer.exact_i64(importer.parse_json(token.encode()))
        if observed != expected:
            raise RuntimeError(f"exact number projection differs from Decimal for {token}")
    return 512


def probe(tools: Path, core: Path, schema_path: Path) -> dict:
    source = schema_path.read_bytes()
    raw_schema = json.loads(source)
    selected = importer.select_schema(importer.parse_json(source))
    reordered = dict(raw_schema)
    reordered["properties"] = dict(reversed(list(raw_schema["properties"].items())))
    reordered["required"] = list(reversed(raw_schema["required"]))
    reordered["title"] = "Annotation changes cannot change native validation"
    selected_reordered = importer.select_schema(importer.parse_json(json.dumps(reordered).encode()))
    first, first_mapping = importer.compile_graph(selected, "same-program")
    second, second_mapping = importer.compile_graph(selected_reordered, "same-program")
    if first != second or first_mapping != second_mapping:
        raise RuntimeError("property order or annotation changed native graph meaning")
    with tempfile.TemporaryDirectory(prefix="sley-schema-probe-") as scratch:
        root = Path(scratch)
        project = root / "device-reading"
        imported = command(str(tools), "import", "schema", str(schema_path), str(project), "--core", str(core))
        rejected_schema = root / "rejected.schema.json"
        rejected_schema.write_text('{"type":"integer","minimum":0,"maximum":10,"pattern":"x"}')
        environment = os.environ.copy()
        package_root = str(Path(importer.__file__).resolve().parent.parent)
        environment["PYTHONPATH"] = package_root + os.pathsep + environment.get("PYTHONPATH", "")
        rejected_project = root / "rejected"
        rejected = subprocess.run(
            [str(tools), "import", "schema", str(rejected_schema), str(rejected_project), "--core", str(core)],
            text=True, capture_output=True, timeout=120, check=False, env=environment,
        )
        diagnostic = json.loads(rejected.stderr) if rejected.stderr else {}
        if (rejected.returncode != 2 or diagnostic.get("code") != "ST_SCHEMA_UNSUPPORTED"
                or not diagnostic.get("message", "").startswith("/pattern:") or rejected_project.exists()):
            raise RuntimeError("unified CLI accepted an unsupported schema or lost its keyword location")
        wrong_core, wrong_core_project = root / "incompatible-core", root / "wrong-core-project"
        wrong_core.write_bytes(b"not the selected Sley core")
        incompatible = subprocess.run(
            [str(tools), "import", "schema", str(schema_path), str(wrong_core_project),
             "--core", str(wrong_core)],
            text=True, capture_output=True, timeout=120, check=False, env=environment,
        )
        incompatibility = json.loads(incompatible.stderr) if incompatible.stderr else {}
        if (incompatible.returncode != 3 or incompatibility.get("code") != "ST_CORE_INCOMPATIBLE"
                or wrong_core_project.exists()):
            raise RuntimeError("Schema Importer lost the selected-core compatibility failure")
        manifest = json.loads((project / "program.json").read_text())
        validate(project / "program.json", Path("schemas/program-v0.schema.json"))
        mapping = json.loads((project / "schema/mapping.json").read_text())
        if ("Accepted Sley graph" not in manifest["native_logic"]
                or "does not decide schema validity" not in manifest["host_responsibility"]):
            raise RuntimeError("generated manifest omits the host/native validation boundary")
        if imported["native_artifact_sha256"] != digest(project / "artifact/native-graph.json"):
            raise RuntimeError("generated artifact digest differs")
        if manifest["entrypoint"]["state_root"] != imported["accepted_state_root"] or manifest["artifact"]["sha256"] != imported["native_artifact_sha256"]:
            raise RuntimeError("generated native artifact is not bound to its accepted manifest")
        if [entry["schema_pointer"] for entry in mapping["properties"]] != ["/properties/active", "/properties/count"]:
            raise RuntimeError("schema property mapping differs")
        count_mapping = next(entry for entry in mapping["properties"] if entry["name"] == "count")
        if (mapping["native_function_entity_id"] != manifest["entrypoint"]["function_id"]
                or mapping["native_result_type_entity_id"] != manifest["result_type_id"]
                or count_mapping["native_bound_entities"]["minimum"] != mapping["native_constant_entity_ids"][count_mapping["minimum_constant"]]
                or count_mapping["native_bound_entities"]["maximum"] != mapping["native_constant_entity_ids"][count_mapping["maximum_constant"]]):
            raise RuntimeError("schema path mapping lost generated native entity identities")
        tested = command(str(tools), "test", "--project", str(project))
        if tested["result"] != "PASS" or tested["external_assertions_against_native_execution"] < 5:
            raise RuntimeError("generated public cases did not run on native graph")
        cases = {
            "valid-min": b'{"active":true,"count":0}',
            "valid-max": b'{"active":false,"count":10}',
            "integral-decimal": b'{"active":true,"count":1.0}',
            "integral-exponent": b'{"active":true,"count":1e0}',
            "missing": b'{"active":true}',
            "extra": b'{"active":true,"count":1,"other":0}',
            "wrong-root": b'[]',
            "boolean-as-integer": b'{"active":true,"count":true}',
            "fractional": b'{"active":true,"count":1.5}',
            "string-as-integer": b'{"active":true,"count":"1"}',
            "wrong-boolean": b'{"active":1,"count":1}',
            "below-min": b'{"active":true,"count":-1}',
            "above-max": b'{"active":true,"count":11}',
            "outside-i64": b'{"active":true,"count":9223372036854775808}',
            "zero-huge-exponent": b'{"active":true,"count":0e1000000}',
        }
        observations = compare(project, raw_schema, cases, tools)
        input_failures = []
        for name, raw, expected_code in (
            ("malformed", b'{"active":', "ST_INPUT_INVALID"),
            ("duplicate-key", b'{"active":true,"count":1,"count":2}', "ST_INPUT_INVALID"),
            ("oversized", b'"' + b'x' * importer.MAX_JSON_BYTES + b'"', "ST_RESOURCE_LIMIT"),
        ):
            bad_input = root / (name + ".json")
            bad_input.write_bytes(raw)
            failure = subprocess.run(
                [sys.executable, "-m", "sley_tools.schema_importer", "validate",
                 "--project", str(project), "--input", str(bad_input), "--tools", str(tools)],
                text=True, capture_output=True, timeout=120, check=False, env=environment,
            )
            diagnostic = json.loads(failure.stderr) if failure.stderr else {}
            if failure.returncode != 2 or diagnostic.get("code") != expected_code:
                raise RuntimeError(f"raw-input failure {name} used the wrong error category")
            input_failures.append({"case": name, "code": expected_code})
        wrong = {entry["case"]: entry["reasons"] for entry in observations}
        if "p1_missing" not in wrong["missing"] or "extra_properties" not in wrong["extra"] or "p1_outside_native_range" not in wrong["outside-i64"]:
            raise RuntimeError("native reason flags omit the relevant property check")
        copied = root / "copied"
        command(str(tools), "artifact", "import", str(project / "artifact/native-graph.json"), str(copied), "--core", str(core))
        accepted = json.loads((copied / "program.json").read_text())["entrypoint"]["state_root"]
        if accepted == imported["accepted_state_root"]:
            raise RuntimeError("imported artifact reused source authority")
        typed = root / "typed.json"
        typed.write_text(json.dumps(importer.envelope(selected, importer.parse_json(cases["valid-max"]))[0]))
        replay = command(str(tools), "run", "--project", str(copied), "--input", str(typed))
        if replay["result"]["valid"] is not True:
            raise RuntimeError("authority-rebased native artifact changed behavior")

        # Normal graph authoring edits the generated validator without reimporting source JSON Schema.
        count_entry = next(entry for entry in mapping["properties"] if entry["name"] == "count")
        value_path, cases_path = root / "new-value.json", root / "edited-cases.json"
        value_path.write_text('"12"\n')
        changed = dict(cases)
        changed["new-max"] = b'{"active":true,"count":12}'
        changed["new-excluded"] = b'{"active":true,"count":13}'
        new_rows = []
        for name in ("new-max", "new-excluded"):
            raw = importer.parse_json(changed[name])
            values = importer.envelope(selected, raw)[0]
            expected = {field["name"]: False for field in first["result"]["fields"]}
            if name == "new-max":
                expected["valid"] = True
            else:
                expected["p1_out_of_bounds"] = True
            new_rows.append({"id": name, "input": values, "expected": expected})
        cases_path.write_text(json.dumps({"contract": "sley-tools.graph-cases.v0", "cases": new_rows}))
        command(str(tools), "propose", "--project", str(project), "--constant", count_entry["maximum_constant"],
                "--value-file", str(value_path), "--tests", str(cases_path))
        command(str(tools), "validate", "--project", str(project))
        committed = command(str(tools), "commit", "--project", str(project))
        command(str(tools), "test", "--project", str(project))
        edited = importer.validate_project(project, changed["new-max"], tools)
        if (edited["valid"] is not True or edited["source_schema_alignment"] != "native_edited"
                or reference(raw_schema, changed["new-max"]) is not False):
            raise RuntimeError("normal native constant edit did not change validator behavior")
        edited_artifact = project / "artifact/edited-native.json"
        command(str(tools), "artifact", "export", "--project", str(project),
                "--output", str(edited_artifact), "--bind-manifest")
        edited_artifact_sha = digest(edited_artifact)
        edited_root = json.loads((project / "program.json").read_text())["entrypoint"]["state_root"]
        if edited_root == imported["accepted_state_root"] or edited_root != edited["accepted_state_root"]:
            raise RuntimeError("native graph edit lost accepted root identity")

        scalar_profiles = []
        for name, schema, scalar_cases in (
            ("boolean", {"type": "boolean"}, {"true": b"true", "false": b"false", "wrong": b"1"}),
            ("integer", {"type": "integer", "minimum": -10, "maximum": 10},
             {"minimum": b"-10", "maximum": b"10", "integral-decimal": b"1.0", "integral-exponent": b"1e0",
              "fraction": b"1.5", "boolean": b"true", "string": b'"1"', "wide-positive": b"9223372036854775808",
              "wide-negative": b"-9223372036854775809"}),
            ("empty-record", {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
             {"empty": b"{}", "extra": b'{"x":1}', "wrong": b"[]"}),
        ):
            path, destination = root / (name + ".schema.json"), root / name
            path.write_text(json.dumps(schema))
            report = command(str(tools), "import", "schema", str(path), str(destination), "--core", str(core))
            results = compare(destination, schema, scalar_cases, tools)
            scalar_profiles.append({"profile": name, "accepted_state_root": report["accepted_state_root"],
                                    "artifact_sha256": report["native_artifact_sha256"], "cases": results})
        maximum_schema = {
            "type": "object", "properties": {
                "__extra__": {"type": "integer", "minimum": 0, "maximum": 10},
                "a/b~c": {"type": "integer", "minimum": 0, "maximum": 10},
                "z": {"type": "integer", "minimum": 0, "maximum": 10},
            }, "required": ["z", "a/b~c", "__extra__"], "additionalProperties": False,
        }
        maximum_path, maximum_project = root / "maximum.schema.json", root / "maximum-record"
        maximum_path.write_text(json.dumps(maximum_schema))
        maximum_report = command(str(tools), "import", "schema", str(maximum_path),
                                 str(maximum_project), "--core", str(core))
        maximum_recipe = json.loads((maximum_project / "build/graph.json").read_text())
        maximum_mapping = json.loads((maximum_project / "schema/mapping.json").read_text())
        if (len(maximum_recipe["parameters"]) != 14 or len(maximum_recipe["result"]["fields"]) != 15
                or "/properties/a~1b~0c" not in [entry["schema_pointer"] for entry in maximum_mapping["properties"]]):
            raise RuntimeError("maximum native record or escaped schema pointer differs")
        maximum_cases = compare(maximum_project, maximum_schema, {
            "valid": b'{"__extra__":0,"a/b~c":10,"z":1}',
            "integral-decimal": b'{"__extra__":0.0,"a/b~c":1e0,"z":1}',
            "missing": b'{"__extra__":0,"a/b~c":10}',
            "extra": b'{"__extra__":0,"a/b~c":10,"z":1,"other":0}',
            "wide": b'{"__extra__":0,"a/b~c":9223372036854775808,"z":1}',
        }, tools)
        maximum_record = {"accepted_state_root": maximum_report["accepted_state_root"],
                          "artifact_sha256": maximum_report["native_artifact_sha256"],
                          "parameter_count": len(maximum_recipe["parameters"]),
                          "result_field_count": len(maximum_recipe["result"]["fields"]),
                          "escaped_pointer": "/properties/a~1b~0c", "cases": maximum_cases}
    return {
        "contract": "sley-tools.schema-importer-probe.v1", "result": "PASS",
        "profile": "json-schema-records-v1", "core_binary_sha256": digest(core),
        "tools_binary_sha256": digest(tools), "schema_sha256": digest(schema_path),
        "importer_sha256": digest(Path(importer.__file__)),
        "probe_sha256": digest(Path(__file__)),
        "requirements_dev_sha256": digest(Path("requirements-dev.lock")),
        "reference": {"library": "jsonschema", "version": importlib.metadata.version("jsonschema"),
                      "draft": "2020-12", "decoder": "json.loads with Decimal for integers and fractions; integer type checker accepts finite integral Decimal"},
        "native": {"source_state_root": imported["accepted_state_root"], "edited_state_root": edited_root,
                   "source_artifact_sha256": imported["native_artifact_sha256"],
                   "edited_artifact_sha256": edited_artifact_sha, "copied_state_root": accepted,
                   "normal_constant_edit": committed.get("contract")},
        "object_cases": observations, "scalar_profiles": scalar_profiles,
        "maximum_record": maximum_record,
        "raw_input_failures": input_failures,
        "incompatible_core_rejected": True,
        "unsupported_schema_locations": unsupported(), "parser_rejections": parser_failures(),
        "exact_number_sweep_cases": exact_number_sweep(),
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--schema", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = probe(args.tools.resolve(), args.core.resolve(), args.schema.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.output}: native schema validators agree with exact draft-2020-12 reference")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"schema importer probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
