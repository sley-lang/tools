#!/usr/bin/env python3
"""Exercise read-only Python and TypeScript cancellation against a native worker."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/python"))
from sley_tools import SleyToolsError, open_runtime  # noqa: E402


FACTS = {"test_run_completed": True, "tests_failed": 0,
         "artifact_verified": True, "blocking_findings": 0}


def stopped(pid: int) -> bool:
    stat = Path(f"/proc/{pid}/stat")
    return not stat.exists() or stat.read_text().split(") ", 1)[1][0] == "Z"


def wait_stopped(pid: int) -> None:
    end = time.monotonic() + 5
    while time.monotonic() < end:
        if stopped(pid):
            return
        time.sleep(0.05)
    raise RuntimeError(f"cancelled process {pid} remains running")


def python_probe(tools: Path, core: Path, artifact: Path) -> None:
    with open_runtime(tools=tools, core=core) as runtime:
        program = runtime.load(artifact=artifact)
        pre_cancel = threading.Event()
        pre_cancel.set()
        try:
            program.call("evaluate", inputs=FACTS, cancel_event=pre_cancel)
        except SleyToolsError as error:
            if (error.code, error.category) != ("ST_CANCELLED", "execution"):
                raise
        else:
            raise RuntimeError("Python pre-dispatch cancellation returned success")
        try:
            program.call("evaluate", inputs=FACTS, cancel_event=object())
        except SleyToolsError as error:
            if error.code != "ST_INPUT_INVALID":
                raise
        else:
            raise RuntimeError("Python accepted a non-Event cancellation input")
        first = program.call("evaluate", inputs=FACTS)
        worker_pid = first.metadata["worker_pid"]
        assert runtime._process is not None
        rpc_pid = runtime._process.pid
        os.kill(worker_pid, signal.SIGSTOP)
        cancelled = threading.Event()
        outcome: list[Exception] = []
        started = runtime._next_id

        def call() -> None:
            try:
                program.call("evaluate", inputs=FACTS, deadline_ms=10_000,
                             cancel_event=cancelled)
                outcome.append(RuntimeError("Python cancelled native call returned success"))
            except Exception as error:
                outcome.append(error)

        thread = threading.Thread(target=call, daemon=True)
        thread.start()
        end = time.monotonic() + 2
        while runtime._next_id == started and time.monotonic() < end:
            time.sleep(0.01)
        if runtime._next_id == started:
            raise RuntimeError("Python native call did not dispatch before cancellation")
        cancelled.set()
        thread.join(timeout=3)
        if thread.is_alive() or len(outcome) != 1:
            raise RuntimeError("Python cancellation did not end the call promptly")
        error = outcome[0]
        if not isinstance(error, SleyToolsError) or (error.code, error.category) != ("ST_CANCELLED", "execution"):
            raise RuntimeError(f"Python cancellation returned {error!r}")
    wait_stopped(rpc_pid)
    wait_stopped(worker_pid)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "core", "artifact"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tools, core, artifact = (getattr(args, name).resolve() for name in ("tools", "core", "artifact"))
    python_probe(tools, core, artifact)
    command = ["node", str(Path(__file__).with_suffix(".mjs")),
               f"tools={tools}", f"core={core}", f"artifact={artifact}"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError(f"TypeScript cancellation probe failed: {result.stderr[-800:]}")
    root = Path(__file__).resolve().parents[1]
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    report = {
        "contract": "sley-tools.bridge-read-cancellation-probe.v1",
        "result": "PASS",
        "tools_binary_sha256": digest(tools),
        "core_binary_sha256": digest(core),
        "artifact_sha256": digest(artifact),
        "python_sdk_sha256": digest(root / "packages/python/sley_tools/runtime.py"),
        "typescript_sdk_sha256": digest(root / "packages/typescript/src/index.js"),
        "typescript_types_sha256": digest(root / "packages/typescript/src/index.d.ts"),
        "python_probe_sha256": digest(Path(__file__)),
        "typescript_probe_sha256": digest(Path(__file__).with_suffix(".mjs")),
        "pre_dispatch_preserved_session": True,
        "invalid_cancel_input_rejected": True,
        "write_cancel_rejected": True,
        "inflight_cancel_stopped_rpc_and_worker": True,
        "read_only_calls_only": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output}: Python and TypeScript native read cancellation passed")


if __name__ == "__main__":
    main()
