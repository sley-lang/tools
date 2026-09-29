#!/usr/bin/env python3
"""Exercise a native branch target replacement through CLI, Python, and TypeScript."""

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/python"))
from sley_tools import GraphBuilder, SleyToolsError, open_runtime  # noqa: E402


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def call(tools, *args, reject=None):
    result = subprocess.run([str(tools), *(str(arg) for arg in args)],
                            capture_output=True, text=True, timeout=120, check=False)
    if reject is None:
        if result.returncode:
            raise RuntimeError(f"{args[:3]} failed: {result.stderr[:600]}")
        return json.loads(result.stdout)
    if not result.returncode or json.loads(result.stderr).get("code") != reject:
        raise RuntimeError(f"{args[:3]} did not reject with {reject}: {result.stderr[:600]}")


def cases_after(initial):
    result = json.loads(initial.read_text())
    for row in result["cases"]:
        value, lower, upper = map(int, row["input"])
        if lower <= upper:
            row["expected"] = {"within_range": False, "invalid_bounds": True,
                               "below_lower": False, "above_upper": False}
        else:
            below, above = value < lower, value > upper
            row["expected"] = {"within_range": not (below or above), "invalid_bounds": False,
                               "below_lower": below, "above_upper": above}
    return result


def sdk_cases(fixture):
    return [{"id": row["id"], "inputs": dict(zip(("value", "lower", "upper"),
                                                  map(int, row["input"]))),
             "expected": row["expected"]} for row in fixture["cases"]]


