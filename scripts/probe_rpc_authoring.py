#!/usr/bin/env python3
"""Verify typed authoring over the project-scoped JSON-RPC service."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from probe_rpc import Client, error_code, invoke, tagged_input


def resource_input() -> dict:
    return {"kind": "record", "fields": {
        name: {"kind": "integer", "type": "i64", "value": value}
        for name, value in (("requested_workers", "1"), ("available_workers", "1"),
                            ("requested_memory_mib", "4096"), ("available_memory_mib", "4096"))
    }}


def execute(client: Client, handle: str, program: str) -> bool:
    facts = tagged_input(True, "0", True, "1") if program == "release-readiness" else resource_input()
    response = client.call("sleytools.program.execute", {
        "handle": handle, "entrypoint": "evaluate", "input": facts,
    })
    if "error" in response:
        raise RuntimeError(f"{program} RPC execution failed: {response['error']}")
    return response["result"]["value"]["fields"]["allow"]["value"]


def one(tools: Path, core: Path, root: Path, program: str) -> dict:
    project = root / program
    invoke(tools, "init", str(project), "--template", program, "--core", str(core))
    client = Client(tools, project)
    try:
        hello = client.call("sleytools.hello", {})["result"]
        required = {"sleytools.program.propose", "sleytools.candidate.validate",
                    "sleytools.candidate.commit", "sleytools.candidate.discard"}
        if not required.issubset(hello["methods"]):
            raise RuntimeError("RPC hello omitted authoring methods")
        opened = client.call("sleytools.workspace.open", {})["result"]
        handle, initial_root = opened["handle"], opened["state_root"]
        before_allow = execute(client, handle, program)
        change = ({"field": "max_blocking_findings",
                   "value": {"kind": "integer", "type": "u32", "value": "2"}}
                  if program == "release-readiness" else
                  {"field": "require_memory_spare", "value": {"kind": "boolean", "value": True}})
        bad_change = dict(change)
        bad_change["value"] = (dict(change["value"], value="02") if program == "release-readiness"
                               else {"kind": "boolean", "value": "true"})
        error_code(client.call("sleytools.program.propose", {"handle": handle, "change": bad_change}), "ST_INPUT_INVALID")
        proposed = client.call("sleytools.program.propose", {"handle": handle, "change": change})["result"]
        candidate = proposed["candidate_id"]
        if proposed["base_root"] != initial_root or len(candidate) != 64:
            raise RuntimeError(f"{program} RPC proposal lost accepted-root binding")
        error_code(client.call("sleytools.candidate.validate", {"handle": handle, "candidate_id": "0" * 64}), "ST_INPUT_INVALID")
        error_code(client.call("sleytools.candidate.commit", {"handle": handle, "candidate_id": candidate}), "ST_INPUT_INVALID")
        validated = client.call("sleytools.candidate.validate", {"handle": handle, "candidate_id": candidate})["result"]
        if validated["candidate_id"] != candidate or not validated["accepted_root_unchanged"]:
            raise RuntimeError(f"{program} RPC validation did not preserve accepted root")
        if execute(client, handle, program) is not before_allow:
            raise RuntimeError(f"{program} RPC validation changed native behavior")
        committed = client.call("sleytools.candidate.commit", {"handle": handle, "candidate_id": candidate})["result"]
        after_root = committed["new_state_root"]
        if (committed["commit"]["candidate_id"] != candidate or not committed["reopen_required"]
                or after_root == initial_root):
            raise RuntimeError(f"{program} RPC commit lost exact candidate or root")
        error_code(client.call("sleytools.program.inspect", {"handle": handle}), "ST_INPUT_INVALID")
        reopened = client.call("sleytools.workspace.open", {})["result"]
        if reopened["handle"] == handle or reopened["state_root"] != after_root:
            raise RuntimeError(f"{program} RPC reopen reused old handle or root")
        handle = reopened["handle"]
        after_allow = execute(client, handle, program)
        if before_allow is after_allow:
            raise RuntimeError(f"{program} RPC native policy boundary did not change")
        tested = client.call("sleytools.tests.run", {"handle": handle})["result"]
        expected_assertions = 60 if program == "release-readiness" else 12
        if tested["result"] != "PASS" or tested["external_assertions_against_native_execution"] != expected_assertions:
            raise RuntimeError(f"{program} RPC accepted root failed external assertions")
        reverse = ({"field": "max_blocking_findings", "value": {"kind": "integer", "type": "u32", "value": "0"}}
                   if program == "release-readiness" else
                   {"field": "require_memory_spare", "value": {"kind": "boolean", "value": False}})
        second = client.call("sleytools.program.propose", {"handle": handle, "change": reverse})["result"]
        discarded = client.call("sleytools.candidate.discard", {"handle": handle, "candidate_id": second["candidate_id"]})["result"]
        if discarded["candidate_id"] != second["candidate_id"] or execute(client, handle, program) is not after_allow:
            raise RuntimeError(f"{program} RPC discard changed accepted behavior")
        return {"program_id": program, "candidate_id": candidate, "initial_root": initial_root,
                "committed_root": after_root, "before_allow": before_allow,
                "after_allow": after_allow, "external_assertions": expected_assertions,
                "typed_rejection": True, "wrong_candidate_rejected": True,
                "commit_before_validation_rejected": True, "old_handle_rejected": True,
                "new_handle_reopened": True, "discard_preserved_root": True}
    finally:
        stderr = client.close()
        if stderr:
            raise RuntimeError(f"{program} RPC wrote stderr during normal authoring: {stderr[:300]}")


def stale(tools: Path, core: Path, root: Path) -> dict:
    project = root / "stale"
    invoke(tools, "init", str(project), "--template", "release-readiness", "--core", str(core))
    client = Client(tools, project)
    try:
        opened = client.call("sleytools.workspace.open", {})["result"]
        handle = opened["handle"]
        proposal = client.call("sleytools.program.propose", {"handle": handle,
            "change": {"field": "max_blocking_findings", "value": {"kind": "integer", "type": "u32", "value": "2"}}})["result"]
        invoke(tools, "edit", "--project", str(project), "--max-blocking-findings", "1")
        error_code(client.call("sleytools.candidate.validate", {"handle": handle,
                     "candidate_id": proposal["candidate_id"]}), "ST_STALE_BASE")
        error_code(client.call("sleytools.workspace.open", {}), "ST_STALE_BASE")
    finally:
        client.close()
    cleanup = Client(tools, project)
    try:
        current = cleanup.call("sleytools.workspace.open", {})["result"]
        # The local record predates the externally committed root. CLI discard is
        # permitted for this recovery path; RPC keeps its handle root-pinned.
        if current["state_root"] == opened["state_root"]:
            raise RuntimeError("independent edit did not advance the accepted root")
        invoke(tools, "propose", "discard", "--project", str(project))
        return {"candidate_id": proposal["candidate_id"], "stale_validation_rejected": True,
                "old_rpc_session_rejected": True, "accepted_root_preserved": True}
    finally:
        cleanup.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    tools, core = args.tools.resolve(), args.core.resolve()
    with tempfile.TemporaryDirectory(prefix="sley-rpc-authoring-") as temporary:
        root = Path(temporary)
        programs = [one(tools, core, root, "release-readiness"),
                    one(tools, core, root, "resource-admission")]
        stale_result = stale(tools, core, root)
    evidence = {"contract": "sley-tools.rpc-authoring-probe.v1", "result": "PASS",
                "tools_binary_sha256": hashlib.sha256(tools.read_bytes()).hexdigest(),
                "core_binary_sha256": hashlib.sha256(core.read_bytes()).hexdigest(),
                "programs": programs, "stale": stale_result,
                "native_core_test_admission": "NOT_RUN: production native executor unavailable"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: both typed RPC candidate lifecycles passed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"RPC authoring probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
