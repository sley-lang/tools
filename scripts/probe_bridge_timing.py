#!/usr/bin/env python3
"""Report correctness-checked cold/warm Bridge timings and honest gaps."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/python"))
from sley_tools import open_runtime  # noqa: E402

COLD_RUNS = 5
RSS_SAMPLE_EVERY = 12


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(samples: list[float]) -> dict:
    ordered = sorted(samples)
    return {"samples": len(ordered), "min_ms": ordered[0],
            "median_ms": median(ordered),
            "p95_ms": ordered[math.ceil(len(ordered) * .95) - 1], "max_ms": ordered[-1]}


def resident_kib(pid: int) -> int:
    with open(f"/proc/{pid}/status", encoding="utf-8") as status:
        for line in status:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    raise RuntimeError(f"process {pid} has no VmRSS measurement")


def python_probe(tools: Path, core: Path, artifact: Path, cases: list[dict]) -> dict:
    warm: list[float] = []
    rpc: list[float] = []
    rss_samples = []
    worker_pid = None
    started = time.perf_counter_ns()
    with open_runtime(tools=tools, core=core) as runtime:
        program = runtime.load(artifact=artifact)
        cold_load_ms = (time.perf_counter_ns() - started) / 1_000_000
        limits = runtime.hello["limits"] if runtime.hello else None
        batch_started = time.perf_counter_ns()
        for index, case in enumerate(cases):
            call_started = time.perf_counter_ns()
            result = program.call("evaluate", inputs=case["input"])
            warm.append((time.perf_counter_ns() - call_started) / 1_000_000)
            if result.value != case["expected"] or result.metadata["state_root"] != program.state_root:
                raise RuntimeError(f"native result differs from fixture or root at {case['id']}")
            if worker_pid is None:
                worker_pid = result.metadata["worker_pid"]
            elif worker_pid != result.metadata["worker_pid"]:
                raise RuntimeError("native worker changed during warm calls")
            if index == 0 or (index + 1) % RSS_SAMPLE_EVERY == 0:
                assert runtime._process is not None
                rss_samples.append({"after_calls": index + 1,
                                    "client_kib": resident_kib(os.getpid()),
                                    "rpc_kib": resident_kib(runtime._process.pid),
                                    "worker_kib": resident_kib(worker_pid)})
            elapsed = result.metadata.get("end_to_end_ms")
            if type(elapsed) is not int or elapsed < 0:
                raise RuntimeError("RPC duration is absent or invalid")
            rpc.append(elapsed)
        batch_seconds = (time.perf_counter_ns() - batch_started) / 1_000_000_000
    cold_samples = [cold_load_ms]
    for _ in range(COLD_RUNS - 1):
        started = time.perf_counter_ns()
        with open_runtime(tools=tools, core=core) as fresh:
            fresh_program = fresh.load(artifact=artifact)
            cold_samples.append((time.perf_counter_ns() - started) / 1_000_000)
            first = fresh_program.call("evaluate", inputs=cases[0]["input"])
            if first.value != cases[0]["expected"] or first.metadata["state_root"] != fresh_program.state_root:
                raise RuntimeError("fresh Python cold load differs from native fixture")
    return {"cold_load_ms": cold_load_ms, "cold_load_samples_ms": cold_samples,
            "cold_load_summary": summarize(cold_samples),
            "cold_load_scope": "open_runtime construction through artifact import and first opened RPC/native worker; excludes first call",
            "warm_client_call": summarize(warm), "rpc_dispatch_to_worker_result": summarize(rpc),
            "rpc_scope": "coarse integer milliseconds from RPC dispatch through native worker result; zero denotes less than one millisecond, not zero elapsed time",
            "batch_elapsed_seconds": batch_seconds,
            "batch_calls_per_second": len(cases) / batch_seconds,
            "declared_rpc_limits": limits,
            "rss_samples": rss_samples,
            "sampled_peak_client_rpc_worker_rss_kib": max(sample["client_kib"] + sample["rpc_kib"] + sample["worker_kib"] for sample in rss_samples),
            "fixture_cases_checked": len(cases), "worker_reused": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "core", "artifact", "fixtures", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    tools, core, artifact, fixtures = (getattr(args, name).resolve() for name in ("tools", "core", "artifact", "fixtures"))
    fixture = json.loads(fixtures.read_text(encoding="utf-8"))
    cases = fixture.get("cases")
    if fixture.get("contract") != "sley-tools.release-readiness-fixtures.v1" or not isinstance(cases, list) or len(cases) != 48:
        raise RuntimeError("timing requires the 48-case native Release Readiness fixture")
    python = python_probe(tools, core, artifact, cases)
    node = subprocess.run(["node", str(Path(__file__).with_suffix(".mjs")),
                           f"tools={tools}", f"core={core}", f"artifact={artifact}", f"fixtures={fixtures}"],
                          capture_output=True, text=True, timeout=120, check=False)
    if node.returncode:
        raise RuntimeError(f"Node timing probe failed: {node.stderr[-1000:]}")
    typescript = json.loads(node.stdout)
    if typescript["fixture_cases_checked"] != 48 or not typescript["worker_reused"]:
        raise RuntimeError("Node timing probe omitted correctness or worker reuse")
    checkout = Path(__file__).resolve().parents[1]
    checkout_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
    checkout_dirty = bool(subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=checkout))
    report = {"contract": "sley-tools.bridge-timing-probe.v1", "result": "PASS",
              "measurement_profile": "five_cold_and_sampled_rss_v1",
              "probe_checkout_commit": checkout_commit,
              "probe_checkout_dirty": checkout_dirty,
              "platform": sys.platform, "architecture": platform.machine(),
              "python_version": platform.python_version(),
              "tools_binary_sha256": digest(tools), "core_binary_sha256": digest(core),
              "artifact_sha256": digest(artifact), "fixtures_sha256": digest(fixtures),
              "python_sdk_sha256": digest(Path(__file__).resolve().parents[1] / "packages/python/sley_tools/runtime.py"),
              "typescript_sdk_sha256": digest(Path(__file__).resolve().parents[1] / "packages/typescript/src/index.js"),
              "python_probe_sha256": digest(Path(__file__)),
              "typescript_probe_sha256": digest(Path(__file__).with_suffix(".mjs")),
              "sample_order": "48 fixture cases in file order; no warmup discarded",
              "batch_scope": "48 correctness-checked post-open SDK calls in file order, including five sampled RSS reads; throughput includes the sampling overhead",
              "memory_scope": "sampled Linux VmRSS sum of client, RPC process, and native worker after calls 1, 12, 24, 36, and 48; not a continuous peak",
              "python": python, "typescript": typescript,
              "unavailable_subdivisions": {
                  "encoding_ms": "SDK encoding has no separate timer; included in client call",
                  "core_execution_ms": "native worker does not time execute_function separately from repository loading and result conversion",
                  "decoding_ms": "SDK decoding has no separate timer; included in client call",
              },
              "interpretation": "local observations only; five fresh process cold loads per SDK with uncontrolled OS page cache, 48 correctness-checked post-open calls each, and sampled Linux process RSS; no cross-language speed ranking or general performance claim"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: 48 correct native calls per SDK")


if __name__ == "__main__":
    main()
