#!/usr/bin/env python3
"""Build a fresh, noncommitting native TestCase candidate for an existing graph project."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


STATE_FIELDS = ("workspace_id", "principal_id", "state_root", "policy_root",
                "transaction_id", "schema_epoch")


def refresh(project: Path, builder: Path, case_id: str, output: Path) -> dict:
    if output.exists():
        raise ValueError("candidate output already exists")
    state_path = project / ".sley-tools/state.json"
    state_bytes = state_path.read_bytes()
    state = json.loads(state_bytes)
    fixture = json.loads((project / "tests/cases.json").read_text(encoding="utf-8"))
    if fixture.get("contract") != "sley-tools.graph-cases.v0":
        raise ValueError("project does not have bounded graph cases")
    cases = [row for row in fixture["cases"] if row.get("id") == case_id]
    if len(cases) != 1:
        raise ValueError("native test case ID must occur exactly once")
    command = [str(builder), "construct-graph-native-test", str(project / "workspace"),
               str(project / "build/graph.json"),
               json.dumps(cases[0], sort_keys=True, separators=(",", ":")),
               *(state[name] for name in STATE_FIELDS), state["function_id"]]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError(f"native TestCase builder refused: {result.stderr[-500:]}")
    candidate = json.loads(result.stdout)
    if (candidate.get("contract") != "sley-tools.graph-native-test-construction-probe.v0"
            or candidate.get("case_id") != case_id
            or candidate.get("target_function_id") != state["function_id"]
            or candidate.get("native_plan_selected_test_count") != 1
            or len(candidate.get("candidate_id", "")) != 64
            or len(candidate.get("candidate_stored_hex", "")) < 64):
        raise RuntimeError("builder returned an incomplete native test candidate")
    now = int(time.time() * 1000)
    if abs(now - candidate["now_unix_millis"]) > 30_000:
        raise RuntimeError("builder candidate clock differs from the current host clock")
    if state_path.read_bytes() != state_bytes:
        raise RuntimeError("native TestCase construction changed accepted project state")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(candidate, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return candidate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--builder", required=True, type=Path)
    parser.add_argument("--case", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.output.is_absolute():
        parser.error("--output must be an absolute new path")
    candidate = refresh(args.project.resolve(), args.builder.resolve(), args.case,
                        args.output)
    print(f"created fresh native TestCase candidate {candidate['candidate_id']} at {args.output}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"native TestCase refresh failed: {error}", file=sys.stderr)
        raise SystemExit(1)
