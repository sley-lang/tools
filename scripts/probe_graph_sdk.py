#!/usr/bin/env python3
"""Verify local Python and TypeScript calls against two accepted native graphs."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/python"))
from sley_tools import SleyToolsError, open_runtime  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def initialize(tools: Path, core: Path, root: Path, spec: Path, cases: Path) -> Path:
    project = root / spec.stem
    result = subprocess.run(
        [str(tools), "init", str(project), "--graph", str(spec), "--tests", str(cases), "--core", str(core)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"native graph init failed: {result.stderr[:400]}")
    if json.loads(result.stdout)["program"]["authoring_profile"] != "graph_v0":
        raise RuntimeError("initialized project is not a native graph")
    return project


def python_profile(tools: Path, core: Path, project: Path, cases: Path) -> int:
    fixture = json.loads(cases.read_text())
    with open_runtime(tools=tools, core=core) as runtime:
        program = runtime.open_project(project)
        if not program.graph_profile:
            raise RuntimeError("Python SDK opened the wrong profile")
        workers = set()
        for row in fixture["cases"]:
            inputs = {
                parameter["name"]: int(row["input"][index]) if parameter["value_type"] != "Bool" else row["input"][index]
                for index, parameter in enumerate(program.graph_parameters)
            }
            observed = program.call("evaluate", inputs=inputs)
            expected = row["expected"]
            if program.graph_result["kind"] == "record":
                expected = {
                    field["name"]: int(expected[field["name"]]) if field["value_type"] != "Bool" else expected[field["name"]]
                    for field in program.graph_result["fields"]
                }
            if observed.value != expected:
                raise RuntimeError(f"Python native graph differs from {row['id']}")
            if observed.metadata["state_root"] != program.state_root or observed.metadata["function_id"] != program.function_id:
                raise RuntimeError("Python native graph result lost accepted identity")
            workers.add(observed.metadata["worker_pid"])
        if len(workers) != 1:
            raise RuntimeError("Python graph worker was not reused")
        if program.test()["external_assertions_against_native_execution"] != len(fixture["cases"]):
            raise RuntimeError("Python graph SDK omitted external assertions")
        first = {
            parameter["name"]: int(fixture["cases"][0]["input"][index])
            for index, parameter in enumerate(program.graph_parameters)
        }
        name = program.graph_parameters[0]["name"]
        for invalid in ({**first, name: "01"}, {**first, "extra": True}, {**first, name: True}):
            try:
                program.call("evaluate", inputs=invalid)
            except SleyToolsError as error:
                if error.code != "ST_INPUT_INVALID":
                    raise RuntimeError(f"wrong Python graph input error: {error.code}") from error
            else:
                raise RuntimeError("Python graph SDK accepted invalid input")
        recipe = project / "build/graph.json"
        original = recipe.read_bytes()
        try:
            recipe.write_bytes(original + b" ")
            try:
                program.call("evaluate", inputs=first)
            except SleyToolsError as error:
                if error.code != "ST_STALE_BASE":
                    raise RuntimeError(f"wrong tampered-recipe error: {error.code}") from error
            else:
                raise RuntimeError("Python graph SDK executed after recipe tampering")
        finally:
            recipe.write_bytes(original)
        for relative in ("program.json", ".sley-tools/state.json",
                         ".sley-tools/status.json", "config.local.json"):
            binding = project / relative
            original = binding.read_bytes()
            try:
                binding.write_bytes(original + b" ")
                try:
                    program.call("evaluate", inputs=first)
                except SleyToolsError as error:
                    if error.code != "ST_STALE_BASE":
                        raise RuntimeError(f"wrong changed-binding error: {error.code}") from error
                else:
                    raise RuntimeError(f"Python graph SDK executed after changing {relative}")
            finally:
                binding.write_bytes(original)
        if program.call("evaluate", inputs=first).metadata["state_root"] != program.state_root:
            raise RuntimeError("restored graph bindings did not resume native execution")
    return len(fixture["cases"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "core", "range-spec", "range-cases", "unsigned-spec", "unsigned-cases",
                 "numeric-spec", "numeric-cases", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    tools, core = args.tools.resolve(), args.core.resolve()
    specs = (args.range_spec.resolve(), args.unsigned_spec.resolve(), args.numeric_spec.resolve())
    cases = (args.range_cases.resolve(), args.unsigned_cases.resolve(), args.numeric_cases.resolve())
    with tempfile.TemporaryDirectory(prefix="sley-graph-sdk-") as temporary:
        root = Path(temporary)
        projects = [initialize(tools, core, root, spec, fixture) for spec, fixture in zip(specs, cases)]
        counts = [python_profile(tools, core, project, fixture) for project, fixture in zip(projects, cases)]
        node = subprocess.run(
            ["node", str(Path(__file__).with_name("probe_graph_typescript.mjs")),
             f"tools={tools}", f"core={core}", f"range_project={projects[0]}", f"range_cases={cases[0]}",
             f"unsigned_project={projects[1]}", f"unsigned_cases={cases[1]}",
             f"numeric_project={projects[2]}", f"numeric_cases={cases[2]}"],
            capture_output=True, text=True, timeout=90, check=False,
        )
        if node.returncode:
            raise RuntimeError(f"TypeScript graph probe failed: {node.stderr[:500]}")
        typed = json.loads(node.stdout)
        if typed.get("result") != "PASS" or [typed.get("range"), typed.get("unsigned"), typed.get("numeric")] != counts:
            raise RuntimeError("TypeScript graph probe did not cover the same native cases")
    report = {
        "contract": "sley-tools.graph-sdk-probe.v1", "result": "PASS",
        "tools_binary_sha256": digest(tools),
        "builder_binary_sha256": digest(tools.with_name("sley-tools-native-builder")),
        "core_binary_sha256": digest(core),
        "recipes_sha256": [digest(spec) for spec in specs],
        "cases_sha256": [digest(fixture) for fixture in cases],
        "python_native_cases": sum(counts), "typescript_native_cases": typed["range"] + typed["unsigned"] + typed["numeric"],
        "record_and_primitive_results": True, "exact_signed_boundaries": True,
        "signed_and_unsigned_record_results": True,
        "one_worker_per_project_and_sdk": True, "typed_input_rejection": True,
        "tampered_recipe_rejected": True,
        "tampered_project_bindings_rejected": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.output}: {sum(counts)} native graph cases per SDK")


if __name__ == "__main__":
    main()
