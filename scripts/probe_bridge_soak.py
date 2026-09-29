#!/usr/bin/env python3
"""Bounded Python/Node SDK soak against one accepted native Sley artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages/python"))

from sley_tools import open_runtime  # noqa: E402


CALLS = 256
SAMPLE_EVERY = 64
MAX_FD_DRIFT = 2


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def descriptor_count(pid: int) -> int:
    return len(os.listdir(f"/proc/{pid}/fd"))


def gone(pid: int) -> bool:
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.01)
    return False


def python_soak(tools: Path, core: Path, artifact: Path, cases: list[dict]) -> dict:
    rpc_pid = worker_pid = None
    with open_runtime(tools=tools, core=core) as runtime:
        program = runtime.load(artifact=artifact)
        assert runtime._process is not None
        rpc_pid = runtime._process.pid
        for case in cases[:16]:
            result = program.call("evaluate", inputs=case["input"])
            if result.value != case["expected"]:
                raise RuntimeError(f"Python warmup differs from native fixture {case['id']}")
            if worker_pid is None:
                worker_pid = result.metadata["worker_pid"]
            elif result.metadata["worker_pid"] != worker_pid:
                raise RuntimeError("Python warmup replaced its native worker")
        assert worker_pid is not None
        baseline = {"client": descriptor_count(os.getpid()), "rpc": descriptor_count(rpc_pid),
                    "worker": descriptor_count(worker_pid)}
        samples = []
        for index in range(CALLS):
            case = cases[index % len(cases)]
            result = program.call("evaluate", inputs=case["input"])
            if result.value != case["expected"]:
                raise RuntimeError(f"Python soak differs from native fixture {case['id']}")
            if result.metadata["worker_pid"] != worker_pid or result.metadata["state_root"] != program.state_root:
                raise RuntimeError("Python soak changed its native worker or accepted root")
            if (index + 1) % SAMPLE_EVERY == 0:
                sample = {"calls": index + 1, "client": descriptor_count(os.getpid()),
                          "rpc": descriptor_count(rpc_pid), "worker": descriptor_count(worker_pid)}
                if any(sample[name] > baseline[name] + MAX_FD_DRIFT for name in ("client", "rpc", "worker")):
                    raise RuntimeError(f"Python soak grew file descriptors: {baseline} -> {sample}")
                samples.append(sample)
    assert rpc_pid is not None and worker_pid is not None
    if not gone(rpc_pid) or not gone(worker_pid):
        raise RuntimeError("Python SDK left its RPC process or native worker alive")
    return {"calls": CALLS, "worker_reused": True, "root_pinned": True, "baseline_fds": baseline,
            "fd_samples": samples, "max_allowed_fd_drift": MAX_FD_DRIFT, "processes_exited": True}


def probe(tools: Path, core: Path, artifact: Path, fixtures: Path) -> dict:
    case_file = json.loads(fixtures.read_text(encoding="utf-8"))
    cases = case_file.get("cases")
    if case_file.get("contract") != "sley-tools.release-readiness-fixtures.v1" or not isinstance(cases, list) or len(cases) != 48:
        raise RuntimeError("soak requires the 48-case Release Readiness native fixture")
    with tempfile.TemporaryDirectory(prefix="sley-bridge-soak-") as temporary:
        bin_dir = Path(temporary) / "bin"
        bin_dir.mkdir()
        copied_tools = bin_dir / "sley-tools"
        shutil.copy2(tools, copied_tools)
        shutil.copy2(tools.with_name("sley-tools-native-builder"), bin_dir / "sley-tools-native-builder")
        python = python_soak(copied_tools, core, artifact, cases)
        node_script = Path(__file__).with_name("probe_bridge_soak.mjs")
        node = subprocess.run(
            ["node", str(node_script), f"tools={copied_tools}", f"core={core}",
             f"artifact={artifact}", f"fixtures={fixtures}"],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if node.returncode:
            raise RuntimeError(f"Node SDK soak failed: {node.stderr[:700]}")
        typescript = json.loads(node.stdout)
        if typescript.get("calls") != CALLS or typescript.get("max_allowed_fd_drift") != MAX_FD_DRIFT or not typescript.get("processes_exited"):
            raise RuntimeError("Node SDK soak returned incomplete measurements")
        return {
            "contract": "sley-tools.bridge-soak-probe.v1", "result": "PASS",
            "core_binary_sha256": digest(core), "artifact_sha256": digest(artifact),
            "fixtures_sha256": digest(fixtures), "copied_binaries_without_source_checkout": True,
            "python": python, "typescript": typescript,
            "native_test_admission": "NOT_RUN: production native executor unavailable",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = probe(args.tools.resolve(), args.core.resolve(), args.artifact.resolve(), args.fixtures.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: {CALLS} native calls per SDK, worker and descriptor checks passed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Bridge soak probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
