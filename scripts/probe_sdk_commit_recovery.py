#!/usr/bin/env python3
"""Verify SDK recovery and durable evidence after a simulated lost commit reply."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/python"))
from sley_tools import SleyToolsError, open_runtime  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def python_scenario(tools: Path, core: Path, artifact: Path, after_commit: bool) -> None:
    runtime = open_runtime(tools=tools, core=core)
    parent = project = None
    operation_id = None
    try:
        program = runtime.load(artifact=artifact)
        project, parent = runtime._project_path, runtime._temporary
        proposal = program.propose({"max_blocking_findings": 1})
        operation_id = program.validate(proposal["candidate_id"])["operation_id"]
        original = runtime._request

        def lost_reply(method, params, *, timeout, cancel_event=None):
            if method != "sleytools.candidate.commit":
                return original(method, params, timeout=timeout, cancel_event=cancel_event)
            if after_commit:
                original(method, params, timeout=timeout, cancel_event=cancel_event)
            raise SleyToolsError("ST_TRANSPORT_FAILED", "transport", "simulated lost commit reply")

        runtime._request = lost_reply
        try:
            program.commit(proposal["candidate_id"])
        except SleyToolsError as error:
            expected = "ST_STALE_BASE" if after_commit else "ST_COMMIT_OUTCOME_UNKNOWN"
            if error.code != expected or operation_id not in str(error):
                raise RuntimeError(f"SDK returned {error.code} without operation identity") from error
        else:
            raise RuntimeError("simulated lost reply returned success")
        status = runtime.operation_status(operation_id)
        if status["state"] != ("completed" if after_commit else "validated"):
            raise RuntimeError("SDK status did not distinguish completed and unattempted commits")
        cancelled = runtime.cancel_operation(operation_id)
        if cancelled["state"] != ("completed" if after_commit else "cancelled_before_commit"):
            raise RuntimeError("SDK cancellation misreported commit outcome")
        if not runtime._preserve_temporary:
            raise RuntimeError("SDK did not preserve uncertain workspace")
    finally:
        runtime.close()
    try:
        assert project is not None and operation_id is not None
        (project / ".sley-tools/operations" / f"{operation_id}.json").read_bytes()
    finally:
        if parent is not None:
            shutil.rmtree(parent)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "core", "artifact", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    tools, core, artifact = (getattr(args, name).resolve() for name in ("tools", "core", "artifact"))
    python_scenario(tools, core, artifact, False)
    python_scenario(tools, core, artifact, True)
    node = subprocess.run(["node", str(Path(__file__).with_suffix(".mjs")),
                           f"tools={tools}", f"core={core}", f"artifact={artifact}"],
                          capture_output=True, text=True, timeout=120, check=False)
    if node.returncode:
        raise RuntimeError(f"TypeScript recovery probe failed: {node.stderr[-900:]}")
    typescript = json.loads(node.stdout)
    if not all(typescript.values()):
        raise RuntimeError("TypeScript recovery probe omitted a scenario")
    report = {"contract": "sley-tools.sdk-commit-recovery-probe.v1", "result": "PASS",
              "tools_binary_sha256": digest(tools), "core_binary_sha256": digest(core),
              "artifact_sha256": digest(artifact),
              "python_sdk_sha256": digest(Path(__file__).resolve().parents[1] / "packages/python/sley_tools/runtime.py"),
              "typescript_sdk_sha256": digest(Path(__file__).resolve().parents[1] / "packages/typescript/src/index.js"),
              "python_probe_sha256": digest(Path(__file__)),
              "typescript_probe_sha256": digest(Path(__file__).with_suffix(".mjs")),
              "python_predispatch_unknown_preserved": True,
              "python_completed_commit_identified_and_preserved": True,
              "python_cancel_respected_commit_boundary": True,
              "typescript": typescript,
              "scope": "simulated SDK reply loss before dispatch and after actual native commit; operation status checks verified core history and SDK close preserves evidence"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.output}: both SDKs retained operation evidence after lost replies")


if __name__ == "__main__":
    main()
