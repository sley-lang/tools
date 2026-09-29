#!/usr/bin/env python3
"""Prove typed graph lowering, core validation, commit, and VM execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from probe_genesis import checked, encode_record, encode_uvar, record_fields, request, run, send


def build(builder: Path, spec: Path, context: list[str], *, accepted: bool) -> dict | None:
    result = subprocess.run([str(builder), "construct-graph", str(spec), *context],
                            capture_output=True, text=True, timeout=30, check=False)
    if accepted:
        if result.returncode:
            raise RuntimeError(f"typed graph construction failed: {result.stderr[:300]}")
        return json.loads(result.stdout)
    if not result.returncode or not result.stderr:
        raise RuntimeError("invalid typed graph was accepted or lacked diagnostics")
    return None


def rejected_specs(builder: Path, spec: Path, context: list[str], folder: Path) -> dict:
    source = json.loads(spec.read_text(encoding="utf-8"))
    changes = {
        "wrong_operand_type": lambda item: item["blocks"][0]["operations"][0].update(op="bool_and"),
        "unknown_branch_target": lambda item: item["blocks"][0]["terminator"].update(if_true="missing"),
        "cycle": lambda item: item["blocks"][1].update(terminator={"kind": "branch", "target": "entry"}),
        "duplicate_operation": lambda item: item["blocks"][2]["operations"][0].update(name="invalid"),
        "wrong_constant_type": lambda item: item["constants"][0].update(value="true"),
    }
    for name, mutate in changes.items():
        candidate = json.loads(json.dumps(source))
        mutate(candidate)
        path = folder / f"{name}.json"
        path.write_text(json.dumps(candidate), encoding="utf-8")
        build(builder, path, context, accepted=False)
    return {name: True for name in changes}


def native_case(builder: Path, repository: Path, root: str, function: str,
                spec: Path, value: int, lower: int, upper: int) -> dict:
    values = [str(value), str(lower), str(upper)]
    result = subprocess.run([str(builder), "run-graph", str(repository), root, function,
                             str(spec), json.dumps(values)], capture_output=True, text=True,
                            timeout=30, check=False)
    if result.returncode:
        raise RuntimeError(f"native graph execution failed for {values}: {result.stderr[:300]}")
    observed = json.loads(result.stdout)
    invalid = lower > upper
    below = not invalid and value < lower
    above = not invalid and value > upper
    expected = {"within_range": not invalid and not below and not above,
                "invalid_bounds": invalid, "below_lower": below, "above_upper": above}
    if observed["result"] != expected or observed["root"] != root or observed["function_id"] != function:
        raise RuntimeError(f"native Range Predicate differs from independent oracle for {values}")
    if len(observed["observation_id"]) != 64 or len(observed["value_hash"]) != 64:
        raise RuntimeError("native graph execution omitted exact observation or value identity")
    return {"input": values, "expected": expected, "value_hash": observed["value_hash"]}


def commit_next(core: Path, workspace: Path, hello: dict, handshake: str, constructed: dict,
                transaction: str, root: str, principal: str) -> tuple[str, str]:
    process = subprocess.Popen([str(core), "serve", "--repository", str(workspace), "--json"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pending = bytearray()
    try:
        if send(process, pending, hello)["kind"] != "hello":
            raise RuntimeError("core hello was refused")
        session = checked(send(process, pending, request("session.open", handshake, 0, None)), "session.open")
        summary = checked(send(process, pending, request("workspace.open", "", 1, session)), "workspace.open")
        fields = record_fields(bytes.fromhex(summary), 8)
        if fields[0].hex() != transaction or fields[1].hex() != root:
            raise RuntimeError("core accepted head differs from typed graph base")
        stored = checked(send(process, pending, request("candidate.create", constructed["candidate_record_hex"], 2, session)), "candidate.create")
        if stored != constructed["candidate_stored_hex"]:
            raise RuntimeError("core candidate storage differs from typed builder")
        body = encode_record([bytes.fromhex(transaction), bytes.fromhex(principal),
                              encode_uvar(constructed["now_unix_millis"]), bytes.fromhex(stored)]).hex()
        checked(send(process, pending, request("candidate.validate", body, 3, session)), "candidate.validate")
        committed = checked(send(process, pending, request("commit", body, 4, session)), "commit")
        receipt = record_fields(bytes.fromhex(committed), 4)
        accepted_root = receipt[2].hex()
        if accepted_root == root:
            raise RuntimeError("typed graph candidate did not change accepted root")
        checked(send(process, pending, request("session.close", "", 5, session)), "session.close")
        assert process.stdin is not None
        process.stdin.close()
        if process.wait(timeout=30):
            raise RuntimeError("core endpoint exited unsuccessfully")
        return receipt[0].hex(), accepted_root
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def unsigned_case(builder: Path, workspace: Path, root: str, function: str,
                  spec: Path, count: int, limit: int) -> dict:
    values = [str(count), str(limit)]
    result = subprocess.run([str(builder), "run-graph", str(workspace), root, function,
                             str(spec), json.dumps(values)], capture_output=True, text=True,
                            timeout=30, check=False)
    if result.returncode:
        raise RuntimeError(f"unsigned graph execution failed: {result.stderr[:300]}")
    observed = json.loads(result.stdout)
    expected = count <= limit
    if observed["result"] is not expected or observed["root"] != root:
        raise RuntimeError(f"native unsigned graph differs from oracle for {values}")
    return {"input": values, "expected": expected, "value_hash": observed["value_hash"]}


def probe(tools: Path, builder: Path, core: Path, spec: Path, unsigned_spec: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="sley-graph-builder-") as temporary:
        folder = Path(temporary)
        project = folder / "project"
        init = subprocess.run([str(tools), "init", str(project), "--template", "release-readiness",
                               "--core", str(core)], capture_output=True, text=True, timeout=30, check=False)
        if init.returncode:
            raise RuntimeError(f"fixture workspace init failed: {init.stderr[:300]}")
        state = json.loads((project / ".sley-tools/state.json").read_text(encoding="utf-8"))
        context = [state[name] for name in ("workspace_id", "principal_id", "state_root",
                                            "policy_root", "transaction_id", "schema_epoch")]
        negatives = rejected_specs(builder, spec, context, folder)
        constructed = build(builder, spec, context, accepted=True)
        assert constructed is not None
        if constructed["contract"] != "sley-tools.construct-graph.v0" or constructed["entity_count"] != 20 or constructed["operation_count"] != 10:
            raise RuntimeError("typed graph did not lower the expected native entities")
        hello = json.loads(run([str(core), "frame", "decode"], run([str(core), "hello"])))
        report = folder / "handshake.json"
        run([str(core), "serve", "--repository", str(folder / "handshake"), "--json", "--report", str(report)],
            json.dumps(hello).encode() + b"\n")
        handshake = json.loads(report.read_text(encoding="utf-8"))["handshake_id"]
        accepted_transaction, accepted_root = commit_next(core, project / "workspace", hello, handshake,
                                                           constructed, state["transaction_id"],
                                                           state["state_root"], state["principal_id"])
        matrix = [(-5, -5, 5), (0, -5, 5), (5, -5, 5), (-6, -5, 5),
                  (6, -5, 5), (0, 5, -5), (-(2**63), -(2**63), 2**63 - 1),
                  (2**63 - 1, -(2**63), 2**63 - 1)]
        cases = [native_case(builder, project / "workspace", accepted_root, constructed["function_id"],
                             spec, *case) for case in matrix]
        bad_input = subprocess.run([str(builder), "run-graph", str(project / "workspace"),
                                    accepted_root, constructed["function_id"], str(spec),
                                    '["01","0","1"]'], capture_output=True, text=True, timeout=30, check=False)
        if not bad_input.returncode:
            raise RuntimeError("noncanonical signed input was accepted")
        unsigned_context = [state["workspace_id"], state["principal_id"], accepted_root,
                            state["policy_root"], accepted_transaction, state["schema_epoch"]]
        unsigned = build(builder, unsigned_spec, unsigned_context, accepted=True)
        assert unsigned is not None
        if unsigned["entity_count"] != 5 or unsigned["operation_count"] != 1 or unsigned["result_type_id"] is not None:
            raise RuntimeError("primitive result graph did not lower the expected native entities")
        _, unsigned_root = commit_next(core, project / "workspace", hello, handshake, unsigned,
                                       accepted_transaction, accepted_root, state["principal_id"])
        unsigned_cases = [unsigned_case(builder, project / "workspace", unsigned_root,
                                        unsigned["function_id"], unsigned_spec, *case)
                          for case in ((0, 0), (9, 10), (10, 9), (2**32 - 1, 2**32 - 1))]
        bad_unsigned = subprocess.run([str(builder), "run-graph", str(project / "workspace"),
                                       unsigned_root, unsigned["function_id"], str(unsigned_spec),
                                       '["-1","0"]'], capture_output=True, text=True, timeout=30, check=False)
        if not bad_unsigned.returncode:
            raise RuntimeError("negative UInt32 input was accepted")
        return {"initial_root": state["state_root"], "accepted_root": accepted_root,
                "candidate_id": constructed["candidate_id"], "function_id": constructed["function_id"],
                "entity_count": constructed["entity_count"], "operation_count": constructed["operation_count"],
                "cases": cases, "negative_construction": negatives, "noncanonical_input_rejected": True,
                "unsigned_graph": {"accepted_root": unsigned_root, "candidate_id": unsigned["candidate_id"],
                                   "function_id": unsigned["function_id"], "entity_count": unsigned["entity_count"],
                                   "operation_count": unsigned["operation_count"], "cases": unsigned_cases,
                                   "negative_input_rejected": True}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "builder", "core", "spec", "unsigned-spec", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    tools, builder, core, spec, unsigned_spec = (getattr(args, name).resolve() for name in ("tools", "builder", "core", "spec", "unsigned_spec"))
    result = probe(tools, builder, core, spec, unsigned_spec)
    evidence = {"contract": "sley-tools.graph-builder-probe.v1", "result": "PASS",
                "tools_binary_sha256": hashlib.sha256(tools.read_bytes()).hexdigest(),
                "builder_binary_sha256": hashlib.sha256(builder.read_bytes()).hexdigest(),
                "core_binary_sha256": hashlib.sha256(core.read_bytes()).hexdigest(),
                "spec_sha256": hashlib.sha256(spec.read_bytes()).hexdigest(),
                "unsigned_spec_sha256": hashlib.sha256(unsigned_spec.read_bytes()).hexdigest(),
                "graph": result,
                "native_core_test_admission": "NOT_RUN: production native executor unavailable"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: two typed graphs core-committed and executed on twelve native cases")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"graph builder probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
