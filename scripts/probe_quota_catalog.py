#!/usr/bin/env python3
"""Verify N09's native quota package, safe subtraction, edit, and replay."""

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
FIXTURE_PAIRS = [
    (0, 0), (1, 0), (1, 1), (7, 6), (8, 7), (9, 8), (8, 8), (5, 8),
    (20, 6), (20, 7), (20, 8), (I64_MAX, 0), (I64_MAX, I64_MAX - 1),
    (I64_MAX, I64_MAX), (-1, 0), (0, -1), (I64_MIN, I64_MAX), (I64_MAX, -1),
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(tools: Path, *args: object, reject: str | None = None) -> dict:
    command = [str(tools), *map(str, args)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if reject is not None:
        if result.returncode == 0:
            raise RuntimeError(f"invalid N09 call succeeded: {command[1:3]}")
        failure = json.loads(result.stderr)
        if failure.get("code") != reject:
            raise RuntimeError(f"N09 rejection differed: {failure}")
        return failure
    if result.returncode:
        raise RuntimeError(f"N09 command failed: {result.stderr[:500]}")
    return json.loads(result.stdout)


def quota_oracle(limit: int, used: int, notify_at: int = 8) -> dict[str, object]:
    if not all(I64_MIN <= item <= I64_MAX for item in (limit, used, notify_at)):
        raise ValueError("quota oracle input exceeds signed 64 bit")
    if limit < 0 or used < 0:
        return {"remaining": {"ok": "0"}, "exhausted": False,
                "invalid_input": True, "at_notification_level": False}
    return {"remaining": {"ok": str(max(0, limit - used))},
            "exhausted": used >= limit, "invalid_input": False,
            "at_notification_level": used >= notify_at}


def fixture(path: Path, notify_at: int) -> list[dict]:
    document = json.loads(path.read_text())
    rows = document["cases"]
    if document["contract"] != "sley-tools.graph-cases.v0" or len(rows) != len(FIXTURE_PAIRS):
        raise RuntimeError("N09 fixture contract or case count differs")
    if len({row["id"] for row in rows}) != len(rows):
        raise RuntimeError("N09 fixture repeats a case ID")
    for row, pair in zip(rows, FIXTURE_PAIRS, strict=True):
        if row["input"] != [str(value) for value in pair] or row["expected"] != quota_oracle(*pair, notify_at):
            raise RuntimeError(f"N09 fixture differs from independent oracle at {pair}")
    return rows


def check_runs(tools: Path, project: Path, scratch: Path,
               pairs: list[tuple[int, int]], notify_at: int) -> None:
    input_file = scratch / "input.json"
    for limit, used in pairs:
        input_file.write_text(json.dumps([str(limit), str(used)]) + "\n")
        observed = invoke(tools, "run", "--project", project, "--input", input_file)["result"]
        if observed != quota_oracle(limit, used, notify_at):
            raise RuntimeError(f"native N09 result differs at limit={limit}, used={used}")
        if not observed["invalid_input"] and set(observed["remaining"]) != {"ok"}:
            raise RuntimeError("valid nonnegative quota produced an arithmetic failure arm")


def edit(tools: Path, project: Path, scratch: Path, value: int, cases: Path) -> dict:
    value_file = scratch / f"notify-at-{value}.json"
    value_file.write_text(json.dumps(str(value)) + "\n")
    before = invoke(tools, "inspect", "--project", project)["accepted_state_root"]
    proposed = invoke(tools, "propose", "--project", project, "--constant", "notify_at_used",
                      "--value-file", value_file, "--tests", cases)
    after_proposal = invoke(tools, "inspect", "--project", project)["accepted_state_root"]
    validated = invoke(tools, "validate", "--project", project)
    after_validation = invoke(tools, "inspect", "--project", project)["accepted_state_root"]
    committed = invoke(tools, "commit", "--project", project)
    tested = invoke(tools, "test", "--project", project)
    if (before != after_proposal or before != after_validation
            or proposed["base_root"] != before
            or not proposed["candidate_id"] == validated["candidate_id"] == committed["candidate_id"]
            or committed["old_root"] != before or committed["new_root"] == before
            or committed["changed_case_count"] < 2 or tested["result"] != "PASS"):
        raise RuntimeError("N09 notification edit did not follow the native lifecycle")
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
    notify_cases = package / "tests/notify-6-cases.json"
    example = root / "packages/python/examples/quota_remaining.py"
    if validate(manifest_path, paths["schema"])["result"] != "PASS":
        raise RuntimeError("N09 manifest does not validate")
    manifest = json.loads(manifest_path.read_text())
    artifact = json.loads(artifact_path.read_text())
    recipe = json.loads(recipe_path.read_text())
    catalog = json.loads(paths["catalog"].read_text())
    entry = next((item for item in catalog["programs"] if item["id"] == "N09"), None)
    relative = package.relative_to(root).as_posix()
    constants = {item["name"]: item["value"] for item in recipe["constants"]}
    if (manifest["program_id"] != "quota-remaining"
            or manifest["status"] != "portable_native_artifact"
            or artifact["contract"] != "sley-tools.native-graph-artifact.v1"
            or artifact["source_state_root"] != manifest["entrypoint"]["state_root"]
            or artifact["source_function_id"] != manifest["entrypoint"]["function_id"]
            or constants.get("notify_at_used") != "8"
            or "notify_at_used" not in artifact["source_constant_ids"]
            or manifest["worked_change"] != {
                "operation": "replace_named_constant", "constant": "notify_at_used",
                "initial_value": "8", "example_value": "6",
                "evidence": "evidence/catalog/quota-remaining-v2.json"}
            or entry is None or entry["program_id"] != "quota-remaining"
            or entry["package"] != relative
            or entry["artifact"] != f"{relative}/artifact/native-graph.json"
            or entry["integration_examples"] != ["packages/python/examples/quota_remaining.py"]
            or entry["verified_behavior"] != "evidence/catalog/quota-remaining-v2.json"
            or entry["worked_modification"] != "evidence/catalog/quota-remaining-v2.json"):
        raise RuntimeError("N09 package metadata or native binding differs")
    fixture(initial_cases, 8)
    fixture(notify_cases, 6)
    randomizer = random.Random(20260927)
    matrix = sorted(set(FIXTURE_PAIRS)
                    | {(limit, used) for limit in (0, 1, 5, 8, 20, I64_MAX)
                       for used in (0, 1, 5, 6, 7, 8, 9, 20, I64_MAX)}
                    | {(randomizer.randrange(0, 1001), randomizer.randrange(0, 1001))
                       for _ in range(20)})
    with tempfile.TemporaryDirectory(prefix="sley-quota-catalog-") as temporary:
        scratch = Path(temporary)
        authored_project = scratch / "authored"
        authored = invoke(paths["tools"], "init", authored_project,
                          "--graph", recipe_path, "--tests", initial_cases, "--core", paths["core"])
        if (authored["verification"]["external_assertions_against_native_execution"] != len(FIXTURE_PAIRS)
                or invoke(paths["tools"], "test", "--project", authored_project)["result"] != "PASS"):
            raise RuntimeError("N09 checked-in recipe did not author a fresh accepted graph")
        project = scratch / "quota"
        imported = invoke(paths["tools"], "artifact", "import", artifact_path,
                          project, "--core", paths["core"])
        imported_root = imported["program"]["entrypoint"]["state_root"]
        if (imported_root == artifact["source_state_root"]
                or imported["verification"]["external_assertions_against_native_execution"] != len(FIXTURE_PAIRS)
                or invoke(paths["tools"], "test", "--project", project)["result"] != "PASS"):
            raise RuntimeError("N09 artifact import did not rebase and test")
        check_runs(paths["tools"], project, scratch, matrix, 8)
        wrong_file = scratch / "wrong.json"
        for contents in ('["1"]\n', '[true,"0"]\n', '["9223372036854775808","0"]\n'):
            wrong_file.write_text(contents)
            invoke(paths["tools"], "run", "--project", project, "--input", wrong_file,
                   reject="ST_INPUT_INVALID")
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(root / "packages/python")
        sdk = subprocess.run([sys.executable, str(example), "--tools", str(paths["tools"]),
                              "--core", str(paths["core"]), "--artifact", str(artifact_path),
                              "--limit", "20", "--used", "7"], capture_output=True,
                             text=True, timeout=120, env=environment)
        if sdk.returncode:
            raise RuntimeError(f"N09 Python integration example failed: {sdk.stderr[:300]}")
        sdk_output = json.loads(sdk.stdout)
        expected_sdk = {**quota_oracle(20, 7), "remaining": {"ok": 13}}
        if sdk_output["result"] != expected_sdk or len(sdk_output["state_root"]) != 64:
            raise RuntimeError("N09 Python integration example returned the wrong typed value")
        notification = edit(paths["tools"], project, scratch, 6, notify_cases)
        check_runs(paths["tools"], project, scratch, matrix, 6)
        for limit, used in matrix:
            before, after = quota_oracle(limit, used, 8), quota_oracle(limit, used, 6)
            if any(before[key] != after[key] for key in ("remaining", "exhausted", "invalid_input")):
                raise RuntimeError("N09 notification edit changed quota computation")
        restored = edit(paths["tools"], project, scratch, 8, initial_cases)
        if restored["new_root"] != imported_root:
            raise RuntimeError("N09 notification restoration did not recover accepted graph")
        check_runs(paths["tools"], project, scratch, matrix, 8)
        corrected_artifact = scratch / "corrected-native-graph.json"
        invoke(paths["tools"], "artifact", "export", "--project", project,
               "--output", corrected_artifact)
        replay_project = scratch / "replay"
        replay = invoke(paths["tools"], "artifact", "import", corrected_artifact,
                        replay_project, "--core", paths["core"])
        if (replay["program"]["entrypoint"]["state_root"] == imported_root
                or invoke(paths["tools"], "test", "--project", replay_project)["result"] != "PASS"):
            raise RuntimeError("N09 corrected artifact did not rebase and test")
        check_runs(paths["tools"], replay_project, scratch, matrix, 8)
    evidence = {
        "contract": "sley-tools.quota-catalog-probe.v1", "result": "PASS",
        "tools_binary_sha256": digest(paths["tools"]),
        "core_binary_sha256": digest(paths["core"]),
        "schema_sha256": digest(paths["schema"]), "catalog_sha256": digest(paths["catalog"]),
        "manifest_sha256": digest(manifest_path), "artifact_sha256": digest(artifact_path),
        "recipe_sha256": digest(recipe_path), "cases_sha256": digest(initial_cases),
        "notify_cases_sha256": digest(notify_cases), "python_example_sha256": digest(example),
        "source_root": artifact["source_state_root"], "imported_root": imported_root,
        "notification_root": notification["new_root"], "restored_root": restored["new_root"],
        "replay_root": replay["program"]["entrypoint"]["state_root"],
        "notification_candidate_id": notification["candidate_id"],
        "notification_receipt_id": notification["receipt_id"],
        "restored_candidate_id": restored["candidate_id"],
        "native_object_count": len(artifact["objects"]),
        "independently_checked_cases_each_phase": len(matrix),
        "fresh_authoring_cases": len(FIXTURE_PAIRS),
        "wrong_typed_inputs_rejected": 3,
        "python_native_example_passed": True,
        "quota_calculation_preserved_on_edit": True,
        "edited_artifact_replayed": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    paths["output"].parent.mkdir(parents=True, exist_ok=True)
    paths["output"].write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"wrote {paths['output']}: N09 native quota and notification edit passed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"quota catalog probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
