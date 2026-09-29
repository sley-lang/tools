#!/usr/bin/env python3
"""Exercise separate native propose, validate, commit, rejection, and discard commands."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def call(tools: Path, *args: str, expect: str | None = None) -> dict:
    result = subprocess.run([str(tools), *args], capture_output=True, text=True, timeout=90, check=False)
    if expect is None:
        if result.returncode:
            raise RuntimeError(f"{' '.join(args[:2])} exited {result.returncode}: {result.stderr[:400]}")
        return json.loads(result.stdout)
    if result.returncode == 0:
        raise RuntimeError(f"{' '.join(args[:2])} unexpectedly succeeded")
    error = json.loads(result.stderr)
    if error["code"] != expect:
        raise RuntimeError(f"{' '.join(args[:2])} returned {error['code']} instead of {expect}")
    return error


def state(project: Path) -> dict:
    return json.loads((project / ".sley-tools/state.json").read_text(encoding="utf-8"))


def one(tools: Path, core: Path, root: Path, program: str) -> dict:
    project = root / program
    call(tools, "init", str(project), "--template", program, "--core", str(core))
    initial = state(project)
    if program == "release-readiness":
        change = ("--max-blocking-findings", "2")
        facts = {"test_run_completed": True, "tests_failed": 0,
                 "artifact_verified": True, "blocking_findings": 1}
        assertions = 60
    else:
        change = ("--require-memory-spare", "true")
        facts = {"requested_workers": 1, "available_workers": 1,
                 "requested_memory_mib": 4096, "available_memory_mib": 4096}
        assertions = 12
    sample = project / "inputs/lifecycle-boundary.json"
    sample.write_text(json.dumps(facts) + "\n", encoding="utf-8")
    before = call(tools, "run", "--project", str(project), "--input", str(sample))
    before_artifact = root / f"{program}-before.json"
    call(tools, "artifact", "export", "--project", str(project), "--output", str(before_artifact))
    if program == "release-readiness":
        with (project / ".sley-tools/write.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            call(tools, "propose", "--project", str(project), *change, expect="ST_RESOURCE_LIMIT")
            fcntl.flock(lock, fcntl.LOCK_UN)
        if state(project) != initial:
            raise RuntimeError("busy project write changed accepted state")
    proposed = call(tools, "propose", "--project", str(project), *change)
    if proposed["base_root"] != initial["state_root"] or state(project) != initial:
        raise RuntimeError(f"{program} proposal changed accepted state")
    call(tools, "commit", "--project", str(project), expect="ST_INPUT_INVALID")
    call(tools, "propose", "--project", str(project), *change, expect="ST_INPUT_INVALID")
    validated = call(tools, "validate", "--project", str(project))
    if validated["candidate_id"] != proposed["candidate_id"] or state(project) != initial:
        raise RuntimeError(f"{program} validation changed candidate or accepted state")
    committed = call(tools, "commit", "--project", str(project))
    current = state(project)
    if (committed["candidate_id"] != proposed["candidate_id"]
            or committed["old_root"] != initial["state_root"]
            or committed["new_root"] != current["state_root"]
            or committed["new_root"] == initial["state_root"]
            or len(current["commit_history"]) != 2
            or not committed["proposal_validated_separately"]
            or not committed["validation_rechecked_on_commit"]):
        raise RuntimeError(f"{program} did not commit its exact validated candidate")
    if any((project / ".sley-tools" / name).exists()
           for name in ("proposal.json", "validated-proposal.json", "pending-edit.json")):
        raise RuntimeError(f"{program} left a completed authoring marker")
    after = call(tools, "run", "--project", str(project), "--input", str(sample))
    if before["result"]["allow"] == after["result"]["allow"]:
        raise RuntimeError(f"{program} native boundary did not change")
    tested = call(tools, "test", "--project", str(project))
    if tested["result"] != "PASS" or tested["external_assertions_against_native_execution"] != assertions:
        raise RuntimeError(f"{program} accepted candidate failed external assertions")
    after_artifact = root / f"{program}-after.json"
    call(tools, "artifact", "export", "--project", str(project), "--output", str(after_artifact))
    old_objects = {item["entity_id"]: item["object_id"] for item in json.loads(before_artifact.read_text(encoding="utf-8"))["objects"]}
    new_objects = {item["entity_id"]: item["object_id"] for item in json.loads(after_artifact.read_text(encoding="utf-8"))["objects"]}
    if old_objects.keys() != new_objects.keys() or sum(old_objects[entity] != new_objects[entity] for entity in old_objects) != 1:
        raise RuntimeError(f"{program} committed more than one native entity version")
    return {"program_id": program, "candidate_id": proposed["candidate_id"],
            "initial_root": initial["state_root"], "committed_root": current["state_root"],
            "before_allow": before["result"]["allow"], "after_allow": after["result"]["allow"],
            "external_assertions": assertions, "commit_before_validation_rejected": True,
            "duplicate_proposal_rejected": True, "exact_candidate_committed": True,
            "concurrent_project_write_rejected": program == "release-readiness",
            "changed_entity_versions": 1,
            "before_artifact_sha256": hashlib.sha256(before_artifact.read_bytes()).hexdigest(),
            "after_artifact_sha256": hashlib.sha256(after_artifact.read_bytes()).hexdigest()}


def rejection(tools: Path, core: Path, root: Path) -> dict:
    project = root / "rejection"
    call(tools, "init", str(project), "--template", "release-readiness", "--core", str(core))
    initial = state(project)
    proposal = call(tools, "propose", "--project", str(project), "--max-blocking-findings", "2")
    record_path = project / ".sley-tools/proposal.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["change"]["value"] = 3
    record_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    call(tools, "validate", "--project", str(project), expect="ST_INPUT_INVALID")
    if state(project) != initial:
        raise RuntimeError("tampered proposal changed accepted state")
    discarded = call(tools, "propose", "discard", "--project", str(project))
    if discarded["candidate_id"] != proposal["candidate_id"] or record_path.exists():
        raise RuntimeError("tampered proposal was not discarded")
    proposed = call(tools, "propose", "--project", str(project), "--max-blocking-findings", "2")
    call(tools, "validate", "--project", str(project))
    direct = call(tools, "edit", "--project", str(project), "--max-blocking-findings", "1")
    call(tools, "commit", "--project", str(project), expect="ST_STALE_BASE")
    if state(project)["state_root"] != direct["new_root"]:
        raise RuntimeError("stale proposal affected the newer accepted root")
    call(tools, "propose", "discard", "--project", str(project))
    return {"tampered_candidate_id": proposal["candidate_id"],
            "stale_candidate_id": proposed["candidate_id"],
            "tamper_rejected": True, "stale_base_rejected": True,
            "accepted_root_preserved_after_rejections": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    tools, core = args.tools.resolve(), args.core.resolve()
    with tempfile.TemporaryDirectory(prefix="sley-tools-authoring-") as temporary:
        root = Path(temporary)
        programs = [one(tools, core, root, "release-readiness"),
                    one(tools, core, root, "resource-admission")]
        negative = rejection(tools, core, root)
    report = {"contract": "sley-tools.authoring-lifecycle-probe.v1", "result": "PASS",
              "tools_binary_sha256": hashlib.sha256(tools.read_bytes()).hexdigest(),
              "core_binary_sha256": hashlib.sha256(core.read_bytes()).hexdigest(),
              "programs": programs, "negative_paths": negative,
              "native_core_test_admission": "NOT_RUN: production native executor unavailable"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: both exact candidate lifecycles and rejection paths passed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"authoring lifecycle probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
