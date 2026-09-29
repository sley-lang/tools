#!/usr/bin/env python3
"""Verify N07's native range package, endpoint regression, and correction."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from validate_program_manifest import validate


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(tools: Path, *args: object) -> dict:
    result = subprocess.run([str(tools), *map(str, args)], capture_output=True,
                            text=True, timeout=120, check=False)
    if result.returncode:
        raise RuntimeError(f"{' '.join(map(str, args[:2]))} failed: {result.stderr[:500]}")
    return json.loads(result.stdout)


def range_oracle(values: list[int], exclude_upper: bool) -> dict[str, bool]:
    value, lower, upper = values
    invalid = lower > upper
    return {
        "within_range": not invalid and lower <= value <= upper
                        and not (exclude_upper and value == upper),
        "invalid_bounds": invalid,
        "below_lower": not invalid and value < lower,
        "above_upper": not invalid and value > upper,
        "excluded_upper": not invalid and exclude_upper and value == upper,
    }


def fixture(path: Path, exclude_upper: bool) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    rows = document["cases"]
    if document["contract"] != "sley-tools.graph-cases.v0" or len(rows) != 11:
        raise RuntimeError("N07 fixture has the wrong contract or case count")
    if len({row["id"] for row in rows}) != len(rows):
        raise RuntimeError("N07 fixture repeats a case ID")
    for row in rows:
        values = [int(value) for value in row["input"]]
        if len(values) != 3 or [str(value) for value in values] != row["input"]:
            raise RuntimeError(f"N07 case {row['id']} uses invalid signed values")
        if any(value < -(1 << 63) or value >= (1 << 63) for value in values):
            raise RuntimeError(f"N07 case {row['id']} exceeds signed 64 bit range")
        if range_oracle(values, exclude_upper) != row["expected"]:
            raise RuntimeError(f"N07 case {row['id']} differs from independent oracle")
    return rows


def check_runs(tools: Path, project: Path, directory: Path,
               rows: list[dict], exclude_upper: bool) -> None:
    for index, row in enumerate(rows):
        values = [int(value) for value in row["input"]]
        input_path = directory / f"input-{index}.json"
        input_path.write_text(json.dumps(row["input"]), encoding="utf-8")
        observed = invoke(tools, "run", "--project", project, "--input", input_path)
        if observed["result"] != range_oracle(values, exclude_upper):
            raise RuntimeError(f"native N07 behavior differs for {row['input']}")


def edit(tools: Path, project: Path, directory: Path, value: bool,
         cases: Path) -> tuple[dict, dict]:
    value_path = directory / f"policy-{str(value).lower()}.json"
    value_path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    before = invoke(tools, "inspect", "--project", project)["accepted_state_root"]
    proposal = invoke(tools, "propose", "--project", project,
                      "--constant", "exclude_upper_endpoint", "--value-file", value_path,
                      "--tests", cases)
    after_propose = invoke(tools, "inspect", "--project", project)["accepted_state_root"]
    validated = invoke(tools, "validate", "--project", project)
    after_validate = invoke(tools, "inspect", "--project", project)["accepted_state_root"]
    committed = invoke(tools, "commit", "--project", project)
    if (before != after_propose or before != after_validate
        or proposal["base_root"] != before
        or not proposal["candidate_id"] == validated["candidate_id"] == committed["candidate_id"]
        or committed["old_root"] != before or committed["new_root"] == before
        or committed["changed_case_count"] < 2
        or invoke(tools, "test", "--project", project)["result"] != "PASS"):
        raise RuntimeError("N07 native proposal, validation, or commit changed unexpectedly")
    return proposal, committed


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
    cases_path = package / "tests/cases.json"
    changed_cases_path = package / "tests/excluded-upper-cases.json"
    python_example = package / "examples/python.py"
    cli_examples = {
        "upper_endpoint": (package / "inputs/upper-endpoint.json", [5, -5, 5]),
        "reversed_bounds": (package / "inputs/reversed-bounds.json", [0, 5, -5]),
    }
    if validate(manifest_path, paths["schema"])["result"] != "PASS":
        raise RuntimeError("N07 manifest does not validate")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    catalog = json.loads(paths["catalog"].read_text(encoding="utf-8"))
    entry = next((item for item in catalog["programs"] if item["id"] == "N07"), None)
    expected_package = package.relative_to(root).as_posix()
    if (manifest["program_id"] != "range-predicate"
        or manifest["status"] != "portable_native_artifact"
        or artifact["contract"] != "sley-tools.native-graph-artifact.v1"
        or artifact["source_state_root"] != manifest["entrypoint"]["state_root"]
        or artifact["source_function_id"] != manifest["entrypoint"]["function_id"]
        or artifact["source_constant_ids"].get("exclude_upper_endpoint") is None
        or len(artifact["objects"]) != 28
        or next(item for item in recipe["constants"]
                if item["name"] == "exclude_upper_endpoint")["value"] is not False
        or manifest["worked_change"] != {
            "operation": "replace_named_constant", "constant": "exclude_upper_endpoint",
            "initial_value": False, "example_value": True,
            "evidence": "evidence/catalog/range-predicate-v1.json"}
        or entry is None or entry["program_id"] != "range-predicate"
        or entry["package"] != expected_package
        or entry["artifact"] != f"{expected_package}/artifact/native-graph.json"
        or entry["integration_examples"] != ["programs/range-predicate/examples/python.py"]
        or entry["verified_behavior"] != "evidence/catalog/range-predicate-v1.json"
        or entry["worked_modification"] != "evidence/catalog/range-predicate-v1.json"):
        raise RuntimeError("N07 catalog package metadata or native binding differs")
    initial_rows = fixture(cases_path, False)
    excluded_rows = fixture(changed_cases_path, True)
    matrix = [{"input": [str(value), str(lower), str(upper)]}
              for lower, upper in ((-5, 5), (0, 0), (5, -5), (-9, -1))
              for value in (-6, -5, -1, 0, 5, 6)]
    with tempfile.TemporaryDirectory(prefix="sley-range-catalog-") as temporary:
        workspace = Path(temporary)
        imported_path = workspace / "range"
        imported = invoke(paths["tools"], "artifact", "import", artifact_path,
                          imported_path, "--core", paths["core"])
        imported_root = imported["program"]["entrypoint"]["state_root"]
        if (imported["verification"]["external_assertions_against_native_execution"] != 11
            or imported_root == artifact["source_state_root"]
            or invoke(paths["tools"], "test", "--project", imported_path)["result"] != "PASS"):
            raise RuntimeError("N07 catalog native import did not rebase and test")
        check_runs(paths["tools"], imported_path, workspace, initial_rows + matrix, False)
        for example_path, values in cli_examples.values():
            if json.loads(example_path.read_text(encoding="utf-8")) != list(map(str, values)):
                raise RuntimeError("N07 bundled CLI input differs from its declared values")
            output = invoke(paths["tools"], "run", "--project", imported_path,
                            "--input", example_path)
            if output["result"] != range_oracle(values, False):
                raise RuntimeError("N07 bundled CLI input differed from its native result")
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(root / "packages/python")
        for value, lower, upper in ((5, -5, 5), (0, 5, -5),
                                    (9_007_199_254_740_993, 9_007_199_254_740_993,
                                     9_007_199_254_740_995)):
            sdk = subprocess.run([
                sys.executable, str(python_example), "--tools", str(paths["tools"]),
                "--core", str(paths["core"]), "--artifact", str(artifact_path),
                "--value", str(value), "--lower", str(lower), "--upper", str(upper),
            ], capture_output=True, text=True, timeout=120, env=environment, check=False)
            if sdk.returncode:
                raise RuntimeError(f"N07 Python integration example failed: {sdk.stderr[:300]}")
            output = json.loads(sdk.stdout)
            if (output["result"] != range_oracle([value, lower, upper], False)
                    or len(output["state_root"]) != 64):
                raise RuntimeError("N07 Python integration example differed from the native oracle")
        _, exclusion = edit(paths["tools"], imported_path, workspace, True, changed_cases_path)
        check_runs(paths["tools"], imported_path, workspace, excluded_rows + matrix, True)
        _, correction = edit(paths["tools"], imported_path, workspace, False, cases_path)
        if correction["new_root"] != imported_root:
            raise RuntimeError("N07 boundary correction did not restore accepted native graph")
        check_runs(paths["tools"], imported_path, workspace, initial_rows + matrix, False)
        edited_artifact = workspace / "corrected-native-graph.json"
        invoke(paths["tools"], "artifact", "export", "--project", imported_path,
               "--output", edited_artifact)
        replay_path = workspace / "replay"
        replay = invoke(paths["tools"], "artifact", "import", edited_artifact,
                        replay_path, "--core", paths["core"])
        if (replay["program"]["entrypoint"]["state_root"] == correction["new_root"]
            or invoke(paths["tools"], "test", "--project", replay_path)["result"] != "PASS"):
            raise RuntimeError("N07 corrected native artifact did not rebase and replay")
        check_runs(paths["tools"], replay_path, workspace, initial_rows + matrix, False)
    evidence = {
        "contract": "sley-tools.range-catalog-probe.v1", "result": "PASS",
        "tools_binary_sha256": digest(paths["tools"]),
        "core_binary_sha256": digest(paths["core"]),
        "schema_sha256": digest(paths["schema"]),
        "catalog_sha256": digest(paths["catalog"]),
        "manifest_sha256": digest(manifest_path), "artifact_sha256": digest(artifact_path),
        "recipe_sha256": digest(recipe_path), "cases_sha256": digest(cases_path),
        "excluded_cases_sha256": digest(changed_cases_path),
        "python_example_sha256": digest(python_example),
        "cli_examples_sha256": {name: digest(path) for name, (path, _) in cli_examples.items()},
        "source_root": artifact["source_state_root"],
        "imported_root": imported_root, "excluded_root": exclusion["new_root"],
        "corrected_root": correction["new_root"],
        "replay_root": replay["program"]["entrypoint"]["state_root"],
        "exclusion_candidate_id": exclusion["candidate_id"],
        "exclusion_receipt_id": exclusion["receipt_id"],
        "correction_candidate_id": correction["candidate_id"],
        "correction_receipt_id": correction["receipt_id"],
        "native_object_count": len(artifact["objects"]),
        "independently_checked_cases_each_phase": len(initial_rows) + len(matrix),
        "python_native_example_cases": 3,
        "bundled_cli_example_cases": len(cli_examples),
        "edited_artifact_replayed": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    paths["output"].parent.mkdir(parents=True, exist_ok=True)
    paths["output"].write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
    print(f"wrote {paths['output']}: N07 native catalog package and boundary correction passed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"range catalog probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
