#!/usr/bin/env python3
"""Exercise native graph constant proposals through RPC, Python, and TypeScript."""

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


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def initialize(tools: Path, core: Path, project: Path, spec: Path, cases: Path) -> None:
    result = subprocess.run(
        [str(tools), "init", str(project), "--graph", str(spec), "--tests", str(cases), "--core", str(core)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"native graph init failed: {result.stderr[:400]}")


def host_cases(fixture: dict) -> list[dict]:
    return [{"id": row["id"], "inputs": {"count": int(row["input"][0])}, "expected": row["expected"]}
            for row in fixture["cases"]]


def expect_input_error(action) -> None:
    try:
        action()
    except SleyToolsError as error:
        if error.code != "ST_INPUT_INVALID":
            raise RuntimeError(f"wrong graph input error: {error.code}") from error
    else:
        raise RuntimeError("invalid graph authoring request was accepted")


def positive(tools: Path, core: Path, project: Path, cases: list[dict]) -> dict:
    with open_runtime(tools=tools, core=core) as runtime:
        program = runtime.open_project(project)
        old_root, old_handle = program.state_root, runtime._handle
        if program.call("evaluate", inputs={"count": 11}).value is not False:
            raise RuntimeError("initial native limit did not reject 11")
        expect_input_error(lambda: program.propose_graph_constant("limit", True, cases))
        expect_input_error(lambda: program.propose_graph_constant("absent", 12, cases))
        expect_input_error(lambda: program.propose_graph_constant("limit", 12, [{**cases[0], "expected": "true"}]))
        proposal = program.propose_graph_constant("limit", 12, cases)
        if proposal["phase"] != "PROPOSED" or proposal["base_root"] != old_root:
            raise RuntimeError("Python graph proposal lost its accepted base")
        expect_input_error(lambda: program.propose_graph_constant("limit", 13, cases))
        validation = program.validate(proposal["candidate_id"])
        if validation["result"] != "PASS" or validation["accepted_root_unchanged"] is not True:
            raise RuntimeError("Python graph validation changed accepted state")
        committed = program.commit(proposal["candidate_id"])
        if (committed["candidate_id"] != proposal["candidate_id"] or committed["new_root"] == old_root
                or committed["changed_case_count"] != 2 or committed["external_assertions"] != 6
                or committed["postcommit_assertions_status"] != "PASS"
                or program.state_root != committed["new_root"] or runtime._handle == old_handle):
            raise RuntimeError("Python graph commit or reopen lost native identity")
        for count, expected in ((11, True), (12, True), (13, False)):
            observed = program.call("evaluate", inputs={"count": count})
            if observed.value is not expected or observed.metadata["state_root"] != committed["new_root"]:
                raise RuntimeError("Python graph changed the wrong native boundary")
        if program.test()["external_assertions_against_native_execution"] != 6:
            raise RuntimeError("Python graph commit omitted updated external assertions")
        return {"old_root": old_root, "new_root": committed["new_root"],
                "candidate_id": proposal["candidate_id"], "changed_case_count": committed["changed_case_count"]}


def false_oracle(tools: Path, core: Path, project: Path, cases: list[dict]) -> dict:
    false_cases = [{**row} for row in cases]
    false_cases[0]["expected"] = False
    with open_runtime(tools=tools, core=core) as runtime:
        program = runtime.open_project(project)
        old_root = program.state_root
        proposal = program.propose_graph_constant("limit", 12, false_cases)
        program.validate(proposal["candidate_id"])
        committed = program.commit(proposal["candidate_id"])
        if (committed["postcommit_assertions_status"] != "FAIL"
                or not committed["verification_error"] or committed["new_root"] == old_root
                or program.state_root != committed["new_root"]):
            raise RuntimeError("false oracle obscured an accepted native graph commit")
        if program.call("evaluate", inputs={"count": 12}).value is not True:
            raise RuntimeError("false oracle made the accepted project unrunnable")
        try:
            program.test()
        except SleyToolsError as error:
            if error.code != "ST_EXECUTION_FAILED":
                raise RuntimeError(f"wrong false-oracle test error: {error.code}") from error
        else:
            raise RuntimeError("false oracle was reported as passing")
        return {"old_root": old_root, "accepted_root": committed["new_root"],
                "status": committed["postcommit_assertions_status"], "project_remains_runnable": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "core", "spec", "initial-cases", "updated-cases", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    tools, core = args.tools.resolve(), args.core.resolve()
    spec, initial, updated = args.spec.resolve(), args.initial_cases.resolve(), args.updated_cases.resolve()
    fixture = json.loads(updated.read_text())
    if fixture.get("contract") != "sley-tools.graph-cases.v0" or len(fixture.get("cases", [])) != 6:
        raise RuntimeError("Fixed Limit updated fixture differs")
    cases = host_cases(fixture)
    with tempfile.TemporaryDirectory(prefix="sley-graph-sdk-authoring-") as temporary:
        root = Path(temporary)
        python_project, false_project, ts_project = (root / name for name in ("python", "false", "typescript"))
        for project in (python_project, false_project, ts_project):
            initialize(tools, core, project, spec, initial)
        python = positive(tools, core, python_project, cases)
        failed = false_oracle(tools, core, false_project, cases)
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("probe_graph_typescript_authoring.mjs")),
             f"tools={tools}", f"core={core}", f"project={ts_project}", f"cases={updated}"],
            capture_output=True, text=True, timeout=90, check=False,
        )
        if result.returncode:
            raise RuntimeError(f"TypeScript graph authoring failed: {result.stderr[:500]}")
        typescript = json.loads(result.stdout)
        if typescript.get("contract") != "sley-tools.graph-typescript-authoring-probe.v1" or typescript.get("result") != "PASS":
            raise RuntimeError("TypeScript graph authoring result differs")
    report = {
        "contract": "sley-tools.graph-sdk-authoring-probe.v1", "result": "PASS",
        "tools_binary_sha256": digest(tools),
        "builder_binary_sha256": digest(tools.with_name("sley-tools-native-builder")),
        "core_binary_sha256": digest(core), "spec_sha256": digest(spec),
        "initial_cases_sha256": digest(initial), "updated_cases_sha256": digest(updated),
        "python": python, "typescript": typescript, "false_oracle": failed,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.output}: Python and TypeScript native graph edits passed")


if __name__ == "__main__":
    main()
