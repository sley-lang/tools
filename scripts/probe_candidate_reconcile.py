#!/usr/bin/env python3
"""Exercise verified core receipt reconciliation even with stale project metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(tools: Path, *args: str, expect_success: bool = True) -> dict:
    completed = subprocess.run([str(tools), *args], capture_output=True, text=True,
                               timeout=90, check=False)
    if (completed.returncode == 0) != expect_success:
        raise RuntimeError(f"unexpected result from {' '.join(args)}: {completed.stderr[-600:]}")
    return json.loads(completed.stdout if expect_success else completed.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", type=Path, required=True)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tools, core = args.tools.resolve(), args.core.resolve()
    with tempfile.TemporaryDirectory(prefix="sley-candidate-reconcile-") as temporary:
        project = Path(temporary) / "project"
        invoke(tools, "init", str(project), "--template", "release-readiness", "--core", str(core))
        proposal = invoke(tools, "propose", "--project", str(project), "--max-blocking-findings", "1")
        record = json.loads((project / ".sley-tools/proposal.json").read_text())
        candidate = record["candidate_id"]
        parent = record["expected_parent"]
        if proposal["candidate_id"] != candidate:
            raise RuntimeError("proposal and durable candidate IDs differ")
        invoke(tools, "validate", "--project", str(project))
        base = ["reconcile", "--project", str(project), "--candidate-id", candidate,
                "--expected-parent", parent]
        before = invoke(tools, *base)
        if before["state"] != "outcome_unknown":
            raise RuntimeError("uncommitted candidate was reported complete")
        metadata = {name: (project / name).read_bytes() for name in
                    ("program.json", ".sley-tools/state.json", ".sley-tools/status.json")}
        committed = invoke(tools, "commit", "--project", str(project))
        after = invoke(tools, *base)
        if after["state"] != "completed" or after["committed_root"] != committed["new_root"]:
            raise RuntimeError("committed candidate was not found in verified core ancestry")
        if after["receipt_id"] != committed["receipt_id"]:
            raise RuntimeError("reconciled receipt differs from successful native commit")
        for name, contents in metadata.items():
            (project / name).write_bytes(contents)
        stale = invoke(tools, "inspect", "--project", str(project), expect_success=False)
        if stale["code"] != "ST_STALE_BASE":
            raise RuntimeError("restored local metadata did not reproduce a stale project")
        recovered = invoke(tools, *base)
        if recovered["state"] != "completed" or recovered["receipt_id"] != after["receipt_id"]:
            raise RuntimeError("stale local metadata hid the verified native commit")
        other = invoke(tools, "reconcile", "--project", str(project),
                       "--candidate-id", "0" * 64, "--expected-parent", parent)
        if other["state"] != "outcome_unknown":
            raise RuntimeError("unknown candidate was reported complete")
        bad = invoke(tools, "reconcile", "--project", str(project),
                     "--candidate-id", "not-hex", "--expected-parent", parent,
                     expect_success=False)
        if bad["code"] != "ST_INPUT_INVALID":
            raise RuntimeError("malformed candidate ID was accepted")
        report = {"contract": "sley-tools.candidate-reconcile-probe.v1", "result": "PASS",
                  "tools_binary_sha256": digest(tools), "core_binary_sha256": digest(core),
                  "probe_sha256": digest(Path(__file__)),
                  "candidate_id": candidate, "expected_parent": parent,
                  "transaction_id": after["transaction_id"], "receipt_id": after["receipt_id"],
                  "committed_root": after["committed_root"],
                  "uncommitted_outcome_unknown": True, "verified_committed_receipt": True,
                  "stale_local_state_recovered": True, "unknown_candidate_not_claimed_complete": True,
                  "malformed_id_rejected": True,
                  "scope": "read-only receipt ancestry reconciliation for one ordinary candidate; no durable tools operation ID or cancellation yet"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.output}: verified ordinary commit recovered through stale local state")


if __name__ == "__main__":
    main()
