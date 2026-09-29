#!/usr/bin/env python3
"""Verify the checked-in N04 native package and its worked predicate edit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from probe_data_pipe_native import selector_oracle
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


def check_fixture(path: Path, include_all: bool) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    rows = document["cases"]
    if document["contract"] != "sley-tools.graph-cases.v0" or len(rows) != 9:
        raise RuntimeError("selector fixture has the wrong contract or case count")
    if len({row["id"] for row in rows}) != len(rows):
        raise RuntimeError("selector fixture repeats a case ID")
    for row in rows:
        values = [int(value) for value in row["input"]]
        if [str(value) for value in values] != row["input"]:
            raise RuntimeError(f"selector case {row['id']} uses noncanonical integers")
        if selector_oracle(values, include_all) != row["expected"]:
            raise RuntimeError(f"selector case {row['id']} differs from independent oracle")
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
    changed_cases_path = package / "tests/include-all-cases.json"
    checked = validate(manifest_path, paths["schema"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    catalog = json.loads(paths["catalog"].read_text(encoding="utf-8"))
    entry = next((item for item in catalog["programs"] if item["id"] == "N04"), None)
    expected_package = package.relative_to(root).as_posix()
    if (checked["result"] != "PASS" or manifest["program_id"] != "benchmark-selector"
        or manifest["status"] != "portable_native_artifact"
        or artifact["contract"] != "sley-tools.native-graph-artifact.v1"
        or len(artifact["objects"]) != 33
        or artifact["source_state_root"] != manifest["entrypoint"]["state_root"]
        or artifact["source_function_id"] != manifest["entrypoint"]["function_id"]
        or artifact["source_constant_ids"].get("include_nonregressions") is None
        or recipe["constants"][1]["value"] is not False
        or manifest["worked_change"] != {
            "operation": "replace_named_constant", "constant": "include_nonregressions",
            "initial_value": False, "example_value": True,
            "evidence": "evidence/catalog/benchmark-selector-v1.json"}
        or entry is None or entry["program_id"] != "benchmark-selector"
        or entry["package"] != expected_package
        or entry["artifact"] != f"{expected_package}/artifact/native-graph.json"
        or entry["verified_behavior"] != "evidence/catalog/benchmark-selector-v1.json"
        or entry["worked_modification"] != "evidence/catalog/benchmark-selector-v1.json"):
        raise RuntimeError("N04 catalog package metadata or native binding differs")
    initial_rows = check_fixture(cases_path, False)
    changed_rows = check_fixture(changed_cases_path, True)
    if (digest(recipe_path) != digest(root / "examples/graph/benchmark-selector.json")
        or digest(cases_path) != digest(root / "examples/graph/benchmark-selector-cases.json")
        or digest(changed_cases_path) != digest(root / "examples/graph/benchmark-selector-include-all-cases.json")):
        raise RuntimeError("N04 catalog recipe or cases differ from independently checked examples")
    with tempfile.TemporaryDirectory(prefix="sley-selector-catalog-") as temporary:
        workspace = Path(temporary)
        selector = workspace / "selector"
        imported = invoke(paths["tools"], "artifact", "import", artifact_path,
                          selector, "--core", paths["core"])
        if (imported["verification"]["external_assertions_against_native_execution"] != 9
            or imported["program"]["entrypoint"]["state_root"] == artifact["source_state_root"]
            or invoke(paths["tools"], "test", "--project", selector)["result"] != "PASS"):
            raise RuntimeError("N04 catalog native import did not rebase and test")
        for row in initial_rows:
            input_path = workspace / f"{row['id']}.json"
            input_path.write_text(json.dumps(row["input"]), encoding="utf-8")
            observed = invoke(paths["tools"], "run", "--project", selector, "--input", input_path)
            if observed["result"] != row["expected"]:
                raise RuntimeError(f"imported N04 graph differs for {row['id']}")
        value_file = workspace / "include-all.json"
        value_file.write_text("true\n", encoding="utf-8")
        proposal = invoke(paths["tools"], "propose", "--project", selector,
                          "--constant", "include_nonregressions", "--value-file", value_file,
                          "--tests", changed_cases_path)
        validated = invoke(paths["tools"], "validate", "--project", selector)
        committed = invoke(paths["tools"], "commit", "--project", selector)
        if (not proposal["candidate_id"] == validated["candidate_id"] == committed["candidate_id"]
            or committed["new_root"] == imported["program"]["entrypoint"]["state_root"]
            or invoke(paths["tools"], "test", "--project", selector)["result"] != "PASS"):
            raise RuntimeError("N04 worked native edit did not retain candidate and receipt")
        for row in changed_rows:
            input_path = workspace / f"changed-{row['id']}.json"
            input_path.write_text(json.dumps(row["input"]), encoding="utf-8")
            observed = invoke(paths["tools"], "run", "--project", selector, "--input", input_path)
            if observed["result"] != row["expected"]:
                raise RuntimeError(f"edited N04 graph differs for {row['id']}")
        examples = root / "examples/graph"
        other = {}
        for role, name in (("comparator", "benchmark-comparator"),
                           ("reducer", "benchmark-summary-reducer")):
            project = workspace / name
            invoke(paths["tools"], "init", project, "--graph", examples / f"{name}.json",
                   "--tests", examples / f"{name}-cases.json", "--core", paths["core"])
            other[role] = project
        sample_rows = [json.loads(line) for line in paths["sample"].read_text(encoding="utf-8").splitlines()]
        expected = pipe_oracle(sample_rows, include_all=True)
        env = {**os.environ, "PYTHONPATH": str(root / "packages/python")}
        command = [sys.executable, "-m", "sley_tools.data_pipe", "--tools", str(paths["tools"]),
                   "--core", str(paths["core"]), "--comparator-project", str(other["comparator"]),
                   "--selector-project", str(selector), "--reducer-project", str(other["reducer"]),
                   "--input", str(paths["sample"]), "--batch-size", "2"]
        pipe = subprocess.run(command, capture_output=True, env=env, timeout=180, check=False)
        if pipe.returncode or pipe.stdout != expected:
            raise RuntimeError(f"N04 catalog graph changed Data Pipe host behavior: {pipe.stderr[-500:]!r}")
        edited_artifact = workspace / "edited-selector.native.json"
        invoke(paths["tools"], "artifact", "export", "--project", selector,
               "--output", edited_artifact)
        replay = workspace / "edited-replay"
        invoke(paths["tools"], "artifact", "import", edited_artifact, replay,
               "--core", paths["core"])
        if invoke(paths["tools"], "test", "--project", replay)["result"] != "PASS":
            raise RuntimeError("N04 edited native artifact did not replay")
    evidence = {
        "contract": "sley-tools.selector-catalog-probe.v1", "result": "PASS",
        "tools_binary_sha256": digest(paths["tools"]),
        "core_binary_sha256": digest(paths["core"]),
        "schema_sha256": digest(paths["schema"]),
        "catalog_sha256": digest(paths["catalog"]),
        "manifest_sha256": digest(manifest_path), "artifact_sha256": digest(artifact_path),
        "recipe_sha256": digest(recipe_path), "cases_sha256": digest(cases_path),
        "changed_cases_sha256": digest(changed_cases_path),
        "sample_sha256": digest(paths["sample"]),
        "source_root": artifact["source_state_root"],
        "imported_root": imported["program"]["entrypoint"]["state_root"],
        "edited_root": committed["new_root"],
        "candidate_id": committed["candidate_id"], "receipt_id": committed["receipt_id"],
        "native_object_count": len(artifact["objects"]),
        "independently_checked_cases_before": len(initial_rows),
        "independently_checked_cases_after": len(changed_rows),
        "unchanged_host_selected_rows_after": len(sample_rows),
        "edited_artifact_replayed": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    paths["output"].parent.mkdir(parents=True, exist_ok=True)
    paths["output"].write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {paths['output']}: N04 native catalog package and worked edit passed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"selector catalog probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
