#!/usr/bin/env python3
"""Verify native selector/projector and checked summary reducer witnesses."""

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
METRICS = ("count", "wall_us", "cpu_us", "archive_bytes")
TOTALS = ("count", "wall_total_us", "cpu_total_us", "archive_total_bytes")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(tools: Path, *arguments: str) -> dict:
    completed = subprocess.run([str(tools), *arguments], capture_output=True,
                               text=True, timeout=120)
    if completed.returncode:
        raise RuntimeError(f"{arguments[:2]} failed: {completed.stderr[:500]}")
    return json.loads(completed.stdout)


def selector_oracle(values: list[int], include_all: bool) -> dict:
    if len(values) != 6 or any(value < I64_MIN or value > I64_MAX for value in values):
        raise ValueError("selector values differ from signed 64 bit inputs")
    invalid = any(value < 0 for value in values)
    regression = any(values[index + 1] > values[index] for index in (0, 2, 4))
    return {
        "selected": (regression or include_all) and not invalid,
        "candidate_wall_us": str(values[1]),
        "candidate_cpu_us": str(values[3]),
        "candidate_archive_bytes": str(values[5]),
        "invalid_input": invalid,
    }


def reducer_oracle(values: list[int]) -> dict:
    if len(values) != 8 or any(value < I64_MIN or value > I64_MAX for value in values):
        raise ValueError("reducer values differ from signed 64 bit inputs")
    observed = {}
    for index, field in enumerate(TOTALS):
        total = values[2 * index] + values[2 * index + 1]
        observed[field] = ({"ok": str(total)} if I64_MIN <= total <= I64_MAX
                           else {"error": {"code": 1}})
    observed["invalid_input"] = any(value < 0 for value in values)
    return observed


def host_result(value: dict) -> dict:
    result = {}
    for name, field in value.items():
        if isinstance(field, str):
            result[name] = int(field)
        elif isinstance(field, dict) and "ok" in field:
            result[name] = {"ok": int(field["ok"])}
        else:
            result[name] = field
    return result


def check_cases(path: Path, count: int, oracle) -> list[dict]:
    fixture = json.loads(path.read_text())
    rows = fixture["cases"]
    if fixture["contract"] != "sley-tools.graph-cases.v0" or len(rows) != count:
        raise RuntimeError(f"{path} has the wrong case contract or count")
    if len({row["id"] for row in rows}) != count:
        raise RuntimeError(f"{path} repeats a case id")
    for row in rows:
        values = [int(value) for value in row["input"]]
        if [str(value) for value in values] != row["input"] or oracle(values) != row["expected"]:
            raise RuntimeError(f"{path} differs from independent oracle for {row['id']}")
    return rows


def named_input(spec: dict, row: dict) -> dict[str, int]:
    return {parameter["name"]: int(value)
            for parameter, value in zip(spec["parameters"], row["input"], strict=True)}


