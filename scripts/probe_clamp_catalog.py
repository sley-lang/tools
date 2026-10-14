#!/usr/bin/env python3
"""Verify N08's native clamp package, bound edits, and portable replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

from validate_program_manifest import validate


I64_MIN, I64_MAX = -(1 << 63), (1 << 63) - 1
FIXTURE_VALUES = [I64_MIN, -6, -5, -4, -1, 0, 1, 4, 5, 6, 7, I64_MAX]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(tools: Path, *args: object, reject: str | None = None) -> dict:
    command = [str(tools), *map(str, args)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if reject is not None:
        if result.returncode == 0:
            raise RuntimeError(f"invalid native clamp call succeeded: {command[1:3]}")
        failure = json.loads(result.stderr)
        if failure.get("code") != reject:
            raise RuntimeError(f"native clamp rejection differed: {failure}")
        return failure
    if result.returncode:
        raise RuntimeError(f"native clamp command failed: {result.stderr[:500]}")
    return json.loads(result.stdout)


def clamp_oracle(value: int, lower: int, upper: int) -> dict[str, object]:
    if not all(I64_MIN <= item <= I64_MAX for item in (value, lower, upper)):
        raise ValueError("clamp oracle input exceeds signed 64 bit")
    if lower > upper:
        return {"clamped_value": "0", "invalid_bounds": True,
                "below_lower": False, "above_upper": False}
    return {"clamped_value": str(max(lower, min(value, upper))),
            "invalid_bounds": False, "below_lower": value < lower,
            "above_upper": value > upper}


def fixture(path: Path, lower: int, upper: int) -> list[dict]:
    document = json.loads(path.read_text())
    rows = document["cases"]
    if document["contract"] != "sley-tools.graph-cases.v0" or len(rows) != len(FIXTURE_VALUES):
        raise RuntimeError("N08 fixture contract or case count differs")
    if len({row["id"] for row in rows}) != len(rows):
        raise RuntimeError("N08 fixture repeats a case ID")
    for row, value in zip(rows, FIXTURE_VALUES, strict=True):
        if row["input"] != [str(value)] or row["expected"] != clamp_oracle(value, lower, upper):
            raise RuntimeError(f"N08 fixture differs from independent oracle at {value}")
    return rows


def check_runs(tools: Path, project: Path, scratch: Path, values: list[int],
               lower: int, upper: int) -> None:
    input_file = scratch / "input.json"
    for value in values:
        input_file.write_text(json.dumps([str(value)]) + "\n")
        observed = invoke(tools, "run", "--project", project, "--input", input_file)
        if observed["result"] != clamp_oracle(value, lower, upper):
            raise RuntimeError(f"native N08 result differs at {value}, bounds {lower}, {upper}")


def edit(tools: Path, project: Path, scratch: Path, constant: str, value: int,
         cases: Path, minimum_changed: int) -> dict:
    value_file = scratch / f"{constant}-{value}.json"
    value_file.write_text(json.dumps(str(value)) + "\n")
    before = invoke(tools, "inspect", "--project", project)["accepted_state_root"]
    proposal = invoke(tools, "propose", "--project", project, "--constant", constant,
                      "--value-file", value_file, "--tests", cases)
    after_proposal = invoke(tools, "inspect", "--project", project)["accepted_state_root"]
    validated = invoke(tools, "validate", "--project", project)
    after_validation = invoke(tools, "inspect", "--project", project)["accepted_state_root"]
    committed = invoke(tools, "commit", "--project", project)
    tested = invoke(tools, "test", "--project", project)
    if (before != after_proposal or before != after_validation
            or proposal["base_root"] != before
            or not proposal["candidate_id"] == validated["candidate_id"] == committed["candidate_id"]
            or committed["old_root"] != before or committed["new_root"] == before
            or committed["changed_case_count"] < minimum_changed or tested["result"] != "PASS"):
        raise RuntimeError(f"N08 {constant} edit did not follow the native lifecycle")
    return committed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "core", "schema", "catalog", "package", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    paths = {name: getattr(args, name).resolve() for name in
             ("tools", "core", "schema", "catalog", "package", "output")}
    root = Path(__file__).resolve().parents[1]
    package = paths["package"]
    manifest_path = package / "program.json"
    artifact_path = package / "artifact/native-graph.json"
    recipe_path = package / "build/graph.json"
    initial_cases = package / "tests/cases.json"
    upper_cases = package / "tests/upper-6-cases.json"
    invalid_cases = package / "tests/invalid-order-cases.json"
    example = root / "packages/python/examples/bounded_clamp.py"
    if validate(manifest_path, paths["schema"])["result"] != "PASS":
        raise RuntimeError("N08 manifest does not validate")
    manifest = json.loads(manifest_path.read_text())
    artifact = json.loads(artifact_path.read_text())
    recipe = json.loads(recipe_path.read_text())
    catalog = json.loads(paths["catalog"].read_text())
    entry = next((item for item in catalog["programs"] if item["id"] == "N08"), None)
    relative = package.relative_to(root).as_posix()
    constants = {row["name"]: row["value"] for row in recipe["constants"]}
    if (manifest["program_id"] != "bounded-clamp"
            or manifest["status"] != "portable_native_artifact"
            or artifact["contract"] != "sley-tools.native-graph-artifact.v1"
            or artifact["source_state_root"] != manifest["entrypoint"]["state_root"]
            or artifact["source_function_id"] != manifest["entrypoint"]["function_id"]
            or constants.get("lower_bound") != "-5" or constants.get("upper_bound") != "5"
            or any(name not in artifact["source_constant_ids"] for name in ("lower_bound", "upper_bound"))
            or manifest["worked_change"] != {
                "operation": "replace_named_constant", "constant": "upper_bound",
                "initial_value": "5", "example_value": "6",
                "evidence": "evidence/catalog/bounded-clamp-v2.json"}
            or entry is None or entry["program_id"] != "bounded-clamp"
            or entry["package"] != relative
            or entry["artifact"] != f"{relative}/artifact/native-graph.json"
            or entry["integration_examples"] != ["packages/python/examples/bounded_clamp.py"]
            or entry["verified_behavior"] != "evidence/catalog/bounded-clamp-v2.json"
            or entry["worked_modification"] != "evidence/catalog/bounded-clamp-v2.json"):
        raise RuntimeError("N08 package metadata or native binding differs")
    fixture(initial_cases, -5, 5)
    fixture(upper_cases, -5, 6)
    fixture(invalid_cases, 7, 6)
    randomizer = random.Random(20260927)
    matrix = sorted(set(range(-10, 11)) | {I64_MIN, I64_MAX}
                    | {randomizer.randrange(-1000, 1001) for _ in range(16)})
    with tempfile.TemporaryDirectory(prefix="sley-clamp-catalog-") as temporary:
        scratch = Path(temporary)
        authored_project = scratch / "authored"
        authored = invoke(paths["tools"], "init", authored_project,
                          "--graph", recipe_path, "--tests", initial_cases,
                          "--core", paths["core"])
        if (authored["verification"]["external_assertions_against_native_execution"] != len(FIXTURE_VALUES)
                or invoke(paths["tools"], "test", "--project", authored_project)["result"] != "PASS"):
            raise RuntimeError("N08 checked-in recipe did not author a fresh accepted native graph")
        project = scratch / "clamp"
        imported = invoke(paths["tools"], "artifact", "import", artifact_path,
                          project, "--core", paths["core"])
        initial_root = imported["program"]["entrypoint"]["state_root"]
        if (initial_root == artifact["source_state_root"]
                or imported["verification"]["external_assertions_against_native_execution"] != len(FIXTURE_VALUES)
                or invoke(paths["tools"], "test", "--project", project)["result"] != "PASS"):
            raise RuntimeError("N08 artifact import did not rebase and test")
        check_runs(paths["tools"], project, scratch, matrix, -5, 5)
        wrong = scratch / "wrong.json"
        for contents in ("[]\n", "[true]\n", '["9223372036854775808"]\n'):
            wrong.write_text(contents)
            invoke(paths["tools"], "run", "--project", project, "--input", wrong,
                   reject="ST_INPUT_INVALID")
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(root / "packages/python")
        sdk = subprocess.run([sys.executable, str(example), "--tools", str(paths["tools"]),
                              "--core", str(paths["core"]), "--artifact", str(artifact_path),
                              "--value", "6"], capture_output=True, text=True,
                             timeout=120, env=environment)
        if sdk.returncode:
            raise RuntimeError(f"N08 Python integration example failed: {sdk.stderr[:300]}")
        sdk_output = json.loads(sdk.stdout)
        expected_sdk = {**clamp_oracle(6, -5, 5), "clamped_value": 5}
        if sdk_output["result"] != expected_sdk or len(sdk_output["state_root"]) != 64:
            raise RuntimeError("N08 Python integration example returned the wrong typed value")
        upper_edit = edit(paths["tools"], project, scratch, "upper_bound", 6, upper_cases, 2)
        check_runs(paths["tools"], project, scratch, matrix, -5, 6)
        if any(clamp_oracle(value, -5, 5) != clamp_oracle(value, -5, 6)
               for value in (I64_MIN, -6, -5, -1, 0, 4, 5)):
            raise RuntimeError("N08 upper-bound edit changed unrelated values")
        invalid_edit = edit(paths["tools"], project, scratch, "lower_bound", 7, invalid_cases, 2)
        check_runs(paths["tools"], project, scratch, matrix, 7, 6)
        restore_lower = edit(paths["tools"], project, scratch, "lower_bound", -5, upper_cases, 2)
        restore_upper = edit(paths["tools"], project, scratch, "upper_bound", 5, initial_cases, 2)
        if restore_upper["new_root"] != initial_root:
            raise RuntimeError("N08 restored bounds did not recover the original native graph")
        check_runs(paths["tools"], project, scratch, matrix, -5, 5)
        corrected_artifact = scratch / "corrected-native-graph.json"
        invoke(paths["tools"], "artifact", "export", "--project", project,
               "--output", corrected_artifact)
        replay_project = scratch / "replayed"
        replay = invoke(paths["tools"], "artifact", "import", corrected_artifact,
                        replay_project, "--core", paths["core"])
        if (replay["program"]["entrypoint"]["state_root"] == initial_root
                or invoke(paths["tools"], "test", "--project", replay_project)["result"] != "PASS"):
            raise RuntimeError("N08 corrected artifact did not rebase and test")
        check_runs(paths["tools"], replay_project, scratch, matrix, -5, 5)
    evidence = {
        "contract": "sley-tools.clamp-catalog-probe.v1", "result": "PASS",
        "tools_binary_sha256": digest(paths["tools"]),
        "core_binary_sha256": digest(paths["core"]),
        "schema_sha256": digest(paths["schema"]),
        "catalog_sha256": digest(paths["catalog"]),
        "manifest_sha256": digest(manifest_path), "artifact_sha256": digest(artifact_path),
        "recipe_sha256": digest(recipe_path),
        "cases_sha256": digest(initial_cases), "upper_cases_sha256": digest(upper_cases),
        "invalid_cases_sha256": digest(invalid_cases), "python_example_sha256": digest(example),
        "source_root": artifact["source_state_root"], "imported_root": initial_root,
        "upper_edited_root": upper_edit["new_root"], "invalid_root": invalid_edit["new_root"],
        "restored_lower_root": restore_lower["new_root"],
        "corrected_root": restore_upper["new_root"],
        "replay_root": replay["program"]["entrypoint"]["state_root"],
        "upper_edit_candidate_id": upper_edit["candidate_id"],
        "invalid_edit_candidate_id": invalid_edit["candidate_id"],
        "native_object_count": len(artifact["objects"]),
        "fresh_authoring_cases": len(FIXTURE_VALUES),
        "independently_checked_cases_each_phase": len(matrix),
        "wrong_typed_inputs_rejected": 3,
        "python_native_example_passed": True,
        "edited_artifact_replayed": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    paths["output"].parent.mkdir(parents=True, exist_ok=True)
    paths["output"].write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"wrote {paths['output']}: N08 native clamp and bound edits passed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"clamp catalog probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
