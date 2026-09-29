#!/usr/bin/env python3
"""Repair one native graph operand through CLI, Python, and TypeScript."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/python"))
from sley_tools import SleyToolsError, open_runtime  # noqa: E402


INPUTS = [(0, 0, 10), (1, 0, 10), (10, 0, 10), (11, 0, 10),
          (-1, 0, 10), (0, 1, 0), (5, 5, 5)]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(tools: Path, *args: object, reject: str | None = None) -> dict:
    result = subprocess.run([str(tools), *(str(arg) for arg in args)], capture_output=True,
                            text=True, timeout=120, check=False)
    if reject is not None:
        if result.returncode == 0 or json.loads(result.stderr).get("code") != reject:
            raise RuntimeError(f"expected {reject} from {args[:3]}: {result.stderr[:500]}")
        return json.loads(result.stderr)
    if result.returncode:
        raise RuntimeError(f"{args[:3]} failed: {result.stderr[:500]}")
    return json.loads(result.stdout)


def expected(value: int, lower: int, upper: int, *, repaired: bool) -> dict:
    if lower > upper:
        return {"within_range": False, "invalid_bounds": True,
                "below_lower": False, "above_upper": False}
    below = value < lower
    above = value > (upper if repaired else lower)
    return {"within_range": not (below or above), "invalid_bounds": False,
            "below_lower": below, "above_upper": above}


def cases(repaired: bool) -> dict:
    return {"contract": "sley-tools.graph-cases.v0", "cases": [
        {"id": f"v{value}l{lower}u{upper}",
         "input": [str(value), str(lower), str(upper)],
         "expected": expected(value, lower, upper, repaired=repaired)}
        for value, lower, upper in INPUTS]}


def host_cases(repaired: bool) -> list[dict]:
    return [{"id": f"v{value}l{lower}u{upper}",
             "inputs": {"value": value, "lower": lower, "upper": upper},
             "expected": expected(value, lower, upper, repaired=repaired)}
            for value, lower, upper in INPUTS]


def initialize(tools: Path, core: Path, project: Path, spec: Path, initial: Path) -> None:
    command(tools, "init", project, "--graph", spec, "--tests", initial, "--core", core)


def state(project: Path) -> dict:
    return json.loads((project / ".sley-tools/state.json").read_text())


def cli_probe(tools: Path, core: Path, root: Path, spec: Path,
              initial: Path, corrected: Path) -> dict:
    project = root / "cli"
    initialize(tools, core, project, spec, initial)
    before = state(project)
    inspected = command(tools, "inspect", "--project", project)["authoring"]
    selected = next(item for item in inspected["operations"]
                    if item["block"] == "valid_bounds" and item["name"] == "above")
    if (selected["operands"]["right"] != "param.lower"
            or "operation_operand" not in inspected["supported_edits"]):
        raise RuntimeError("inspect omitted the bound native operand")
    base = ("propose", "--project", project, "--operation", "valid_bounds.above",
            "--operand", "right", "--reference")
    command(tools, *base, "op.inside", "--tests", corrected, reject="ST_INPUT_INVALID")
    invalid = command(tools, *base, "op.below", "--tests", corrected)
    command(tools, "validate", "--project", project, reject="ST_VALIDATION_FAILED")
    command(tools, "propose", "discard", "--project", project)
    if state(project)["state_root"] != before["state_root"]:
        raise RuntimeError("rejected type mismatch advanced the accepted root")
    proposed = command(tools, *base, "param.upper", "--tests", corrected)
    proposal_path = project / ".sley-tools/proposal.json"
    original = json.loads(proposal_path.read_text())
    tampered = json.loads(proposal_path.read_text())
    tampered["change"]["value"] = "param.value"
    proposal_path.write_text(json.dumps(tampered))
    command(tools, "validate", "--project", project, reject="ST_INPUT_INVALID")
    proposal_path.write_text(json.dumps(original))
    validated = command(tools, "validate", "--project", project)
    if validated["candidate_id"] != proposed["candidate_id"]:
        raise RuntimeError("validation changed candidate identity")
    committed = command(tools, "commit", "--project", project)
    after = state(project)
    recipe = json.loads((project / "build/graph.json").read_text())
    repaired = recipe["blocks"][2]["operations"][1]["right"]
    if (committed["changed_case_count"] != 2 or committed["external_assertions"] != len(INPUTS)
            or before["operation_ids"] != after["operation_ids"]
            or repaired != "param.upper"
            or command(tools, "test", "--project", project)["result"] != "PASS"):
        raise RuntimeError("CLI edit did not make the exact native repair")
    return {"old_root": before["state_root"], "new_root": after["state_root"],
            "candidate_id": proposed["candidate_id"],
            "operation_id": selected["entity_id"],
            "old_object_id": original["builder"]["old_object_id"],
            "new_object_id": original["builder"]["new_object_id"],
            "changed_case_count": committed["changed_case_count"],
            "native_cases": len(INPUTS), "type_mismatch_candidate": invalid["candidate_id"],
            "type_mismatch_rejected": True, "forward_reference_rejected": True,
            "tampered_proposal_rejected": True}


def python_probe(tools: Path, core: Path, root: Path, spec: Path, initial: Path) -> dict:
    project = root / "python"
    initialize(tools, core, project, spec, initial)
    with open_runtime(tools=tools, core=core) as runtime:
        program = runtime.open_project(project)
        base_root = program.state_root
        try:
            program.propose_graph_operand("valid_bounds", "above", "right", "bad.upper",
                                          host_cases(True))
        except SleyToolsError as error:
            if error.code != "ST_INPUT_INVALID":
                raise
        else:
            raise RuntimeError("Python accepted an invalid operand reference")
        proposed = program.propose_graph_operand("valid_bounds", "above", "right", "param.upper",
                                                 host_cases(True))
        program.validate(proposed["candidate_id"])
        committed = program.commit(proposed["candidate_id"])
        if (committed["new_root"] == base_root or committed["changed_case_count"] != 2
                or committed["external_assertions"] != len(INPUTS)):
            raise RuntimeError("Python operand edit did not change the native behavior")
        for value, lower, upper in INPUTS:
            result = program.call("evaluate", inputs={"value": value, "lower": lower, "upper": upper})
            if result.value != expected(value, lower, upper, repaired=True):
                raise RuntimeError("Python observed a wrong native operand result")
        return {"old_root": base_root, "new_root": committed["new_root"],
                "candidate_id": proposed["candidate_id"], "native_cases": len(INPUTS),
                "invalid_reference_rejected": True}


def typescript_probe(tools: Path, core: Path, root: Path, spec: Path, initial: Path) -> dict:
    project = root / "typescript"
    initialize(tools, core, project, spec, initial)
    script = Path(__file__).with_name("probe_graph_operand_edit_ts.mjs")
    result = subprocess.run(["node", str(script), str(tools), str(core), str(project)],
                            capture_output=True, text=True, timeout=120, check=False)
    if result.returncode:
        raise RuntimeError(f"TypeScript operand probe failed: {result.stderr[:500]}")
    return json.loads(result.stdout)


def additional_profiles(tools: Path, core: Path, root: Path) -> dict:
    profiles = {}
    unary = {
        "contract": "sley-tools.graph-authoring.v0", "program_id": "negation-source",
        "title": "Negation Source", "purpose": "Select one signed input for checked negation",
        "parameters": [{"name": name, "value_type": "SInt64"} for name in ("x", "y")],
        "constants": [], "result": {"kind": "primitive", "value_type": "CheckedSInt64"},
        "entry_block": "entry", "blocks": [{"name": "entry", "operations": [
            {"op": "int_neg_checked", "name": "negated", "operand": "param.x"}],
            "terminator": {"kind": "return", "value": "op.negated"}}],
    }
    minimum = -(2**63)
    unary_inputs = [(1, 2), (minimum, 1), (0, -4), (0, minimum)]
    def negated(value: int) -> dict:
        return {"error": {"code": 1}} if value == minimum else {"ok": str(-value)}
    unary_cases = lambda edited: {"contract": "sley-tools.graph-cases.v0", "cases": [
        {"id": f"n{index}", "input": [str(x), str(y)],
         "expected": negated(y if edited else x)}
        for index, (x, y) in enumerate(unary_inputs)]}
    spec_path = root / "unary.json"
    initial_path = root / "unary-initial.json"
    edited_path = root / "unary-edited.json"
    spec_path.write_text(json.dumps(unary))
    initial_path.write_text(json.dumps(unary_cases(False)))
    edited_path.write_text(json.dumps(unary_cases(True)))
    project = root / "unary"
    initialize(tools, core, project, spec_path, initial_path)
    proposed = command(tools, "propose", "--project", project, "--operation", "entry.negated",
                       "--operand", "operand", "--reference", "param.y", "--tests", edited_path)
    command(tools, "validate", "--project", project)
    committed = command(tools, "commit", "--project", project)
    if (committed["changed_case_count"] != 4 or committed["external_assertions"] != 4
            or command(tools, "test", "--project", project)["result"] != "PASS"):
        raise RuntimeError("checked unary operand edit failed boundary cases")
    profiles["checked_unary"] = {"candidate_id": proposed["candidate_id"],
                                 "native_cases": 4, "changed_case_count": 4}

    op_graph = {
        "contract": "sley-tools.graph-authoring.v0", "program_id": "earlier-operation",
        "title": "Earlier Operation", "purpose": "Select a Boolean operand produced earlier in one block",
        "parameters": [{"name": name, "value_type": "Bool"} for name in ("a", "b")],
        "constants": [], "result": {"kind": "primitive", "value_type": "Bool"},
        "entry_block": "entry", "blocks": [{"name": "entry", "operations": [
            {"op": "bool_not", "name": "inverse_a", "operand": "param.a"},
            {"op": "bool_and", "name": "both", "left": "param.a", "right": "param.b"}],
            "terminator": {"kind": "return", "value": "op.both"}}],
    }
    def bool_cases(edited: bool) -> dict:
        return {"contract": "sley-tools.graph-cases.v0", "cases": [
            {"id": f"a{a}b{b}", "input": [a, b],
             "expected": a and ((not a) if edited else b)}
            for a in (False, True) for b in (False, True)]}
    spec_path = root / "earlier-operation.json"
    initial_path = root / "earlier-initial.json"
    edited_path = root / "earlier-edited.json"
    spec_path.write_text(json.dumps(op_graph))
    initial_path.write_text(json.dumps(bool_cases(False)))
    edited_path.write_text(json.dumps(bool_cases(True)))
    project = root / "earlier-operation"
    initialize(tools, core, project, spec_path, initial_path)
    proposed = command(tools, "propose", "--project", project, "--operation", "entry.both",
                       "--operand", "right", "--reference", "op.inverse_a", "--tests", edited_path)
    command(tools, "validate", "--project", project)
    committed = command(tools, "commit", "--project", project)
    if (committed["changed_case_count"] != 1 or committed["external_assertions"] != 4
            or command(tools, "test", "--project", project)["result"] != "PASS"):
        raise RuntimeError("earlier-operation operand edit failed native cases")
    profiles["earlier_operation"] = {"candidate_id": proposed["candidate_id"],
                                     "native_cases": 4, "changed_case_count": 1}
    return profiles


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "core", "spec", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    tools, core, spec = (getattr(args, name).resolve() for name in ("tools", "core", "spec"))
    source = json.loads(spec.read_text())
    block = next(item for item in source["blocks"] if item["name"] == "valid_bounds")
    above = next(item for item in block["operations"] if item["name"] == "above")
    if above["right"] != "param.upper":
        raise RuntimeError("source graph no longer has the upper-bound predicate")
    above["right"] = "param.lower"
    with tempfile.TemporaryDirectory(prefix="sley-operand-edit-") as directory:
        root = Path(directory)
        initial_spec = root / "initial.json"
        initial_cases = root / "initial-cases.json"
        corrected_cases = root / "corrected-cases.json"
        initial_spec.write_text(json.dumps(source))
        initial_cases.write_text(json.dumps(cases(False)))
        corrected_cases.write_text(json.dumps(cases(True)))
        result = {"contract": "sley-tools.graph-operand-probe.v1",
                  "tools_sha256": digest(tools), "core_sha256": digest(core),
                  "source_spec_sha256": digest(spec),
                  "initial_spec_sha256": digest(initial_spec),
                  "initial_cases_sha256": digest(initial_cases),
                  "corrected_cases_sha256": digest(corrected_cases),
                  "cli": cli_probe(tools, core, root, initial_spec, initial_cases, corrected_cases),
                  "python": python_probe(tools, core, root, initial_spec, initial_cases),
                  "typescript": typescript_probe(tools, core, root, initial_spec, initial_cases),
                  "additional_profiles": additional_profiles(tools, core, root)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"result": "PASS", "evidence": str(args.output),
                      "native_cases_per_adapter": len(INPUTS)}))


if __name__ == "__main__":
    main()