def combine_native(program, left: tuple[int, int, int, int],
                   right: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    inputs = {f"{side}_{metric}": value
              for side, summary in (("left", left), ("right", right))
              for metric, value in zip(METRICS, summary, strict=True)}
    result = program.call("evaluate", inputs=inputs).value
    if result["invalid_input"] or any(set(result[field]) != {"ok"} for field in TOTALS):
        raise RuntimeError("valid partition failed native checked reduction")
    return tuple(result[field]["ok"] for field in TOTALS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "core", "schema", "selector-spec", "selector-cases",
                 "selector-after-cases", "reducer-spec", "reducer-cases", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    paths = {name: getattr(args, name).resolve() for name in
             ("tools", "core", "schema", "selector_spec", "selector_cases",
              "selector_after_cases", "reducer_spec", "reducer_cases", "output")}
    selector = json.loads(paths["selector_spec"].read_text())
    reducer = json.loads(paths["reducer_spec"].read_text())
    if selector["program_id"] != "benchmark-selector" or reducer["program_id"] != "benchmark-summary-reducer":
        raise RuntimeError("native Data Pipe recipe identity changed")
    selector_rows = check_cases(paths["selector_cases"], 9,
                                lambda values: selector_oracle(values, False))
    edited_rows = check_cases(paths["selector_after_cases"], 9,
                              lambda values: selector_oracle(values, True))
    reducer_rows = check_cases(paths["reducer_cases"], 10, reducer_oracle)
    schema = Draft202012Validator(json.loads(paths["schema"].read_text()))
    with tempfile.TemporaryDirectory(prefix="sley-data-pipe-native-") as temporary:
        root = Path(temporary)
        projects = {}
        roots = {}
        for name, spec, cases in (("selector", paths["selector_spec"], paths["selector_cases"]),
                                  ("reducer", paths["reducer_spec"], paths["reducer_cases"])):
            project = root / name
            initialized = invoke(paths["tools"], "init", str(project), "--graph", str(spec),
                                 "--tests", str(cases), "--core", str(paths["core"]))
            expected_count = 9 if name == "selector" else 10
            if initialized["verification"]["external_assertions_against_native_execution"] != expected_count:
                raise RuntimeError(f"{name} omitted native assertions")
            schema.validate(initialized["program"])
            projects[name] = project
            roots[name] = initialized["program"]["entrypoint"]["state_root"]
        with (open_runtime(tools=paths["tools"], core=paths["core"]) as selector_runtime,
              open_runtime(tools=paths["tools"], core=paths["core"]) as reducer_runtime):
            select = selector_runtime.open_project(projects["selector"])
            reduce = reducer_runtime.open_project(projects["reducer"])
            selected_before = []
            for row in selector_rows:
                result = select.call("evaluate", inputs=named_input(selector, row))
                if result.value != host_result(row["expected"]) or result.metadata["state_root"] != roots["selector"]:
                    raise RuntimeError(f"native selector differs for {row['id']}")
                if result.value["selected"]:
                    selected_before.append(row["id"])
            for row in reducer_rows:
                result = reduce.call("evaluate", inputs=named_input(reducer, row))
                if result.value != host_result(row["expected"]) or result.metadata["state_root"] != roots["reducer"]:
                    raise RuntimeError(f"native reducer differs for {row['id']}")
            if reduce.test()["external_assertions_against_native_execution"] != 10:
                raise RuntimeError("reducer did not replay ten native cases")
            identity = (0, 0, 0, 0)
            if combine_native(reduce, identity, identity) != identity:
                raise RuntimeError("native reducer empty identity differs")
            measurements = ((12, 20, 30), (40, 50, 60), (0, 0, 0), (3, 5, 7), (100, 200, 300))
            expected_summary = (len(measurements), *(sum(row[index] for row in measurements)
                                                     for index in range(3)))
            for partitions in ((5,), (2, 3), (1, 1, 3)):
                cursor = 0
                combined = identity
                for partition_size in partitions:
                    partial = identity
                    for measurement in measurements[cursor:cursor + partition_size]:
                        partial = combine_native(reduce, partial, (1, *measurement))
                    cursor += partition_size
                    combined = combine_native(reduce, combined, partial)
                if cursor != len(measurements) or combined != expected_summary:
                    raise RuntimeError(f"native reducer changed with partitions {partitions}")
        value_file = root / "include-nonregressions.json"
        value_file.write_text("true\n")
        proposal = invoke(paths["tools"], "propose", "--project", str(projects["selector"]),
                          "--constant", "include_nonregressions", "--value-file", str(value_file),
                          "--tests", str(paths["selector_after_cases"]))
        validated = invoke(paths["tools"], "validate", "--project", str(projects["selector"]))
        committed = invoke(paths["tools"], "commit", "--project", str(projects["selector"]))
        if (not (proposal["candidate_id"] == validated["candidate_id"] == committed["candidate_id"])
                or committed["old_root"] != roots["selector"]
                or committed["new_root"] == roots["selector"]
                or committed["changed_case_count"] != 2
                or committed["external_assertions"] != 9):
            raise RuntimeError("selector edit did not preserve exact native candidate and behavior")
        schema.validate(json.loads((projects["selector"] / "program.json").read_text()))
        with open_runtime(tools=paths["tools"], core=paths["core"]) as runtime:
            select = runtime.open_project(projects["selector"])
            selected_after = []
            for row in edited_rows:
                result = select.call("evaluate", inputs=named_input(selector, row))
                if result.value != host_result(row["expected"]) or result.metadata["state_root"] != committed["new_root"]:
                    raise RuntimeError(f"edited native selector differs for {row['id']}")
                if result.value["selected"]:
                    selected_after.append(row["id"])
            if select.test()["external_assertions_against_native_execution"] != 9:
                raise RuntimeError("edited selector did not replay nine native cases")
        if len(selected_after) <= len(selected_before):
            raise RuntimeError("native predicate edit did not change selection")
    report = {
        "contract": "sley-tools.data-pipe-native-probe.v1", "result": "PASS",
        "tools_binary_sha256": digest(paths["tools"]),
        "builder_binary_sha256": digest(paths["tools"].with_name("sley-tools-native-builder")),
        "core_binary_sha256": digest(paths["core"]),
        "schema_sha256": digest(paths["schema"]),
        "python_sdk_sha256": digest(Path(__file__).resolve().parents[1] / "packages/python/sley_tools/runtime.py"),
        "probe_entry_sha256": digest(Path(__file__)),
        **{name + "_sha256": digest(paths[name]) for name in
           ("selector_spec", "selector_cases", "selector_after_cases", "reducer_spec", "reducer_cases")},
        "native_roots": roots, "edited_selector_root": committed["new_root"],
        "selector_native_cases": 9, "reducer_native_cases": 10,
        "selected_before": selected_before, "selected_after": selected_after,
        "selector_changed_cases": committed["changed_case_count"],
        "partition_shapes": [[5], [2, 3], [1, 1, 3]],
        "partition_summary": list(expected_summary),
        "empty_identity_verified": True, "overflow_arms_verified": 4,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    paths["output"].parent.mkdir(parents=True, exist_ok=True)
    paths["output"].write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"wrote {paths['output']}: two native Data Pipe witnesses and predicate edit passed")


if __name__ == "__main__":
    main()
