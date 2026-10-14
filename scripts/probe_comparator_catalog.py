#!/usr/bin/env python3
"""Verify the checked-in N03 native comparator and its negative-input policy edit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from probe_checked_graph import oracle as comparator_oracle
from probe_data_pipe_stream import oracle as pipe_oracle
from validate_program_manifest import validate


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(tools: Path, *args: object, reject: str | None = None) -> dict:
    result = subprocess.run([str(tools), *map(str, args)], capture_output=True,
                            text=True, timeout=120, check=False)
    if reject is None:
        if result.returncode:
            raise RuntimeError(f"{' '.join(map(str, args[:2]))} failed: {result.stderr[:500]}")
        return json.loads(result.stdout)
    if result.returncode == 0:
        raise RuntimeError(f"{' '.join(map(str, args[:2]))} unexpectedly succeeded")
    error = json.loads(result.stderr)
    if error.get("code") != reject:
        raise RuntimeError(f"expected {reject}, got {error}")
    return error


def check_fixture(path: Path, *, reject_negatives: bool) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    rows = document["cases"]
    if document["contract"] != "sley-tools.graph-cases.v0" or len(rows) != 12:
        raise RuntimeError("comparator fixture has the wrong contract or case count")
    if len({row["id"] for row in rows}) != len(rows):
        raise RuntimeError("comparator fixture repeats a case ID")
    for row in rows:
        values = [int(value) for value in row["input"]]
        if [str(value) for value in values] != row["input"]:
            raise RuntimeError(f"comparator case {row['id']} uses noncanonical integers")
        expected = comparator_oracle(values)
        if not reject_negatives:
            expected["invalid_input"] = False
        if expected != row["expected"]:
            raise RuntimeError(f"comparator case {row['id']} differs from independent oracle")
    required = {"all-zero", "mixed-cpu-regression", "archive-only-regression",
                "negative-domain", "negative-cpu", "negative-archive",
                "negative-candidate", "overflow", "minimum-boundary-overflow"}
    if not required.issubset({row["id"] for row in rows}):
        raise RuntimeError("comparator fixture omits required boundary and negative cases")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "core", "schema", "catalog", "package", "sample", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    paths = {name: getattr(args, name).resolve() for name in
             ("tools", "core", "schema", "catalog", "package", "sample", "output")}
    root = Path(__file__).resolve().parents[1]
    package = paths["package"]
    manifest_path = package / "program.json"
    artifact_path = package / "artifact/native-graph.json"
    recipe_path = package / "build/graph.json"
    cases_path = package / "tests/cases.json"
    changed_cases_path = package / "tests/allow-corrections-cases.json"
    checked = validate(manifest_path, paths["schema"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    catalog = json.loads(paths["catalog"].read_text(encoding="utf-8"))
    entry = next((item for item in catalog["programs"] if item["id"] == "N03"), None)
    expected_package = package.relative_to(root).as_posix()
    constant = next((item for item in recipe["constants"]
                     if item["name"] == "reject_negative_inputs"), None)
    if (checked["result"] != "PASS" or manifest["program_id"] != "benchmark-comparator"
        or manifest["status"] != "portable_native_artifact"
        or artifact["contract"] != "sley-tools.native-graph-artifact.v1"
        or len(artifact["objects"]) != 34
        or artifact["source_state_root"] != manifest["entrypoint"]["state_root"]
        or artifact["source_function_id"] != manifest["entrypoint"]["function_id"]
        or artifact["source_constant_ids"].get("reject_negative_inputs") is None
        or constant is None or constant["value_type"] != "Bool" or constant["value"] is not True
        or manifest["worked_change"] != {
            "operation": "replace_named_constant", "constant": "reject_negative_inputs",
            "initial_value": True, "example_value": False,
            "evidence": "evidence/catalog/benchmark-comparator-v2.json"}
        or entry is None or entry["program_id"] != "benchmark-comparator"
        or entry["package"] != expected_package
        or entry["artifact"] != f"{expected_package}/artifact/native-graph.json"
        or entry["verified_behavior"] != "evidence/catalog/benchmark-comparator-v2.json"
        or entry["worked_modification"] != "evidence/catalog/benchmark-comparator-v2.json"):
        raise RuntimeError("N03 catalog package metadata or native binding differs")
    initial_rows = check_fixture(cases_path, reject_negatives=True)
    changed_rows = check_fixture(changed_cases_path, reject_negatives=False)
    if [row["input"] for row in initial_rows] != [row["input"] for row in changed_rows]:
        raise RuntimeError("N03 worked change replaced the test inputs")
    negative_cases = sum(comparator_oracle(list(map(int, row["input"])))['invalid_input']
                         for row in initial_rows)
    if negative_cases != 6:
        raise RuntimeError("N03 fixture does not test the policy on six negative rows")
    with tempfile.TemporaryDirectory(prefix="sley-comparator-catalog-") as temporary:
        workspace = Path(temporary)
        comparator = workspace / "comparator"
        imported = invoke(paths["tools"], "artifact", "import", artifact_path,
                          comparator, "--core", paths["core"])
        if (imported["verification"]["external_assertions_against_native_execution"] != 12
            or imported["program"]["entrypoint"]["state_root"] == artifact["source_state_root"]
            or invoke(paths["tools"], "test", "--project", comparator)["result"] != "PASS"):
            raise RuntimeError("N03 native import did not rebase and test")
        for row in initial_rows:
            input_path = workspace / f"{row['id']}.json"
            input_path.write_text(json.dumps(row["input"]), encoding="utf-8")
            observed = invoke(paths["tools"], "run", "--project", comparator, "--input", input_path)
            if observed["result"] != row["expected"]:
                raise RuntimeError(f"imported N03 graph differs for {row['id']}")
        missing_path = workspace / "missing.json"
        missing_path.write_text(json.dumps(initial_rows[0]["input"][:-1]), encoding="utf-8")
        invoke(paths["tools"], "run", "--project", comparator, "--input", missing_path,
               reject="ST_INPUT_INVALID")
        examples = root / "examples/graph"
        other = {}
        for role, name in (("selector", "benchmark-selector"),
                           ("reducer", "benchmark-summary-reducer")):
            project = workspace / name
            invoke(paths["tools"], "init", project, "--graph", examples / f"{name}.json",
                   "--tests", examples / f"{name}-cases.json", "--core", paths["core"])
            other[role] = project
        sample_rows = [json.loads(line) for line in paths["sample"].read_text(encoding="utf-8").splitlines()]
        expected_pipe = pipe_oracle(sample_rows)
        env = {**os.environ, "PYTHONPATH": str(root / "packages/python")}
        command = [sys.executable, "-m", "sley_tools.data_pipe", "--tools", str(paths["tools"]),
                   "--core", str(paths["core"]), "--comparator-project", str(comparator),
                   "--selector-project", str(other["selector"]),
                   "--reducer-project", str(other["reducer"]),
                   "--input", str(paths["sample"]), "--batch-size", "2"]
        pipe = subprocess.run(command, capture_output=True, env=env, timeout=180, check=False)
        if pipe.returncode or pipe.stdout != expected_pipe:
            raise RuntimeError(f"imported N03 graph changed Data Pipe output: {pipe.stderr[-500:]!r}")
        value_file = workspace / "allow-corrections.json"
        value_file.write_text("false\n", encoding="utf-8")
        proposal = invoke(paths["tools"], "propose", "--project", comparator,
                          "--constant", "reject_negative_inputs", "--value-file", value_file,
                          "--tests", changed_cases_path)
        validated = invoke(paths["tools"], "validate", "--project", comparator)
        committed = invoke(paths["tools"], "commit", "--project", comparator)
        if (not proposal["candidate_id"] == validated["candidate_id"] == committed["candidate_id"]
            or committed["new_root"] == imported["program"]["entrypoint"]["state_root"]
            or invoke(paths["tools"], "test", "--project", comparator)["result"] != "PASS"):
            raise RuntimeError("N03 worked native edit did not retain candidate and receipt")
        for row in changed_rows:
            input_path = workspace / f"changed-{row['id']}.json"
            input_path.write_text(json.dumps(row["input"]), encoding="utf-8")
            observed = invoke(paths["tools"], "run", "--project", comparator, "--input", input_path)
            if observed["result"] != row["expected"]:
                raise RuntimeError(f"edited N03 graph differs for {row['id']}")
        pipe = subprocess.run(command, capture_output=True, env=env, timeout=180, check=False)
        if pipe.returncode or pipe.stdout != expected_pipe:
            raise RuntimeError(f"N03 policy edit changed valid-row Data Pipe output: {pipe.stderr[-500:]!r}")
        edited_artifact = workspace / "edited-comparator.native.json"
        invoke(paths["tools"], "artifact", "export", "--project", comparator,
               "--output", edited_artifact)
        replay = workspace / "edited-replay"
        invoke(paths["tools"], "artifact", "import", edited_artifact, replay,
               "--core", paths["core"])
        if invoke(paths["tools"], "test", "--project", replay)["result"] != "PASS":
            raise RuntimeError("N03 edited artifact did not replay")
    evidence = {
        "contract": "sley-tools.comparator-catalog-probe.v1", "result": "PASS",
        "tools_binary_sha256": digest(paths["tools"]),
        "core_binary_sha256": digest(paths["core"]),
        "schema_sha256": digest(paths["schema"]), "catalog_sha256": digest(paths["catalog"]),
        "manifest_sha256": digest(manifest_path), "artifact_sha256": digest(artifact_path),
        "recipe_sha256": digest(recipe_path), "cases_sha256": digest(cases_path),
        "changed_cases_sha256": digest(changed_cases_path),
        "sample_sha256": digest(paths["sample"]),
        "sample_output_sha256": hashlib.sha256(expected_pipe).hexdigest(),
        "source_root": artifact["source_state_root"],
        "imported_root": imported["program"]["entrypoint"]["state_root"],
        "edited_root": committed["new_root"],
        "candidate_id": committed["candidate_id"], "receipt_id": committed["receipt_id"],
        "native_object_count": len(artifact["objects"]),
        "independently_checked_cases_before": len(initial_rows),
        "independently_checked_cases_after": len(changed_rows),
        "negative_cases_changed": negative_cases,
        "missing_measurement_rejected": True,
        "unchanged_host_sample_verified_before_and_after": True,
        "edited_artifact_replayed": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    paths["output"].parent.mkdir(parents=True, exist_ok=True)
    paths["output"].write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {paths['output']}: N03 native catalog package and worked edit passed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"comparator catalog probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
