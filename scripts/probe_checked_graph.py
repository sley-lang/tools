#!/usr/bin/env python3
"""Verify native checked benchmark deltas through CLI, Python, and TypeScript."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/python"))
from sley_tools import open_runtime  # noqa: E402


I64_MIN = -(1 << 63)
I64_MAX = (1 << 63) - 1
METRICS = ("wall", "cpu", "archive")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def oracle(values: list[int]) -> dict:
    if len(values) != 6 or any(value < I64_MIN or value > I64_MAX for value in values):
        raise ValueError("comparator input is outside its declared signed domain")
    result = {}
    regression = []
    for index, metric in enumerate(METRICS):
        baseline, candidate = values[2 * index:2 * index + 2]
        delta = candidate - baseline
        suffix = "bytes" if metric == "archive" else "us"
        result[f"{metric}_delta_{suffix}"] = (
            {"ok": str(delta)} if I64_MIN <= delta <= I64_MAX else {"error": {"code": 1}}
        )
        changed = candidate > baseline
        result[f"{metric}_regressed"] = changed
        regression.append(changed)
    result["any_regression"] = any(regression)
    result["invalid_input"] = any(value < 0 for value in values)
    return result


def host_expected(expected: dict) -> dict:
    return {name: ({"ok": int(value["ok"])} if isinstance(value, dict) and "ok" in value
                   else value) for name, value in expected.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "core", "spec", "cases", "schema", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    tools, core = args.tools.resolve(), args.core.resolve()
    spec, cases_path, schema = args.spec.resolve(), args.cases.resolve(), args.schema.resolve()
    recipe, fixture = json.loads(spec.read_text()), json.loads(cases_path.read_text())
    if recipe["program_id"] != "benchmark-comparator" or len(recipe["parameters"]) != 6:
        raise RuntimeError("benchmark comparator recipe changed its declared responsibility")
    rows = fixture["cases"]
    if len(rows) != 9 or len({row["id"] for row in rows}) != 9:
        raise RuntimeError("comparator fixture omitted or repeated a case")
    for row in rows:
        values = [int(value) for value in row["input"]]
        if [str(value) for value in values] != row["input"] or oracle(values) != row["expected"]:
            raise RuntimeError(f"comparator fixture differs from independent integer oracle: {row['id']}")
    if not any(row["expected"]["wall_delta_us"] == {"error": {"code": 1}} for row in rows):
        raise RuntimeError("comparator fixture does not exercise native arithmetic overflow")
    host_rows = [{"id": row["id"],
                  "inputs": {parameter["name"]: int(value)
                             for parameter, value in zip(recipe["parameters"], row["input"], strict=True)},
                  "expected": host_expected(row["expected"])} for row in rows]
    recipe["constants"][0]["value"] = 0
    with tempfile.TemporaryDirectory(prefix="sley-checked-graph-") as temporary:
        root = Path(temporary)
        cli_project = root / "cli"
        result = subprocess.run([str(tools), "init", str(cli_project), "--graph", str(spec),
                                 "--tests", str(cases_path), "--core", str(core)],
                                capture_output=True, text=True, timeout=120, check=True)
        created = json.loads(result.stdout)
        cli_root = created["program"]["entrypoint"]["state_root"]
        if created["verification"]["external_assertions_against_native_execution"] != 9:
            raise RuntimeError("CLI native comparator omitted cases")
        Draft202012Validator(json.loads(schema.read_text())).validate(created["program"])
        missing_path = root / "missing-measurement.json"
        missing_path.write_text(json.dumps(rows[0]["input"][:-1]) + "\n")
        missing = subprocess.run([str(tools), "run", "--project", str(cli_project),
                                  "--input", str(missing_path)], capture_output=True,
                                 text=True, timeout=120)
        if missing.returncode == 0 or json.loads(missing.stderr).get("code") != "ST_INPUT_INVALID":
            raise RuntimeError("missing benchmark measurement was not rejected")
        with open_runtime(tools=tools, core=core) as runtime:
            program = runtime.create_graph(project=root / "python", recipe=recipe, cases=host_rows)
            python_root = program.state_root
            for row in host_rows:
                observed = program.call("evaluate", inputs=row["inputs"])
                if observed.value != row["expected"] or observed.metadata["state_root"] != python_root:
                    raise RuntimeError(f"Python native comparator differs from {row['id']}")
            if program.test()["external_assertions_against_native_execution"] != 9:
                raise RuntimeError("Python native comparator omitted cases")
        node = subprocess.run(["node", str(Path(__file__).with_name("probe_checked_graph_typescript.mjs")),
                               f"tools={tools}", f"core={core}", f"spec={spec}",
                               f"cases={cases_path}", f"project={root / 'typescript'}"],
                              capture_output=True, text=True, timeout=120, check=True)
        typed = json.loads(node.stdout)
        if typed.get("contract") != "sley-tools.checked-graph-typescript-probe.v1" or typed.get("result") != "PASS":
            raise RuntimeError("TypeScript comparator probe did not pass")
        add_recipe = {
            "contract": "sley-tools.graph-authoring.v0", "program_id": "checked-sum",
            "title": "Checked Sum", "purpose": "Exercise exact native checked addition",
            "parameters": [{"name": "left", "value_type": "SInt64"},
                           {"name": "right", "value_type": "SInt64"}],
            "constants": [], "result": {"kind": "primitive", "value_type": "CheckedSInt64"},
            "entry_block": "entry", "blocks": [{"name": "entry", "operations": [
                {"op": "int_add_checked", "name": "sum", "left": "param.left", "right": "param.right"}],
                "terminator": {"kind": "return", "value": "op.sum"}}],
        }
        add_cases = {"contract": "sley-tools.graph-cases.v0", "cases": [
            {"id": "ordinary", "input": ["1", "2"], "expected": {"ok": "3"}},
            {"id": "positive-overflow", "input": [str(I64_MAX), "1"],
             "expected": {"error": {"code": 1}}},
            {"id": "negative-overflow", "input": [str(I64_MIN), "-1"],
             "expected": {"error": {"code": 1}}},
        ]}
        add_spec_path, add_cases_path = root / "checked-add.json", root / "checked-add-cases.json"
        add_spec_path.write_text(json.dumps(add_recipe) + "\n")
        add_cases_path.write_text(json.dumps(add_cases) + "\n")
        addition = subprocess.run([str(tools), "init", str(root / "checked-add"),
                                   "--graph", str(add_spec_path), "--tests", str(add_cases_path),
                                   "--core", str(core)], capture_output=True, text=True,
                                  timeout=120, check=True)
        added = json.loads(addition.stdout)
        if added["verification"]["external_assertions_against_native_execution"] != 3:
            raise RuntimeError("native checked addition omitted overflow cases")
        Draft202012Validator(json.loads(schema.read_text())).validate(added["program"])
        add_root = added["program"]["entrypoint"]["state_root"]
        advanced_recipe = {
            "contract": "sley-tools.graph-authoring.v0", "program_id": "checked-arithmetic-ops",
            "title": "Checked arithmetic operations",
            "purpose": "Verify native checked multiplication, negation, and result construction",
            "parameters": [{"name": "value", "value_type": "SInt64"}],
            "constants": [],
            "result": {"kind": "record", "fields": [
                {"name": "product", "value_type": "CheckedSInt64"},
                {"name": "negative", "value_type": "CheckedSInt64"},
                {"name": "wrapped", "value_type": "CheckedSInt64"},
            ]},
            "entry_block": "entry", "blocks": [{"name": "entry", "operations": [
                {"op": "int_mul_checked", "name": "product", "left": "param.value", "right": "param.value"},
                {"op": "int_neg_checked", "name": "negative", "operand": "param.value"},
                {"op": "result_ok", "name": "wrapped", "operand": "param.value"},
                {"op": "record_new", "name": "answer", "fields": ["op.product", "op.negative", "op.wrapped"]},
            ], "terminator": {"kind": "return", "value": "op.answer"}}],
        }
        overflow = {"error": {"code": 1}}
        advanced_cases = {"contract": "sley-tools.graph-cases.v0", "cases": [
            {"id": "zero", "input": ["0"], "expected": {
                "product": {"ok": "0"}, "negative": {"ok": "0"}, "wrapped": {"ok": "0"}}},
            {"id": "ordinary", "input": ["3"], "expected": {
                "product": {"ok": "9"}, "negative": {"ok": "-3"}, "wrapped": {"ok": "3"}}},
            {"id": "positive-maximum", "input": [str(I64_MAX)], "expected": {
                "product": overflow, "negative": {"ok": str(-I64_MAX)}, "wrapped": {"ok": str(I64_MAX)}}},
            {"id": "negative-minimum", "input": [str(I64_MIN)], "expected": {
                "product": overflow, "negative": overflow, "wrapped": {"ok": str(I64_MIN)}}},
        ]}
        advanced_spec_path = root / "checked-arithmetic.json"
        advanced_cases_path = root / "checked-arithmetic-cases.json"
        advanced_spec_path.write_text(json.dumps(advanced_recipe) + "\n")
        advanced_cases_path.write_text(json.dumps(advanced_cases) + "\n")
        advanced = subprocess.run([str(tools), "init", str(root / "checked-arithmetic"),
                                   "--graph", str(advanced_spec_path), "--tests", str(advanced_cases_path),
                                   "--core", str(core)], capture_output=True, text=True,
                                  timeout=120, check=True)
        advanced_created = json.loads(advanced.stdout)
        if advanced_created["verification"]["external_assertions_against_native_execution"] != 4:
            raise RuntimeError("native checked operations omitted boundary cases")
        Draft202012Validator(json.loads(schema.read_text())).validate(advanced_created["program"])
        advanced_root = advanced_created["program"]["entrypoint"]["state_root"]
        advanced_state = json.loads((root / "checked-arithmetic/.sley-tools/state.json").read_text())
        operations = advanced_state["operation_ids"]["entry"]
        if (set(operations) != {"product", "negative", "wrapped", "answer"}
                or any(not isinstance(entity, str) or len(entity) != 64 for entity in operations.values())):
            raise RuntimeError("native operation identities were not preserved for source mapping")
        wrong_type = json.loads(json.dumps(advanced_recipe))
        wrong_type["parameters"][0]["value_type"] = "Bool"
        wrong_type_path = root / "wrong-checked-type.json"
        wrong_type_path.write_text(json.dumps(wrong_type))
        wrong_cases = json.loads(json.dumps(advanced_cases))
        for row in wrong_cases["cases"]:
            row["input"] = [False]
        wrong_cases_path = root / "wrong-checked-type-cases.json"
        wrong_cases_path.write_text(json.dumps(wrong_cases))
        refused = subprocess.run([str(tools), "init", str(root / "wrong-checked-type"),
                                  "--graph", str(wrong_type_path), "--tests", str(wrong_cases_path),
                                  "--core", str(core)], capture_output=True, text=True,
                                 timeout=120, check=False)
        if refused.returncode == 0 or json.loads(refused.stderr).get("code") != "ST_INPUT_INVALID":
            raise RuntimeError("checked arithmetic accepted a Boolean operand")
    report = {
        "contract": "sley-tools.checked-graph-probe.v1", "result": "PASS",
        "tools_binary_sha256": digest(tools),
        "builder_binary_sha256": digest(tools.with_name("sley-tools-native-builder")),
        "core_binary_sha256": digest(core),
        "spec_sha256": digest(spec), "cases_sha256": digest(cases_path),
        "schema_sha256": digest(schema),
        "python_sdk_sha256": digest(Path(__file__).resolve().parents[1] / "packages/python/sley_tools/runtime.py"),
        "typescript_sdk_sha256": digest(Path(__file__).resolve().parents[1] / "packages/typescript/src/index.js"),
        "typescript_probe_sha256": digest(Path(__file__).with_name("probe_checked_graph_typescript.mjs")),
        "probe_entry_sha256": digest(Path(__file__)),
        "oracle_verified_cases": len(rows), "checked_overflow_observed": True,
        "checked_add_native_cases": 3, "checked_add_root": add_root,
        "checked_mul_neg_result_native_cases": 4, "checked_mul_neg_result_root": advanced_root,
        "checked_operation_entities_verified": True,
        "invalid_negative_flag_observed": True, "missing_measurement_rejected": True,
        "native_roots": {"cli": cli_root, "python": python_root, "typescript": typed["accepted_root"]},
        "native_cases_by_adapter": {"cli": 9, "python": 9, "typescript": typed["native_cases"]},
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.output}: exact native comparator passed 9 cases through three adapters")


if __name__ == "__main__":
    main()
