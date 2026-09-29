#!/usr/bin/env python3
"""Verify a native Resource Admission policy edit, stale rejection, and replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from probe_genesis import checked, decode_bytes, encode_record, encode_uvar, record_fields, request, run, send


def invoke(tools: Path, *args: str, expected_exit: int = 0) -> dict:
    result = subprocess.run([str(tools), *args], capture_output=True, text=True, timeout=60, check=False)
    if result.returncode != expected_exit:
        raise RuntimeError(f"{' '.join(args[:2])} exited {result.returncode}, expected {expected_exit}: {result.stderr[:350]}")
    return json.loads(result.stdout if expected_exit == 0 else result.stderr)


def state(project: Path) -> dict:
    return json.loads((project / ".sley-tools/state.json").read_text(encoding="utf-8"))


def reason(response: dict) -> str:
    return decode_bytes(record_fields(bytes.fromhex(response["body"]), 6)[1]).decode("ascii")


def stale_proposal(core: Path, project: Path, initial: dict, current: dict, built: dict) -> str:
    hello = json.loads(run([str(core), "frame", "decode"], run([str(core), "hello"])))
    handshake_dir = project.parent / "stale-handshake"
    report_file = project.parent / "stale-handshake-report.json"
    run([str(core), "serve", "--repository", str(handshake_dir), "--json", "--report", str(report_file)], json.dumps(hello).encode() + b"\n")
    handshake = json.loads(report_file.read_text(encoding="utf-8"))["handshake_id"]
    process = subprocess.Popen([str(core), "serve", "--repository", str(project / "workspace"), "--json"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pending = bytearray()
    try:
        if send(process, pending, hello)["kind"] != "hello":
            raise RuntimeError("core refused stale Resource Admission hello")
        session = checked(send(process, pending, request("session.open", handshake, 0, None)), "session.open")
        opened = checked(send(process, pending, request("workspace.open", "", 1, session)), "workspace.open")
        fields = record_fields(bytes.fromhex(opened), 8)
        if fields[0].hex() != current["transaction_id"] or fields[1].hex() != current["state_root"]:
            raise RuntimeError("stale test did not start at current accepted head")
        created = send(process, pending, request("candidate.create", built["candidate_record_hex"], 2, session))
        if created["flags"]["failed"]:
            symbol = reason(created)
            next_id = 3
        else:
            stored = created["body"]
            if stored != built["candidate_stored_hex"]:
                raise RuntimeError("stale candidate differs from builder bytes")
            body = encode_record([
                bytes.fromhex(initial["transaction_id"]), bytes.fromhex(initial["principal_id"]),
                encode_uvar(built["now_unix_millis"]), bytes.fromhex(stored),
            ]).hex()
            validation = send(process, pending, request("candidate.validate", body, 3, session))
            if validation["flags"]["failed"]:
                symbol = reason(validation)
                next_id = 4
            else:
                committed = send(process, pending, request("commit", body, 4, session))
                if not committed["flags"]["failed"]:
                    raise RuntimeError("stale Resource Admission candidate unexpectedly committed")
                symbol = reason(committed)
                next_id = 5
        after = checked(send(process, pending, request("workspace.open", "", next_id, session)), "workspace.open after refusal")
        after_fields = record_fields(bytes.fromhex(after), 8)
        if after_fields[0] != fields[0] or after_fields[1] != fields[1]:
            raise RuntimeError("stale rejection advanced accepted head")
        checked(send(process, pending, request("session.close", "", next_id + 1, session)), "session.close")
        assert process.stdin is not None
        process.stdin.close()
        if process.wait(timeout=30):
            raise RuntimeError("core endpoint exited unsuccessfully after stale proposal")
        return symbol
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--builder", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    tools, builder, core, artifact = (item.resolve() for item in (args.tools, args.builder, args.core, args.artifact))
    with tempfile.TemporaryDirectory(prefix="sley-resource-edit-probe-") as work:
        base = Path(work)
        project, replay = base / "project", base / "replay"
        invoke(tools, "artifact", "import", str(artifact), str(project), "--core", str(core))
        initial = state(project)
        built = json.loads(run([
            str(builder), "edit-resource",
            initial["workspace_id"], initial["principal_id"], initial["state_root"],
            initial["policy_root"], initial["transaction_id"], initial["schema_epoch"],
            str(project / "workspace"), initial["memory_comparison_id"],
            initial["memory_comparison_object_id"], "true",
        ]))
        changed = invoke(tools, "edit", "--project", str(project), "--require-memory-spare", "true")
        current = state(project)
        if changed["old_root"] != initial["state_root"] or changed["new_root"] != current["state_root"] or current["require_memory_spare"] is not True:
            raise RuntimeError("public edit lost old/new accepted native identities")
        if changed["before"]["allow"] is not True or changed["after"]["allow"] is not False or changed["after"]["insufficient_memory"] is not True:
            raise RuntimeError("native exact-fit boundary did not change as intended")
        if current["memory_comparison_object_id"] == initial["memory_comparison_object_id"] or len(current["commit_history"]) != 2:
            raise RuntimeError("exact operation edit did not persist one new native version")
        if len(changed["receipt_id"]) != 64 or (project / ".sley-tools/pending-edit.json").exists():
            raise RuntimeError("public edit left invalid receipt or pending marker")
        if changed["verification"]["external_assertions_against_native_execution"] != 12:
            raise RuntimeError("edited native graph failed independent assertions")
        for requested, available, allowed in ((4095, 4096, True), (4096, 4096, False), (4097, 4096, False), (2**53 + 1, 2**53 + 2, True)):
            source = project / "inputs/probe.json"
            source.write_text(json.dumps({
                "requested_workers": 1, "available_workers": 1,
                "requested_memory_mib": requested, "available_memory_mib": available,
            }), encoding="utf-8")
            outcome = invoke(tools, "run", "--project", str(project), "--input", str(source))
            if outcome["result"]["allow"] is not allowed or outcome["result"]["requested_memory_mib"] != str(requested):
                raise RuntimeError(f"edited native policy differs at memory boundary {requested}/{available}")
        noop = invoke(tools, "edit", "--project", str(project), "--require-memory-spare", "true", expected_exit=2)
        if noop["code"] != "ST_INPUT_INVALID":
            raise RuntimeError("no-op Resource Admission edit was accepted")
        stale_symbol = stale_proposal(core, project, initial, current, built)
        if state(project) != current:
            raise RuntimeError("rejected old-base proposal changed local state")
        edited_artifact = base / "edited-artifact.json"
        exported = invoke(tools, "artifact", "export", "--project", str(project), "--output", str(edited_artifact))
        imported = invoke(tools, "artifact", "import", str(edited_artifact), str(replay), "--core", str(core))
        if state(replay)["require_memory_spare"] is not True or imported["verification"]["external_assertions_against_native_execution"] != 12:
            raise RuntimeError("edited native policy did not survive artifact transfer")
        if state(replay)["state_root"] == current["state_root"] or exported["sha256"] != hashlib.sha256(edited_artifact.read_bytes()).hexdigest():
            raise RuntimeError("edited transfer did not rebase root or preserve artifact digest")
        reversed_edit = invoke(tools, "edit", "--project", str(replay), "--require-memory-spare", "false")
        if reversed_edit["before"]["allow"] is not False or reversed_edit["after"]["allow"] is not True or state(replay)["require_memory_spare"] is not False:
            raise RuntimeError("native Resource Admission policy did not reverse on imported graph")
        report = {
            "contract": "sley-tools.resource-edit-probe.v1", "result": "PASS",
            "core_binary_sha256": hashlib.sha256(core.read_bytes()).hexdigest(),
            "source_artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "initial_root": initial["state_root"], "edited_root": current["state_root"],
            "candidate_id": changed["candidate_id"], "receipt_id": changed["receipt_id"],
            "old_memory_object_id": initial["memory_comparison_object_id"],
            "new_memory_object_id": current["memory_comparison_object_id"],
            "exact_fit_before": True, "exact_fit_after": False,
            "edited_external_assertions": 12, "boundary_cases": 4,
            "stale_candidate_id": built["candidate_id"], "stale_rejection_symbol": stale_symbol,
            "root_unchanged_after_stale_rejection": True,
            "edited_artifact_sha256": exported["sha256"], "edited_artifact_reimport_passed": True,
            "reverse_edit_passed": True,
            "native_test_admission": "NOT_RUN: production native executor unavailable",
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: exact native memory policy edit and stale rejection passed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, TimeoutError, subprocess.TimeoutExpired) as error:
        print(f"Resource Admission edit probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