def unconditional_path(tools, core, root):
    builder = GraphBuilder(program_id="branch-jump", title="Branch Jump",
                           purpose="Exercise an unconditional native branch replacement")
    builder.parameter("choose_left", "Bool").parameter("choice", "Bool")
    builder.constant("yes", "Bool", True).constant("no", "Bool", False).primitive_result("Bool")
    builder.block("entry").conditional("param.choose_left", "left_lane", "right_lane")
    builder.block("left_lane").jump("true_result")
    builder.block("right_lane").conditional("param.choice", "true_result", "false_result")
    builder.block("true_result").constant_ref("true_value", "yes").return_value("op.true_value")
    builder.block("false_result").constant_ref("false_value", "no").return_value("op.false_value")
    spec = root / "jump.json"
    spec.write_text(json.dumps(builder.build()))
    paths = []
    for changed in (False, True):
        cases = {"contract": "sley-tools.graph-cases.v0", "cases": []}
        for left in (False, True):
            for choice in (False, True):
                cases["cases"].append({"id": f"{left}-{choice}", "input": [left, choice],
                                       "expected": (not changed) if left else choice})
        path = root / f"jump-cases-{changed}.json"
        path.write_text(json.dumps(cases))
        paths.append(path)
    project = root / "jump"
    call(tools, "init", project, "--graph", spec, "--tests", paths[0], "--core", core)
    target = root / "jump-target.json"
    target.write_text(json.dumps({"target": "false_result"}))
    proposed = call(tools, "propose", "--project", project, "--branch", "left_lane",
                    "--targets-file", target, "--tests", paths[1])
    call(tools, "validate", "--project", project)
    committed = call(tools, "commit", "--project", project)
    if (committed["changed_case_count"] != 2 or committed["external_assertions"] != 4
            or call(tools, "test", "--project", project)["result"] != "PASS"):
        raise RuntimeError("unconditional native branch edit did not preserve routing")
    return {"candidate_id": proposed["candidate_id"], "old_root": committed["old_root"],
            "new_root": committed["new_root"], "native_cases": 4,
            "changed_case_count": committed["changed_case_count"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "core", "spec", "initial_cases", "updated_cases", "output"):
        parser.add_argument(f"--{name.replace('_', '-')}", type=Path, required=True)
    args = parser.parse_args()
    tools, core, spec, initial = (getattr(args, name).resolve()
                                  for name in ("tools", "core", "spec", "initial_cases"))
    source = Path(__file__).resolve().parents[1]
    updated_path = args.updated_cases.resolve()
    updated = cases_after(initial)
    if json.loads(updated_path.read_text()) != updated:
        raise RuntimeError("checked-in branch edit cases differ from the independent oracle")
    with tempfile.TemporaryDirectory(prefix="sley-branch-edit-") as temporary:
        root = Path(temporary)
        cases_file = updated_path
        targets = {"if_true": "valid_bounds", "if_false": "invalid_bounds"}
        targets_file = root / "targets.json"
        targets_file.write_text(json.dumps(targets))
        cli = root / "cli"
        call(tools, "init", cli, "--graph", spec, "--tests", initial, "--core", core)
        before = call(tools, "inspect", "--project", cli)
        if before["authoring"]["branches"] != [{"block": "entry", "targets":
            {"if_true": "invalid_bounds", "if_false": "valid_bounds"}}]:
            raise RuntimeError("branch inspection omitted accepted targets")
        base = before["accepted_state_root"]
        options = ("propose", "--project", cli, "--branch", "entry", "--targets-file")
        for invalid in ({"if_true": "invalid_bounds", "if_false": "valid_bounds"},
                        {"if_true": "missing", "if_false": "invalid_bounds"},
                        {"if_true": "valid_bounds", "if_false": "valid_bounds"}):
            targets_file.write_text(json.dumps(invalid))
            call(tools, *options, targets_file, "--tests", cases_file, reject="ST_INPUT_INVALID")
        if call(tools, "inspect", "--project", cli)["accepted_state_root"] != base:
            raise RuntimeError("rejected branch request changed accepted root")
        targets_file.write_text(json.dumps(targets))
        proposed = call(tools, *options, targets_file, "--tests", cases_file)
        proposal_path = cli / ".sley-tools/proposal.json"
        original = json.loads(proposal_path.read_text())
        for field, value, code in (("value", {"if_true": "invalid_bounds", "if_false": "valid_bounds"}, "ST_INPUT_INVALID"),
                                   ("base_root", "0" * 64, "ST_STALE_BASE"),
                                   ("new_object_id", "0" * 64, "ST_INPUT_INVALID")):
            tampered = json.loads(json.dumps(original))
            if field == "value":
                tampered["change"][field] = value
            elif field == "new_object_id":
                tampered["builder"][field] = value
            else:
                tampered[field] = value
            proposal_path.write_text(json.dumps(tampered))
            call(tools, "validate", "--project", cli, reject=code)
        proposal_path.write_text(json.dumps(original))
        call(tools, "validate", "--project", cli)
        committed = call(tools, "commit", "--project", cli)
        state = json.loads((cli / ".sley-tools/state.json").read_text())
        if (committed["old_root"] != base or committed["new_root"] == base
                or committed["changed_case_count"] != len(updated["cases"])
                or committed["external_assertions"] != len(updated["cases"])
                or state["commit_history"][-1]["kind"] != "graph_branch_edit"
                or call(tools, "test", "--project", cli)["result"] != "PASS"):
            raise RuntimeError("native branch commit did not preserve its lifecycle or behavior")
        python_project = root / "python"
        call(tools, "init", python_project, "--graph", spec, "--tests", initial, "--core", core)
        with open_runtime(tools=tools, core=core) as runtime:
            program = runtime.open_project(python_project)
            try:
                program.propose_graph_branch("entry", {"target": "valid_bounds"}, sdk_cases(updated))
            except SleyToolsError as error:
                if error.code != "ST_INPUT_INVALID":
                    raise
            else:
                raise RuntimeError("Python accepted wrong branch target shape")
            proposal = program.propose_graph_branch("entry", targets, sdk_cases(updated))
            program.validate(proposal["candidate_id"])
            python_commit = program.commit(proposal["candidate_id"])
            if python_commit["external_assertions"] != len(updated["cases"]):
                raise RuntimeError("Python branch edit did not run native cases")
        ts_project = root / "typescript"
        call(tools, "init", ts_project, "--graph", spec, "--tests", initial, "--core", core)
        node = subprocess.run(["node", str(source / "scripts/probe_graph_typescript_branch.mjs"),
                               f"tools={tools}", f"core={core}", f"project={ts_project}",
                               f"cases={cases_file}"], capture_output=True, text=True, timeout=120)
        if node.returncode:
            raise RuntimeError(f"TypeScript branch probe failed: {node.stderr[:600]}")
        ts = json.loads(node.stdout)
        if ts["result"] != "PASS":
            raise RuntimeError("TypeScript branch probe did not pass")
        unconditional = unconditional_path(tools, core, root)
    report = {"contract": "sley-tools.graph-branch-edit-probe.v1", "result": "PASS",
              "tools_binary_sha256": digest(tools), "core_binary_sha256": digest(core),
              "builder_binary_sha256": digest(tools.with_name("sley-tools-native-builder")),
              "spec_sha256": digest(spec), "initial_cases_sha256": digest(initial),
              "updated_cases_sha256": digest(updated_path),
              "python_probe_sha256": digest(Path(__file__).resolve()),
              "typescript_probe_sha256": digest(source / "scripts/probe_graph_typescript_branch.mjs"),
              "python_sdk_sha256": digest(source / "packages/python/sley_tools/runtime.py"),
              "typescript_sdk_sha256": digest(source / "packages/typescript/src/index.js"),
              "mcp_server_sha256": digest(source / "packages/mcp/src/server.mjs"),
              "cli": {"old_root": base, "new_root": committed["new_root"],
                      "candidate_id": proposed["candidate_id"], "native_cases": len(updated["cases"]),
                      "changed_case_count": committed["changed_case_count"],
                      "invalid_edits_rejected": 3, "tampered_proposals_rejected": 3},
              "python": {"new_root": python_commit["new_root"], "native_cases": len(updated["cases"])},
              "typescript": ts,
              "unconditional": unconditional,
              "native_core_test_admission": "NOT_RUN: production native executor unavailable"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.output}: native branch edit passed through CLI and both SDKs")


if __name__ == "__main__":
    main()
