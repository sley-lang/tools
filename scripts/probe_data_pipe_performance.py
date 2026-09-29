#!/usr/bin/env python3
"""Measure bounded native Data Pipe throughput and process-tree resident memory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


SIZES = (64, 8192)
UNITS = {"wall": "us", "cpu": "us", "archive": "bytes"}
BASELINE = {"wall_us": "100", "cpu_us": "200", "archive_bytes": "300"}
REGRESSION = {"wall_us": "101", "cpu_us": "200", "archive_bytes": "300"}
IMPROVEMENT = {"wall_us": "99", "cpu_us": "199", "archive_bytes": "299"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rss_tree_kb(pid: int) -> int:
    try:
        lines = Path(f"/proc/{pid}/status").read_text().splitlines()
        own = next(int(line.split()[1]) for line in lines if line.startswith("VmRSS:"))
        children = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
    except (FileNotFoundError, ProcessLookupError, StopIteration):
        return 0
    return own + sum(rss_tree_kb(int(child)) for child in children)


def input_file(path: Path, count: int) -> None:
    with path.open("wb") as stream:
        for index in range(count):
            record = {"case_id": f"case-{index:06}", "units": UNITS,
                      "baseline": BASELINE,
                      "candidate": REGRESSION if index % 2 == 0 else IMPROVEMENT}
            stream.write((json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode())


def selected_expected(index: int) -> dict:
    return {"kind": "selected", "case_id": f"case-{index:06}",
            "candidate": REGRESSION,
            "comparison": {"wall_delta_us": {"ok": "1"},
                           "cpu_delta_us": {"ok": "0"},
                           "archive_delta_bytes": {"ok": "0"},
                           "wall_regressed": True, "cpu_regressed": False,
                           "archive_regressed": False, "any_regression": True}}


def verify_output(path: Path, count: int) -> None:
    pairs = count // 2
    with path.open("rb") as stream:
        for index in range(0, count, 2):
            line = stream.readline()
            if not line or json.loads(line) != selected_expected(index):
                raise RuntimeError(f"{count}-row selected output differs at {index}")
        summary = {"kind": "summary", "count": str(count),
                   "wall_total_us": str(pairs * 200),
                   "cpu_total_us": str(pairs * 399),
                   "archive_total_bytes": str(pairs * 599)}
        if json.loads(stream.readline()) != summary or stream.readline():
            raise RuntimeError(f"{count}-row native summary or output length differs")


def run(command: list[str], env: dict[str, str], count: int, output_path: Path) -> dict:
    started = time.perf_counter()
    stderr_path = output_path.with_suffix(".stderr")
    with output_path.open("wb") as output, stderr_path.open("wb") as diagnostics_file:
        process = subprocess.Popen(command, env=env, stdout=output, stderr=diagnostics_file)
        peak = 0
        samples = 0
        deadline = time.monotonic() + 300
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"{count}-row native pipeline timed out")
                peak = max(peak, rss_tree_kb(process.pid))
                samples += 1
                time.sleep(0.05)
        except BaseException:
            process.kill()
            process.wait()
            raise
        process.wait(timeout=5)
    elapsed = time.perf_counter() - started
    diagnostics = stderr_path.read_bytes()
    if process.returncode:
        raise RuntimeError(f"{count}-row native pipeline failed: {diagnostics[-600:]!r}")
    verify_output(output_path, count)
    report = json.loads(diagnostics.splitlines()[-1])
    if (report.get("result") != "PASS" or report.get("processed_records") != count
            or report.get("emitted_records") != count // 2 or not samples or peak <= 0):
        raise RuntimeError(f"{count}-row process or memory observation is incomplete")
    return {"records": count, "elapsed_seconds": round(elapsed, 3),
            "rows_per_second": round(count / elapsed, 3),
            "sampled_peak_process_tree_kib": peak, "memory_samples": samples,
            "selected_records": count // 2, "output_bytes": output_path.stat().st_size,
            "output_sha256": digest(output_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "core", "comparator-spec", "comparator-cases",
                 "selector-spec", "selector-cases", "reducer-spec", "reducer-cases", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    paths = {name: getattr(args, name).resolve() for name in
             ("tools", "core", "comparator_spec", "comparator_cases",
              "selector_spec", "selector_cases", "reducer_spec", "reducer_cases", "output")}
    with tempfile.TemporaryDirectory(prefix="sley-data-pipe-performance-") as temporary:
        root = Path(temporary)
        projects = {}
        roots = {}
        for role, name in (("comparator", "benchmark-comparator"),
                           ("selector", "benchmark-selector"),
                           ("reducer", "benchmark-summary-reducer")):
            project = root / name
            result = subprocess.run([str(paths["tools"]), "init", str(project),
                                     "--graph", str(paths[f"{role}_spec"]),
                                     "--tests", str(paths[f"{role}_cases"]),
                                     "--core", str(paths["core"])], capture_output=True,
                                    text=True, timeout=120)
            if result.returncode:
                raise RuntimeError(f"native {role} initialization failed: {result.stderr[:350]}")
            roots[role] = json.loads(result.stdout)["program"]["entrypoint"]["state_root"]
            projects[role] = project
        base = [sys.executable, "-m", "sley_tools.data_pipe", "--tools", str(paths["tools"]),
                "--core", str(paths["core"]), "--batch-size", "512"]
        for role, project in projects.items():
            base.extend([f"--{role}-project", str(project)])
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "packages/python")}
        measurements = []
        inputs = []
        for count in SIZES:
            source = root / f"{count}.jsonl"
            input_file(source, count)
            inputs.append({"records": count, "bytes": source.stat().st_size, "sha256": digest(source)})
            measurements.append(run([*base, "--input", str(source)], env, count,
                                    root / f"{count}.output.jsonl"))
    small, large = measurements
    if inputs[1]["bytes"] <= 1_048_576 or measurements[1]["output_bytes"] <= 65_536:
        raise RuntimeError("mixed native workload is too small to exercise streaming output")
    if (large["sampled_peak_process_tree_kib"] > small["sampled_peak_process_tree_kib"] + 32_768
            or large["sampled_peak_process_tree_kib"] * 2 > small["sampled_peak_process_tree_kib"] * 3):
        raise RuntimeError("observed process-tree resident memory scaled too far with input length")
    report = {
        "contract": "sley-tools.data-pipe-performance-probe.v2", "result": "PASS",
        "tools_binary_sha256": digest(paths["tools"]),
        "builder_binary_sha256": digest(paths["tools"].with_name("sley-tools-native-builder")),
        "core_binary_sha256": digest(paths["core"]),
        "module_sha256": digest(Path(__file__).resolve().parents[1] / "packages/python/sley_tools/data_pipe.py"),
        "python_sdk_sha256": digest(Path(__file__).resolve().parents[1] / "packages/python/sley_tools/runtime.py"),
        "probe_entry_sha256": digest(Path(__file__)),
        **{name + "_sha256": digest(paths[name]) for name in
           ("comparator_spec", "comparator_cases", "selector_spec", "selector_cases",
            "reducer_spec", "reducer_cases")},
        "native_roots": roots, "inputs": inputs, "observations": measurements,
        "workload": "alternating wall regression and all-metric improvement",
        "selected_output_verified": True,
        "peak_scope": "sampled RSS sum of pipeline process and live descendants on Linux /proc",
        "memory_growth_bound_verified": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    paths["output"].parent.mkdir(parents=True, exist_ok=True)
    paths["output"].write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"wrote {paths['output']}: 64 and 8192 mixed native rows stayed within the memory bound")


if __name__ == "__main__":
    main()
