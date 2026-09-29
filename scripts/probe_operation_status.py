#!/usr/bin/env python3
"""Verify a durable tools operation ID against a native ordinary commit."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from probe_rpc import Client, error_code


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(tools: Path, *args: str, success: bool = True) -> dict:
    completed = subprocess.run([str(tools), *args], capture_output=True, text=True,
                               timeout=90, check=False)
    if (completed.returncode == 0) != success:
        raise RuntimeError(f"unexpected result from {' '.join(args)}: {completed.stderr[-700:]}")
    return json.loads(completed.stdout if success else completed.stderr)


def result(response: dict) -> dict:
    if "error" in response:
        raise RuntimeError(f"RPC call failed: {response['error']}")
    return response["result"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", type=Path, required=True)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tools, core = args.tools.resolve(), args.core.resolve()
    with tempfile.TemporaryDirectory(prefix="sley-operation-status-") as temporary:
        project = Path(temporary) / "project"
        invoke(tools, "init", str(project), "--template", "release-readiness", "--core", str(core))
        client = Client(tools, project)
        try:
            result(client.call("sleytools.hello", {}))
            handle = result(client.call("sleytools.workspace.open", {}))["handle"]
            proposal = result(client.call("sleytools.program.propose", {"handle": handle,
                "change": {"field": "max_blocking_findings",
                           "value": {"kind": "integer", "type": "u32", "value": "1"}}}))
            candidate = proposal["candidate_id"]
            validation = result(client.call("sleytools.candidate.validate", {
                "handle": handle, "candidate_id": candidate}))
            operation_id = validation["operation_id"]
            if len(operation_id) != 64:
                raise RuntimeError("validation omitted durable tools operation identity")
            params = {"operation_id": operation_id}
            before = result(client.call("sleytools.operation.status", params))
            if before["state"] != "validated":
                raise RuntimeError("pre-commit operation was not validated")
            metadata = {name: (project / name).read_bytes() for name in
                        ("program.json", ".sley-tools/state.json", ".sley-tools/status.json")}
            journal_path = project / ".sley-tools/operations" / f"{operation_id}.json"
            committed = result(client.call("sleytools.candidate.commit", {
                "handle": handle, "candidate_id": candidate}))
            if committed["commit"]["operation_id"] != operation_id:
                raise RuntimeError("commit returned a different operation ID")
            after = result(client.call("sleytools.operation.status", params))
            if after["state"] != "completed" or after["core_reconciliation"]["receipt_id"] != committed["commit"]["receipt_id"]:
                raise RuntimeError("operation status did not verify the native receipt")
            if result(client.call("sleytools.operation.cancel", params))["state"] != "completed":
                raise RuntimeError("cancelling a completed operation misreported rollback")
        finally:
            client.close()
        journal = json.loads(journal_path.read_text())
        journal["phase"] = "commit_requested"
        for name in ("transaction_id", "receipt_id", "committed_root"):
            journal.pop(name, None)
        journal_path.write_text(json.dumps(journal) + "\n")
        for name, contents in metadata.items():
            (project / name).write_bytes(contents)
        if invoke(tools, "inspect", "--project", str(project), success=False)["code"] != "ST_STALE_BASE":
            raise RuntimeError("stale local state was not reproduced")
        recovered = invoke(tools, "operation", "status", "--project", str(project), "--id", operation_id)
        if recovered["state"] != "completed" or recovered["core_reconciliation"]["receipt_id"] != after["core_reconciliation"]["receipt_id"]:
            raise RuntimeError("operation status lost a verified commit after local metadata rollback")
        stale_client = Client(tools, project)
        try:
            rpc_recovered = result(stale_client.call("sleytools.operation.status", params))
            if rpc_recovered["state"] != "completed":
                raise RuntimeError("stale-project RPC status did not recover the native commit")
        finally:
            stale_client.close()
        (project / ".sley-tools/state.json").write_text("{truncated\n")
        corrupt_client = Client(tools, project)
        try:
            if result(corrupt_client.call("sleytools.operation.status", params))["state"] != "completed":
                raise RuntimeError("partial project metadata hid the verified native commit")
        finally:
            corrupt_client.close()
        cancelled_project = Path(temporary) / "cancelled-project"
        invoke(tools, "init", str(cancelled_project), "--template", "release-readiness", "--core", str(core))
        cancel_client = Client(tools, cancelled_project)
        try:
            cancel_handle = result(cancel_client.call("sleytools.workspace.open", {}))["handle"]
            starting_root = result(cancel_client.call("sleytools.program.inspect", {"handle": cancel_handle}))["accepted_state_root"]
            pending = result(cancel_client.call("sleytools.program.propose", {"handle": cancel_handle,
                "change": {"field": "max_blocking_findings",
                           "value": {"kind": "integer", "type": "u32", "value": "1"}}}))
            cancel_candidate = pending["candidate_id"]
            cancel_id = result(cancel_client.call("sleytools.candidate.validate", {
                "handle": cancel_handle, "candidate_id": cancel_candidate}))["operation_id"]
            cancelled = result(cancel_client.call("sleytools.operation.cancel", {"operation_id": cancel_id}))
            if cancelled["state"] != "cancelled_before_commit":
                raise RuntimeError("precommit cancellation did not record its boundary")
            error_code(cancel_client.call("sleytools.candidate.commit", {
                "handle": cancel_handle, "candidate_id": cancel_candidate}), "ST_COMMIT_OUTCOME_UNKNOWN")
            if result(cancel_client.call("sleytools.operation.status", {"operation_id": cancel_id}))["state"] != "cancelled_before_commit":
                raise RuntimeError("cancelled operation changed state after refused RPC commit")
        finally:
            cancel_client.close()
        if invoke(tools, "commit", "--project", str(cancelled_project), success=False)["code"] != "ST_COMMIT_OUTCOME_UNKNOWN":
            raise RuntimeError("CLI commit bypassed cancelled operation")
        if invoke(tools, "inspect", "--project", str(cancelled_project))["accepted_state_root"] != starting_root:
            raise RuntimeError("precommit cancellation advanced the accepted root")
        report = {"contract": "sley-tools.operation-status-probe.v1", "result": "PASS",
                  "tools_binary_sha256": digest(tools), "core_binary_sha256": digest(core),
                  "probe_sha256": digest(Path(__file__)),
                  "operation_id": operation_id, "candidate_id": candidate,
                  "verified_receipt_id": recovered["core_reconciliation"]["receipt_id"],
                  "precommit_validated": True, "commit_bound_to_operation": True,
                  "completed_verified_by_core": True, "stale_project_cli_recovered": True,
                  "stale_project_rpc_recovered": True,
                  "partial_project_file_rpc_recovered": True,
                  "cancelled_before_commit_blocked_rpc_and_cli": True,
                  "completed_cancel_did_not_claim_rollback": True,
                  "scope": "ordinary Release Readiness commit with durable status, core receipt recovery, and safe precommit cancellation; concurrent in-flight cancellation remains unverified"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.output}: durable operation status survived stale local project metadata")


if __name__ == "__main__":
    main()
