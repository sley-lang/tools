#!/usr/bin/env python3
"""Prove fresh native Resource Admission authoring and exact signed SDK calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/python"))
from sley_tools import SleyToolsError, open_runtime  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(args: list[str], *, expected_exit: int = 0) -> dict:
    result = subprocess.run(args, capture_output=True, text=True, timeout=60, check=False)
    if result.returncode != expected_exit:
        raise RuntimeError(f"command exited {result.returncode}, expected {expected_exit}: {result.stderr[:400]}")
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    tools, core, fixtures_path = args.tools.resolve(), args.core.resolve(), args.fixtures.resolve()
    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
    if fixtures.get("contract") != "sley-tools.resource-admission-fixtures.v1" or len(fixtures.get("cases", [])) != 49:
        raise RuntimeError("Resource Admission fixture contract differs")
    with tempfile.TemporaryDirectory(prefix="sley-resource-probe-") as work:
        base = Path(work)
        bin_dir = base / "bin"
        bin_dir.mkdir()
        copied_tools = bin_dir / "sley-tools"
        copied_builder = bin_dir / "sley-tools-native-builder"
        shutil.copy2(tools, copied_tools)
        shutil.copy2(tools.with_name("sley-tools-native-builder"), copied_builder)
        project = base / "project"
        initialized = command([str(copied_tools), "init", str(project), "--template", "resource-admission", "--core", str(core)])
        if initialized["program"]["program_id"] != "resource-admission" or initialized["status"]["native_commit_count"] != 1:
            raise RuntimeError("fresh Resource Admission graph was not committed")
        tested = command([str(copied_tools), "test", "--project", str(project)])
        if tested.get("external_assertions_against_native_execution") != 12 or tested.get("native_core_test_admission") != "NOT_RUN: production native executor unavailable":
            raise RuntimeError("native Resource Admission oracle or test classification differs")
        failure_input = base / "insufficient.json"
        failure_input.write_text(json.dumps({"requested_workers": 1, "available_workers": 1, "requested_memory_mib": 2**53 + 1, "available_memory_mib": 2**53}), encoding="utf-8")
        checked = command([str(copied_tools), "run", "--project", str(project), "--input", str(failure_input), "--check"], expected_exit=1)
        if checked["result"]["allow"] is not False or checked["result"]["requested_memory_mib"] != str(2**53 + 1):
            raise RuntimeError("check-style CLI lost native rejection or exact signed result")
        with open_runtime(tools=copied_tools, core=core) as runtime:
            program = runtime.open_project(project)
            rpc_pid = runtime._process.pid
            workers = set()
            for case in fixtures["cases"]:
                inputs = {name: int(value) for name, value in case["input"].items()}
                observed = program.call("evaluate", inputs=inputs)
                expected = {name: int(value) if name.endswith("_memory_mib") else value for name, value in case["expected"].items()}
                if observed.value != expected or observed.metadata["state_root"] != program.state_root:
                    raise RuntimeError(f"Python SDK differs from independent fixture {case['id']}")
                workers.add(observed.metadata["worker_pid"])
            if len(workers) != 1:
                raise RuntimeError("Python SDK did not reuse one native Resource Admission worker")
            valid = {"requested_workers": 1, "available_workers": 1, "requested_memory_mib": 1, "available_memory_mib": 1}
            for invalid in (
                {**valid, "requested_memory_mib": -1},
                {**valid, "requested_memory_mib": 2**63},
                {**valid, "requested_memory_mib": True},
                {**valid, "requested_memory_mib": 1.5},
                {"requested_workers": 1},
            ):
                try:
                    program.call("evaluate", inputs=invalid)
                except SleyToolsError as error:
                    if error.code != "ST_INPUT_INVALID":
                        raise RuntimeError(f"wrong signed input error: {error.code}") from error
                else:
                    raise RuntimeError("Python SDK accepted invalid signed input")
        try:
            os.kill(rpc_pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise RuntimeError("Python SDK leaked Resource Admission RPC process")
        node = subprocess.run([
            "node", str(Path(__file__).with_name("probe_resource_typescript.mjs")),
            f"tools={copied_tools}", f"core={core}", f"project={project}", f"fixtures={fixtures_path}",
        ], capture_output=True, text=True, timeout=60, check=False)
        if node.returncode:
            raise RuntimeError(f"TypeScript Resource Admission probe failed: {node.stderr[:500]}")
        node_report = json.loads(node.stdout)
        if node_report.get("contract") != "sley-tools.resource-typescript-probe.v1" or node_report.get("result") != "PASS" or node_report.get("native_cases") != 49:
            raise RuntimeError("TypeScript Resource Admission evidence differs")
        report = {
            "contract": "sley-tools.resource-admission-probe.v1", "result": "PASS",
            "core_binary_sha256": digest(core), "fixtures_sha256": digest(fixtures_path),
            "copied_binaries_without_core_source_checkout": True,
            "fresh_native_commit": True, "external_native_assertions": 12,
            "python_native_cases": 49, "typescript_native_cases": 49,
            "python_and_typescript_one_worker_each": True,
            "signed_limits_and_over_safe_integer_exact": True,
            "typed_input_negatives_per_sdk": 5,
            "check_exit_on_computed_rejection": True,
            "native_test_admission": "NOT_RUN: production native executor unavailable",
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: fresh native Resource Admission and exact Python/TypeScript calls passed")


if __name__ == "__main__":
    main()
