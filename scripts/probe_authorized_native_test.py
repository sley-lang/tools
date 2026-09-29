#!/usr/bin/env python3
"""Exercise one real F0 native TestCase through a provisioned v3 receiver."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from probe_genesis import (checked, decode_bytes, encode_record, encode_uvar,
                           record_fields, request, run, send, uvar)
from probe_native_test_route import validation_decision


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def exact_uvar(value: bytes) -> int:
    decoded, end = uvar(value, 0)
    if end != len(value):
        raise ValueError("trailing bytes after SCB1 integer")
    return decoded


def encoded_list(items: list[bytes]) -> bytes:
    return encode_uvar(len(items)) + b"".join(encode_uvar(len(item)) + item for item in items)


def encoded_option(value: int | None) -> bytes:
    payload = b"" if value is None else encode_uvar(value)
    return encode_uvar(0 if value is None else 1) + encode_uvar(len(payload)) + payload


def report_summary(stored: bytes, report_id: bytes, plan_id: bytes,
                   test_entity_id: bytes, schema_epoch: bytes) -> dict:
    if len(report_id) != 32 or len(plan_id) != 32 or stored[:8] != b"SLEYNTS1":
        raise ValueError("native test report identity or magic differs")
    version, offset = uvar(stored, 8)
    length, offset = uvar(stored, offset)
    if version != 1 or offset + length + 32 != len(stored) or stored[-32:] != report_id:
        raise ValueError("native test report envelope differs")
    fields = record_fields(stored[offset:offset + length], 8)
    if fields[1] != plan_id or fields[2] != schema_epoch:
        raise ValueError("native test report plan or epoch differs")
    entry_count, entry_offset = uvar(fields[4], 0)
    if entry_count != 1:
        raise ValueError("native test report does not contain one entry")
    entry_length, entry_offset = uvar(fields[4], entry_offset)
    if entry_offset + entry_length != len(fields[4]):
        raise ValueError("native test report entry length differs")
    entry = record_fields(fields[4][entry_offset:], 5)
    if entry[0] != test_entity_id or exact_uvar(entry[4]) != 1:
        raise ValueError("native test report entry does not match the selected TestCase")
    counts = [exact_uvar(fields[index]) for index in (5, 6, 7)]
    if counts != [1, 0, 0]:
        raise ValueError(f"selected native test did not match: {counts}")
    return {"report_id": report_id.hex(), "plan_id": plan_id.hex(),
            "proposed_root": fields[3].hex(), "match_count": counts[0],
            "mismatch_count": counts[1], "rejected_count": counts[2],
            "test_entity_id": test_entity_id.hex(),
            "stored_sha256": hashlib.sha256(stored).hexdigest()}


def inspected_report_summary(stored: bytes, inspector: Path, report_id: bytes,
                             plan_id: bytes, test_entity_id: bytes,
                             schema_epoch: bytes) -> dict:
    with tempfile.TemporaryDirectory(prefix="sley-native-report-inspection-") as temporary:
        report_path = Path(temporary) / "report.sleyns1"
        report_path.write_bytes(stored)
        result = subprocess.run([str(inspector), "inspect-native-test-report", str(report_path)],
                                capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError(f"core report parser refused the native report: {result.stderr[-300:]}")
    inspected = json.loads(result.stdout)
    entries = inspected.get("entries")
    if (inspected.get("contract") != "sley-tools.native-test-report-inspection.v0"
            or inspected.get("report_id") != report_id.hex()
            or inspected.get("plan_id") != plan_id.hex()
            or inspected.get("schema_epoch") != schema_epoch.hex()
            or inspected.get("stored_sha256") != hashlib.sha256(stored).hexdigest()
            or not isinstance(entries, list) or len(entries) != 1
            or not isinstance(entries[0], dict)
            or entries[0].get("test_entity_id") != test_entity_id.hex()
            or entries[0].get("comparison_tag") != 1
            or [inspected.get(name) for name in
                ("match_count", "mismatch_count", "rejected_count")] != [1, 0, 0]):
        raise RuntimeError("core-parsed native report differs from the selected TestCase")
    return {"report_id": inspected["report_id"], "plan_id": inspected["plan_id"],
            "proposed_root": inspected["proposed_root"], "match_count": 1,
            "mismatch_count": 0, "rejected_count": 0,
            "test_entity_id": test_entity_id.hex(),
            "stored_sha256": inspected["stored_sha256"],
            "core_parser_verified": True}


def read_report(process: subprocess.Popen, pending: bytearray, session: str,
                next_id: int, response_body: str, test_entity_id: bytes,
                schema_epoch: bytes, inspector: Path | None = None) -> tuple[dict, int]:
    fields = record_fields(bytes.fromhex(response_body), 6)
    plan_id, report_id = fields[:2]
    status, selected = exact_uvar(fields[2]), exact_uvar(fields[3])
    token, total = fields[4], exact_uvar(fields[5])
    if status != 1 or selected != 1 or len(token) != 32 or not 0 < total <= 4 * 1024 * 1024:
        raise RuntimeError("native diagnostic did not complete exactly one selected test")
    collected = bytearray()
    bound_root = None
    while len(collected) < total:
        offset = len(collected)
        body = encode_record([token, encode_uvar(offset), encode_uvar(65_536)]).hex()
        page = record_fields(bytes.fromhex(checked(send(process, pending, request(
            "tests.report_read", body, next_id, session, 3)), "tests.report_read")), 6)
        next_id += 1
        segment = decode_bytes(page[4])
        if (page[0] != report_id or exact_uvar(page[2]) != offset
                or exact_uvar(page[3]) != total or not 0 < len(segment) <= 65_536):
            raise RuntimeError("native diagnostic report page is incomplete")
        if bound_root is None:
            bound_root = page[1]
        elif bound_root != page[1]:
            raise RuntimeError("native diagnostic report root changed between pages")
        expected_next = offset + len(segment)
        if page[5] != encoded_option(None if expected_next == total else expected_next):
            raise RuntimeError("native diagnostic report continuation differs")
        collected.extend(segment)
    if inspector is None:
        summary = report_summary(bytes(collected), report_id, plan_id,
                                 test_entity_id, schema_epoch)
    else:
        summary = inspected_report_summary(bytes(collected), inspector, report_id, plan_id,
                                           test_entity_id, schema_epoch)
    if bound_root is None or summary["proposed_root"] != bound_root.hex():
        raise RuntimeError("native diagnostic report root binding differs")
    return summary, next_id


def failure_symbol(response: dict) -> str:
    if response.get("kind") != "response" or not response.get("flags", {}).get("failed"):
        raise RuntimeError("invalid selected native test unexpectedly succeeded")
    return decode_bytes(record_fields(bytes.fromhex(response["body"]), 6)[1]).decode("ascii")


def probe(core: Path, project: Path, candidate_path: Path, receiver_config: Path,
          plan_path: Path, stage_path: Path, inspector: Path | None = None) -> dict:
    state_path = project / ".sley-tools/state.json"
    state_bytes = state_path.read_bytes()
    state = json.loads(state_bytes)
    candidate_bytes = candidate_path.read_bytes()
    candidate = json.loads(candidate_bytes)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    stage = json.loads(stage_path.read_text(encoding="utf-8"))
    staged_core = next((entry for entry in stage["files"]
                        if entry["install_path"] == "/usr/lib/sley/sley"), None)
    now = int(time.time() * 1000)
    if (plan["contract"] != "sley.native-f0-authority-plan.v0"
            or plan["stage_manifest_sha256"] != digest(stage_path)
            or staged_core is None or staged_core["sha256"] != digest(core)
            or plan["state_sha256"] != hashlib.sha256(state_bytes).hexdigest()
            or plan["candidate_sha256"] != hashlib.sha256(candidate_bytes).hexdigest()
            or plan["workspace_id"] != state["workspace_id"]
            or plan["commit_principal_id"] != state["principal_id"]
            or plan["receiver_uid"] != os.getuid()
            or plan["candidate_expires_unix_millis"] <= now + 120_000
            or candidate["native_plan_selected_test_count"] != 1):
        raise RuntimeError("authority plan does not bind a fresh selected fixture")
    if any(path.is_symlink() for path in project.rglob("*")):
        raise RuntimeError("fixture project contains a symlink")
    hello = json.loads(run(
        [str(core), "frame", "decode", "--protocol-profile", "v3-capable", "--expected-version", "1"],
        run([str(core), "hello", "--protocol-profile", "v3-capable"]),
    ))
    with tempfile.TemporaryDirectory(prefix="sley-f0-authorized-native-test-") as temporary:
        base = Path(temporary)
        copied = base / "project"
        shutil.copytree(project, copied)
        if (copied / ".sley-tools/state.json").read_bytes() != state_bytes:
            raise RuntimeError("fixture copy changed accepted state")
        repository = copied / "workspace"
        handshake_report = base / "handshake.json"
        run([str(core), "serve", "--repository", str(base / "handshake"), "--json",
             "--protocol-profile", "v3-capable", "--report", str(handshake_report)],
            json.dumps(hello).encode() + b"\n")
        handshake = json.loads(handshake_report.read_text(encoding="utf-8"))["handshake_id"]
        process = subprocess.Popen(
            [str(core), "serve", "--repository", str(repository), "--json",
             "--protocol-profile", "v3-capable", "--native-authority-config", str(receiver_config)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        pending = bytearray()
        try:
            if send(process, pending, hello)["kind"] != "hello":
                raise RuntimeError("configured core refused the v3 hello")
            session = checked(send(process, pending, request("session.open", handshake, 0, None, 3)),
                              "session.open")
            before = record_fields(bytes.fromhex(checked(send(process, pending, request(
                "workspace.open", "", 1, session, 3)), "workspace.open")), 8)
            if before[0].hex() != state["transaction_id"] or before[1].hex() != state["state_root"]:
                raise RuntimeError("accepted head differs from prepared fixture")
            stored = checked(send(process, pending, request(
                "candidate.create", candidate["candidate_record_hex"], 2, session, 3)), "candidate.create")
            if stored != candidate["candidate_stored_hex"]:
                raise RuntimeError("core candidate bytes differ from prepared candidate")
            validation = encode_record([
                bytes.fromhex(state["transaction_id"]), bytes.fromhex(state["principal_id"]),
                encode_uvar(int(time.time() * 1000)), bytes.fromhex(stored),
            ]).hex()
            validated = checked(send(process, pending, request(
                "candidate.validate", validation, 3, session, 3)), "candidate.validate")
            decision = validation_decision(validated)
            if decision != 1:
                raise RuntimeError("core did not statically validate the selected candidate")
            affected_body = encode_record([
                bytes.fromhex(stored), bytes.fromhex(candidate["native_execution_profile_id"]),
                bytes(range(16)),
            ]).hex()
            affected, next_id = read_report(process, pending, session, 5, checked(send(
                process, pending, request("tests.affected", affected_body, 4, session, 3)),
                "tests.affected"), bytes.fromhex(candidate["test_entity_id"]),
                bytes.fromhex(state["schema_epoch"]), inspector)
            after_diagnostic = record_fields(bytes.fromhex(checked(send(
                process, pending, request("workspace.open", "", next_id, session, 3)),
                "workspace.open")), 8)
            next_id += 1
            if after_diagnostic[:2] != before[:2] or affected["proposed_root"] == state["state_root"]:
                raise RuntimeError("candidate diagnostic advanced the head or did not propose a new root")
            commit_attempt = bytes(range(16, 32))
            commit_body = encode_record([
                bytes.fromhex(stored), bytes.fromhex(state["transaction_id"]),
                commit_attempt, bytes.fromhex(plan["native_admission_profile_id"]),
            ]).hex()
            commit = record_fields(bytes.fromhex(checked(send(process, pending, request(
                "commit", commit_body, next_id, session, 3)), "commit")), 6)
            next_id += 1
            if (commit[0] == before[0] or commit[2].hex() != affected["proposed_root"]
                    or commit[5] != commit_attempt):
                raise RuntimeError("native commit did not accept the diagnostic's proposed root")
            checked(send(process, pending, request("session.close", "", next_id, session, 3)),
                    "session.close")
            next_id += 1
            session = checked(send(process, pending, request(
                "session.open", handshake, 0, None, 3)), "session.open")
            next_id = 1
            # The legacy workspace.open reader is v1-only after a native v2
            # commit. A new session and tests.selected use the mixed-format
            # head path, and the selected report binds the committed root.
            selected_body = encode_record([
                commit[2], encoded_list([bytes.fromhex(candidate["test_entity_id"])]),
                bytes.fromhex(candidate["native_execution_profile_id"]), bytes(range(32, 48)),
            ]).hex()
            selected, next_id = read_report(process, pending, session, next_id + 1, checked(send(
                process, pending, request("tests.selected", selected_body, next_id, session, 3)),
                "tests.selected"), bytes.fromhex(candidate["test_entity_id"]),
                bytes.fromhex(state["schema_epoch"]), inspector)
            if selected["proposed_root"] != commit[2].hex():
                raise RuntimeError("explicit-root diagnostic report lost the accepted root")
            rejected_body = encode_record([
                commit[2], encoded_list([bytes([0xFF]) * 32]),
                bytes.fromhex(candidate["native_execution_profile_id"]), bytes(range(48, 64)),
            ]).hex()
            rejected = failure_symbol(send(process, pending, request(
                "tests.selected", rejected_body, next_id, session, 3)))
            next_id += 1
            if rejected != "NATIVE_TEST_SELECTION_INVALID":
                raise RuntimeError(f"invalid selection refused for an unexpected reason: {rejected}")
            checked(send(process, pending, request("session.close", "", next_id, session, 3)),
                    "session.close")
            assert process.stdin is not None
            process.stdin.close()
            if process.wait(timeout=30):
                raise RuntimeError(f"configured receiver exited unsuccessfully: {process.stderr.read()[-500:]!r}")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        if state_path.read_bytes() != state_bytes:
            raise RuntimeError("host probe changed the source fixture")
    return {"contract": "sley-tools.f0-authorized-native-test-probe.v0", "result": "PASS",
            "core_sha256": digest(core), "stage_manifest_sha256": digest(stage_path),
            "candidate_sha256": digest(candidate_path),
            "receiver_config_sha256": digest(receiver_config), "authority_plan_sha256": digest(plan_path),
            "workspace_id": state["workspace_id"], "candidate_id": candidate["candidate_id"],
            "test_entity_id": candidate["test_entity_id"], "original_root": state["state_root"],
            "committed_root": commit[2].hex(), "commit_receipt_id": commit[1].hex(),
            "affected_report": affected, "selected_report": selected,
            "invalid_selection_symbol": rejected, "source_fixture_unchanged": True,
            "native_test_admission": "PASS_CONFIGURED_ROUTE_PENDING_HOST_QUALIFICATION",
            "host_enforcement_qualification": "NOT_RUN_BY_THIS_PROBE",
            "core_report_parser_sha256": digest(inspector) if inspector else None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("core", "project", "candidate", "receiver-config", "authority-plan",
                 "stage-manifest", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--report-inspector", type=Path,
                        help="native builder whose core parser validates report digests and shape")
    args = parser.parse_args()
    report = probe(args.core.resolve(), args.project.resolve(), args.candidate.resolve(),
                   args.receiver_config.resolve(), args.authority_plan.resolve(),
                   args.stage_manifest.resolve(),
                   args.report_inspector.resolve() if args.report_inspector else None)
    if args.output.exists():
        raise ValueError("evidence output already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(f"wrote {args.output}: one native TestCase committed and selected again")


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"authorized native TestCase probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
