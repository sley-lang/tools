#!/usr/bin/env python3
"""Verify the checked-in N05 native reducer and its signed-correction edit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from probe_data_pipe_native import reducer_oracle
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


def check_fixture(path: Path, *, reject_negative_totals: bool) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    rows = document["cases"]
    if document["contract"] != "sley-tools.graph-cases.v0" or len(rows) != 17:
        raise RuntimeError("reducer fixture has the wrong contract or case count")
    if len({row["id"] for row in rows}) != len(rows):
        raise RuntimeError("reducer fixture repeats a case ID")
    for row in rows:
        values = [int(value) for value in row["input"]]
        if [str(value) for value in values] != row["input"]:
            raise RuntimeError(f"reducer case {row['id']} uses noncanonical integers")
        expected = reducer_oracle(values)
        if not reject_negative_totals:
            expected["invalid_input"] = values[0] < 0 or values[1] < 0
        if expected != row["expected"]:
            raise RuntimeError(f"reducer case {row['id']} differs from independent oracle")
    required = {"empty-identity", "left-identity", "right-identity", "zero-valued-record",
                "count-overflow", "wall-overflow", "cpu-overflow", "archive-overflow",
                "negative-domain", "negative-right-count", "negative-left-wall",
                "negative-right-wall", "negative-cpu", "negative-archive",
                "negative-total-overflow"}
    if not required.issubset({row["id"] for row in rows}):
        raise RuntimeError("reducer fixture omits identity, overflow, or negative cases")
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
    entry = next((item for item in catalog["programs"] if item["id"] == "N05"), None)
    expected_package = package.relative_to(root).as_posix()
    constant = next((item for item in recipe["constants"]
                     if item["name"] == "reject_negative_totals"), None)
    if (checked["result"] != "PASS" or manifest["program_id"] != "benchmark-summary-reducer"
        or manifest["status"] != "portable_native_artifact"
        or artifact["contract"] != "sley-tools.native-graph-artifact.v1"
        or len(artifact["objects"]) != 36
        or artifact["source_state_root"] != manifest["entrypoint"]["state_root"]
        or artifact["source_function_id"] != manifest["entrypoint"]["function_id"]
        or artifact["source_constant_ids"].get("reject_negative_totals") is None
        or constant is None or constant["value_type"] != "Bool" or constant["value"] is not True
        or manifest["worked_change"] != {
            "operation": "replace_named_constant", "constant": "reject_negative_totals",
            "initial_value": True, "example_value": False,
            "evidence": "evidence/catalog/benchmark-summary-reducer-v2.json"}
        or entry is None or entry["program_id"] != "benchmark-summary-reducer"
        or entry["package"] != expected_package
        or entry["artifact"] != f"{expected_package}/artifact/native-graph.json"
        or entry["verified_behavior"] != "evidence/catalog/benchmark-summary-reducer-v2.json"
        or entry["worked_modification"] != "evidence/catalog/benchmark-summary-reducer-v2.json"):
        raise RuntimeError("N05 catalog package metadata or native binding differs")
    initial_rows = check_fixture(cases_path, reject_negative_totals=True)
    changed_rows = check_fixture(changed_cases_path, reject_negative_totals=False)
    if [row["input"] for row in initial_rows] != [row["input"] for row in changed_rows]:
        raise RuntimeError("N05 worked change replaced the test inputs")
    changed_negative_cases = sum(
        reducer_oracle(list(map(int, row["input"]))) != row["expected"]
        for row in changed_rows)
    if changed_negative_cases != 5:
        raise RuntimeError("N05 fixture does not test five changed negative-total cases")
    with tempfile.TemporaryDirectory(prefix="sley-reducer-catalog-") as temporary:
        workspace = Path(temporary)
        reducer = workspace / "reducer"
        imported = invoke(paths["tools"], "artifact", "import", artifact_path,
                          reducer, "--core", paths["core"])
        if (imported["verification"]["external_assertions_against_native_execution"] != 17
            or imported["program"]["entrypoint"]["state_root"] == artifact["source_state_root"]
            or invoke(paths["tools"], "test", "--project", reducer)["result"] != "PASS"):
            raise RuntimeError("N05 native import did not rebase and test")
        for row in initial_rows:
            input_path = workspace / f"{row['id']}.json"
            input_path.write_text(json.dumps(row["input"]), encoding="utf-8")
            observed = invoke(paths["tools"], "run", "--project", reducer, "--input", input_path)
            if observed["result"] != row["expected"]:
                raise RuntimeError(f"imported N05 graph differs for {row['id']}")
        missing_path = workspace / "missing.json"
        missing_path.write_text(json.dumps(initial_rows[0]["input"][:-1]), encoding="utf-8")
        invoke(paths["tools"], "run", "--project", reducer, "--input", missing_path,
               reject="ST_INPUT_INVALID")
        examples = root / "examples/graph"
        other = {}
        for role, name in (("comparator", "benchmark-comparator"),
                           ("selector", "benchmark-selector")):
            project = workspace / name
            invoke(paths["tools"], "init", project, "--graph", examples / f"{name}.json",
                   "--tests", examples / f"{name}-cases.json", "--core", paths["core"])
            other[role] = project
        sample_rows = [json.loads(line) for line in paths["sample"].read_text(encoding="utf-8").splitlines()]
        expected_pipe = pipe_oracle(sample_rows)
        env = {**os.environ, "PYTHONPATH": str(root / "packages/python")}
        command = [sys.executable, "-m", "sley_tools.data_pipe", "--tools", str(paths["tools"]),
                   "--core", str(paths["core"]), "--comparator-project", str(other["comparator"]),
                   "--selector-project", str(other["selector"]),
                   "--reducer-project", str(reducer), "--input", str(paths["sample"])]
        for batch_size in (1, 2, 4):
            pipe = subprocess.run([*command, "--batch-size", str(batch_size)],
                                  capture_output=True, env=env, timeout=180, check=False)
            if pipe.returncode or pipe.stdout != expected_pipe:
                raise RuntimeError(f"imported N05 graph changed partition {batch_size}: {pipe.stderr[-500:]!r}")
        value_file = workspace / "allow-corrections.json"
        value_file.write_text("false\n", encoding="utf-8")
        proposal = invoke(paths["tools"], "propose", "--project", reducer,
                          "--constant", "reject_negative_totals", "--value-file", value_file,
                          "--tests", changed_cases_path)
        validated = invoke(paths["tools"], "validate", "--project", reducer)
        committed = invoke(paths["tools"], "commit", "--project", reducer)
        if (not proposal["candidate_id"] == validated["candidate_id"] == committed["candidate_id"]
            or committed["new_root"] == imported["program"]["entrypoint"]["state_root"]
            or invoke(paths["tools"], "test", "--project", reducer)["result"] != "PASS"):
            raise RuntimeError("N05 worked native edit did not retain candidate and receipt")
        for row in changed_rows:
            input_path = workspace / f"changed-{row['id']}.json"
            input_path.write_text(json.dumps(row["input"]), encoding="utf-8")
            observed = invoke(paths["tools"], "run", "--project", reducer, "--input", input_path)
            if observed["result"] != row["expected"]:
                raise RuntimeError(f"edited N05 graph differs for {row['id']}")
        pipe = subprocess.run([*command, "--batch-size", "2"], capture_output=True,
                              env=env, timeout=180, check=False)
        if pipe.returncode or pipe.stdout != expected_pipe:
            raise RuntimeError(f"N05 policy edit changed valid-row Data Pipe output: {pipe.stderr[-500:]!r}")
        edited_artifact = workspace / "edited-reducer.native.json"
        invoke(paths["tools"], "artifact", "export", "--project", reducer,
               "--output", edited_artifact)
        replay = workspace / "edited-replay"
        invoke(paths["tools"], "artifact", "import", edited_artifact, replay,
               "--core", paths["core"])
        if invoke(paths["tools"], "test", "--project", replay)["result"] != "PASS":
            raise RuntimeError("N05 edited artifact did not replay")
    evidence = {
        "contract": "sley-tools.reducer-catalog-probe.v1", "result": "PASS",
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
        "negative_total_cases_changed": changed_negative_cases,
        "missing_summary_input_rejected": True,
        "empty_identity_distinguished_from_zero_valued_record": True,
        "host_partitions_verified_before": [1, 2, 4],
        "unchanged_host_sample_verified_after": True,
        "edited_artifact_replayed": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    paths["output"].parent.mkdir(parents=True, exist_ok=True)
    paths["output"].write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {paths['output']}: N05 native catalog package and worked edit passed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"reducer catalog probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
