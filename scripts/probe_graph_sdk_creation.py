#!/usr/bin/env python3
"""Construct and execute fresh native Sley graphs from both SDKs."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/python"))
from sley_tools import GraphBuilder, SleyToolsError, open_runtime  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def host_value(value: object, kind: str) -> bool | int:
    return value if kind == "Bool" else int(value)


def host_cases(recipe: dict, fixture: dict) -> list[dict]:
    cases = []
    for row in fixture["cases"]:
        inputs = {parameter["name"]: host_value(row["input"][index], parameter["value_type"])
                  for index, parameter in enumerate(recipe["parameters"])}
        if recipe["result"]["kind"] == "record":
            expected = {field["name"]: host_value(row["expected"][field["name"]], field["value_type"])
                        for field in recipe["result"]["fields"]}
        else:
            expected = host_value(row["expected"], recipe["result"]["value_type"])
        cases.append({"id": row["id"], "inputs": inputs, "expected": expected})
    return cases


def expect_input_error(operation) -> None:
    try:
        operation()
    except SleyToolsError as error:
        if error.code != "ST_INPUT_INVALID":
            raise RuntimeError(f"wrong graph creation error: {error.code}") from error
    else:
        raise RuntimeError("invalid graph creation was accepted")


def range_builder() -> GraphBuilder:
    builder = GraphBuilder(
        program_id="range-predicate", title="Range Predicate",
        purpose="Decide whether a signed value is within inclusive bounds and explain invalid bounds",
    )
    for name in ("value", "lower", "upper"):
        builder.parameter(name, "SInt64")
    builder.constant("yes", "Bool", True).constant("no", "Bool", False)
    builder.record_result({name: "Bool" for name in
                           ("within_range", "invalid_bounds", "below_lower", "above_upper")})
    builder.block("entry").binary("greater_than", "invalid", "param.lower", "param.upper") \
        .conditional("op.invalid", "invalid_bounds", "valid_bounds")
    builder.block("invalid_bounds").constant_ref("invalid_yes", "yes") \
        .constant_ref("invalid_no", "no") \
        .record("invalid_result", ["op.invalid_no", "op.invalid_yes", "op.invalid_no", "op.invalid_no"]) \
        .return_value("op.invalid_result")
    builder.block("valid_bounds").binary("less_than", "below", "param.value", "param.lower") \
        .binary("greater_than", "above", "param.value", "param.upper") \
        .binary("bool_or", "outside", "op.below", "op.above") \
        .unary("bool_not", "inside", "op.outside") \
        .constant_ref("valid_no", "no") \
        .record("valid_result", ["op.inside", "op.valid_no", "op.below", "op.above"]) \
        .return_value("op.valid_result")
    return builder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "core", "range-spec", "range-cases", "fixed-spec", "fixed-cases",
                 "numeric-spec", "numeric-cases", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    tools, core = args.tools.resolve(), args.core.resolve()
    recipes = {name: getattr(args, f"{name}_spec").resolve() for name in ("range", "fixed", "numeric")}
    fixtures = {name: getattr(args, f"{name}_cases").resolve() for name in ("range", "fixed", "numeric")}
    range_recipe = range_builder().build()
    if range_recipe != json.loads(recipes["range"].read_text()):
        raise RuntimeError("Python fluent builder differs from the checked-in Range Predicate recipe")
    expect_input_error(lambda: range_builder().parameter("value", "Bool"))
    expect_input_error(lambda: GraphBuilder(program_id="bad-id", title="Bad", purpose="Bad")
                       .primitive_result("Bool").block("entry").end().build())
    expect_input_error(lambda: GraphBuilder(program_id="bad-id", title="Bad", purpose="Bad")
                       .parameter("value", []))
    range_fixture = json.loads(fixtures["range"].read_text())
    cases = host_cases(range_recipe, range_fixture)
    with tempfile.TemporaryDirectory(prefix="sley-graph-sdk-creation-") as temporary:
        root = Path(temporary)
        project = root / "python-range"
        with open_runtime(tools=tools, core=core) as runtime:
            expect_input_error(lambda: runtime.create_graph(project=project, recipe=range_recipe,
                                                             cases=[{**cases[0], "inputs": {"value": "0", "lower": -5, "upper": 5}}]))
            if project.exists():
                raise RuntimeError("invalid Python graph input created a project")
            program = runtime.create_graph(project=project, recipe=range_recipe, cases=cases)
            if not program.graph_profile or len(program.state_root) != 64 or len(program.function_id) != 64:
                raise RuntimeError("Python graph creation lost native identity")
            for row in cases:
                observed = program.call("evaluate", inputs=row["inputs"])
                if observed.value != row["expected"] or observed.metadata["state_root"] != program.state_root:
                    raise RuntimeError(f"Python created Range Predicate differs from {row['id']}")
            if program.test()["external_assertions_against_native_execution"] != len(cases):
                raise RuntimeError("Python created graph omitted external assertions")
            python_root = program.state_root
        with open_runtime(tools=tools, core=core) as duplicate_runtime:
            expect_input_error(lambda: duplicate_runtime.create_graph(project=project,
                                                                       recipe=range_recipe, cases=cases))
        node = subprocess.run(
            ["node", str(Path(__file__).with_name("probe_graph_typescript_creation.mjs")),
             f"tools={tools}", f"core={core}", f"fixed_spec={recipes['fixed']}",
             f"fixed_cases={fixtures['fixed']}", f"fixed_project={root / 'typescript-fixed'}",
             f"numeric_spec={recipes['numeric']}", f"numeric_cases={fixtures['numeric']}",
             f"numeric_project={root / 'typescript-numeric'}"],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if node.returncode:
            raise RuntimeError(f"TypeScript graph creation failed: {node.stderr[:500]}")
        typescript = json.loads(node.stdout)
        if typescript.get("contract") != "sley-tools.graph-typescript-creation-probe.v1" or typescript.get("result") != "PASS":
            raise RuntimeError("TypeScript graph creation report differs")
    report = {
        "contract": "sley-tools.graph-sdk-creation-probe.v1", "result": "PASS",
        "tools_binary_sha256": digest(tools),
        "builder_binary_sha256": digest(tools.with_name("sley-tools-native-builder")),
        "core_binary_sha256": digest(core),
        "recipes_sha256": {name: digest(path) for name, path in recipes.items()},
        "cases_sha256": {name: digest(path) for name, path in fixtures.items()},
        "sdk_sources_sha256": {name: digest(path) for name, path in {
            "python_builder": Path(__file__).resolve().parents[1] / "packages/python/sley_tools/graph_builder.py",
            "python_runtime": Path(__file__).resolve().parents[1] / "packages/python/sley_tools/runtime.py",
            "typescript_sdk": Path(__file__).resolve().parents[1] / "packages/typescript/src/index.js",
            "typescript_types": Path(__file__).resolve().parents[1] / "packages/typescript/src/index.d.ts",
            "python_probe": Path(__file__).resolve(),
            "typescript_probe": Path(__file__).with_name("probe_graph_typescript_creation.mjs").resolve(),
        }.items()},
        "python": {"program": "range-predicate", "accepted_root": python_root,
                   "native_cases": len(cases), "fluent_recipe_matched": True,
                   "fluent_rejections": 3, "invalid_input_rejected": True,
                   "existing_project_rejected": True},
        "typescript": typescript,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.output}: three SDK-created native graph projects passed")


if __name__ == "__main__":
    main()
