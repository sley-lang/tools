#!/usr/bin/env python3
"""Exercise the thin synchronous Python SDK against the native RPC backend."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages/python"))

from sley_tools import SleyToolsError, encode_integer, open_runtime  # noqa: E402


def expect_input_error(callback) -> None:
    try:
        callback()
    except SleyToolsError as error:
        if error.code != "ST_INPUT_INVALID" or error.category != "input_shape":
            raise RuntimeError(f"wrong SDK input error: {error.code}, {error.category}") from error
    else:
        raise RuntimeError("invalid Python input was accepted")


def probe(tools: Path, core: Path, artifact: Path, fixtures: Path) -> dict:
    cases = json.loads(fixtures.read_text(encoding="utf-8"))
    if cases.get("contract") != "sley-tools.release-readiness-fixtures.v1" or cases.get("max_blocking_findings") != 0 or len(cases.get("cases", [])) != 48:
        raise RuntimeError("shared Release Readiness fixtures have an unexpected contract")
    with tempfile.TemporaryDirectory(prefix="sley-python-sdk-probe-") as temporary:
        folder = Path(temporary)
        installed = folder / "bin"
        installed.mkdir()
        copied_tools = installed / "sley-tools"
        shutil.copy2(tools, copied_tools)
        shutil.copy2(tools.with_name("sley-tools-native-builder"), installed / "sley-tools-native-builder")
        with open_runtime(tools=copied_tools, core=core) as runtime:
            program = runtime.load(artifact=artifact)
            assert runtime._process is not None
            rpc_pid = runtime._process.pid
            count = 0
            worker_pids = set()
            for case in cases["cases"]:
                result = program.call("evaluate", inputs=case["input"])
                if result.value != case["expected"]:
                    raise RuntimeError(f"Python SDK native output differs from shared fixture {case['id']}")
                if result.metadata["state_root"] != program.state_root:
                    raise RuntimeError("Python SDK lost the accepted root binding")
                worker_pids.add(result.metadata["worker_pid"])
                count += 1
            if count != 48 or len(worker_pids) != 1:
                raise RuntimeError("Python SDK did not reuse one native worker for the matrix")
            if program.test()["external_assertions_against_native_execution"] != 36:
                raise RuntimeError("Python SDK test call omitted external native assertions")
            if program.inspect()["accepted_state_root"] != program.state_root:
                raise RuntimeError("Python SDK inspect lost exact root identity")
            base = {"test_run_completed": True, "tests_failed": 0, "artifact_verified": True, "blocking_findings": 0}
            for value in (True, -1, 2**32, 1.0, "1"):
                expect_input_error(lambda value=value: program.call("evaluate", inputs={**base, "tests_failed": value}))
            expect_input_error(lambda: program.call("evaluate", inputs={**base, "artifact_verified": 1}))
            expect_input_error(lambda: program.call("wrong", inputs=base))
            expect_input_error(lambda: program.call("evaluate", inputs=base, deadline_ms=0))
            if encode_integer(-(2**63), "i64")["value"] != str(-(2**63)) or encode_integer(2**63 - 1, "i64")["value"] != str(2**63 - 1):
                raise RuntimeError("Python SDK i64 wire encoding lost signed limits")
            for value in (True, -(2**63) - 1, 2**63):
                expect_input_error(lambda value=value: encode_integer(value, "i64"))
        try:
            os.kill(rpc_pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise RuntimeError("Python SDK left its RPC process running after close")

        with open_runtime(tools=copied_tools, core=core) as runtime:
            program = runtime.load(artifact=artifact)
            ready = program.call("evaluate", inputs={"test_run_completed": True, "tests_failed": 0, "artifact_verified": True, "blocking_findings": 0})
            os.kill(ready.metadata["worker_pid"], signal.SIGKILL)
            try:
                program.call("evaluate", inputs={"test_run_completed": True, "tests_failed": 0, "artifact_verified": True, "blocking_findings": 0})
            except SleyToolsError as error:
                if error.code != "ST_TRANSPORT_FAILED":
                    raise RuntimeError(f"worker loss returned {error.code}, expected transport") from error
            else:
                raise RuntimeError("Python SDK hid native worker loss")
        return {
            "contract": "sley-tools.python-sdk-probe.v1",
            "result": "PASS",
            "core_binary_sha256": hashlib.sha256(core.read_bytes()).hexdigest(),
            "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "fixtures_sha256": hashlib.sha256(fixtures.read_bytes()).hexdigest(),
            "native_cases": count,
            "one_worker_for_matrix": True,
            "copied_binaries_without_source_checkout": True,
            "typed_input_negative_cases": 8,
            "i64_wire_limits_exact": True,
            "worker_loss_reported": True,
            "runtime_closed_process": True,
            "native_test_admission": "NOT_RUN: production native executor unavailable",
        }


def main() -> int:
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
    print(f"wrote {args.output}: Python SDK native execution and failure paths passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Python SDK probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
