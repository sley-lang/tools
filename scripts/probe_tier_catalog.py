#!/usr/bin/env python3
"""Verify N06's native tier package, ordered thresholds, and branch edits."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from validate_program_manifest import validate


I64_MIN = -(1 << 63)
I64_MAX = (1 << 63) - 1
MATRIX = [I64_MIN, -9, -1, 0, 9, 10, 11, 12, 13, 19, 20, 21, 22, I64_MAX]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(tools: Path, *args: object, reject: str | None = None) -> dict:
    result = subprocess.run([str(tools), *map(str, args)], capture_output=True,
                            text=True, timeout=120, check=False)
    if reject is not None:
        if result.returncode == 0:
            raise RuntimeError("invalid native tier call unexpectedly succeeded")
        failure = json.loads(result.stderr)
        if failure.get("code") != reject:
            raise RuntimeError(f"expected {reject}, got {failure}")
        return failure
    if result.returncode:
        raise RuntimeError(f"native tier command failed: {result.stderr[:500]}")
    return json.loads(result.stdout)


def tier_oracle(value: int, bronze: int = 10, silver: int = 20,
                middle_enabled: bool = True) -> dict:
    if bronze >= silver:
        return {"tier": "-1", "invalid_configuration": True}
    tier = 0 if value <= bronze else 1 if value <= silver and middle_enabled else 2
    return {"tier": str(tier), "invalid_configuration": False}


def fixture(path: Path, bronze: int, silver: int, middle_enabled: bool) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    rows = document["cases"]
    if document["contract"] != "sley-tools.graph-cases.v0" or len(rows) != 11:
        raise RuntimeError("N06 case fixture has the wrong contract or count")
    if len({row["id"] for row in rows}) != len(rows):
        raise RuntimeError("N06 case fixture repeats an ID")
    for row in rows:
        if len(row["input"]) != 1:
            raise RuntimeError("N06 case has the wrong input shape")
        number = int(row["input"][0])
        if str(number) != row["input"][0] or not I64_MIN <= number <= I64_MAX:
            raise RuntimeError("N06 case input is not canonical SInt64")
        if row["expected"] != tier_oracle(number, bronze, silver, middle_enabled):
            raise RuntimeError(f"N06 case {row['id']} differs from the independent oracle")
    return rows


def check_runs(tools: Path, project: Path, scratch: Path, values: list[int],
               bronze: int, silver: int, middle_enabled: bool) -> None:
    for index, value in enumerate(values):
        input_path = scratch / f"tier-input-{index}.json"
        input_path.write_text(json.dumps([str(value)]), encoding="utf-8")
        observed = invoke(tools, "run", "--project", project, "--input", input_path)
        if observed["result"] != tier_oracle(value, bronze, silver, middle_enabled):
            raise RuntimeError(f"native tier classification differs for {value}")


def edit(tools: Path, project: Path, scratch: Path, name: str,
         value: str | bool, cases: Path) -> dict:
    value_path = scratch / f"{name}-{str(value).lower()}.json"
    value_path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    before = invoke(tools, "inspect", "--project", project)["accepted_state_root"]
    proposal = invoke(tools, "propose", "--project", project,
                      "--constant", name, "--value-file", value_path, "--tests", cases)
    after_proposal = invoke(tools, "inspect", "--project", project)["accepted_state_root"]
    validated = invoke(tools, "validate", "--project", project)
    after_validation = invoke(tools, "inspect", "--project", project)["accepted_state_root"]
    committed = invoke(tools, "commit", "--project", project)
    if (before != after_proposal or before != after_validation
        or proposal["base_root"] != before
        or not proposal["candidate_id"] == validated["candidate_id"] == committed["candidate_id"]
        or committed["old_root"] != before or committed["new_root"] == before
        or invoke(tools, "test", "--project", project)["result"] != "PASS"):
        raise RuntimeError(f"N06 {name} edit did not follow native lifecycle")
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
    cases = {name: package / "tests" / f"{name}.json" for name in
             ("cases", "bronze-12-cases", "invalid-order-cases", "disabled-middle-cases")}
    if validate(manifest_path, paths["schema"])["result"] != "PASS":
        raise RuntimeError("N06 manifest does not validate")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    catalog = json.loads(paths["catalog"].read_text(encoding="utf-8"))
    entry = next((item for item in catalog["programs"] if item["id"] == "N06"), None)
    expected_package = package.relative_to(root).as_posix()
    constants = {item["name"]: item["value"] for item in recipe["constants"]}
    if (manifest["program_id"] != "tier-classifier"
        or manifest["status"] != "portable_native_artifact"
        or artifact["contract"] != "sley-tools.native-graph-artifact.v1"
        or artifact["source_state_root"] != manifest["entrypoint"]["state_root"]
        or artifact["source_function_id"] != manifest["entrypoint"]["function_id"]
        or len(artifact["objects"]) != 40
        or constants.get("bronze_max") != "10" or constants.get("silver_max") != "20"
        or constants.get("middle_tier_enabled") is not True
        or any(artifact["source_constant_ids"].get(name) is None for name in
               ("bronze_max", "silver_max", "middle_tier_enabled"))
        or manifest["worked_change"] != {
            "operation": "replace_named_constant", "constant": "bronze_max",
            "initial_value": "10", "example_value": "12",
            "evidence": "evidence/catalog/tier-classifier-v1.json"}
        or entry is None or entry["program_id"] != "tier-classifier"
        or entry["package"] != expected_package
        or entry["artifact"] != f"{expected_package}/artifact/native-graph.json"
        or entry["verified_behavior"] != "evidence/catalog/tier-classifier-v1.json"
        or entry["worked_modification"] != "evidence/catalog/tier-classifier-v1.json"):
        raise RuntimeError("N06 catalog metadata or native binding differs")
    fixture(cases["cases"], 10, 20, True)
    fixture(cases["bronze-12-cases"], 12, 20, True)
    fixture(cases["invalid-order-cases"], 20, 20, True)
    fixture(cases["disabled-middle-cases"], 10, 20, False)
    with tempfile.TemporaryDirectory(prefix="sley-tier-catalog-") as temporary:
        scratch = Path(temporary)
        project = scratch / "tier"
        imported = invoke(paths["tools"], "artifact", "import", artifact_path,
                          project, "--core", paths["core"])
        imported_root = imported["program"]["entrypoint"]["state_root"]
        if (imported_root == artifact["source_state_root"]
            or imported["verification"]["external_assertions_against_native_execution"] != 11):
            raise RuntimeError("N06 native import did not rebase and test")
        check_runs(paths["tools"], project, scratch, MATRIX, 10, 20, True)
        wrong_shape = scratch / "wrong-input.json"
        wrong_shape.write_text("[]\n", encoding="utf-8")
        invoke(paths["tools"], "run", "--project", project, "--input", wrong_shape,
               reject="ST_INPUT_INVALID")
        threshold = edit(paths["tools"], project, scratch, "bronze_max", "12",
                         cases["bronze-12-cases"])
        check_runs(paths["tools"], project, scratch, MATRIX, 12, 20, True)
        if (tier_oracle(11, 10, 20) == tier_oracle(11, 12, 20)
            or any(tier_oracle(value, 10, 20) != tier_oracle(value, 12, 20)
                   for value in (I64_MIN, -1, 0, 9, 10, 13, 19, 20, 21, I64_MAX))):
            raise RuntimeError("N06 threshold edit changed unrelated categories")
        invalid = edit(paths["tools"], project, scratch, "bronze_max", "20",
                       cases["invalid-order-cases"])
        check_runs(paths["tools"], project, scratch, MATRIX, 20, 20, True)
        restored = edit(paths["tools"], project, scratch, "bronze_max", "10", cases["cases"])
        if restored["new_root"] != imported_root:
            raise RuntimeError("N06 threshold restoration did not recover original graph")
        disabled = edit(paths["tools"], project, scratch, "middle_tier_enabled", False,
                        cases["disabled-middle-cases"])
        check_runs(paths["tools"], project, scratch, MATRIX, 10, 20, False)
        enabled = edit(paths["tools"], project, scratch, "middle_tier_enabled", True,
                       cases["cases"])
        if enabled["new_root"] != imported_root:
            raise RuntimeError("N06 middle branch restoration did not recover original graph")
        check_runs(paths["tools"], project, scratch, MATRIX, 10, 20, True)
        exported_path = scratch / "corrected-native-graph.json"
        invoke(paths["tools"], "artifact", "export", "--project", project,
               "--output", exported_path)
        replay_path = scratch / "replay"
        replay = invoke(paths["tools"], "artifact", "import", exported_path,
                        replay_path, "--core", paths["core"])
        replay_root = replay["program"]["entrypoint"]["state_root"]
        if replay_root == imported_root:
            raise RuntimeError("N06 exported artifact did not rebase in fresh workspace")
        check_runs(paths["tools"], replay_path, scratch, MATRIX, 10, 20, True)
    evidence = {
        "contract": "sley-tools.tier-catalog-probe.v1", "result": "PASS",
        "tools_binary_sha256": digest(paths["tools"]),
        "core_binary_sha256": digest(paths["core"]),
        "schema_sha256": digest(paths["schema"]), "catalog_sha256": digest(paths["catalog"]),
        "manifest_sha256": digest(manifest_path), "artifact_sha256": digest(artifact_path),
        "recipe_sha256": digest(recipe_path),
        "cases_sha256": {name: digest(path) for name, path in cases.items()},
        "source_root": artifact["source_state_root"], "imported_root": imported_root,
        "threshold_root": threshold["new_root"], "invalid_root": invalid["new_root"],
        "disabled_root": disabled["new_root"], "restored_root": enabled["new_root"],
        "replay_root": replay_root,
        "threshold_candidate_id": threshold["candidate_id"],
        "threshold_receipt_id": threshold["receipt_id"],
        "invalid_candidate_id": invalid["candidate_id"],
        "invalid_receipt_id": invalid["receipt_id"],
        "disabled_candidate_id": disabled["candidate_id"],
        "disabled_receipt_id": disabled["receipt_id"],
        "native_object_count": len(artifact["objects"]),
        "independent_native_cases_each_phase": len(MATRIX),
        "published_cases_each_policy": 11,
        "missing_input_rejected": True,
        "unrelated_categories_preserved": True,
        "invalid_configuration_returned_no_category": True,
        "edited_artifact_replayed": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    paths["output"].parent.mkdir(parents=True, exist_ok=True)
    paths["output"].write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
    print(f"wrote {paths['output']}: N06 native tier package, edits, and replay passed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"tier catalog probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
