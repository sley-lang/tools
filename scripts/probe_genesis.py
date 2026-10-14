#!/usr/bin/env python3
"""Submit a newly built workspace through the selected release's public SMP1 path."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ZERO_BOUNDS = {
    "applied_limits": dict.fromkeys(
        ("max_depth", "max_edges", "max_entities", "max_frame_bytes", "max_inflight", "max_response_bytes", "max_sessions", "max_work"), 0
    ),
    "continuation": False,
    "omitted": 0,
    "reached_depth": 0,
    "returned_bytes": 0,
    "returned_edges": 0,
    "returned_entities": 0,
    "truncated": False,
}
FLAGS = {"cancel": False, "failed": False, "stream": False}


def run(command: list[str], data: bytes = b"") -> bytes:
    completed = subprocess.run(command, input=data, capture_output=True, timeout=30, check=False)
    if completed.returncode:
        raise RuntimeError(f"{command[1]} exited {completed.returncode}: {completed.stderr[:300]!r}")
    return completed.stdout


def read_line(process: subprocess.Popen, pending: bytearray) -> dict:
    assert process.stdout is not None
    deadline = time.monotonic() + 30
    while b"\n" not in pending:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("SMP1 response timed out")
        if not select.select([process.stdout], [], [], remaining)[0]:
            raise TimeoutError("SMP1 response timed out")
        chunk = os.read(process.stdout.fileno(), 65_536)
        if not chunk:
            raise RuntimeError("SMP1 endpoint closed before response")
        pending.extend(chunk)
    line, _, rest = pending.partition(b"\n")
    pending[:] = rest
    return json.loads(line)


def send(process: subprocess.Popen, pending: bytearray, frame: dict) -> dict:
    assert process.stdin is not None
    process.stdin.write(json.dumps(frame, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    process.stdin.flush()
    while True:
        response = read_line(process, pending)
        if response["kind"] in ("hello", "response"):
            return response


def request(method: str, body: str, request_id: int, session: str | None, version: int = 1) -> dict:
    return {
        "kind": "request",
        "method": method,
        "body": body,
        "protocol_version": version,
        "request_id": request_id,
        "session": session,
        "bounds": ZERO_BOUNDS,
        "flags": FLAGS,
    }


def checked(response: dict, method: str) -> str:
    if response["kind"] != "response" or response["flags"]["failed"]:
        body = response.get("body", "")
        try:
            symbol = decode_bytes(record_fields(bytes.fromhex(body), 6)[1]).decode("ascii")
        except (ValueError, IndexError, UnicodeDecodeError):
            symbol = body[:200]
        raise RuntimeError(f"{method} refused: {symbol}")
    return response["body"]


def uvar(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if offset >= len(data) or shift > 63:
            raise ValueError("invalid SCB1 integer in workspace summary")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7f) << shift
        if byte < 128:
            return value, offset
        shift += 7


def record_fields(data: bytes, expected_count: int) -> list[bytes]:
    count, offset = uvar(data, 0)
    if count != expected_count:
        raise ValueError("unexpected record field count")
    fields = []
    for expected_tag in range(1, expected_count + 1):
        tag, offset = uvar(data, offset)
        length, offset = uvar(data, offset)
        if tag != expected_tag or length > len(data) - offset:
            raise ValueError("invalid workspace summary field")
        fields.append(data[offset : offset + length])
        offset += length
    if offset != len(data):
        raise ValueError("trailing workspace summary bytes")
    return fields


def decode_bytes(data: bytes) -> bytes:
    length, offset = uvar(data, 0)
    if length != len(data) - offset:
        raise ValueError("invalid SCB1 byte string")
    return data[offset:]


def encode_uvar(value: int) -> bytes:
    output = bytearray()
    while True:
        byte = value & 127
        value >>= 7
        output.append(byte | 128 if value else byte)
        if not value:
            return bytes(output)


def encode_record(fields: list[bytes]) -> bytes:
    output = bytearray(encode_uvar(len(fields)))
    for tag, value in enumerate(fields, start=1):
        output.extend(encode_uvar(tag))
        output.extend(encode_uvar(len(value)))
        output.extend(value)
    return bytes(output)


def observed_output_hash(execution_response: str, function_id: str, root_id: str, input_count: int = 1) -> tuple[str, str]:
    report_id, encoded_preimage = record_fields(bytes.fromhex(execution_response), 2)
    length, offset = uvar(encoded_preimage, 0)
    preimage = encoded_preimage[offset:]
    if length != len(preimage) or preimage[:8] != b"SLEYEXR1":
        raise ValueError("malformed execution report preimage")
    if preimage[108:140].hex() != root_id or preimage[140:172].hex() != function_id:
        raise RuntimeError("execution report is not bound to the committed root and function")
    if int.from_bytes(preimage[188:192], "big") != 1 or int.from_bytes(preimage[192:200], "big") != input_count:
        raise RuntimeError(f"execution report did not validate {input_count} caller inputs")
    result_offset = 236 + 32 * input_count
    termination_offset = result_offset + 36
    result_kind = int.from_bytes(preimage[result_offset:result_offset + 4], "big")
    termination_kind = int.from_bytes(preimage[termination_offset:termination_offset + 4], "big")
    if result_kind != 1 or termination_kind != 1:
        phase = int.from_bytes(preimage[result_offset + 4:result_offset + 8], "big")
        code = int.from_bytes(preimage[result_offset + 8:result_offset + 12], "big")
        raise RuntimeError(f"execution report did not observe successful termination (result={result_kind}, phase={phase}, code={code}, termination={termination_kind})")
    return report_id.hex(), preimage[termination_offset + 4:termination_offset + 36].hex()


def probe(binary: Path, builder: Path) -> dict:
    generated = json.loads(run([str(builder)]))
    hello = json.loads(run([str(binary), "frame", "decode"], run([str(binary), "hello"])))
    with tempfile.TemporaryDirectory(prefix="sley-tools-f0-genesis-") as work:
        base = Path(work)
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
            transaction = checked(send(process, pending, request("workspace.create", generated["workspace_create_hex"], 0, None)), "workspace.create")
            session = checked(send(process, pending, request("session.open", handshake, 0, None)), "session.open")
            summary = checked(send(process, pending, request("workspace.open", "", 1, session)), "workspace.open")
            fields = record_fields(bytes.fromhex(summary), 8)
            for index, expected in enumerate((transaction, generated["state_root"], generated["policy_root"], generated["workspace_id"], generated["schema_epoch"])):
                if fields[index].hex() != expected:
                    raise RuntimeError(f"workspace.open field {index + 1} differs from constructed genesis")
            object_count, end = uvar(fields[5], 0)
            if end != len(fields[5]) or object_count != 1:
                raise RuntimeError("workspace.open did not report the new namespace object")
            candidate = json.loads(run([
                str(builder), "candidate", generated["workspace_id"], generated["principal_id"],
                generated["state_root"], generated["policy_root"], transaction, generated["schema_epoch"],
            ]))
            created = checked(send(process, pending, request("candidate.create", candidate["candidate_record_hex"], 2, session)), "candidate.create")
            if created != candidate["candidate_stored_hex"]:
                raise RuntimeError("candidate.create differs from public builder's stored bytes")
            validation_body = encode_record([
                bytes.fromhex(transaction), bytes.fromhex(generated["principal_id"]),
                encode_uvar(candidate["now_unix_millis"]), bytes.fromhex(created),
            ]).hex()
            validation = checked(send(process, pending, request("candidate.validate", validation_body, 3, session)), "candidate.validate")
            committed = checked(send(process, pending, request("commit", validation_body, 4, session)), "commit")
            commit_fields = record_fields(bytes.fromhex(committed), 4)
            if len(commit_fields[0]) != 32 or len(commit_fields[2]) != 32:
                raise RuntimeError("commit returned malformed transaction or root")
            checked(send(process, pending, request("session.close", "", 5, session)), "session.close")
            new_session = checked(send(process, pending, request("session.open", handshake, 0, None)), "session.open after commit")
            executions = []
            next_request_id = 1
            for bit in (False, True):
                run_input = json.loads(run([str(builder), "execute", candidate["function_id"], generated["schema_epoch"], str(bit).lower()]))
                response = checked(send(process, pending, request("execute", run_input["execute_request_hex"], next_request_id, new_session)), "execute")
                next_request_id += 1
                report_id, observed_hash = observed_output_hash(response, candidate["function_id"], commit_fields[2].hex())
                if observed_hash != run_input["expected_output_hash"]:
                    raise RuntimeError(f"Bool identity execution differed for {bit}")
                stored = checked(send(process, pending, request("report", report_id, next_request_id, new_session)), "report")
                next_request_id += 1
                if stored != response:
                    raise RuntimeError("stored report differs from execution response")
                executions.append({"input": bit, "observed_output_hash": observed_hash, "report_id": report_id})
            if executions[0]["observed_output_hash"] == executions[1]["observed_output_hash"]:
                raise RuntimeError("distinct inputs produced the same output hash")
            edit_args = [
                generated["workspace_id"], generated["principal_id"], commit_fields[2].hex(),
                generated["policy_root"], commit_fields[0].hex(), generated["schema_epoch"],
                candidate["function_id"], candidate["parameter_id"], candidate["block_id"],
                candidate["block_object_id"],
            ]
            def make_edit(kind: str) -> tuple[dict, str]:
                nonlocal next_request_id
                built = json.loads(run([str(builder), "edit", *edit_args, kind]))
                stored = checked(send(process, pending, request("candidate.create", built["candidate_record_hex"], next_request_id, new_session)), "candidate.create edit")
                next_request_id += 1
                if stored != built["candidate_stored_hex"]:
                    raise RuntimeError("edit candidate differs from core builder")
                body = encode_record([
                    commit_fields[0], bytes.fromhex(generated["principal_id"]),
                    encode_uvar(built["now_unix_millis"]), bytes.fromhex(stored),
                ]).hex()
                checked(send(process, pending, request("candidate.validate", body, next_request_id, new_session)), "candidate.validate edit")
                next_request_id += 1
                return built, body

            invalid, invalid_body = make_edit("invalid")
            refusal = send(process, pending, request("commit", invalid_body, next_request_id, new_session))
            next_request_id += 1
            if not refusal["flags"]["failed"]:
                raise RuntimeError("semantic type defect unexpectedly committed")
            refusal_symbol = decode_bytes(record_fields(bytes.fromhex(refusal["body"]), 6)[1]).decode("ascii")
            after_refusal = checked(send(process, pending, request("workspace.open", "", next_request_id, new_session)), "workspace.open after refusal")
            next_request_id += 1
            unchanged = record_fields(bytes.fromhex(after_refusal), 8)
            if unchanged[0] != commit_fields[0] or unchanged[1] != commit_fields[2]:
                raise RuntimeError("rejected edit advanced accepted head")

            valid, valid_body = make_edit("valid")
            edited = checked(send(process, pending, request("commit", valid_body, next_request_id, new_session)), "commit valid edit")
            next_request_id += 1
            edited_fields = record_fields(bytes.fromhex(edited), 4)
            if edited_fields[2] == commit_fields[2]:
                raise RuntimeError("valid edit did not change accepted root")
            checked(send(process, pending, request("session.close", "", next_request_id, new_session)), "session.close after edit")
            final_session = checked(send(process, pending, request("session.open", handshake, 0, None)), "session.open final")
            edited_executions = []
            next_request_id = 1
            for bit in (False, True):
                run_input = json.loads(run([str(builder), "execute", candidate["function_id"], generated["schema_epoch"], str(bit).lower()]))
                response = checked(send(process, pending, request("execute", run_input["execute_request_hex"], next_request_id, final_session)), "execute edited")
                next_request_id += 1
                report_id, observed_hash = observed_output_hash(response, candidate["function_id"], edited_fields[2].hex())
                opposite = json.loads(run([str(builder), "execute", candidate["function_id"], generated["schema_epoch"], str(not bit).lower()]))
                if observed_hash != opposite["expected_output_hash"]:
                    raise RuntimeError(f"edited BoolNot execution differed for {bit}")
                edited_executions.append({"input": bit, "observed_output_hash": observed_hash, "report_id": report_id})
            branch_body = encode_record([b"main", edited_fields[0]]).hex()
            checked(send(process, pending, request("branch.create", branch_body, next_request_id, final_session)), "branch.create")
            next_request_id += 1
            export_hex = checked(send(process, pending, request("exchange.export", "", next_request_id, final_session)), "exchange.export")
            next_request_id += 1
            checked(send(process, pending, request("session.close", "", next_request_id, final_session)), "session.close final")
            assert process.stdin is not None
            process.stdin.close()
            if process.wait(timeout=30):
                raise RuntimeError("SMP1 endpoint exited unsuccessfully")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        (base / "clone").mkdir()
        clone = subprocess.Popen(
            [str(binary), "serve", "--repository", str(base / "clone"), "--json"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        clone_pending = bytearray()
        try:
            if send(clone, clone_pending, hello)["kind"] != "hello":
                raise RuntimeError("clone SMP1 hello was refused")
            checked(send(clone, clone_pending, request("exchange.import", export_hex, 0, None)), "exchange.import")
            clone_session = checked(send(clone, clone_pending, request("session.open", handshake, 0, None)), "clone session.open")
            for request_id, bit in enumerate((False, True), start=1):
                run_input = json.loads(run([str(builder), "execute", candidate["function_id"], generated["schema_epoch"], str(bit).lower()]))
                replay = checked(send(clone, clone_pending, request("execute", run_input["execute_request_hex"], request_id, clone_session)), "clone execute")
                replay_id, replay_hash = observed_output_hash(replay, candidate["function_id"], edited_fields[2].hex())
                if replay_id != edited_executions[request_id - 1]["report_id"] or replay_hash != edited_executions[request_id - 1]["observed_output_hash"]:
                    raise RuntimeError("fresh workspace did not reproduce the edited artifact")
            checked(send(clone, clone_pending, request("session.close", "", 3, clone_session)), "clone session.close")
            assert clone.stdin is not None
            clone.stdin.close()
            if clone.wait(timeout=30):
                raise RuntimeError("clone SMP1 endpoint exited unsuccessfully")
        finally:
            if clone.poll() is None:
                clone.kill()
                clone.wait()
        v3_hello = json.loads(run(
            [str(binary), "frame", "decode", "--protocol-profile", "v3-capable", "--expected-version", "1"],
            run([str(binary), "hello", "--protocol-profile", "v3-capable"]),
        ))
        v3_report_file = base / "v3-handshake.json"
        run(
            [str(binary), "serve", "--repository", str(base / "clone"), "--json", "--protocol-profile", "v3-capable", "--report", str(v3_report_file)],
            json.dumps(v3_hello).encode() + b"\n",
        )
        v3_handshake = json.loads(v3_report_file.read_text(encoding="utf-8"))["handshake_id"]
        native = subprocess.Popen(
            [str(binary), "serve", "--repository", str(base / "clone"), "--json", "--protocol-profile", "v3-capable"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        native_pending = bytearray()
        try:
            if send(native, native_pending, v3_hello)["kind"] != "hello":
                raise RuntimeError("v3 SMP1 hello was refused")
            native_session = checked(send(native, native_pending, request("session.open", v3_handshake, 0, None, 3)), "v3 session.open")
            test_request = json.loads(run([str(builder), "native-tests-request", edited_fields[2].hex()]))
            test_response = send(native, native_pending, request("tests.selected", test_request["tests_selected_request_hex"], 1, native_session, 3))
            native_test_result = "accepted" if not test_response["flags"]["failed"] else decode_bytes(record_fields(bytes.fromhex(test_response["body"]), 6)[1]).decode("ascii")
            checked(send(native, native_pending, request("session.close", "", 2, native_session, 3)), "v3 session.close")
            assert native.stdin is not None
            native.stdin.close()
            if native.wait(timeout=30):
                raise RuntimeError("v3 SMP1 endpoint exited unsuccessfully")
        finally:
            if native.poll() is None:
                native.kill()
                native.wait()
    return {
        "contract": "sley-tools.f0-genesis-probe.v1",
        "result": "PASS",
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "route": "public codecs at af34be3 -> release binary SMP1 workspace.create, candidate.create, candidate.validate, commit, execute, exchange.export/import",
        "workspace_id": generated["workspace_id"],
        "principal_id": generated["principal_id"],
        "state_root": generated["state_root"],
        "policy_root": generated["policy_root"],
        "namespace_entity": generated["namespace_entity"],
        "schema_epoch": generated["schema_epoch"],
        "transaction_id": transaction,
        "candidate_id": candidate["candidate_id"],
        "candidate_stored_matches_core": True,
        "candidate_validation_result_magic": bytes.fromhex(validation)[:8].decode("ascii"),
        "committed_transaction_id": commit_fields[0].hex(),
        "committed_state_root": commit_fields[2].hex(),
        "function_id": candidate["function_id"],
        "executions": executions,
        "rejected_candidate_id": invalid["candidate_id"],
        "rejection_symbol": refusal_symbol,
        "root_unchanged_after_rejection": True,
        "edited_candidate_id": valid["candidate_id"],
        "edited_transaction_id": edited_fields[0].hex(),
        "edited_state_root": edited_fields[2].hex(),
        "edited_executions": edited_executions,
        "export_bytes": len(export_hex) // 2,
        "fresh_import_reproduced_report_ids": True,
        "native_tests_selected_v3_result": native_test_result,
        "receipt_id": fields[7].hex(),
        "workspace_open_exact_identity_match": True,
        "object_count": object_count,
        "scope": "Fresh scoped workspace genesis, new Bool identity function, two caller-input executions, rejected type defect with unchanged root, accepted BoolNot edit, and export/import replay; native tests pending",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sley", required=True, type=Path)
    parser.add_argument("--builder", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = probe(args.sley.resolve(), args.builder.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: fresh construction, execution, rejection, and edit passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError, TimeoutError, subprocess.TimeoutExpired) as error:
        print(f"genesis probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
