#!/usr/bin/env python3
"""Exercise a newly constructed native Release Readiness SSMC1 graph."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from probe_genesis import (
    checked,
    encode_record,
    encode_uvar,
    record_fields,
    request,
    run,
    send,
)


def oracle(completed: bool, failed: int, verified: bool, blocking: int, threshold: int) -> list[bool]:
    reasons = [not completed, failed > 0, not verified, blocking > threshold]
    return [not any(reasons), *reasons]


def initial_cases() -> list[tuple[bool, int, bool, int]]:
    # The two count dimensions each cross zero in every boolean combination.
    cases = list(itertools.product((False, True), (0, 1), (False, True), (0, 1)))
    cases += [(True, 2**32 - 1, True, 0), (True, 0, True, 2**32 - 1)]
    return cases


def edited_cases() -> list[tuple[bool, int, bool, int]]:
    return list(itertools.product((False, True), (0, 1), (False, True), (0, 1, 2, 3, 2**32 - 1)))


def probe(binary: Path, genesis_builder: Path, native_builder: Path) -> dict:
    genesis = json.loads(run([str(genesis_builder), "genesis"]))
    hello = json.loads(run([str(binary), "frame", "decode"], run([str(binary), "hello"])))
    with tempfile.TemporaryDirectory(prefix="sley-tools-n01-") as temp:
        base = Path(temp)
        report_file = base / "handshake.json"
        run(
            [str(binary), "serve", "--repository", str(base / "handshake"), "--json", "--report", str(report_file)],
            json.dumps(hello).encode() + b"\n",
        )
        handshake = json.loads(report_file.read_text(encoding="utf-8"))["handshake_id"]
        (base / "workspace").mkdir()
        process = subprocess.Popen(
            [str(binary), "serve", "--repository", str(base / "workspace"), "--json"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        pending = bytearray()
        try:
            if send(process, pending, hello)["kind"] != "hello":
                raise RuntimeError("SMP1 hello was refused")
            transaction = checked(send(process, pending, request("workspace.create", genesis["workspace_create_hex"], 0, None)), "workspace.create")
            session = checked(send(process, pending, request("session.open", handshake, 0, None)), "session.open")
            context = [
                genesis["workspace_id"], genesis["principal_id"], genesis["state_root"],
                genesis["policy_root"], transaction, genesis["schema_epoch"],
            ]
            constructed = json.loads(run([str(native_builder), "construct", *context]))
            expected_names = ["allow", "test_run_incomplete", "tests_failed", "artifact_unverified", "blocking_findings_exceeded"]
            fields = constructed["result_fields"]
            if [field["name"] for field in fields] != expected_names or len({field["member_id"] for field in fields}) != 5:
                raise RuntimeError("native result field identities do not match the declared contract")
            created = checked(send(process, pending, request("candidate.create", constructed["candidate_record_hex"], 1, session)), "candidate.create")
            if created != constructed["candidate_stored_hex"]:
                raise RuntimeError("constructed candidate bytes differ from core storage")
            validation_body = encode_record([
                bytes.fromhex(transaction), bytes.fromhex(genesis["principal_id"]),
                encode_uvar(constructed["now_unix_millis"]), bytes.fromhex(created),
            ]).hex()
            checked(send(process, pending, request("candidate.validate", validation_body, 2, session)), "candidate.validate")
            committed = checked(send(process, pending, request("commit", validation_body, 3, session)), "commit")
            first_commit = record_fields(bytes.fromhex(committed), 4)
            checked(send(process, pending, request("session.close", "", 4, session)), "session.close")
            session = checked(send(process, pending, request("session.open", handshake, 0, None)), "session.open after commit")
            sequence = 1

            def run_case(facts: tuple[bool, int, bool, int], threshold: int, root: str) -> dict:
                nonlocal sequence
                expected = oracle(*facts, threshold)
                observed = json.loads(run([
                    str(native_builder), "run", str(base / "workspace"), root,
                    constructed["function_id"], str(facts[0]).lower(), str(facts[1]),
                    str(facts[2]).lower(), str(facts[3]),
                ]))
                if observed["root"] != root or observed["flags"] != expected:
                    raise RuntimeError(f"Sley output differs from independent oracle: {facts}, threshold={threshold}")
                if observed["result"] != dict(zip(expected_names, expected)):
                    raise RuntimeError("named result differs from native flags")
                if observed["reason_codes"] != [name for name, active in zip(expected_names[1:], expected[1:]) if active]:
                    raise RuntimeError("reason codes differ from native flags")
                return {"input": list(facts), "expected": expected, "observation_id": observed["observation_id"], "output_hash": observed["value_hash"]}

            before = [run_case(case, 0, first_commit[2].hex()) for case in initial_cases()]
            # Native UInt32 makes negative and >U32_MAX count values unrepresentable.
            invalid_inputs = []
            for bad in ("-1", str(2**32)):
                result = subprocess.run(
                    [str(native_builder), "run", str(base / "workspace"), first_commit[2].hex(),
                     constructed["function_id"], "true", bad, "true", "0"],
                    capture_output=True, check=False, timeout=30,
                )
                if result.returncode == 0:
                    raise RuntimeError(f"out-of-domain count was accepted: {bad}")
                invalid_inputs.append(bad)

            edit_context = [
                genesis["workspace_id"], genesis["principal_id"], first_commit[2].hex(),
                genesis["policy_root"], first_commit[0].hex(), genesis["schema_epoch"],
            ]
            edit = json.loads(run([
                str(native_builder), "edit", *edit_context,
                constructed["threshold_id"], constructed["threshold_object_id"], "2",
            ]))
            edit_stored = checked(send(process, pending, request("candidate.create", edit["candidate_record_hex"], sequence, session)), "candidate.create edit")
            sequence += 1
            if edit_stored != edit["candidate_stored_hex"]:
                raise RuntimeError("edited candidate bytes differ from core storage")
            edit_body = encode_record([
                first_commit[0], bytes.fromhex(genesis["principal_id"]),
                encode_uvar(edit["now_unix_millis"]), bytes.fromhex(edit_stored),
            ]).hex()
            checked(send(process, pending, request("candidate.validate", edit_body, sequence, session)), "candidate.validate edit")
            sequence += 1
            second = checked(send(process, pending, request("commit", edit_body, sequence, session)), "commit edit")
            sequence += 1
            second_commit = record_fields(bytes.fromhex(second), 4)
            if first_commit[2] == second_commit[2]:
                raise RuntimeError("threshold edit did not change the accepted root")
            checked(send(process, pending, request("session.close", "", sequence, session)), "session.close after edit")
            session = checked(send(process, pending, request("session.open", handshake, 0, None)), "session.open edited")
            sequence = 1
            after = [run_case(case, 2, second_commit[2].hex()) for case in edited_cases()]
            indexed = {tuple(record["input"]): record["expected"] for record in after}
            for completed, failed, verified in itertools.product((False, True), (0, 1), (False, True)):
                decisions = [indexed[(completed, failed, verified, blocking)][0] for blocking in (0, 1, 2, 3, 2**32 - 1)]
                if any(later and not earlier for earlier, later in zip(decisions, decisions[1:])):
                    raise RuntimeError("increasing blocking findings turned rejection into approval")
            before_by_input = {tuple(record["input"]): record["expected"] for record in before}
            if before_by_input[(True, 0, True, 1)][0] or not indexed[(True, 0, True, 1)][0]:
                raise RuntimeError("native threshold edit did not change the intended boundary decision")
            for facts, old in before_by_input.items():
                if facts in indexed and facts[3] == 0 and indexed[facts] != old:
                    raise RuntimeError("threshold edit changed a zero-blocking decision")
            checked(send(process, pending, request("session.close", "", sequence, session)), "session.close final")
            assert process.stdin is not None
            process.stdin.close()
            if process.wait(timeout=30):
                raise RuntimeError("SMP1 endpoint exited unsuccessfully")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    return {
        "contract": "sley-tools.release-readiness-probe.v1",
        "result": "PASS",
        "core_binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "program": "release-readiness",
        "representation": "native SSMC1 graph accepted by public Sley core",
        "execution": "Public Sley VM library pinned to release commit, loading accepted repository head with extended_v1; typed flags compared with independent Python oracle",
        "workspace_id": genesis["workspace_id"],
        "function_id": constructed["function_id"],
        "result_type_id": constructed["result_type_id"],
        "result_fields": constructed["result_fields"],
        "first_candidate_id": constructed["candidate_id"],
        "first_root": first_commit[2].hex(),
        "threshold_entity_id": constructed["threshold_id"],
        "threshold_initial": 0,
        "initial_cases": before,
        "invalid_count_inputs_rejected_at_tools_boundary": invalid_inputs,
        "edit_candidate_id": edit["candidate_id"],
        "edited_root": second_commit[2].hex(),
        "threshold_edited": 2,
        "edited_cases": after,
        "native_test_admission": "NOT_RUN: F0 native executor unavailable",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sley", required=True, type=Path)
    parser.add_argument("--genesis-builder", required=True, type=Path)
    parser.add_argument("--native-builder", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = probe(args.sley.resolve(), args.genesis_builder.resolve(), args.native_builder.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: {len(result['initial_cases'])} initial and {len(result['edited_cases'])} edited native executions passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError, TimeoutError, subprocess.TimeoutExpired) as error:
        print(f"Release Readiness probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
