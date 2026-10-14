#!/usr/bin/env python3
"""Local, public Repair Lab adapter around accepted native Sley programs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
CONTRACT = "sley-tools.repair-lab-workspace.v0"
JUDGE_CONTRACT = "sley-tools.repair-lab-judge.v0"
EXERCISES = {
    "rl01": ("Create", "Construct and export a new native range predicate"),
    "rl02": ("Boundary", "Correct an exclusive upper endpoint through a native edit"),
    "rl03": ("Branch", "Restore the middle tier without changing the outer categories"),
    "rl04": ("Tests", "Add an upper-endpoint case that fails the flawed native graph"),
    "rl05": ("Type", "Diagnose a typed-value failure and commit the corrected proposal"),
    "rl06": ("Stale state", "Reinspect an advanced native root before proposing again"),
    "rl07": ("Authority", "Resolve a denied local edit through a fresh scoped workspace"),
    "rl08": ("Portability", "Export and reimport a changed native classifier"),
}
I64_MIN = -(1 << 63)
I64_MAX = (1 << 63) - 1
MATRIX = [
    (-6, -5, 5), (-5, -5, 5), (0, -5, 5), (5, -5, 5), (6, -5, 5),
    (-1, 0, 0), (0, 0, 0), (1, 0, 0), (0, 5, -5), (5, 5, -5),
    (I64_MIN, I64_MIN, I64_MAX), (I64_MAX, I64_MIN, I64_MAX),
    (-1, -9, -1), (0, -9, -1), (7, 7, 7), (8, 7, 7),
]
TIER_MATRIX = [I64_MIN, -9, -1, 0, 9, 10, 11, 12, 13, 19, 20, 21, 22, I64_MAX]


class LabError(Exception):
    pass


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(value, file, indent=2, sort_keys=True)
            file.write("\n")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def invoke(tools: Path, *args: object) -> dict:
    try:
        result = subprocess.run([str(tools), *map(str, args)], capture_output=True,
                                text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LabError(f"tools command could not finish: {error}") from error
    if result.returncode:
        try:
            failure = json.loads(result.stderr)
            detail = f"{failure.get('code', 'unknown')}: {failure.get('message', 'tool failed')}"
        except json.JSONDecodeError:
            detail = result.stderr[:300] or f"exit {result.returncode}"
        raise LabError(f"native tools command failed: {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise LabError("native tools command returned non-JSON output") from error


def checked_binary(path: str, label: str) -> Path:
    binary = Path(path).expanduser().resolve(strict=True)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise LabError(f"{label} must be an executable file")
    return binary


def workspace_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink():
        raise LabError("lab workspace must not be a symlink")
    return path.resolve(strict=False)


def load_marker(workspace: Path, tools: Path, core: Path) -> dict:
    marker_path = workspace / ".sley-repair-lab.json"
    if not workspace.is_dir() or workspace.is_symlink() or marker_path.is_symlink():
        raise LabError("choose a real Repair Lab workspace")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LabError("Repair Lab workspace marker is missing or invalid") from error
    if (marker.get("contract") != CONTRACT or marker.get("workspace") != str(workspace)
        or marker.get("exercise") not in EXERCISES):
        raise LabError("workspace marker does not match this lab directory")
    if marker.get("tools_sha256") != digest(tools) or marker.get("core_sha256") != digest(core):
        raise LabError("tools or core binary differs from the workspace's selected version")
    return marker


def seed_rl02(workspace: Path, tools: Path, core: Path, marker: dict,
              cases_name: str = "excluded-cases.json") -> dict:
    project = workspace / "project"
    imported = invoke(tools, "artifact", "import",
                      REPO / "programs/range-predicate/artifact/native-graph.json",
                      project, "--core", core)
    seed_root = imported["program"]["entrypoint"]["state_root"]
    policy = workspace / "exclude-upper.json"
    policy.write_text("true\n", encoding="utf-8")
    proposal = invoke(tools, "propose", "--project", project,
                      "--constant", "exclude_upper_endpoint", "--value-file", policy,
                      "--tests", workspace / cases_name)
    validation = invoke(tools, "validate", "--project", project)
    flawed = invoke(tools, "commit", "--project", project)
    if (proposal["base_root"] != seed_root
        or not proposal["candidate_id"] == validation["candidate_id"] == flawed["candidate_id"]
        or flawed["old_root"] != seed_root or flawed["new_root"] == seed_root
        or invoke(tools, "test", "--project", project)["result"] != "PASS"):
        raise LabError("could not prepare the native exclusive-endpoint seed")
    marker.update({"seed_root": seed_root, "flaw_root": flawed["new_root"],
                   "flaw_candidate_id": flawed["candidate_id"],
                   "flaw_receipt_id": flawed["receipt_id"]})
    write_json(workspace / ".sley-repair-lab.json", marker)
    return flawed


def seed_rl03(workspace: Path, tools: Path, core: Path, marker: dict) -> dict:
    project = workspace / "project"
    imported = invoke(tools, "artifact", "import",
                      REPO / "programs/tier-classifier/artifact/native-graph.json",
                      project, "--core", core)
    seed_root = imported["program"]["entrypoint"]["state_root"]
    policy = workspace / "disable-middle.json"
    policy.write_text("false\n", encoding="utf-8")
    proposal = invoke(tools, "propose", "--project", project,
                      "--constant", "middle_tier_enabled", "--value-file", policy,
                      "--tests", workspace / "disabled-middle-cases.json")
    validation = invoke(tools, "validate", "--project", project)
    flawed = invoke(tools, "commit", "--project", project)
    if (proposal["base_root"] != seed_root
        or not proposal["candidate_id"] == validation["candidate_id"] == flawed["candidate_id"]
        or flawed["old_root"] != seed_root or flawed["new_root"] == seed_root
        or invoke(tools, "test", "--project", project)["result"] != "PASS"):
        raise LabError("could not prepare the native disabled-middle seed")
    marker.update({"seed_root": seed_root, "flaw_root": flawed["new_root"],
                   "flaw_candidate_id": flawed["candidate_id"],
                   "flaw_receipt_id": flawed["receipt_id"]})
    write_json(workspace / ".sley-repair-lab.json", marker)
    return flawed


def seed_rl05(workspace: Path, tools: Path, core: Path, marker: dict) -> dict:
    imported = invoke(tools, "artifact", "import",
                      REPO / "programs/tier-classifier/artifact/native-graph.json",
                      workspace / "project", "--core", core)
    marker["seed_root"] = imported["program"]["entrypoint"]["state_root"]
    write_json(workspace / ".sley-repair-lab.json", marker)
    return imported


def seed_rl06(workspace: Path, tools: Path, core: Path, marker: dict) -> dict:
    project = workspace / "project"
    imported = invoke(tools, "artifact", "import",
                      REPO / "programs/release-readiness/artifact/native-program.json",
                      project, "--core", core)
    seed_root = imported["program"]["entrypoint"]["state_root"]
    stale = invoke(tools, "propose", "--project", project,
                   "--max-blocking-findings", 3)
    advanced = invoke(tools, "edit", "--project", project,
                      "--max-blocking-findings", 4)
    if (stale["base_root"] != seed_root or advanced["old_root"] != seed_root
        or advanced["new_root"] == seed_root
        or invoke(tools, "inspect", "--project", project)["threshold"] != 4):
        raise LabError("could not prepare the native advanced-head scenario")
    stale_path = workspace / "stale-proposal.json"
    shutil.copyfile(project / ".sley-tools/proposal.json", stale_path)
    marker.update({"seed_root": seed_root, "advanced_root": advanced["new_root"],
                   "advance_candidate_id": advanced["candidate_id"],
                   "stale_candidate_id": stale["candidate_id"],
                   "stale_proposal_sha256": digest(stale_path)})
    write_json(workspace / ".sley-repair-lab.json", marker)
    return advanced


def seed_rl07(workspace: Path, tools: Path, core: Path, marker: dict) -> dict:
    initialized = invoke(tools, "init", workspace / "project", "--template",
                         "release-readiness", "--core", core,
                         "--grant-profile", "create-only")
    inspection = invoke(tools, "inspect", "--project", workspace / "project")
    authority = inspection["authority"]
    if (authority["source"] != "accepted_workspace_policy"
        or authority["replace_entity_version_allowed"] is not False
        or inspection["threshold"] != 0
        or initialized["program"]["entrypoint"]["state_root"] != inspection["accepted_state_root"]):
        raise LabError("could not prepare the scoped create-only native workspace")
    marker.update({"seed_root": inspection["accepted_state_root"],
                   "seed_policy_root": authority["policy_root"],
                   "seed_principal_id": authority["principal_id"]})
    write_json(workspace / ".sley-repair-lab.json", marker)
    return initialized


def seed_rl08(workspace: Path, tools: Path, core: Path, marker: dict) -> dict:
    imported = invoke(tools, "artifact", "import",
                      REPO / "programs/tier-classifier/artifact/native-graph.json",
                      workspace / "project", "--core", core)
    marker["seed_root"] = imported["program"]["entrypoint"]["state_root"]
    write_json(workspace / ".sley-repair-lab.json", marker)
    return imported


def start(exercise: str, workspace: Path, tools: Path, core: Path) -> dict:
    if exercise not in EXERCISES:
        raise LabError("exercise is pending or unknown; run list for available lessons")
    if workspace.exists() or workspace.is_symlink():
        raise LabError("start requires a new workspace path")
    if not workspace.parent.is_dir():
        raise LabError("workspace parent directory must already exist")
    doctor = invoke(tools, "doctor", "--core", core)
    if doctor.get("status") != "compatible":
        raise LabError("selected core is not compatible with this tools build")
    workspace.mkdir(mode=0o700)
    marker = {"contract": CONTRACT, "workspace": str(workspace), "exercise": exercise,
              "tools_sha256": digest(tools), "core_sha256": digest(core),
              "core_profile": "sley-2.1.1-extended-v1"}
    try:
        write_json(workspace / ".sley-repair-lab.json", marker)
        materials = REPO / "apps/repair-lab/exercises" / exercise
        for name in ("TASK.md", "HINTS.md", "SOLUTION.md"):
            shutil.copyfile(materials / name, workspace / name)
        if exercise == "rl01":
            shutil.copyfile(materials / "starter-recipe.json", workspace / "starter-recipe.json")
            shutil.copyfile(REPO / "examples/graph/range-predicate-cases.json",
                            workspace / "public-cases.json")
        elif exercise == "rl02":
            shutil.copyfile(REPO / "programs/range-predicate/tests/cases.json",
                            workspace / "inclusive-cases.json")
            shutil.copyfile(REPO / "programs/range-predicate/tests/excluded-upper-cases.json",
                            workspace / "excluded-cases.json")
            seed_rl02(workspace, tools, core, marker)
        elif exercise == "rl03":
            shutil.copyfile(REPO / "programs/tier-classifier/tests/cases.json",
                            workspace / "correct-cases.json")
            shutil.copyfile(REPO / "programs/tier-classifier/tests/disabled-middle-cases.json",
                            workspace / "disabled-middle-cases.json")
            seed_rl03(workspace, tools, core, marker)
        elif exercise == "rl04":
            shutil.copyfile(materials / "missing-upper-cases.json",
                            workspace / "missing-upper-cases.json")
            seed_rl02(workspace, tools, core, marker, "missing-upper-cases.json")
        elif exercise == "rl05":
            shutil.copyfile(materials / "wrong-value.json", workspace / "wrong-value.json")
            shutil.copyfile(REPO / "programs/tier-classifier/tests/bronze-12-cases.json",
                            workspace / "bronze-12-cases.json")
            seed_rl05(workspace, tools, core, marker)
        elif exercise == "rl06":
            seed_rl06(workspace, tools, core, marker)
        elif exercise == "rl07":
            seed_rl07(workspace, tools, core, marker)
        else:
            shutil.copyfile(REPO / "programs/tier-classifier/tests/bronze-12-cases.json",
                            workspace / "bronze-12-cases.json")
            shutil.copyfile(materials / "input-12.json", workspace / "input-12.json")
            seed_rl08(workspace, tools, core, marker)
        return {"contract": CONTRACT, "result": "READY", "exercise": exercise,
                "workspace": str(workspace), "project": str(workspace / "project"),
                "flaw_root": marker.get("flaw_root")}
    except Exception:
        shutil.rmtree(workspace)
        raise


def oracle(value: int, lower: int, upper: int, extended: bool) -> dict[str, bool]:
    invalid = lower > upper
    result = {"within_range": not invalid and lower <= value <= upper,
              "invalid_bounds": invalid,
              "below_lower": not invalid and value < lower,
              "above_upper": not invalid and value > upper}
    if extended:
        result["excluded_upper"] = False
    return result


def native_matrix(tools: Path, project: Path, scratch: Path,
                  extended: bool) -> list[dict]:
    rows = []
    for index, (value, lower, upper) in enumerate(MATRIX):
        input_path = scratch / f"case-{index}.json"
        input_path.write_text(json.dumps([str(value), str(lower), str(upper)]), encoding="utf-8")
        result = invoke(tools, "run", "--project", project, "--input", input_path)
        expected = oracle(value, lower, upper, extended)
        if result.get("result") != expected:
            raise LabError(f"native result differs at input {[value, lower, upper]}")
        rows.append({"input": [str(value), str(lower), str(upper)],
                     "result": result["result"], "state_root": result["state_root"]})
    return rows


def tier_oracle(value: int, bronze_max: int = 10) -> dict[str, object]:
    return {"tier": str(0 if value <= bronze_max else 1 if value <= 20 else 2),
            "invalid_configuration": False}


def native_tier_matrix(tools: Path, project: Path, scratch: Path,
                       bronze_max: int = 10) -> list[dict]:
    rows = []
    for index, value in enumerate(TIER_MATRIX):
        input_path = scratch / f"tier-{index}.json"
        input_path.write_text(json.dumps([str(value)]), encoding="utf-8")
        result = invoke(tools, "run", "--project", project, "--input", input_path)
        if result.get("result") != tier_oracle(value, bronze_max):
            raise LabError(f"native tier result differs for {value}")
        rows.append({"input": [str(value)], "result": result["result"],
                     "state_root": result["state_root"]})
    return rows


def release_oracle(facts: dict, threshold: int) -> dict[str, bool]:
    reasons = {"artifact_unverified": not facts["artifact_verified"],
               "tests_failed": facts["tests_failed"] > 0,
               "test_run_incomplete": not facts["test_run_completed"],
               "blocking_findings_exceeded": facts["blocking_findings"] > threshold}
    return {"allow": not any(reasons.values()), **reasons}


def native_release_matrix(tools: Path, project: Path, scratch: Path,
                          threshold: int) -> list[dict]:
    facts = [{"artifact_verified": True, "tests_failed": 0,
              "test_run_completed": True, "blocking_findings": count}
             for count in (0, 1, 2, 3, 4, 5, 2**32 - 1)]
    facts.extend({"artifact_verified": artifact, "tests_failed": failures,
                  "test_run_completed": completed, "blocking_findings": count}
                 for artifact, failures, completed, count in
                 ((False, 0, True, 0), (True, 1, True, 0), (True, 0, False, 0),
                  (False, 1, False, 4), (True, 2, True, 3)))
    rows = []
    for index, item in enumerate(facts):
        input_path = scratch / f"release-{index}.json"
        input_path.write_text(json.dumps(item), encoding="utf-8")
        result = invoke(tools, "run", "--project", project, "--input", input_path)
        if result.get("result") != release_oracle(item, threshold):
            raise LabError(f"native release result differs at input {item}")
        rows.append({"input": item, "result": result["result"],
                     "state_root": result["state_root"]})
    return rows


def checked_project(workspace: Path, tools: Path,
                    program_id: str = "range-predicate",
                    profile: str | None = "graph_v0") -> tuple[Path, dict, dict]:
    project = workspace / "project"
    if project.is_symlink() or not project.is_dir():
        raise LabError("construct the native project in WORKSPACE/project")
    inspection = invoke(tools, "inspect", "--project", project)
    if inspection.get("root_verified_against_local_core_repository") is not True:
        raise LabError("project root was not verified against the native repository")
    manifest = inspection["program"]
    if manifest.get("program_id") != program_id or manifest.get("authoring_profile") != profile:
        raise LabError(f"project must be a native {program_id} program")
    if inspection["accepted_state_root"] != manifest["entrypoint"]["state_root"]:
        raise LabError("inspected root differs from the bound manifest")
    return project, inspection, manifest


def judge_rl01(workspace: Path, tools: Path, core: Path) -> dict:
    project, inspection, manifest = checked_project(workspace, tools)
    expected_parameters = [{"name": name, "value_type": "SInt64"}
                           for name in ("value", "lower", "upper")]
    expected_fields = [{"name": name, "value_type": "Bool"}
                       for name in ("within_range", "invalid_bounds", "below_lower", "above_upper")]
    if (inspection["authoring"]["parameters"] != expected_parameters
        or inspection["authoring"]["result"] != {"kind": "record", "fields": expected_fields}
        or manifest.get("imported_source_artifact") is not None):
        raise LabError("RL-01 needs a newly constructed graph with the declared interface")
    state = json.loads((project / ".sley-tools/state.json").read_text(encoding="utf-8"))
    if not state.get("commit_history") or state["commit_history"][0]["kind"] != "graph_construct":
        raise LabError("RL-01 must start with a native graph construction commit")
    artifact = manifest.get("artifact")
    if not isinstance(artifact, dict) or artifact.get("contract") != "sley-tools.native-graph-artifact.v1":
        raise LabError("export and bind the accepted native graph artifact")
    relative = artifact.get("path")
    if (not isinstance(relative, str) or not relative.startswith("artifact/")
        or Path(relative).is_absolute() or ".." in Path(relative).parts):
        raise LabError("artifact binding must stay inside the project artifact directory")
    artifact_path = project / relative
    if artifact_path.is_symlink() or not artifact_path.is_file() or digest(artifact_path) != artifact["sha256"]:
        raise LabError("bound native artifact is missing or differs from its digest")
    stored = json.loads(artifact_path.read_text(encoding="utf-8"))
    if stored.get("source_state_root") != inspection["accepted_state_root"]:
        raise LabError("bound native artifact is not the accepted version")
    test = invoke(tools, "test", "--project", project)
    if test.get("result") != "PASS" or test.get("external_assertions_against_native_execution", 0) < 8:
        raise LabError("RL-01 needs at least eight passing published native cases")
    with tempfile.TemporaryDirectory(prefix="sley-lab-rl01-judge-") as temporary:
        scratch = Path(temporary)
        rows = native_matrix(tools, project, scratch, False)
        replay_path = scratch / "replay"
        replay = invoke(tools, "artifact", "import", artifact_path,
                        replay_path, "--core", core)
        replay_root = replay["program"]["entrypoint"]["state_root"]
        if replay_root == inspection["accepted_state_root"]:
            raise LabError("fresh artifact import did not rebase the native authority")
        replay_rows = native_matrix(tools, replay_path, scratch, False)
    return {"accepted_root": inspection["accepted_state_root"], "replay_root": replay_root,
            "artifact_sha256": digest(artifact_path), "published_cases": test["external_assertions_against_native_execution"],
            "independent_native_cases": len(rows), "replayed_native_cases": len(replay_rows),
            "sample": rows[3]}


def judge_rl02(workspace: Path, tools: Path, marker: dict) -> dict:
    project, inspection, manifest = checked_project(workspace, tools)
    constants = {item["name"]: item["value"] for item in inspection["authoring"]["constants"]}
    if constants.get("exclude_upper_endpoint") is not False:
        raise LabError("upper endpoint remains excluded; commit the inclusive policy")
    state = json.loads((project / ".sley-tools/state.json").read_text(encoding="utf-8"))
    history = state.get("commit_history", [])
    if (len(history) < 3 or history[0]["state_root"] != marker["seed_root"]
        or history[1]["state_root"] != marker["flaw_root"]
        or history[1].get("candidate_id") != marker["flaw_candidate_id"]
        or history[1].get("receipt_id") != marker["flaw_receipt_id"]
        or history[-1].get("kind") != "graph_constant_edit"
        or history[-1].get("constant") != "exclude_upper_endpoint"
        or history[-1].get("old_value") is not True
        or history[-1].get("new_value") is not False
        or history[-1].get("state_root") != inspection["accepted_state_root"]
        or inspection["accepted_state_root"] != marker["seed_root"]):
        raise LabError("RL-02 needs a native correction from this prepared exclusive seed")
    if manifest.get("imported_source_artifact", {}).get("sha256") != digest(
        REPO / "programs/range-predicate/artifact/native-graph.json"):
        raise LabError("RL-02 must preserve the imported native seed lineage")
    test = invoke(tools, "test", "--project", project)
    if test.get("result") != "PASS" or test.get("external_assertions_against_native_execution") != 11:
        raise LabError("corrected public boundary cases did not pass")
    with tempfile.TemporaryDirectory(prefix="sley-lab-rl02-judge-") as temporary:
        rows = native_matrix(tools, project, Path(temporary), True)
    return {"seed_root": marker["seed_root"], "flaw_root": marker["flaw_root"],
            "accepted_root": inspection["accepted_state_root"],
            "correction_candidate_id": history[-1]["candidate_id"],
            "correction_receipt_id": history[-1]["receipt_id"],
            "published_cases": test["external_assertions_against_native_execution"],
            "independent_native_cases": len(rows), "sample": rows[3]}


def judge_rl03(workspace: Path, tools: Path, marker: dict) -> dict:
    project, inspection, manifest = checked_project(workspace, tools, "tier-classifier")
    constants = {item["name"]: item["value"] for item in inspection["authoring"]["constants"]}
    if constants.get("middle_tier_enabled") is not True:
        raise LabError("middle tier branch remains disabled; commit the enabled policy")
    if constants.get("bronze_max") != "10" or constants.get("silver_max") != "20":
        raise LabError("RL-03 must preserve both ordered thresholds")
    if (inspection["authoring"]["parameters"] != [{"name": "value", "value_type": "SInt64"}]
        or inspection["authoring"]["result"] != {"kind": "record", "fields": [
            {"name": "tier", "value_type": "SInt64"},
            {"name": "invalid_configuration", "value_type": "Bool"}]}):
        raise LabError("RL-03 needs the declared native tier interface")
    state = json.loads((project / ".sley-tools/state.json").read_text(encoding="utf-8"))
    history = state.get("commit_history", [])
    if (len(history) < 3 or history[0]["state_root"] != marker["seed_root"]
        or history[1]["state_root"] != marker["flaw_root"]
        or history[1].get("candidate_id") != marker["flaw_candidate_id"]
        or history[1].get("receipt_id") != marker["flaw_receipt_id"]
        or history[-1].get("kind") != "graph_constant_edit"
        or history[-1].get("constant") != "middle_tier_enabled"
        or history[-1].get("old_value") is not False
        or history[-1].get("new_value") is not True
        or history[-1].get("state_root") != inspection["accepted_state_root"]
        or inspection["accepted_state_root"] != marker["seed_root"]):
        raise LabError("RL-03 needs a native correction from this prepared branch seed")
    source = manifest.get("imported_source_artifact")
    if (not isinstance(source, dict)
        or source.get("sha256") != digest(
            REPO / "programs/tier-classifier/artifact/native-graph.json")):
        raise LabError("RL-03 must preserve the imported native classifier lineage")
    test = invoke(tools, "test", "--project", project)
    if test.get("result") != "PASS" or test.get("external_assertions_against_native_execution") != 11:
        raise LabError("corrected tier cases did not pass")
    with tempfile.TemporaryDirectory(prefix="sley-lab-rl03-judge-") as temporary:
        rows = native_tier_matrix(tools, project, Path(temporary))
    return {"seed_root": marker["seed_root"], "flaw_root": marker["flaw_root"],
            "accepted_root": inspection["accepted_state_root"],
            "correction_candidate_id": history[-1]["candidate_id"],
            "correction_receipt_id": history[-1]["receipt_id"],
            "published_cases": test["external_assertions_against_native_execution"],
            "independent_native_cases": len(rows),
            "bronze_sample": rows[5], "middle_sample": rows[6], "gold_sample": rows[11]}


def judge_rl04(workspace: Path, tools: Path, marker: dict) -> dict:
    project, inspection, manifest = checked_project(workspace, tools)
    root = inspection["accepted_state_root"]
    constants = {item["name"]: item["value"] for item in inspection["authoring"]["constants"]}
    history = json.loads((project / ".sley-tools/state.json").read_text(
        encoding="utf-8"))["commit_history"]
    source = manifest.get("imported_source_artifact")
    if (root != marker["flaw_root"] or constants.get("exclude_upper_endpoint") is not True
        or len(history) != 2 or history[0]["state_root"] != marker["seed_root"]
        or history[1].get("candidate_id") != marker["flaw_candidate_id"]
        or history[1].get("receipt_id") != marker["flaw_receipt_id"]
        or not isinstance(source, dict)
        or source.get("sha256") != digest(
            REPO / "programs/range-predicate/artifact/native-graph.json")):
        raise LabError("RL-04 must test the unchanged prepared native flaw")
    bound = invoke(tools, "test", "--project", project)
    if bound.get("result") != "PASS" or bound.get("external_assertions_against_native_execution") != 7:
        raise LabError("the incomplete seven-case native suite no longer passes")
    cases_path = workspace / "new-boundary-cases.json"
    if cases_path.is_symlink() or not cases_path.is_file() or cases_path.stat().st_size > 65_536:
        raise LabError("write a bounded new-boundary-cases.json file in the lab workspace")
    document = json.loads(cases_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise LabError("new boundary file must contain one graph-cases.v0 object")
    rows = document.get("cases")
    if (document.get("contract") != "sley-tools.graph-cases.v0"
        or not isinstance(rows, list) or len(rows) != 3
        or any(not isinstance(row, dict) or set(row) != {"id", "input", "expected"}
               for row in rows)):
        raise LabError("new boundary file needs exactly three graph-cases.v0 rows")
    identifiers = [row["id"] for row in rows]
    if (any(not isinstance(case_id, str) or not 1 <= len(case_id) <= 64
            for case_id in identifiers) or len(set(identifiers)) != 3):
        raise LabError("new boundary cases need three unique short IDs")
    parsed = []
    for row in rows:
        values = row["input"]
        if (not isinstance(values, list) or len(values) != 3
            or any(not isinstance(value, str) for value in values)):
            raise LabError("new boundary inputs must be three signed decimal strings")
        numbers = [int(value) for value in values]
        if (any(str(number) != value or not I64_MIN <= number <= I64_MAX
                for number, value in zip(numbers, values, strict=True))):
            raise LabError("new boundary inputs must be canonical signed 64 bit values")
        value, lower, upper = numbers
        if row["expected"] != oracle(value, lower, upper, True):
            raise LabError("new boundary expectation differs from the inclusive contract")
        parsed.append((value, lower, upper, row))
    intervals = {(lower, upper) for _, lower, upper, _ in parsed}
    if len(intervals) != 1:
        raise LabError("all three cases must share one interval")
    lower, upper = intervals.pop()
    if (lower >= upper or upper >= I64_MAX
        or {value for value, _, _, _ in parsed} != {upper - 1, upper, upper + 1}):
        raise LabError("cases must cover upper-1, upper, and upper+1 for valid bounds")
    mismatches = []
    with tempfile.TemporaryDirectory(prefix="sley-lab-rl04-judge-") as temporary:
        scratch = Path(temporary)
        for index, (value, _, _, row) in enumerate(parsed):
            input_path = scratch / f"case-{index}.json"
            input_path.write_text(json.dumps(row["input"]), encoding="utf-8")
            observed = invoke(tools, "run", "--project", project, "--input", input_path)
            if observed.get("state_root") != root:
                raise LabError("native result used a different accepted root")
            if observed["result"] != row["expected"]:
                mismatches.append((value, row, observed))
    if (len(mismatches) != 1 or mismatches[0][0] != upper
        or mismatches[0][2]["result"].get("within_range") is not False
        or mismatches[0][2]["result"].get("excluded_upper") is not True
        or invoke(tools, "inspect", "--project", project)["accepted_state_root"] != root):
        raise LabError("new case did not expose exactly the native upper-endpoint flaw")
    _, failed_case, native = mismatches[0]
    diagnostic = {"contract": "sley-tools.repair-lab-boundary-failure.v0",
                  "accepted_root": root, "case_file_sha256": digest(cases_path),
                  "bound_suite_cases": 7, "case_id": failed_case["id"],
                  "input": failed_case["input"], "expected": failed_case["expected"],
                  "observed": native["result"], "native_result_root": native["state_root"]}
    diagnostic_path = workspace / "boundary-failure.json"
    if diagnostic_path.is_symlink():
        raise LabError("refusing to replace a symlinked diagnostic")
    write_json(diagnostic_path, diagnostic)
    return {"accepted_root": root, "bound_suite_cases": 7,
            "new_cases": 3, "matched_neighbors": 2,
            "failed_case_id": failed_case["id"], "expected": failed_case["expected"],
            "observed": native["result"], "case_file_sha256": digest(cases_path),
            "diagnostic_sha256": digest(diagnostic_path)}


def judge_rl05(workspace: Path, tools: Path, marker: dict) -> dict:
    project, inspection, manifest = checked_project(workspace, tools, "tier-classifier")
    root = inspection["accepted_state_root"]
    constants = {item["name"]: item["value"] for item in inspection["authoring"]["constants"]}
    state = json.loads((project / ".sley-tools/state.json").read_text(encoding="utf-8"))
    history = state.get("commit_history", [])
    source = manifest.get("imported_source_artifact")
    if (constants.get("bronze_max") != "12" or constants.get("silver_max") != "20"
        or constants.get("middle_tier_enabled") is not True
        or len(history) != 2 or history[0].get("state_root") != marker["seed_root"]
        or history[1].get("kind") != "graph_constant_edit"
        or history[1].get("constant") != "bronze_max"
        or history[1].get("old_value") != "10"
        or history[1].get("new_value") != "12"
        or history[1].get("state_root") != root or root == marker["seed_root"]
        or not isinstance(source, dict)
        or source.get("sha256") != digest(
            REPO / "programs/tier-classifier/artifact/native-graph.json")):
        raise LabError("RL-05 needs one native typed threshold edit from its prepared seed")
    test = invoke(tools, "test", "--project", project)
    if test.get("result") != "PASS" or test.get("external_assertions_against_native_execution") != 11:
        raise LabError("corrected typed proposal did not pass the eleven bound cases")
    cases_path = workspace / "bronze-12-cases.json"
    if (cases_path.is_symlink() or not cases_path.is_file()
        or digest(cases_path) != digest(
            REPO / "programs/tier-classifier/tests/bronze-12-cases.json")
        or (project / ".sley-tools/proposal.json").exists()
        or (project / ".sley-tools/validated-proposal.json").exists()):
        raise LabError("RL-05 requires the published cases and no pending proposal")
    with tempfile.TemporaryDirectory(prefix="sley-lab-rl05-judge-") as temporary:
        rows = native_tier_matrix(tools, project, Path(temporary), 12)
    wrong_path = workspace / "wrong-value.json"
    fixture = REPO / "apps/repair-lab/exercises/rl05/wrong-value.json"
    diagnostic_path = workspace / "type-failure.json"
    if (wrong_path.is_symlink() or not wrong_path.is_file() or digest(wrong_path) != digest(fixture)
        or diagnostic_path.is_symlink() or not diagnostic_path.is_file()
        or diagnostic_path.stat().st_size > 4096):
        raise LabError("preserve the original wrong value and a bounded type-failure.json diagnostic")
    saved = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    try:
        failed = subprocess.run([str(tools), "propose", "--project", str(project),
                                 "--constant", "bronze_max", "--value-file", str(wrong_path),
                                 "--tests", str(cases_path)],
                                capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LabError(f"could not reproduce the typed-value failure: {error}") from error
    actual = json.loads(failed.stderr)
    if (failed.returncode != 2 or saved != actual
        or actual != {"code": "ST_INPUT_INVALID", "category": "input_shape",
                      "message": 'graph constant "bronze_max" has the wrong typed value',
                      "core_symbol": None, "core_numeric": None}
        or invoke(tools, "inspect", "--project", project)["accepted_state_root"] != root):
        raise LabError("saved diagnostic does not match the reproducible structured type failure")
    return {"seed_root": marker["seed_root"], "accepted_root": root,
            "correction_candidate_id": history[1]["candidate_id"],
            "correction_receipt_id": history[1]["receipt_id"],
            "diagnostic_sha256": digest(diagnostic_path),
            "diagnostic_code": actual["code"], "diagnostic_category": actual["category"],
            "published_cases": test["external_assertions_against_native_execution"],
            "independent_native_cases": len(rows),
            "bronze_sample": rows[7], "silver_sample": rows[8], "gold_sample": rows[11]}


def judge_rl06(workspace: Path, tools: Path, marker: dict) -> dict:
    project, inspection, manifest = checked_project(workspace, tools, "release-readiness", None)
    root = inspection["accepted_state_root"]
    history = json.loads((project / ".sley-tools/state.json").read_text(
        encoding="utf-8")).get("commit_history", [])
    source = manifest.get("imported_source_artifact")
    if (inspection.get("threshold") != 3 or len(history) != 3
        or history[0].get("state_root") != marker["seed_root"]
        or history[1].get("kind") != "threshold_edit"
        or history[1].get("old_threshold") != 0
        or history[1].get("new_threshold") != 4
        or history[1].get("state_root") != marker["advanced_root"]
        or history[1].get("candidate_id") != marker["advance_candidate_id"]
        or history[2].get("kind") != "threshold_edit"
        or history[2].get("old_threshold") != 4
        or history[2].get("new_threshold") != 3
        or history[2].get("state_root") != root or root == marker["advanced_root"]
        or not isinstance(source, dict)
        or source.get("sha256") != digest(
            REPO / "programs/release-readiness/artifact/native-program.json")
        or (project / ".sley-tools/proposal.json").exists()
        or (project / ".sley-tools/validated-proposal.json").exists()):
        raise LabError("RL-06 needs a fresh native threshold proposal after the advanced head")
    stale_path = workspace / "stale-proposal.json"
    if (stale_path.is_symlink() or not stale_path.is_file()
        or digest(stale_path) != marker["stale_proposal_sha256"]):
        raise LabError("preserve the prepared stale proposal for root comparison")
    stale = json.loads(stale_path.read_text(encoding="utf-8"))
    if (stale.get("base_root") != marker["seed_root"]
        or stale.get("candidate_id") != marker["stale_candidate_id"]):
        raise LabError("prepared stale proposal does not belong to the old root")
    records = {}
    for name in ("stale-failure.json", "reinspection.json", "fresh-proposal.json"):
        path = workspace / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 65_536:
            raise LabError(f"preserve a bounded {name} record from the native CLI")
        records[name] = json.loads(path.read_text(encoding="utf-8"))
    failure = records["stale-failure.json"]
    reinspected = records["reinspection.json"]
    fresh = records["fresh-proposal.json"]
    if (failure != {"code": "ST_STALE_BASE", "category": "stale_state",
                    "message": "proposal is not bound to the accepted project head",
                    "core_symbol": None, "core_numeric": None}
        or reinspected.get("contract") != "sley-tools.inspect.v0"
        or reinspected.get("accepted_state_root") != marker["advanced_root"]
        or reinspected.get("threshold") != 4
        or reinspected.get("root_verified_against_local_core_repository") is not True
        or reinspected.get("program", {}).get("entrypoint", {}).get("state_root") != marker["advanced_root"]
        or fresh.get("contract") != "sley-tools.propose.v0"
        or fresh.get("phase") != "PROPOSED"
        or fresh.get("base_root") != marker["advanced_root"]
        or fresh.get("candidate_id") == marker["stale_candidate_id"]
        or fresh.get("candidate_id") != history[2].get("candidate_id")
        or fresh.get("old_threshold") != 4 or fresh.get("new_threshold") != 3):
        raise LabError("RL-06 needs the stale diagnostic, reinspection, and new base-bound proposal")
    test = invoke(tools, "test", "--project", project)
    if (test.get("result") != "PASS" or test.get("threshold") != 3
        or test.get("external_assertions_against_native_execution", 0) < 40):
        raise LabError("the new threshold did not pass native execution assertions")
    with tempfile.TemporaryDirectory(prefix="sley-lab-rl06-judge-") as temporary:
        rows = native_release_matrix(tools, project, Path(temporary), 3)
    if any(row["state_root"] != root for row in rows):
        raise LabError("independent native runs used a different root")
    return {"seed_root": marker["seed_root"], "advanced_root": marker["advanced_root"],
            "accepted_root": root, "stale_candidate_id": marker["stale_candidate_id"],
            "fresh_candidate_id": history[2]["candidate_id"],
            "fresh_receipt_id": history[2]["receipt_id"],
            "stale_diagnostic_sha256": digest(workspace / "stale-failure.json"),
            "reinspection_sha256": digest(workspace / "reinspection.json"),
            "fresh_proposal_sha256": digest(workspace / "fresh-proposal.json"),
            "published_cases": test["external_assertions_against_native_execution"],
            "independent_native_cases": len(rows),
            "below_sample": rows[2], "at_sample": rows[3], "above_sample": rows[4]}


def judge_rl07(workspace: Path, tools: Path, marker: dict) -> dict:
    source_project, source_inspection, _ = checked_project(
        workspace, tools, "release-readiness", None)
    limited = source_inspection["authority"]
    if (source_inspection["accepted_state_root"] != marker["seed_root"]
        or source_inspection.get("threshold") != 0
        or limited.get("source") != "accepted_workspace_policy"
        or limited.get("policy_root") != marker["seed_policy_root"]
        or limited.get("principal_id") != marker["seed_principal_id"]
        or limited.get("replace_entity_version_allowed") is not False
        or (source_project / ".sley-tools/validated-proposal.json").exists()):
        raise LabError("RL-07 must preserve the original limited-grant accepted root")
    records = {}
    for name in ("limited-inspection.json", "permission-failure.json",
                 "recovered-inspection.json", "authority-explanation.json"):
        path = workspace / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 65_536:
            raise LabError(f"preserve a bounded {name} record")
        records[name] = json.loads(path.read_text(encoding="utf-8"))
    inspected_limited = records["limited-inspection.json"]
    denied = records["permission-failure.json"]
    if (inspected_limited.get("accepted_state_root") != marker["seed_root"]
        or inspected_limited.get("authority") != limited
        or denied != {"code": "ST_PERMISSION_DENIED", "category": "permission",
                      "message": "candidate.validate: CANDIDATE_VALIDATION_CAPABILITY_DENIED (POLICY_GRANT_DENIED)",
                      "core_symbol": "CANDIDATE_VALIDATION_CAPABILITY_DENIED",
                      "core_numeric": 36010}):
        raise LabError("saved inspection and diagnostic must show the real missing replace grant")
    artifact_path = workspace / "portable.json"
    if artifact_path.is_symlink() or not artifact_path.is_file() or artifact_path.stat().st_size > 1_048_576:
        raise LabError("export a bounded portable native artifact from the limited workspace")
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if (artifact.get("contract") != "sley-tools.native-program-artifact.v0"
        or artifact.get("program_id") != "release-readiness"
        or artifact.get("source_state_root") != marker["seed_root"]
        or artifact.get("threshold") != 0
        or any(name in artifact for name in ("policy_root", "principal_id", "grant"))):
        raise LabError("portable artifact does not identify the limited source root")
    destination = workspace / "recovered"
    if destination.is_symlink() or not destination.is_dir():
        raise LabError("import the artifact into a new recovered workspace")
    recovered = invoke(tools, "inspect", "--project", destination)
    manifest = recovered["program"]
    full = recovered["authority"]
    history = json.loads((destination / ".sley-tools/state.json").read_text(
        encoding="utf-8")).get("commit_history", [])
    imported_source = manifest.get("imported_source_artifact")
    first = records["recovered-inspection.json"]
    explanation = records["authority-explanation.json"]
    if (recovered["accepted_state_root"] == marker["seed_root"]
        or recovered.get("threshold") != 2
        or full.get("source") != "accepted_workspace_policy"
        or full.get("replace_entity_version_allowed") is not True
        or full.get("policy_root") == marker["seed_policy_root"]
        or full.get("principal_id") == marker["seed_principal_id"]
        or len(history) != 2 or history[1].get("kind") != "threshold_edit"
        or history[1].get("old_threshold") != 0
        or history[1].get("new_threshold") != 2
        or history[1].get("state_root") != recovered["accepted_state_root"]
        or not isinstance(imported_source, dict)
        or imported_source.get("sha256") != digest(artifact_path)
        or first.get("accepted_state_root") != history[0].get("state_root")
        or first.get("threshold") != 0
        or first.get("authority") != full
        or first.get("program", {}).get("entrypoint", {}).get("state_root") != history[0].get("state_root")
        or explanation != {"denied_mutation_class": "ReplaceEntityVersion",
                           "source_policy_root": marker["seed_policy_root"],
                           "destination_policy_root": full["policy_root"],
                           "resolution": "fresh_workspace_import"}):
        raise LabError("RL-07 requires a fresh owner-controlled grant and native correction")
    source_test = invoke(tools, "test", "--project", source_project)
    target_test = invoke(tools, "test", "--project", destination)
    if (source_test.get("result") != "PASS" or source_test.get("threshold") != 0
        or target_test.get("result") != "PASS" or target_test.get("threshold") != 2):
        raise LabError("source and recovered native programs did not pass their bound tests")
    with tempfile.TemporaryDirectory(prefix="sley-lab-rl07-judge-") as temporary:
        scratch = Path(temporary)
        source_rows = native_release_matrix(tools, source_project, scratch, 0)
        target_rows = native_release_matrix(tools, destination, scratch, 2)
    return {"source_root": marker["seed_root"],
            "source_policy_root": marker["seed_policy_root"],
            "imported_root": history[0]["state_root"],
            "accepted_root": recovered["accepted_state_root"],
            "destination_policy_root": full["policy_root"],
            "artifact_sha256": digest(artifact_path),
            "permission_diagnostic_sha256": digest(workspace / "permission-failure.json"),
            "correction_candidate_id": history[1]["candidate_id"],
            "correction_receipt_id": history[1]["receipt_id"],
            "source_native_cases": len(source_rows),
            "recovered_native_cases": len(target_rows),
            "source_at_two": source_rows[2], "recovered_at_two": target_rows[2]}


def judge_rl08(workspace: Path, tools: Path, marker: dict) -> dict:
    project, source_inspection, source_manifest = checked_project(
        workspace, tools, "tier-classifier")
    source_root = source_inspection["accepted_state_root"]
    constants = {item["name"]: item["value"] for item in source_inspection["authoring"]["constants"]}
    source_history = json.loads((project / ".sley-tools/state.json").read_text(
        encoding="utf-8")).get("commit_history", [])
    binding = source_manifest.get("artifact")
    if (constants.get("bronze_max") != "12" or constants.get("silver_max") != "20"
        or constants.get("middle_tier_enabled") is not True
        or len(source_history) != 2
        or source_history[0].get("state_root") != marker["seed_root"]
        or source_history[1].get("kind") != "graph_constant_edit"
        or source_history[1].get("constant") != "bronze_max"
        or source_history[1].get("old_value") != "10"
        or source_history[1].get("new_value") != "12"
        or source_history[1].get("state_root") != source_root
        or not isinstance(binding, dict)
        or binding.get("path") != "artifact/edited.json"):
        raise LabError("RL-08 needs one native classifier edit and a bound current artifact")
    artifact_path = project / "artifact/edited.json"
    if (artifact_path.is_symlink() or not artifact_path.is_file()
        or digest(artifact_path) != binding.get("sha256")):
        raise LabError("bound edited native artifact is missing or differs from its digest")
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if (artifact.get("contract") != "sley-tools.native-graph-artifact.v1"
        or artifact.get("source_state_root") != source_root
        or artifact.get("program_id") != "tier-classifier"):
        raise LabError("bound artifact does not represent the changed accepted graph")
    destination = workspace / "copied"
    if destination.is_symlink() or not destination.is_dir():
        raise LabError("import the edited artifact into WORKSPACE/copied")
    imported = invoke(tools, "inspect", "--project", destination)
    target_manifest = imported["program"]
    target_root = imported["accepted_state_root"]
    target_constants = {item["name"]: item["value"] for item in imported["authoring"]["constants"]}
    target_history = json.loads((destination / ".sley-tools/state.json").read_text(
        encoding="utf-8")).get("commit_history", [])
    imported_source = target_manifest.get("imported_source_artifact")
    if (imported.get("root_verified_against_local_core_repository") is not True
        or target_root == source_root
        or imported["authority"]["policy_root"] == source_inspection["authority"]["policy_root"]
        or imported["authority"]["principal_id"] == source_inspection["authority"]["principal_id"]
        or target_constants != constants
        or len(target_history) != 1 or target_history[0].get("kind") != "graph_artifact_import"
        or target_history[0].get("state_root") != target_root
        or target_manifest.get("artifact") is not None
        or not isinstance(imported_source, dict)
        or imported_source.get("sha256") != digest(artifact_path)):
        raise LabError("imported graph must preserve behavior under fresh workspace authority")
    input_path = workspace / "input-12.json"
    if (input_path.is_symlink() or not input_path.is_file()
        or digest(input_path) != digest(
            REPO / "apps/repair-lab/exercises/rl08/input-12.json")):
        raise LabError("preserve the published typed input for both native runs")
    source_test = invoke(tools, "test", "--project", project)
    target_test = invoke(tools, "test", "--project", destination)
    source_run = invoke(tools, "run", "--project", project, "--input", input_path)
    target_run = invoke(tools, "run", "--project", destination, "--input", input_path)
    if (source_test.get("result") != "PASS"
        or target_test.get("result") != "PASS"
        or source_test.get("external_assertions_against_native_execution") != 11
        or target_test.get("external_assertions_against_native_execution") != 11
        or source_run.get("state_root") != source_root
        or target_run.get("state_root") != target_root
        or source_run.get("result") != {"tier": "0", "invalid_configuration": False}
        or target_run.get("result") != source_run["result"]):
        raise LabError("source and imported native behavior or bound cases differ")
    for name, actual in (("source-inspection.json", source_inspection),
                         ("destination-inspection.json", imported),
                         ("source-test.json", source_test),
                         ("destination-test.json", target_test),
                         ("source-run.json", source_run),
                         ("destination-run.json", target_run)):
        path = workspace / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 65_536:
            raise LabError(f"save a bounded {name} from the native CLI")
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved != actual:
            raise LabError(f"saved {name} differs from the current native observation")
    with tempfile.TemporaryDirectory(prefix="sley-lab-rl08-judge-") as temporary:
        scratch = Path(temporary)
        source_rows = native_tier_matrix(tools, project, scratch, 12)
        target_rows = native_tier_matrix(tools, destination, scratch, 12)
    if ([row["result"] for row in source_rows] != [row["result"] for row in target_rows]
        or any(row["state_root"] != source_root for row in source_rows)
        or any(row["state_root"] != target_root for row in target_rows)):
        raise LabError("independent native results did not survive artifact transfer")
    return {"seed_root": marker["seed_root"], "source_root": source_root,
            "destination_root": target_root,
            "source_policy_root": source_inspection["authority"]["policy_root"],
            "destination_policy_root": imported["authority"]["policy_root"],
            "artifact_sha256": digest(artifact_path),
            "source_manifest_sha256": digest(project / "program.json"),
            "destination_manifest_sha256": digest(destination / "program.json"),
            "source_run_sha256": digest(workspace / "source-run.json"),
            "destination_run_sha256": digest(workspace / "destination-run.json"),
            "source_native_cases": len(source_rows),
            "destination_native_cases": len(target_rows),
            "source_at_twelve": source_run["result"],
            "destination_at_twelve": target_run["result"]}


def judge(workspace: Path, tools: Path, core: Path) -> tuple[int, dict]:
    marker = load_marker(workspace, tools, core)
    exercise = marker["exercise"]
    try:
        details = (judge_rl01(workspace, tools, core) if exercise == "rl01"
                   else judge_rl02(workspace, tools, marker) if exercise == "rl02"
                   else judge_rl03(workspace, tools, marker) if exercise == "rl03"
                   else judge_rl04(workspace, tools, marker) if exercise == "rl04"
                   else judge_rl05(workspace, tools, marker) if exercise == "rl05"
                   else judge_rl06(workspace, tools, marker) if exercise == "rl06"
                   else judge_rl07(workspace, tools, marker) if exercise == "rl07"
                   else judge_rl08(workspace, tools, marker))
    except (LabError, OSError, KeyError, ValueError, json.JSONDecodeError) as error:
        return 1, {"contract": JUDGE_CONTRACT, "result": "FAIL",
                   "exercise": exercise, "reason": str(error)}
    return 0, {"contract": JUDGE_CONTRACT, "result": "PASS",
               "exercise": exercise, "evidence": details,
               "native_core_test_admission": "NOT_RUN: production native executor unavailable"}


def reset(workspace: Path, tools: Path, core: Path) -> dict:
    marker = load_marker(workspace, tools, core)
    project = workspace / "project"
    recovered = workspace / "recovered"
    copied = workspace / "copied"
    if project.is_symlink():
        raise LabError("refusing to reset a symlinked project")
    if marker["exercise"] == "rl07" and recovered.is_symlink():
        raise LabError("refusing to reset a symlinked recovered project")
    if marker["exercise"] == "rl08" and copied.is_symlink():
        raise LabError("refusing to reset a symlinked copied project")
    if marker["exercise"] == "rl07" and recovered.exists() and not recovered.is_dir():
        raise LabError("recovered project path is not a directory")
    if marker["exercise"] == "rl08" and copied.exists() and not copied.is_dir():
        raise LabError("copied project path is not a directory")
    if project.exists() and not project.is_dir():
        raise LabError("project path is not a directory")
    if marker["exercise"] == "rl07" and recovered.exists():
        shutil.rmtree(recovered)
    if marker["exercise"] == "rl08" and copied.exists():
        shutil.rmtree(copied)
    if project.exists():
        shutil.rmtree(project)
    if marker["exercise"] == "rl02":
        seed_rl02(workspace, tools, core, marker)
    elif marker["exercise"] == "rl03":
        seed_rl03(workspace, tools, core, marker)
    elif marker["exercise"] == "rl04":
        seed_rl02(workspace, tools, core, marker, "missing-upper-cases.json")
    elif marker["exercise"] == "rl05":
        seed_rl05(workspace, tools, core, marker)
    elif marker["exercise"] == "rl06":
        seed_rl06(workspace, tools, core, marker)
    elif marker["exercise"] == "rl07":
        seed_rl07(workspace, tools, core, marker)
    elif marker["exercise"] == "rl08":
        seed_rl08(workspace, tools, core, marker)
    return {"contract": CONTRACT, "result": "RESET", "exercise": marker["exercise"],
            "workspace": str(workspace), "project": str(project),
            "flaw_root": marker.get("flaw_root")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    starter = commands.add_parser("start")
    starter.add_argument("exercise", choices=tuple(EXERCISES))
    for command in (starter, commands.add_parser("judge"), commands.add_parser("reset")):
        command.add_argument("--workspace", required=True)
        command.add_argument("--core", required=True)
        command.add_argument("--tools", required=True)
    args = parser.parse_args()
    if args.command == "list":
        print(json.dumps({"contract": "sley-tools.repair-lab-index.v0",
                          "available": [{"id": key, "name": value[0], "goal": value[1]}
                                        for key, value in EXERCISES.items()],
                          "pending": []},
                         sort_keys=True))
        return 0
    try:
        workspace = workspace_path(args.workspace)
        core = checked_binary(args.core, "core")
        tools = checked_binary(args.tools, "tools")
        if args.command == "start":
            output = start(args.exercise, workspace, tools, core)
            code = 0
        elif args.command == "judge":
            code, output = judge(workspace, tools, core)
        else:
            output = reset(workspace, tools, core)
            code = 0
    except (LabError, OSError, KeyError, ValueError) as error:
        code = 2
        output = {"contract": "sley-tools.repair-lab-error.v0", "result": "ERROR",
                  "message": str(error)}
    print(json.dumps(output, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
