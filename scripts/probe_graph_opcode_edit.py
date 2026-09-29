#!/usr/bin/env python3
"""Verify exact one-operation opcode edits through CLI, Python, and TypeScript."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/python"))
from sley_tools import GraphBuilder, SleyToolsError, open_runtime  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def call(tools: Path, *args: object, reject: str | None = None) -> dict:
    command = [str(tools), *(str(arg) for arg in args)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    if reject is None:
        if result.returncode:
            raise RuntimeError(f"{command[1:4]} failed: {result.stderr[:500]}")
        return json.loads(result.stdout)
    if not result.returncode:
        raise RuntimeError(f"{command[1:4]} unexpectedly succeeded")
    error = json.loads(result.stderr)
    if error.get("code") != reject:
        raise RuntimeError(f"expected {reject}, got {error}")
    return error


def initialize(tools: Path, core: Path, project: Path, spec: Path, initial: Path) -> None:
    call(tools, "init", project, "--graph", spec, "--tests", initial, "--core", core)


def current_root(project: Path) -> str:
    return json.loads((project / ".sley-tools/state.json").read_text())["state_root"]


def run(tools: Path, project: Path, root: Path, count: int) -> bool:
    input_file = root / f"input-{count}.json"
    input_file.write_text(json.dumps([str(count)]))
    return call(tools, "run", "--project", project, "--input", input_file)["result"]


def cli_path(tools: Path, core: Path, project: Path, root: Path,
             spec: Path, initial: Path, updated: Path) -> dict:
    initialize(tools, core, project, spec, initial)
    before = call(tools, "inspect", "--project", project)["authoring"]
    old_root = current_root(project)
    selected = next((item for item in before["operations"]
                     if item["block"] == "entry" and item["name"] == "within"), None)
    if (selected is None or selected["opcode"] != "less_equal"
            or len(selected["entity_id"]) != 64
            or before["supported_edits"] != ["constant_value", "operation_opcode", "operation_operand"]):
        raise RuntimeError("graph inspection omitted native operation selector")
    if (run(tools, project, root, 9), run(tools, project, root, 10)) != (True, True):
        raise RuntimeError("initial native boundary is wrong")
    args = ("propose", "--project", project, "--operation", "entry.within", "--opcode")
    call(tools, *args, "less_equal", "--tests", updated, reject="ST_INPUT_INVALID")
    call(tools, *args, "bool_or", "--tests", updated, reject="ST_INPUT_INVALID")
    if current_root(project) != old_root:
        raise RuntimeError("invalid opcode request changed accepted root")
    proposed = call(tools, *args, "less_than", "--tests", updated)
    proposal_path = project / ".sley-tools/proposal.json"
    proposal = json.loads(proposal_path.read_text())
    if (proposed["base_root"] != old_root or current_root(project) != old_root
            or proposed["base_object_id"] == proposal["builder"]["new_object_id"]):
        raise RuntimeError("opcode proposal omitted exact old object or advanced root")
    tampered = json.loads(proposal_path.read_text())
    tampered["change"]["value"] = "greater_equal"
    proposal_path.write_text(json.dumps(tampered))
    call(tools, "validate", "--project", project, reject="ST_INPUT_INVALID")
    proposal_path.write_text(json.dumps(proposal))
    tampered = json.loads(proposal_path.read_text())
    tampered["base_root"] = "0" * 64
    proposal_path.write_text(json.dumps(tampered))
    call(tools, "validate", "--project", project, reject="ST_STALE_BASE")
    proposal_path.write_text(json.dumps(proposal))
    validated = call(tools, "validate", "--project", project)
    if validated["candidate_id"] != proposed["candidate_id"] or current_root(project) != old_root:
        raise RuntimeError("opcode validation changed accepted base or candidate")
    tampered = json.loads(proposal_path.read_text())
    tampered["builder"]["new_object_id"] = "0" * 64
    proposal_path.write_text(json.dumps(tampered))
    call(tools, "commit", "--project", project, reject="ST_INPUT_INVALID")
    proposal_path.write_text(json.dumps(proposal))
    committed = call(tools, "commit", "--project", project)
    state = json.loads((project / ".sley-tools/state.json").read_text())
    after = call(tools, "inspect", "--project", project)["authoring"]
    after_selected = next(item for item in after["operations"]
                          if item["block"] == "entry" and item["name"] == "within")
    recipe = json.loads((project / "build/graph.json").read_text())
    if (committed["old_root"] != old_root or committed["new_root"] == old_root
            or committed["new_root"] != state["state_root"]
            or committed["candidate_id"] != proposed["candidate_id"]
            or committed["changed_case_count"] != 1 or committed["external_assertions"] != 5
            or state["commit_history"][-1]["kind"] != "graph_opcode_edit"
            or state["operation_ids"]["entry"]["within"] != selected["entity_id"]
            or after_selected["entity_id"] != selected["entity_id"]
            or after_selected["opcode"] != "less_than"
            or recipe["blocks"][0]["operations"][1]["op"] != "less_than"):
        raise RuntimeError("opcode commit did not preserve one native entity and recipe binding")
    if (run(tools, project, root, 9), run(tools, project, root, 10),
            run(tools, project, root, 11)) != (True, False, False):
        raise RuntimeError("edited native boundary is wrong")
    if call(tools, "test", "--project", project)["external_assertions_against_native_execution"] != 5:
        raise RuntimeError("edited external oracle was not executed")
    return {"old_root": old_root, "new_root": committed["new_root"],
            "candidate_id": committed["candidate_id"], "operation_id": selected["entity_id"],
            "old_object_id": proposal["builder"]["old_object_id"],
            "new_object_id": proposal["builder"]["new_object_id"],
            "changed_case_count": committed["changed_case_count"], "native_cases": 5,
            "tampered_value_rejected": True, "stale_base_rejected": True,
            "tampered_object_rejected": True, "cross_profile_rejected": True}


def python_path(tools: Path, core: Path, project: Path, fixture: dict) -> dict:
    cases = [{"id": row["id"], "inputs": {"count": int(row["input"][0])},
              "expected": row["expected"]} for row in fixture["cases"]]
    with open_runtime(tools=tools, core=core) as runtime:
        program = runtime.open_project(project)
        old_root, old_handle = program.state_root, runtime._handle
        try:
            program.propose_graph_opcode("entry", "within", "record_new", cases)
        except SleyToolsError as error:
            if error.code != "ST_INPUT_INVALID":
                raise
        else:
            raise RuntimeError("Python accepted an unsupported graph opcode")
        proposed = program.propose_graph_opcode("entry", "within", "less_than", cases)
        program.validate(proposed["candidate_id"])
        committed = program.commit(proposed["candidate_id"])
        if (committed["new_root"] == old_root or committed["changed_case_count"] != 1
                or committed["external_assertions"] != 5 or runtime._handle == old_handle
                or program.state_root != committed["new_root"]):
            raise RuntimeError("Python opcode edit lost native identity or assertions")
        for count, expected in ((9, True), (10, False), (11, False)):
            observed = program.call("evaluate", inputs={"count": count})
            if observed.value is not expected or observed.metadata["state_root"] != committed["new_root"]:
                raise RuntimeError("Python opcode edit changed the wrong boundary")
        return {"old_root": old_root, "new_root": committed["new_root"],
                "candidate_id": proposed["candidate_id"], "changed_case_count": 1,
                "native_cases": len(cases), "handle_rotated": True}


def family_matrix(tools: Path, core: Path, root: Path) -> dict:
    minimum, maximum = -(2**63), 2**63 - 1
    examples = {
        "boolean": ("bool_and", "bool_or", [("a", "Bool"), ("b", "Bool")], "Bool", [
            ([False, False], False, False), ([False, True], False, True),
            ([True, False], False, True), ([True, True], True, True)]),
        "equality": ("equal", "not_equal", [("a", "SInt64"), ("b", "SInt64")], "Bool", [
            ([0, 0], True, False), ([0, 1], False, True), ([-1, 1], False, True)]),
        "checked_binary": ("int_add_checked", "int_sub_checked",
                           [("a", "SInt64"), ("b", "SInt64")], "CheckedSInt64", [
            ([9, 2], {"ok": "11"}, {"ok": "7"}),
            ([0, 1], {"ok": "1"}, {"ok": "-1"}),
            ([minimum, 1], {"ok": str(minimum + 1)}, {"error": {"code": 1}}),
            ([maximum, -1], {"ok": str(maximum - 1)}, {"error": {"code": 1}})]),
        "checked_unary": ("int_neg_checked", "result_ok", [("a", "SInt64")],
                          "CheckedSInt64", [
            ([0], {"ok": "0"}, {"ok": "0"}),
            ([1], {"ok": "-1"}, {"ok": "1"}),
            ([-1], {"ok": "1"}, {"ok": "-1"}),
            ([minimum], {"error": {"code": 1}}, {"ok": str(minimum)})]),
    }
    observed = {}
    for name, (old_opcode, new_opcode, parameters, result, rows) in examples.items():
        folder = root / name
        folder.mkdir()
        builder = GraphBuilder(program_id=f"opcode-{name.replace('_', '-')}", title=f"Opcode {name}",
                               purpose=f"Exercise the native {name} opcode replacement profile")
        for parameter, kind in parameters:
            builder.parameter(parameter, kind)
        builder.primitive_result(result)
        block = builder.block("entry")
        if len(parameters) == 1:
            block.unary(old_opcode, "selected", "param.a")
        else:
            block.binary(old_opcode, "selected", "param.a", "param.b")
        block.return_value("op.selected")
        spec = folder / "spec.json"
        spec.write_text(json.dumps(builder.build()))
        fixtures = []
        for index in (1, 2):
            fixture = {"contract": "sley-tools.graph-cases.v0", "cases": [
                {"id": f"case-{row_index}",
                 "input": [value if type(value) is bool else str(value) for value in inputs],
                 "expected": before if index == 1 else after}
                for row_index, (inputs, before, after) in enumerate(rows)]}
            path = folder / f"cases-{index}.json"
            path.write_text(json.dumps(fixture))
            fixtures.append(path)
        project = folder / "project"
        initialize(tools, core, project, spec, fixtures[0])
        before_state = json.loads((project / ".sley-tools/state.json").read_text())
        proposal = call(tools, "propose", "--project", project, "--operation", "entry.selected",
                        "--opcode", new_opcode, "--tests", fixtures[1])
        call(tools, "validate", "--project", project)
        committed = call(tools, "commit", "--project", project)
        after_state = json.loads((project / ".sley-tools/state.json").read_text())
        expected_changed = sum(before != after for _, before, after in rows)
        if (committed["old_root"] != before_state["state_root"]
                or committed["new_root"] != after_state["state_root"]
                or committed["candidate_id"] != proposal["candidate_id"]
                or committed["changed_case_count"] != expected_changed
                or committed["external_assertions"] != len(rows)
                or before_state["operation_ids"]["entry"]["selected"]
                   != after_state["operation_ids"]["entry"]["selected"]
                or call(tools, "test", "--project", project)["result"] != "PASS"):
            raise RuntimeError(f"native {name} opcode family did not preserve its entity or behavior")
        observed[name] = {"old_root": committed["old_root"], "new_root": committed["new_root"],
                          "native_cases": len(rows), "changed_case_count": expected_changed,
                          "operation_id": after_state["operation_ids"]["entry"]["selected"]}
    return observed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "core", "spec", "initial-cases", "updated-cases", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    tools, core, spec, initial, updated = (
        getattr(args, name).resolve() for name in
        ("tools", "core", "spec", "initial_cases", "updated_cases"))
    fixture = json.loads(updated.read_text())
    with tempfile.TemporaryDirectory(prefix="sley-graph-opcode-") as temporary:
        root = Path(temporary)
        cli_project, python_project, typescript_project = (
            root / name for name in ("cli", "python", "typescript"))
        cli = cli_path(tools, core, cli_project, root, spec, initial, updated)
        initialize(tools, core, python_project, spec, initial)
        python = python_path(tools, core, python_project, fixture)
        initialize(tools, core, typescript_project, spec, initial)
        node = subprocess.run(["node", str(Path(__file__).with_name("probe_graph_typescript_opcode.mjs")),
                               f"tools={tools}", f"core={core}", f"project={typescript_project}",
                               f"cases={updated}"], capture_output=True, text=True,
                              timeout=120, check=False)
        if node.returncode:
            raise RuntimeError(f"TypeScript opcode probe failed: {node.stderr[:500]}")
        typescript = json.loads(node.stdout)
        if typescript.get("contract") != "sley-tools.graph-typescript-opcode-probe.v1" or typescript.get("result") != "PASS":
            raise RuntimeError("TypeScript opcode evidence differs")
        family_root = root / "families"
        family_root.mkdir()
        families = family_matrix(tools, core, family_root)
    source = Path(__file__).resolve().parents[1]
    report = {"contract": "sley-tools.graph-opcode-edit-probe.v1", "result": "PASS",
              "tools_binary_sha256": digest(tools),
              "builder_binary_sha256": digest(tools.with_name("sley-tools-native-builder")),
              "core_binary_sha256": digest(core),
              "spec_sha256": digest(spec), "initial_cases_sha256": digest(initial),
              "updated_cases_sha256": digest(updated),
              "python_sdk_sha256": digest(source / "packages/python/sley_tools/runtime.py"),
              "python_builder_sha256": digest(source / "packages/python/sley_tools/graph_builder.py"),
              "typescript_sdk_sha256": digest(source / "packages/typescript/src/index.js"),
              "typescript_types_sha256": digest(source / "packages/typescript/src/index.d.ts"),
              "typescript_type_test_sha256": digest(source / "packages/typescript/tests/types.ts"),
              "python_probe_sha256": digest(Path(__file__).resolve()),
              "typescript_probe_sha256": digest(Path(__file__).with_name("probe_graph_typescript_opcode.mjs")),
              "cli": cli, "python": python, "typescript": typescript,
              "additional_families": families,
              "native_core_test_admission": "NOT_RUN: production native executor unavailable"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.output}: exact native graph opcode edit passed through CLI and both SDKs")


if __name__ == "__main__":
    main()
