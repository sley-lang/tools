#!/usr/bin/env python3
"""Check the unified CLI Repair Lab route with a real native RL-01 solution."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(command: list[str], expected_exit: int = 0) -> dict:
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode != expected_exit:
        raise RuntimeError(
            f"command exited {result.returncode}, expected {expected_exit}: "
            f"{result.stdout[-400:]} {result.stderr[-400:]}"
        )
    return json.loads(result.stdout if result.stdout else result.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "core", "adapter", "graph", "cases", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    tools, core, adapter, graph, cases = (
        getattr(args, name).resolve() for name in
        ("tools", "core", "adapter", "graph", "cases")
    )
    root = [str(tools), "lab"]
    listed = invoke([*root, "list", "--adapter", str(adapter)])
    if ([entry["id"] for entry in listed["available"]]
            != [f"rl{index:02}" for index in range(1, 9)]
            or listed["pending"]):
        raise RuntimeError("CLI Lab listing differs from the eight public exercises")
    missing = invoke([*root, "list"], expected_exit=4)
    if missing.get("code") != "ST_FEATURE_UNSUPPORTED":
        raise RuntimeError("CLI did not explain the missing optional Lab adapter")

    with tempfile.TemporaryDirectory(prefix="sley-lab-launcher-probe-") as temporary:
        workspace = Path(temporary) / "rl01"
        common = ["--adapter", str(adapter), "--workspace", str(workspace),
                  "--core", str(core)]
        started = invoke([*root, "start", "rl01", *common])
        if started.get("result") != "READY":
            raise RuntimeError("CLI Lab did not start RL-01")
        unfinished = invoke([*root, "check", *common], expected_exit=1)
        if unfinished.get("result") != "FAIL":
            raise RuntimeError("unfinished native exercise passed through CLI")

        project = workspace / "project"
        invoke([str(tools), "init", str(project), "--graph", str(graph),
                "--tests", str(cases), "--core", str(core)])
        invoke([str(tools), "artifact", "export", "--project", str(project),
                "--output", str(project / "artifact/lab-range.json"), "--bind-manifest"])
        passed = invoke([*root, "check", *common])
        if (passed.get("result") != "PASS"
                or passed.get("evidence", {}).get("independent_native_cases") != 16
                or passed["evidence"].get("replayed_native_cases") != 16):
            raise RuntimeError("CLI Lab did not judge the native RL-01 solution")
        note = workspace / "notes.txt"
        note.write_text("keep learner notes\n", encoding="utf-8")
        reset = invoke([*root, "reset", *common])
        if (reset.get("result") != "RESET" or project.exists()
                or note.read_text(encoding="utf-8") != "keep learner notes\n"):
            raise RuntimeError("CLI Lab reset did not preserve learner notes")

    report = {
        "contract": "sley-tools.repair-lab-launcher-probe.v1", "result": "PASS",
        "tools_binary_sha256": digest(tools), "core_binary_sha256": digest(core),
        "adapter_sha256": digest(adapter), "graph_sha256": digest(graph),
        "cases_sha256": digest(cases), "probe_sha256": digest(Path(__file__)),
        "listed_exercises": [f"rl{index:02}" for index in range(1, 9)],
        "missing_adapter_diagnostic": "ST_FEATURE_UNSUPPORTED",
        "unfinished_check_exit": 1, "solved_native_cases": 16,
        "replayed_native_cases": 16, "reset_preserved_notes": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(f"wrote {args.output}: CLI Lab native RL-01 flow passed")


if __name__ == "__main__":
    main()
