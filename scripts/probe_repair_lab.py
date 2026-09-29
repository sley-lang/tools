#!/usr/bin/env python3
"""Exercise public RL-01 through RL-08 wrong and worked native paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
LAB = REPO / "apps/repair-lab/lab.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lab(*args: object, code: int = 0) -> dict:
    result = subprocess.run([sys.executable, str(LAB), *map(str, args)],
                            cwd=REPO, capture_output=True, text=True, timeout=180, check=False)
    if result.returncode != code:
        raise RuntimeError(f"lab command exited {result.returncode}, expected {code}: {result.stdout} {result.stderr}")
    return json.loads(result.stdout)


def tools(binary: Path, *args: object) -> dict:
    result = subprocess.run([str(binary), *map(str, args)], cwd=REPO,
                            capture_output=True, text=True, timeout=120, check=False)
    if result.returncode:
        raise RuntimeError(f"native tools command failed: {result.stderr[:500]}")
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    binary = args.tools.resolve()
    core = args.core.resolve()
    listing = lab("list")
    if ([entry["id"] for entry in listing["available"]] != ["rl01", "rl02", "rl03", "rl04", "rl05", "rl06", "rl07", "rl08"]
        or listing["pending"] != []):
        raise RuntimeError("Repair Lab index misstates the current curriculum")
    with tempfile.TemporaryDirectory(prefix="sley-repair-lab-probe-") as temporary:
        parent = Path(temporary)
        outside = parent / "outside.txt"
        outside.write_text("unrelated user file\n", encoding="utf-8")
        common = ("--core", core, "--tools", binary)
        first = parent / "rl01"
        created = lab("start", "rl01", "--workspace", first, *common)
        if created["result"] != "READY" or created["project"] != str(first / "project"):
            raise RuntimeError("RL-01 did not start an isolated workspace")
        missing = lab("judge", "--workspace", first, *common, code=1)
        if missing["result"] != "FAIL":
            raise RuntimeError("unconstructed RL-01 project passed")
        imported = tools(binary, "artifact", "import",
                         REPO / "programs/range-predicate/artifact/native-graph.json",
                         first / "project", "--core", core)
        tools(binary, "artifact", "export", "--project", first / "project",
              "--output", first / "project/artifact/imported.json", "--bind-manifest")
        wrong = lab("judge", "--workspace", first, *common, code=1)
        if wrong["result"] != "FAIL" or "newly constructed" not in wrong["reason"]:
            raise RuntimeError("RL-01 accepted an imported answer")
        note = first / "notes.txt"
        note.write_text("keep my work\n", encoding="utf-8")
        reset_first = lab("reset", "--workspace", first, *common)
        if (reset_first["result"] != "RESET" or (first / "project").exists()
            or note.read_text(encoding="utf-8") != "keep my work\n"
            or outside.read_text(encoding="utf-8") != "unrelated user file\n"):
            raise RuntimeError("RL-01 reset removed unrelated work")
        tools(binary, "init", first / "project", "--graph",
              REPO / "examples/graph/range-predicate.json", "--tests",
              REPO / "examples/graph/range-predicate-cases.json", "--core", core)
        tools(binary, "artifact", "export", "--project", first / "project",
              "--output", first / "project/artifact/lab-range.json", "--bind-manifest")
        solved_first = lab("judge", "--workspace", first, *common)
        if (solved_first["result"] != "PASS"
            or solved_first["evidence"]["independent_native_cases"] != 16
            or solved_first["evidence"]["replayed_native_cases"] != 16):
            raise RuntimeError("RL-01 worked native construction did not pass")
        saved_project = first / "project-saved"
        (first / "project").rename(saved_project)
        (first / "project").symlink_to(outside)
        symlink_reset = lab("reset", "--workspace", first, *common, code=2)
        if (symlink_reset["result"] != "ERROR"
            or outside.read_text(encoding="utf-8") != "unrelated user file\n"):
            raise RuntimeError("Repair Lab accepted a symlinked project reset")
        (first / "project").unlink()
        saved_project.rename(first / "project")
        unmarked = parent / "unmarked"
        unmarked.mkdir()
        (unmarked / "project").mkdir()
        unmarked_reset = lab("reset", "--workspace", unmarked, *common, code=2)
        if (unmarked_reset["result"] != "ERROR"
            or not (unmarked / "project").is_dir()):
            raise RuntimeError("Repair Lab reset an unmarked directory")

        second = parent / "rl02"
        prepared = lab("start", "rl02", "--workspace", second, *common)
        if prepared["result"] != "READY" or not prepared["flaw_root"]:
            raise RuntimeError("RL-02 did not prepare an accepted flawed graph")
        flawed = lab("judge", "--workspace", second, *common, code=1)
        if flawed["result"] != "FAIL" or "remains excluded" not in flawed["reason"]:
            raise RuntimeError("RL-02 intended endpoint flaw was not rejected")
        policy = second / "include-upper.json"
        policy.write_text("false\n", encoding="utf-8")
        proposal = tools(binary, "propose", "--project", second / "project",
                         "--constant", "exclude_upper_endpoint", "--value-file", policy,
                         "--tests", second / "inclusive-cases.json")
        validated = tools(binary, "validate", "--project", second / "project")
        committed = tools(binary, "commit", "--project", second / "project")
        if not proposal["candidate_id"] == validated["candidate_id"] == committed["candidate_id"]:
            raise RuntimeError("RL-02 worked resolution lost native candidate identity")
        solved_second = lab("judge", "--workspace", second, *common)
        if (solved_second["result"] != "PASS"
            or solved_second["evidence"]["accepted_root"] == prepared["flaw_root"]
            or solved_second["evidence"]["independent_native_cases"] != 16):
            raise RuntimeError("RL-02 worked native correction did not pass")
        reset_second = lab("reset", "--workspace", second, *common)
        flawed_again = lab("judge", "--workspace", second, *common, code=1)
        if (reset_second["result"] != "RESET" or flawed_again["result"] != "FAIL"
            or outside.read_text(encoding="utf-8") != "unrelated user file\n"):
            raise RuntimeError("RL-02 reset did not confine and restore its native seed")

        third = parent / "rl03"
        prepared_branch = lab("start", "rl03", "--workspace", third, *common)
        if prepared_branch["result"] != "READY" or not prepared_branch["flaw_root"]:
            raise RuntimeError("RL-03 did not prepare an accepted flawed classifier")
        flawed_branch = lab("judge", "--workspace", third, *common, code=1)
        if (flawed_branch["result"] != "FAIL"
            or "remains disabled" not in flawed_branch["reason"]):
            raise RuntimeError("RL-03 disabled middle branch was not rejected")
        enabled_policy = third / "enable-middle.json"
        enabled_policy.write_text("true\n", encoding="utf-8")
        branch_proposal = tools(binary, "propose", "--project", third / "project",
                                "--constant", "middle_tier_enabled",
                                "--value-file", enabled_policy,
                                "--tests", third / "correct-cases.json")
        branch_validation = tools(binary, "validate", "--project", third / "project")
        branch_commit = tools(binary, "commit", "--project", third / "project")
        if not branch_proposal["candidate_id"] == branch_validation["candidate_id"] == branch_commit["candidate_id"]:
            raise RuntimeError("RL-03 worked resolution lost native candidate identity")
        solved_third = lab("judge", "--workspace", third, *common)
        if (solved_third["result"] != "PASS"
            or solved_third["evidence"]["accepted_root"] == prepared_branch["flaw_root"]
            or solved_third["evidence"]["independent_native_cases"] != 14
            or [solved_third["evidence"][key]["result"]["tier"] for key in
                ("bronze_sample", "middle_sample", "gold_sample")] != ["0", "1", "2"]):
            raise RuntimeError("RL-03 worked native branch correction did not pass")
        reset_third = lab("reset", "--workspace", third, *common)
        flawed_branch_again = lab("judge", "--workspace", third, *common, code=1)
        if (reset_third["result"] != "RESET" or flawed_branch_again["result"] != "FAIL"
            or outside.read_text(encoding="utf-8") != "unrelated user file\n"):
            raise RuntimeError("RL-03 reset did not confine and restore its native seed")

        fourth = parent / "rl04"
        prepared_tests = lab("start", "rl04", "--workspace", fourth, *common)
        if prepared_tests["result"] != "READY" or not prepared_tests["flaw_root"]:
            raise RuntimeError("RL-04 did not prepare an accepted flawed range graph")
        if (tools(binary, "test", "--project", fourth / "project").get("external_assertions_against_native_execution") != 7
            or lab("judge", "--workspace", fourth, *common, code=1)["result"] != "FAIL"):
            raise RuntimeError("RL-04 incomplete suite or missing new case did not fail as expected")
        submission = fourth / "new-boundary-cases.json"
        solution = REPO / "apps/repair-lab/exercises/rl04/solution-cases.json"
        wrong = json.loads(solution.read_text(encoding="utf-8"))
        wrong["cases"][1]["input"][0] = "5"
        submission.write_text(json.dumps(wrong), encoding="utf-8")
        missed = lab("judge", "--workspace", fourth, *common, code=1)
        if missed["result"] != "FAIL" or "upper-1, upper, and upper+1" not in missed["reason"]:
            raise RuntimeError("RL-04 accepted a submission missing the upper endpoint")
        wrong["cases"][1]["input"][0] = "7"
        wrong["cases"][1]["expected"]["within_range"] = False
        submission.write_text(json.dumps(wrong), encoding="utf-8")
        incorrect = lab("judge", "--workspace", fourth, *common, code=1)
        if incorrect["result"] != "FAIL" or "inclusive contract" not in incorrect["reason"]:
            raise RuntimeError("RL-04 accepted the flawed result as the expected value")
        shutil.copyfile(solution, submission)
        solved_fourth = lab("judge", "--workspace", fourth, *common)
        diagnostic_path = fourth / "boundary-failure.json"
        diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
        if (solved_fourth["result"] != "PASS"
            or solved_fourth["evidence"]["accepted_root"] != prepared_tests["flaw_root"]
            or solved_fourth["evidence"]["bound_suite_cases"] != 7
            or solved_fourth["evidence"]["new_cases"] != 3
            or solved_fourth["evidence"]["matched_neighbors"] != 2
            or solved_fourth["evidence"]["diagnostic_sha256"] != digest(diagnostic_path)
            or diagnostic["native_result_root"] != prepared_tests["flaw_root"]
            or diagnostic["case_file_sha256"] != digest(submission)
            or diagnostic["input"] != ["7", "-7", "7"]
            or diagnostic["expected"]["within_range"] is not True
            or diagnostic["observed"]["within_range"] is not False
            or diagnostic["observed"]["excluded_upper"] is not True):
            raise RuntimeError("RL-04 did not capture the native upper-endpoint failure")
        note_fourth = fourth / "notes.txt"
        note_fourth.write_text("keep my tests\n", encoding="utf-8")
        reset_fourth = lab("reset", "--workspace", fourth, *common)
        if (reset_fourth["result"] != "RESET"
            or note_fourth.read_text(encoding="utf-8") != "keep my tests\n"
            or digest(submission) != digest(solution)
            or outside.read_text(encoding="utf-8") != "unrelated user file\n"
            or tools(binary, "test", "--project", fourth / "project")["external_assertions_against_native_execution"] != 7):
            raise RuntimeError("RL-04 reset did not confine and restore its native seed")
        solved_after_reset = lab("judge", "--workspace", fourth, *common)
        if (solved_after_reset["result"] != "PASS"
            or solved_after_reset["evidence"]["accepted_root"] != reset_fourth["flaw_root"]):
            raise RuntimeError("RL-04 new test failed to expose the reset native seed")

        fifth = parent / "rl05"
        prepared_type = lab("start", "rl05", "--workspace", fifth, *common)
        if prepared_type["result"] != "READY":
            raise RuntimeError("RL-05 did not prepare the native typed classifier")
        initial_type_root = tools(binary, "inspect", "--project", fifth / "project")["accepted_state_root"]
        missing_type = lab("judge", "--workspace", fifth, *common, code=1)
        if missing_type["result"] != "FAIL" or "native typed threshold edit" not in missing_type["reason"]:
            raise RuntimeError("RL-05 accepted an unchanged classifier")
        wrong_type = subprocess.run([str(binary), "propose", "--project", str(fifth / "project"),
                                     "--constant", "bronze_max", "--value-file",
                                     str(fifth / "wrong-value.json"), "--tests",
                                     str(fifth / "bronze-12-cases.json")],
                                    cwd=REPO, capture_output=True, text=True,
                                    timeout=120, check=False)
        if (wrong_type.returncode != 2
            or json.loads(wrong_type.stderr).get("code") != "ST_INPUT_INVALID"
            or (fifth / "project/.sley-tools/proposal.json").exists()
            or tools(binary, "inspect", "--project", fifth / "project")["accepted_state_root"] != initial_type_root):
            raise RuntimeError("RL-05 wrong typed proposal did not fail without changing state")
        corrected = fifth / "correct-value.json"
        corrected.write_text('"12"\n', encoding="utf-8")
        type_proposal = tools(binary, "propose", "--project", fifth / "project",
                              "--constant", "bronze_max", "--value-file", corrected,
                              "--tests", fifth / "bronze-12-cases.json")
        type_validation = tools(binary, "validate", "--project", fifth / "project")
        type_commit = tools(binary, "commit", "--project", fifth / "project")
        if not type_proposal["candidate_id"] == type_validation["candidate_id"] == type_commit["candidate_id"]:
            raise RuntimeError("RL-05 corrected proposal lost native candidate identity")
        diagnostic = fifth / "type-failure.json"
        diagnostic.write_text("{}\n", encoding="utf-8")
        forged = lab("judge", "--workspace", fifth, *common, code=1)
        if forged["result"] != "FAIL" or "structured type failure" not in forged["reason"]:
            raise RuntimeError("RL-05 accepted an invented type diagnostic")
        diagnostic.write_text(wrong_type.stderr, encoding="utf-8")
        solved_fifth = lab("judge", "--workspace", fifth, *common)
        fifth_evidence = solved_fifth["evidence"]
        if (solved_fifth["result"] != "PASS"
            or fifth_evidence["seed_root"] != initial_type_root
            or fifth_evidence["accepted_root"] == initial_type_root
            or fifth_evidence["correction_candidate_id"] != type_commit["candidate_id"]
            or fifth_evidence["diagnostic_sha256"] != digest(diagnostic)
            or fifth_evidence["diagnostic_code"] != "ST_INPUT_INVALID"
            or fifth_evidence["diagnostic_category"] != "input_shape"
            or fifth_evidence["published_cases"] != 11
            or fifth_evidence["independent_native_cases"] != 14
            or [fifth_evidence[key]["result"]["tier"] for key in
                ("bronze_sample", "silver_sample", "gold_sample")] != ["0", "1", "2"]):
            raise RuntimeError("RL-05 did not preserve the failure and correct native behavior")
        note_fifth = fifth / "notes.txt"
        note_fifth.write_text("keep my diagnosis\n", encoding="utf-8")
        reset_fifth = lab("reset", "--workspace", fifth, *common)
        reset_fifth_judge = lab("judge", "--workspace", fifth, *common, code=1)
        if (reset_fifth["result"] != "RESET" or reset_fifth_judge["result"] != "FAIL"
            or note_fifth.read_text(encoding="utf-8") != "keep my diagnosis\n"
            or digest(diagnostic) != fifth_evidence["diagnostic_sha256"]
            or outside.read_text(encoding="utf-8") != "unrelated user file\n"):
            raise RuntimeError("RL-05 reset did not confine and restore its native seed")

        sixth = parent / "rl06"
        prepared_stale = lab("start", "rl06", "--workspace", sixth, *common)
        if prepared_stale["result"] != "READY":
            raise RuntimeError("RL-06 did not prepare the advanced native head")
        advanced_inspection = tools(binary, "inspect", "--project", sixth / "project")
        stale_proposal = json.loads((sixth / "stale-proposal.json").read_text(encoding="utf-8"))
        if (advanced_inspection["threshold"] != 4
            or stale_proposal["base_root"] == advanced_inspection["accepted_state_root"]
            or lab("judge", "--workspace", sixth, *common, code=1)["result"] != "FAIL"):
            raise RuntimeError("RL-06 advanced seed or stale proposal was not rejected")
        blocked_proposal = subprocess.run([str(binary), "propose", "--project", str(sixth / "project"),
                                           "--max-blocking-findings", "3"], cwd=REPO,
                                          capture_output=True, text=True, timeout=120, check=False)
        if (blocked_proposal.returncode != 2
            or json.loads(blocked_proposal.stderr).get("code") != "ST_INPUT_INVALID"):
            raise RuntimeError("RL-06 silently replaced an existing proposal")
        stale_validation = subprocess.run([str(binary), "validate", "--project", str(sixth / "project")],
                                          cwd=REPO, capture_output=True, text=True,
                                          timeout=120, check=False)
        if (stale_validation.returncode != 4
            or json.loads(stale_validation.stderr).get("code") != "ST_STALE_BASE"):
            raise RuntimeError("RL-06 stale candidate was not rejected by validation")
        stale_failure = sixth / "stale-failure.json"
        stale_failure.write_text(stale_validation.stderr, encoding="utf-8")
        reinspection = sixth / "reinspection.json"
        reinspection.write_text(json.dumps(advanced_inspection) + "\n", encoding="utf-8")
        discarded = tools(binary, "propose", "discard", "--project", sixth / "project")
        if (discarded["candidate_id"] != stale_proposal["candidate_id"]
            or discarded["accepted_root"] != advanced_inspection["accepted_state_root"]):
            raise RuntimeError("RL-06 discard did not preserve the accepted head")
        fresh_proposal = tools(binary, "propose", "--project", sixth / "project",
                               "--max-blocking-findings", 3)
        fresh_record = sixth / "fresh-proposal.json"
        fresh_record.write_text(json.dumps(fresh_proposal) + "\n", encoding="utf-8")
        fresh_validation = tools(binary, "validate", "--project", sixth / "project")
        fresh_commit = tools(binary, "commit", "--project", sixth / "project")
        if (fresh_proposal["base_root"] != advanced_inspection["accepted_state_root"]
            or not fresh_proposal["candidate_id"] == fresh_validation["candidate_id"] == fresh_commit["candidate_id"]):
            raise RuntimeError("RL-06 fresh proposal lost its new base or candidate identity")
        reinspection.write_text(json.dumps({**advanced_inspection,
                                            "accepted_state_root": stale_proposal["base_root"]}) + "\n",
                                encoding="utf-8")
        forged_reinspection = lab("judge", "--workspace", sixth, *common, code=1)
        if (forged_reinspection["result"] != "FAIL"
            or "reinspection" not in forged_reinspection["reason"]):
            raise RuntimeError("RL-06 accepted an old-root reinspection")
        reinspection.write_text(json.dumps(advanced_inspection) + "\n", encoding="utf-8")
        solved_sixth = lab("judge", "--workspace", sixth, *common)
        sixth_evidence = solved_sixth["evidence"]
        if (solved_sixth["result"] != "PASS"
            or sixth_evidence["seed_root"] != stale_proposal["base_root"]
            or sixth_evidence["advanced_root"] != advanced_inspection["accepted_state_root"]
            or sixth_evidence["accepted_root"] != fresh_commit["new_root"]
            or sixth_evidence["stale_candidate_id"] != stale_proposal["candidate_id"]
            or sixth_evidence["fresh_candidate_id"] != fresh_commit["candidate_id"]
            or sixth_evidence["independent_native_cases"] != 12
            or sixth_evidence["published_cases"] < 40
            or [sixth_evidence[key]["result"]["allow"] for key in
                ("below_sample", "at_sample", "above_sample")] != [True, True, False]):
            raise RuntimeError("RL-06 did not recover from the stale native proposal")
        note_sixth = sixth / "notes.txt"
        note_sixth.write_text("keep my recovery notes\n", encoding="utf-8")
        reset_sixth = lab("reset", "--workspace", sixth, *common)
        reset_sixth_judge = lab("judge", "--workspace", sixth, *common, code=1)
        reset_stale = subprocess.run([str(binary), "validate", "--project", str(sixth / "project")],
                                     cwd=REPO, capture_output=True, text=True,
                                     timeout=120, check=False)
        if (reset_sixth["result"] != "RESET" or reset_sixth_judge["result"] != "FAIL"
            or reset_stale.returncode != 4
            or json.loads(reset_stale.stderr).get("code") != "ST_STALE_BASE"
            or note_sixth.read_text(encoding="utf-8") != "keep my recovery notes\n"
            or outside.read_text(encoding="utf-8") != "unrelated user file\n"):
            raise RuntimeError("RL-06 reset did not confine and restore the stale native seed")

        seventh = parent / "rl07"
        prepared_authority = lab("start", "rl07", "--workspace", seventh, *common)
        if prepared_authority["result"] != "READY":
            raise RuntimeError("RL-07 did not prepare the scoped native workspace")
        limited = tools(binary, "inspect", "--project", seventh / "project")
        limited_authority = limited["authority"]
        if (limited["threshold"] != 0 or limited_authority["replace_entity_version_allowed"] is not False
            or [item["name"] for item in limited_authority["allowed_mutation_classes"]]
                != ["CreateEntity", "AddEntryPoint", "AddTest"]
            or lab("judge", "--workspace", seventh, *common, code=1)["result"] != "FAIL"):
            raise RuntimeError("RL-07 limited grant or initial judge did not match the native policy")
        limited_record = seventh / "limited-inspection.json"
        limited_record.write_text(json.dumps(limited) + "\n", encoding="utf-8")
        limited_proposal = tools(binary, "propose", "--project", seventh / "project",
                                 "--max-blocking-findings", 2)
        denied = subprocess.run([str(binary), "validate", "--project", str(seventh / "project")],
                                cwd=REPO, capture_output=True, text=True,
                                timeout=120, check=False)
        denied_json = json.loads(denied.stderr)
        if (denied.returncode != 4 or denied_json.get("code") != "ST_PERMISSION_DENIED"
            or denied_json.get("core_symbol") != "CANDIDATE_VALIDATION_CAPABILITY_DENIED"
            or denied_json.get("core_numeric") != 36010
            or (seventh / "project/.sley-tools/validated-proposal.json").exists()
            or tools(binary, "inspect", "--project", seventh / "project")["accepted_state_root"] != limited["accepted_state_root"]):
            raise RuntimeError("RL-07 native validation did not deny the missing replace grant")
        permission_record = seventh / "permission-failure.json"
        permission_record.write_text(denied.stderr, encoding="utf-8")
        denied_commit = subprocess.run([str(binary), "commit", "--project", str(seventh / "project")],
                                       cwd=REPO, capture_output=True, text=True,
                                       timeout=120, check=False)
        if (denied_commit.returncode == 0
            or tools(binary, "inspect", "--project", seventh / "project")["accepted_state_root"] != limited["accepted_state_root"]):
            raise RuntimeError("RL-07 committed without the accepted principal grant")
        artifact_path = seventh / "portable.json"
        tools(binary, "artifact", "export", "--project", seventh / "project",
              "--output", artifact_path)
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        if (artifact["source_state_root"] != limited["accepted_state_root"]
            or any(name in artifact for name in ("policy_root", "principal_id", "grant"))):
            raise RuntimeError("RL-07 artifact inherited authority or lost source identity")
        recovered_project = seventh / "recovered"
        tools(binary, "artifact", "import", artifact_path, recovered_project, "--core", core)
        full = tools(binary, "inspect", "--project", recovered_project)
        full_authority = full["authority"]
        if (full_authority["replace_entity_version_allowed"] is not True
            or full_authority["policy_root"] == limited_authority["policy_root"]
            or full_authority["principal_id"] == limited_authority["principal_id"]
            or full["accepted_state_root"] == limited["accepted_state_root"]):
            raise RuntimeError("RL-07 fresh import did not receive independent authority")
        recovered_record = seventh / "recovered-inspection.json"
        recovered_record.write_text(json.dumps(full) + "\n", encoding="utf-8")
        correction_proposal = tools(binary, "propose", "--project", recovered_project,
                                    "--max-blocking-findings", 2)
        correction_validation = tools(binary, "validate", "--project", recovered_project)
        correction_commit = tools(binary, "commit", "--project", recovered_project)
        if not correction_proposal["candidate_id"] == correction_validation["candidate_id"] == correction_commit["candidate_id"]:
            raise RuntimeError("RL-07 recovered edit lost the native candidate identity")
        explanation_path = seventh / "authority-explanation.json"
        explanation = {"denied_mutation_class": "ReplaceEntityVersion",
                       "source_policy_root": limited_authority["policy_root"],
                       "destination_policy_root": full_authority["policy_root"],
                       "resolution": "fresh_workspace_import"}
        explanation_path.write_text(json.dumps({**explanation,
                                                "destination_policy_root": limited_authority["policy_root"]}) + "\n",
                                    encoding="utf-8")
        false_explanation = lab("judge", "--workspace", seventh, *common, code=1)
        if (false_explanation["result"] != "FAIL"
            or "fresh owner-controlled grant" not in false_explanation["reason"]):
            raise RuntimeError("RL-07 accepted an explanation that reused the denied policy")
        explanation_path.write_text(json.dumps(explanation) + "\n", encoding="utf-8")
        solved_seventh = lab("judge", "--workspace", seventh, *common)
        seventh_evidence = solved_seventh["evidence"]
        if (solved_seventh["result"] != "PASS"
            or seventh_evidence["source_root"] != limited["accepted_state_root"]
            or seventh_evidence["source_policy_root"] != limited_authority["policy_root"]
            or seventh_evidence["imported_root"] != full["accepted_state_root"]
            or seventh_evidence["accepted_root"] != correction_commit["new_root"]
            or seventh_evidence["destination_policy_root"] != full_authority["policy_root"]
            or seventh_evidence["artifact_sha256"] != digest(artifact_path)
            or seventh_evidence["permission_diagnostic_sha256"] != digest(permission_record)
            or seventh_evidence["correction_candidate_id"] != correction_commit["candidate_id"]
            or seventh_evidence["source_native_cases"] != 12
            or seventh_evidence["recovered_native_cases"] != 12
            or seventh_evidence["source_at_two"]["result"]["allow"] is not False
            or seventh_evidence["recovered_at_two"]["result"]["allow"] is not True):
            raise RuntimeError("RL-07 did not prove legitimate authority recovery")
        note_seventh = seventh / "notes.txt"
        note_seventh.write_text("keep my grant notes\n", encoding="utf-8")
        saved_recovered = parent / "saved-recovered"
        recovered_project.rename(saved_recovered)
        recovered_project.symlink_to(outside)
        unsafe_reset = lab("reset", "--workspace", seventh, *common, code=2)
        if (unsafe_reset["result"] != "ERROR"
            or not (seventh / "project").is_dir()
            or outside.read_text(encoding="utf-8") != "unrelated user file\n"):
            raise RuntimeError("RL-07 reset followed a symlink or deleted its source")
        recovered_project.unlink()
        saved_recovered.rename(recovered_project)
        reset_seventh = lab("reset", "--workspace", seventh, *common)
        limited_again = tools(binary, "inspect", "--project", seventh / "project")
        if (reset_seventh["result"] != "RESET" or recovered_project.exists()
            or limited_again["authority"]["replace_entity_version_allowed"] is not False
            or note_seventh.read_text(encoding="utf-8") != "keep my grant notes\n"
            or outside.read_text(encoding="utf-8") != "unrelated user file\n"):
            raise RuntimeError("RL-07 reset did not confine and restore its native seed")

        eighth = parent / "rl08"
        prepared_transfer = lab("start", "rl08", "--workspace", eighth, *common)
        if (prepared_transfer["result"] != "READY"
            or lab("judge", "--workspace", eighth, *common, code=1)["result"] != "FAIL"):
            raise RuntimeError("RL-08 accepted an unchanged source graph")
        value_file = eighth / "new-threshold.json"
        value_file.write_text('"12"\n', encoding="utf-8")
        source_project = eighth / "project"
        source_proposal = tools(binary, "propose", "--project", source_project,
                                "--constant", "bronze_max", "--value-file", value_file,
                                "--tests", eighth / "bronze-12-cases.json")
        source_validation = tools(binary, "validate", "--project", source_project)
        source_commit = tools(binary, "commit", "--project", source_project)
        if not source_proposal["candidate_id"] == source_validation["candidate_id"] == source_commit["candidate_id"]:
            raise RuntimeError("RL-08 changed source lost native candidate identity")
        unbound = lab("judge", "--workspace", eighth, *common, code=1)
        if unbound["result"] != "FAIL" or "bound current artifact" not in unbound["reason"]:
            raise RuntimeError("RL-08 accepted an edited graph without a bound export")
        edited_artifact = source_project / "artifact/edited.json"
        exported = tools(binary, "artifact", "export", "--project", source_project,
                         "--output", edited_artifact, "--bind-manifest")
        if exported["sha256"] != digest(edited_artifact):
            raise RuntimeError("RL-08 exported report differs from actual artifact bytes")
        copied_project = eighth / "copied"
        tools(binary, "artifact", "import", edited_artifact, copied_project, "--core", core)
        for label, project in (("source", source_project), ("destination", copied_project)):
            for name, command in (("inspection", ("inspect", "--project", project)),
                                  ("test", ("test", "--project", project)),
                                  ("run", ("run", "--project", project,
                                           "--input", eighth / "input-12.json"))):
                result = tools(binary, *command)
                (eighth / f"{label}-{name}.json").write_text(
                    json.dumps(result) + "\n", encoding="utf-8")
        saved_destination_run = eighth / "destination-run.json"
        correct_destination_run = saved_destination_run.read_text(encoding="utf-8")
        fake_run = json.loads(correct_destination_run)
        fake_run["result"]["tier"] = "1"
        saved_destination_run.write_text(json.dumps(fake_run) + "\n", encoding="utf-8")
        forged_run = lab("judge", "--workspace", eighth, *common, code=1)
        if forged_run["result"] != "FAIL" or "destination-run.json differs" not in forged_run["reason"]:
            raise RuntimeError("RL-08 accepted a fabricated destination execution result")
        saved_destination_run.write_text(correct_destination_run, encoding="utf-8")
        solved_eighth = lab("judge", "--workspace", eighth, *common)
        eighth_evidence = solved_eighth["evidence"]
        if (solved_eighth["result"] != "PASS"
            or eighth_evidence["seed_root"] == eighth_evidence["source_root"]
            or eighth_evidence["source_root"] == eighth_evidence["destination_root"]
            or eighth_evidence["source_policy_root"] == eighth_evidence["destination_policy_root"]
            or eighth_evidence["artifact_sha256"] != digest(edited_artifact)
            or eighth_evidence["source_manifest_sha256"] != digest(source_project / "program.json")
            or eighth_evidence["destination_manifest_sha256"] != digest(copied_project / "program.json")
            or eighth_evidence["source_native_cases"] != 14
            or eighth_evidence["destination_native_cases"] != 14
            or eighth_evidence["source_at_twelve"] != {"tier": "0", "invalid_configuration": False}
            or eighth_evidence["destination_at_twelve"] != eighth_evidence["source_at_twelve"]):
            raise RuntimeError("RL-08 edited native program did not survive portable transfer")
        note_eighth = eighth / "notes.txt"
        note_eighth.write_text("keep my transfer notes\n", encoding="utf-8")
        saved_copied = parent / "saved-copied"
        copied_project.rename(saved_copied)
        copied_project.symlink_to(outside)
        unsafe_transfer_reset = lab("reset", "--workspace", eighth, *common, code=2)
        if (unsafe_transfer_reset["result"] != "ERROR"
            or not source_project.is_dir()
            or outside.read_text(encoding="utf-8") != "unrelated user file\n"):
            raise RuntimeError("RL-08 reset followed a symlink or deleted its source")
        copied_project.unlink()
        saved_copied.rename(copied_project)
        reset_eighth = lab("reset", "--workspace", eighth, *common)
        if (reset_eighth["result"] != "RESET" or copied_project.exists()
            or note_eighth.read_text(encoding="utf-8") != "keep my transfer notes\n"
            or outside.read_text(encoding="utf-8") != "unrelated user file\n"
            or tools(binary, "inspect", "--project", source_project)["authoring"]["constants"][0]["value"] != "10"):
            raise RuntimeError("RL-08 reset did not confine and restore its native seed")

    materials = {}
    for exercise in ("rl01", "rl02", "rl03", "rl04", "rl05", "rl06", "rl07", "rl08"):
        for name in ("TASK.md", "HINTS.md", "SOLUTION.md"):
            path = REPO / "apps/repair-lab/exercises" / exercise / name
            materials[f"{exercise}/{name}"] = digest(path)
    materials["rl01/starter-recipe.json"] = digest(
        REPO / "apps/repair-lab/exercises/rl01/starter-recipe.json")
    for name in ("missing-upper-cases.json", "solution-cases.json"):
        materials[f"rl04/{name}"] = digest(
            REPO / "apps/repair-lab/exercises/rl04" / name)
    materials["rl05/wrong-value.json"] = digest(
        REPO / "apps/repair-lab/exercises/rl05/wrong-value.json")
    materials["rl08/input-12.json"] = digest(
        REPO / "apps/repair-lab/exercises/rl08/input-12.json")
    report = {
        "contract": "sley-tools.repair-lab-probe.v6", "result": "PASS",
        "tools_binary_sha256": digest(binary), "core_binary_sha256": digest(core),
        "lab_sha256": digest(LAB), "probe_sha256": digest(Path(__file__)),
        "materials_sha256": materials,
        "range_artifact_sha256": digest(REPO / "programs/range-predicate/artifact/native-graph.json"),
        "tier_artifact_sha256": digest(REPO / "programs/tier-classifier/artifact/native-graph.json"),
        "public_exercises_available": ["rl01", "rl02", "rl03", "rl04", "rl05", "rl06", "rl07", "rl08"],
        "remaining_exercises_pending": [],
        "rl01_imported_wrong_solution_rejected": True,
        "rl01": solved_first["evidence"],
        "rl02_exclusive_seed_rejected": True,
        "rl02": solved_second["evidence"],
        "rl03_disabled_seed_rejected": True,
        "rl03": solved_third["evidence"],
        "rl04_missing_new_case_rejected": True,
        "rl04_missing_boundary_rejected": True,
        "rl04_wrong_expectation_rejected": True,
        "rl04": solved_fourth["evidence"],
        "rl04_reset_reexposed_failure": True,
        "rl05_unchanged_seed_rejected": True,
        "rl05_wrong_type_rejected_without_candidate": True,
        "rl05_forged_diagnostic_rejected": True,
        "rl05": fifth_evidence,
        "rl05_reset_confined": True,
        "rl06_advanced_seed_rejected": True,
        "rl06_silent_overwrite_rejected": True,
        "rl06_stale_validation_rejected": True,
        "rl06_forged_reinspection_rejected": True,
        "rl06": sixth_evidence,
        "rl06_reset_confined": True,
        "rl07_limited_seed_rejected": True,
        "rl07_native_permission_denied": True,
        "rl07_commit_without_validation_rejected": True,
        "rl07_artifact_excluded_authority": True,
        "rl07_policy_reuse_explanation_rejected": True,
        "rl07_symlinked_recovered_reset_rejected": True,
        "rl07": seventh_evidence,
        "rl07_reset_confined": True,
        "rl08_unchanged_source_rejected": True,
        "rl08_unbound_source_rejected": True,
        "rl08_forged_destination_run_rejected": True,
        "rl08_symlinked_copied_reset_rejected": True,
        "rl08": eighth_evidence,
        "rl08_reset_confined": True,
        "reset_confined_to_marked_projects": True,
        "unmarked_reset_rejected": True,
        "symlinked_project_reset_rejected": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output}: RL-01 through RL-08 native worked paths and rejected starts passed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Repair Lab probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
