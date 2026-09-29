#!/usr/bin/env python3
"""Exercise both native authoring profiles through the Python SDK."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages/python"))
from sley_tools import SleyToolsError, open_runtime  # noqa: E402


def expect_error(callback, code: str) -> None:
    try:
        callback()
    except SleyToolsError as error:
        if error.code != code:
            raise RuntimeError(f"expected {code}, got {error.code}") from error
    else:
        raise RuntimeError(f"expected {code}")


def one(tools: Path, core: Path, root: Path, name: str) -> dict:
    project = root / name
    subprocess.run([str(tools), "init", str(project), "--template", name, "--core", str(core)],
                   check=True, capture_output=True, text=True, timeout=30)
    facts = ({"test_run_completed": True, "tests_failed": 0, "artifact_verified": True, "blocking_findings": 1}
             if name == "release-readiness" else
             {"requested_workers": 1, "available_workers": 1,
              "requested_memory_mib": 4096, "available_memory_mib": 4096})
    change = ({"max_blocking_findings": 2} if name == "release-readiness" else
              {"require_memory_spare": True})
    reverse = ({"max_blocking_findings": 0} if name == "release-readiness" else
               {"require_memory_spare": False})
    invalid = ({"max_blocking_findings": True} if name == "release-readiness" else
               {"require_memory_spare": "true"})
    with open_runtime(tools=tools, core=core) as runtime:
        program = runtime.open_project(project)
        initial_root = program.state_root
        before = program.call("evaluate", inputs=facts).value["allow"]
        expect_error(lambda: program.propose(invalid), "ST_INPUT_INVALID")
        proposed = program.propose(change)
        candidate = proposed["candidate_id"]
        if proposed["base_root"] != initial_root or len(candidate) != 64:
            raise RuntimeError("proposal lost exact identity")
        expect_error(lambda: program.validate("0" * 64), "ST_INPUT_INVALID")
        expect_error(lambda: program.commit(candidate), "ST_INPUT_INVALID")
        if program.validate(candidate)["candidate_id"] != candidate:
            raise RuntimeError("validation changed candidate")
        if program.call("evaluate", inputs=facts).value["allow"] is not before:
            raise RuntimeError("validation changed accepted behavior")
        prior_handle = runtime._handle
        committed = program.commit(candidate)
        after_root = program.state_root
        if committed["candidate_id"] != candidate or after_root == initial_root or runtime._handle == prior_handle:
            raise RuntimeError("SDK failed to reopen changed native root")
        after = program.call("evaluate", inputs=facts).value["allow"]
        if before is after or before is not (name == "resource-admission"):
            raise RuntimeError("native decision boundary did not change")
        expected = 60 if name == "release-readiness" else 12
        if program.test()["external_assertions_against_native_execution"] != expected:
            raise RuntimeError("edited native assertions failed")
        second = program.propose(reverse)
        discarded = program.discard(second["candidate_id"])
        if discarded["candidate_id"] != second["candidate_id"] or program.state_root != after_root:
            raise RuntimeError("discard changed accepted root")
        return {"program_id": name, "candidate_id": candidate, "initial_root": initial_root,
                "committed_root": after_root, "before_allow": before, "after_allow": after,
                "external_assertions": expected, "typed_rejection": True,
                "wrong_candidate_rejected": True, "commit_before_validation_rejected": True,
                "handle_rotated": True, "discard_preserved_root": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "core", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    tools, core = args.tools.resolve(), args.core.resolve()
    with tempfile.TemporaryDirectory(prefix="sley-python-authoring-") as folder:
        programs = [one(tools, core, Path(folder), name)
                    for name in ("release-readiness", "resource-admission")]
    report = {"contract": "sley-tools.python-authoring-probe.v1", "result": "PASS",
              "tools_binary_sha256": hashlib.sha256(tools.read_bytes()).hexdigest(),
              "core_binary_sha256": hashlib.sha256(core.read_bytes()).hexdigest(),
              "programs": programs,
              "native_core_test_admission": "NOT_RUN: production native executor unavailable"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: both Python SDK authoring lifecycles passed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Python authoring probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
