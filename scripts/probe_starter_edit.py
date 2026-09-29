#!/usr/bin/env python3
"""Verify public threshold editing and a rejected stale core precondition."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from probe_genesis import checked, decode_bytes, record_fields, request, run, send


def invoke(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)


def native_rejected_stale_edit(core: Path, builder: Path, project: Path, old_object: str, current: dict) -> dict:
    hello = json.loads(run([str(core), "frame", "decode"], run([str(core), "hello"])))
    handshake_dir = project.parent / "stale-handshake"
    report_file = project.parent / "stale-handshake-report.json"
    run(
        [str(core), "serve", "--repository", str(handshake_dir), "--json", "--report", str(report_file)],
        json.dumps(hello).encode() + b"\n",
    )
    handshake = json.loads(report_file.read_text(encoding="utf-8"))["handshake_id"]
    process = subprocess.Popen(
        [str(core), "serve", "--repository", str(project / "workspace"), "--json"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    pending = bytearray()
    try:
        if send(process, pending, hello)["kind"] != "hello":
            raise RuntimeError("core refused stale-candidate test hello")
        session = checked(send(process, pending, request("session.open", handshake, 0, None)), "session.open")
        opened = checked(send(process, pending, request("workspace.open", "", 1, session)), "workspace.open")
        fields = record_fields(bytes.fromhex(opened), 8)
        if fields[0].hex() != current["transaction_id"] or fields[1].hex() != current["state_root"]:
            raise RuntimeError("stale-candidate test did not start at the edited accepted head")
        built = json.loads(run([
            str(builder), "edit", current["workspace_id"], current["principal_id"],
            current["state_root"], current["policy_root"], current["transaction_id"],
            current["schema_epoch"], current["threshold_id"], old_object, "3",
        ]))
        stored = checked(send(process, pending, request("candidate.create", built["candidate_record_hex"], 2, session)), "candidate.create")
        if stored != built["candidate_stored_hex"]:
            raise RuntimeError("stale candidate differs from public core builder")
        from probe_genesis import encode_record, encode_uvar
        body = encode_record([
            bytes.fromhex(current["transaction_id"]), bytes.fromhex(current["principal_id"]),
            encode_uvar(built["now_unix_millis"]), bytes.fromhex(stored),
        ]).hex()
        validation = send(process, pending, request("candidate.validate", body, 3, session))
        if validation["kind"] != "response":
            raise RuntimeError("stale candidate validation had no response")
        refusal = send(process, pending, request("commit", body, 4, session))
        if refusal["kind"] != "response" or not refusal["flags"]["failed"]:
            raise RuntimeError("stale exact-object precondition unexpectedly committed")
        symbol = decode_bytes(record_fields(bytes.fromhex(refusal["body"]), 6)[1]).decode("ascii")
        after = checked(send(process, pending, request("workspace.open", "", 5, session)), "workspace.open after refusal")
        after_fields = record_fields(bytes.fromhex(after), 8)
        if after_fields[0] != fields[0] or after_fields[1] != fields[1]:
            raise RuntimeError("rejected stale candidate advanced accepted head")
        checked(send(process, pending, request("session.close", "", 6, session)), "session.close")
        assert process.stdin is not None
        process.stdin.close()
        if process.wait(timeout=30):
            raise RuntimeError("core endpoint exited unsuccessfully after stale rejection")
        return {"candidate_id": built["candidate_id"], "core_symbol": symbol, "root_unchanged": True}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def probe(tools: Path, builder: Path, core: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="sley-tools-edit-") as temporary:
        project = Path(temporary) / "project"
        created = invoke([str(tools), "init", str(project), "--template", "release-readiness", "--core", str(core)])
        if created.returncode != 0:
            raise RuntimeError(f"init failed: {created.stderr[:300]}")
        initial = json.loads((project / ".sley-tools/state.json").read_text(encoding="utf-8"))
        one = project / "inputs/one-blocking.json"
        one.write_text(json.dumps({
            "test_run_completed": True, "tests_failed": 0,
            "artifact_verified": True, "blocking_findings": 1,
        }), encoding="utf-8")
        before = invoke([str(tools), "run", "--project", str(project), "--input", str(one)])
        if before.returncode != 0 or json.loads(before.stdout)["result"]["allow"] is not False:
            raise RuntimeError("initial native threshold did not reject one blocking finding")
        edit = invoke([str(tools), "edit", "--project", str(project), "--max-blocking-findings", "2"])
        if edit.returncode != 0:
            raise RuntimeError(f"public worked edit failed: {edit.stderr[:300]}")
        change = json.loads(edit.stdout)
        current = json.loads((project / ".sley-tools/state.json").read_text(encoding="utf-8"))
        status = json.loads((project / ".sley-tools/status.json").read_text(encoding="utf-8"))
        if change["old_root"] != initial["state_root"] or change["new_root"] != current["state_root"]:
            raise RuntimeError("worked edit did not report exact committed roots")
        if change["before"]["allow"] is not False or change["after"]["allow"] is not True:
            raise RuntimeError("worked edit did not change the intended boundary")
        if current["threshold"] != 2 or len(current["commit_history"]) != 2 or status["native_commit_count"] != 2:
            raise RuntimeError("worked edit did not persist both native commits")
        if len(current["commit_receipt_id"]) != 64 or (project / ".sley-tools/pending-edit.json").exists():
            raise RuntimeError("worked edit left invalid receipt or pending marker")
        tests = invoke([str(tools), "test", "--project", str(project)])
        if tests.returncode != 0 or json.loads(tests.stdout)["external_assertions_against_native_execution"] != 60:
            raise RuntimeError("edited native program failed boundary and truth-table assertions")
        for blocking, allowed in [(0, True), (1, True), (2, True), (3, False)]:
            case = project / f"inputs/blocking-{blocking}.json"
            case.write_text(json.dumps({
                "test_run_completed": True, "tests_failed": 0,
                "artifact_verified": True, "blocking_findings": blocking,
            }), encoding="utf-8")
            output = invoke([str(tools), "run", "--project", str(project), "--input", str(case)])
            if output.returncode != 0 or json.loads(output.stdout)["result"]["allow"] is not allowed:
                raise RuntimeError(f"edited native threshold returned wrong result at {blocking}")
        stale = native_rejected_stale_edit(core, builder, project, initial["threshold_object_id"], current)
        still = invoke([str(tools), "run", "--project", str(project), "--input", str(one)])
        if still.returncode != 0 or json.loads(still.stdout)["result"]["allow"] is not True:
            raise RuntimeError("accepted edited program stopped working after stale rejection")
        if json.loads((project / ".sley-tools/state.json").read_text(encoding="utf-8")) != current:
            raise RuntimeError("stale rejected proposal changed local project state")
        return {
            "contract": "sley-tools.starter-edit-probe.v1",
            "result": "PASS",
            "core_binary_sha256": hashlib.sha256(core.read_bytes()).hexdigest(),
            "initial_root": initial["state_root"],
            "edited_root": current["state_root"],
            "edit_candidate_id": change["candidate_id"],
            "edit_receipt_id": change["receipt_id"],
            "threshold_before": 0,
            "threshold_after": 2,
            "before_allow_at_one": False,
            "after_allow_at_one": True,
            "edited_external_assertions": 60,
            "stale_candidate_id": stale["candidate_id"],
            "stale_rejection_symbol": stale["core_symbol"],
            "root_unchanged_after_stale_rejection": stale["root_unchanged"],
            "accepted_copy_executable_after_rejection": True,
            "native_test_admission": "NOT_RUN: production native executor unavailable",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--builder", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = probe(args.tools.resolve(), args.builder.resolve(), args.core.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: native threshold edit, boundaries, stale rejection, and accepted-root replay passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, KeyError, ValueError, RuntimeError, TimeoutError, subprocess.TimeoutExpired) as error:
        print(f"Starter edit probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
