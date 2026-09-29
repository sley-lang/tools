#!/usr/bin/env python3
"""Verify a named graph constant edit through exact native proposal/commit."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from validate_program_manifest import validate


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(tools: Path, *args: str, reject: str | None = None) -> dict:
    result = subprocess.run([str(tools), *args], capture_output=True, text=True,
                            timeout=120, check=False)
    if reject is None:
        if result.returncode:
            raise RuntimeError(f"{' '.join(args[:2])} failed: {result.stderr[:450]}")
        return json.loads(result.stdout)
    if not result.returncode:
        raise RuntimeError(f"{' '.join(args[:2])} unexpectedly succeeded")
    error = json.loads(result.stderr)
    if error.get("code") != reject:
        raise RuntimeError(f"expected {reject}, got {error}")
    return error


def state(project: Path) -> dict:
    return json.loads((project / ".sley-tools/state.json").read_text())


def run(tools: Path, project: Path, folder: Path, count: int) -> bool:
    input_file = folder / f"count-{count}.json"
    input_file.write_text(json.dumps([str(count)]))
    observed = invoke(tools, "run", "--project", str(project), "--input", str(input_file))
    if observed["program_id"] != "fixed-limit" or observed["state_root"] != state(project)["state_root"]:
        raise RuntimeError("native run lost accepted graph identity")
    return observed["result"]


def positive(tools: Path, core: Path, schema: Path, spec: Path, initial: Path,
             updated: Path, root: Path) -> dict:
    project = root / "fixed-limit"
    value_file = root / "value-12.json"
    value_file.write_text('"12"')
    invoke(tools, "init", str(project), "--graph", str(spec), "--tests", str(initial),
           "--core", str(core))
    old_state = state(project)
    old_root = old_state["state_root"]
    authoring = invoke(tools, "inspect", "--project", str(project))["authoring"]
    if (authoring["profile"] != "graph_v0" or authoring["supported_edits"] != ["constant_value", "operation_opcode", "operation_operand"]
        or authoring["constants"] != [{"name": "limit", "value_type": "UInt32", "value": "10",
                                         "entity_id": old_state["constant_ids"]["limit"]}]):
        raise RuntimeError("inspect omitted the readable native graph constant selector")
    if (run(tools, project, root, 10), run(tools, project, root, 11),
        run(tools, project, root, 13)) != (True, False, False):
        raise RuntimeError("initial native graph boundary is wrong")
    bad_value = root / "bad-value.json"
    bad_value.write_text('"-1"')
    invoke(tools, "propose", "--project", str(project), "--constant", "limit",
           "--value-file", str(bad_value), "--tests", str(updated), reject="ST_INPUT_INVALID")
    if state(project)["state_root"] != old_root:
        raise RuntimeError("invalid graph edit advanced accepted root")
    proposed = invoke(tools, "propose", "--project", str(project), "--constant", "limit",
                      "--value-file", str(value_file), "--tests", str(updated))
    proposal_file = project / ".sley-tools/proposal.json"
    proposal = json.loads(proposal_file.read_text())
    if proposed["base_root"] != old_root or state(project)["state_root"] != old_root:
        raise RuntimeError("proposal changed accepted graph before commit")
    candidate = proposed["candidate_id"]
    if len(candidate) != 64 or proposal["builder"]["old_object_id"] == proposal["builder"]["new_object_id"]:
        raise RuntimeError("graph proposal omitted exact object replacement identity")
    tampered = json.loads(proposal_file.read_text())
    tampered["change"]["value"] = "13"
    proposal_file.write_text(json.dumps(tampered))
    invoke(tools, "validate", "--project", str(project), reject="ST_INPUT_INVALID")
    proposal_file.write_text(json.dumps(proposal))
    tampered = json.loads(proposal_file.read_text())
    tampered["base_root"] = "0" * 64
    proposal_file.write_text(json.dumps(tampered))
    invoke(tools, "validate", "--project", str(project), reject="ST_STALE_BASE")
    proposal_file.write_text(json.dumps(proposal))
    if state(project)["state_root"] != old_root:
        raise RuntimeError("rejected graph proposal advanced accepted root")
    validation = invoke(tools, "validate", "--project", str(project))
    if validation["candidate_id"] != candidate or validation["accepted_root_unchanged"] is not True:
        raise RuntimeError("validation did not preserve exact candidate and base root")
    invoke(tools, "validate", "--project", str(project), reject="ST_INPUT_INVALID")
    tampered = json.loads(proposal_file.read_text())
    tampered["builder"]["new_object_id"] = "0" * 64
    proposal_file.write_text(json.dumps(tampered))
    invoke(tools, "commit", "--project", str(project), reject="ST_INPUT_INVALID")
    proposal_file.write_text(json.dumps(proposal))
    if state(project)["state_root"] != old_root:
        raise RuntimeError("tampered validated proposal advanced accepted graph")
    committed = invoke(tools, "commit", "--project", str(project))
    new_state = state(project)
    if (committed["candidate_id"] != candidate or committed["old_root"] != old_root
        or committed["new_root"] != new_state["state_root"] or old_root == new_state["state_root"]
        or committed["changed_case_count"] != 2 or committed["external_assertions"] != 6
        or new_state["commit_history"][-1]["kind"] != "graph_constant_edit"):
        raise RuntimeError("native graph edit did not preserve candidate, change, and receipt evidence")
    if (run(tools, project, root, 0), run(tools, project, root, 10),
        run(tools, project, root, 11), run(tools, project, root, 12),
        run(tools, project, root, 13)) != (True, True, True, True, False):
        raise RuntimeError("edited native graph boundary or unrelated behavior is wrong")
    tested = invoke(tools, "test", "--project", str(project))
    if tested["result"] != "PASS" or tested["external_assertions_against_native_execution"] != 6:
        raise RuntimeError("edited graph external assertions did not pass")
    manifest = validate(project / "program.json", schema)
    if manifest["result"] != "PASS" or json.loads((project / "build/graph.json").read_text())["constants"][0]["value"] != "12":
        raise RuntimeError("edited recipe and manifest are not bound to accepted graph")
    after_authoring = invoke(tools, "inspect", "--project", str(project))["authoring"]
    if (after_authoring["constants"][0]["value"] != "12"
        or after_authoring["constants"][0]["entity_id"] != authoring["constants"][0]["entity_id"]):
        raise RuntimeError("native graph selector identity changed across a value edit")
    if proposal_file.exists() or (project / ".sley-tools/validated-proposal.json").exists():
        raise RuntimeError("committed graph proposal was not cleared")
    next_value = root / "value-13.json"
    next_value.write_text('"13"')
    invoke(tools, "propose", "--project", str(project), "--constant", "limit",
           "--value-file", str(next_value), "--tests", str(updated))
    discarded = invoke(tools, "propose", "discard", "--project", str(project))
    if discarded["accepted_root_unchanged"] is not True or state(project)["state_root"] != new_state["state_root"]:
        raise RuntimeError("discard changed accepted graph")
    return {"initial_root": old_root, "accepted_root": new_state["state_root"],
            "candidate_id": candidate, "receipt_id": committed["receipt_id"],
            "constant_id": new_state["constant_ids"]["limit"],
            "old_object_id": proposal["builder"]["old_object_id"],
            "new_object_id": proposal["builder"]["new_object_id"],
            "changed_case_count": committed["changed_case_count"],
            "external_assertions": tested["external_assertions_against_native_execution"],
            "negative_paths": ["invalid_typed_value", "tampered_candidate_value",
                               "stale_proposal_root", "duplicate_validation",
                               "tampered_validated_proposal", "discard"]}


def false_oracle(tools: Path, core: Path, spec: Path, initial: Path, root: Path) -> dict:
    project = root / "false-oracle"
    value_file = root / "false-oracle-value.json"
    value_file.write_text('"12"')
    invoke(tools, "init", str(project), "--graph", str(spec), "--tests", str(initial),
           "--core", str(core))
    old_root = state(project)["state_root"]
    invoke(tools, "propose", "--project", str(project), "--constant", "limit",
           "--value-file", str(value_file), "--tests", str(initial))
    invoke(tools, "validate", "--project", str(project))
    error = invoke(tools, "commit", "--project", str(project), reject="ST_EXECUTION_FAILED")
    status = json.loads((project / ".sley-tools/status.json").read_text())
    new_root = state(project)["state_root"]
    if (new_root == old_root or status["last_external_assertions"] != "FAIL"
        or status["state_root"] != new_root or (project / ".sley-tools/proposal.json").exists()
        or run(tools, project, root, 11) is not True):
        raise RuntimeError("false external oracle hid an accepted native commit")
    invoke(tools, "test", "--project", str(project), reject="ST_EXECUTION_FAILED")
    return {"old_root": old_root, "accepted_root": new_root,
            "reported_code": error["code"], "external_assertions": "FAIL",
            "project_remains_runnable": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "builder", "core", "schema", "spec", "initial-cases",
                 "updated-cases", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    tools, builder, core, schema, spec, initial, updated = (
        getattr(args, name).resolve() for name in
        ("tools", "builder", "core", "schema", "spec", "initial_cases", "updated_cases"))
    with tempfile.TemporaryDirectory(prefix="sley-graph-edit-") as temporary:
        root = Path(temporary)
        accepted = positive(tools, core, schema, spec, initial, updated, root)
        failed_oracle = false_oracle(tools, core, spec, initial, root)
    report = {"contract": "sley-tools.graph-edit-probe.v1", "result": "PASS",
              "tools_binary_sha256": digest(tools), "builder_binary_sha256": digest(builder),
              "core_binary_sha256": digest(core), "schema_sha256": digest(schema),
              "spec_sha256": digest(spec), "initial_cases_sha256": digest(initial),
              "updated_cases_sha256": digest(updated),
              "edit": accepted, "false_oracle": failed_oracle,
              "native_core_test_admission": "NOT_RUN: production native executor unavailable"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.output}: one-object native graph edit and false-oracle handling passed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"graph edit probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
