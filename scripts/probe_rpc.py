#!/usr/bin/env python3
"""Verify process-scoped JSON-RPC execution, typed values, and failure paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path


def invoke(tools: Path, *args: str) -> dict:
    outcome = subprocess.run([str(tools), *args], capture_output=True, text=True, timeout=30, check=False)
    if outcome.returncode:
        raise RuntimeError(f"{' '.join(args[:2])} exited {outcome.returncode}: {outcome.stderr[:350]}")
    return json.loads(outcome.stdout)


def tagged_input(completed: bool, failed: object, verified: bool, blocking: object) -> dict:
    return {
        "kind": "record",
        "fields": {
            "test_run_completed": {"kind": "boolean", "value": completed},
            "tests_failed": {"kind": "integer", "type": "u32", "value": failed},
            "artifact_verified": {"kind": "boolean", "value": verified},
            "blocking_findings": {"kind": "integer", "type": "u32", "value": blocking},
        },
    }


def oracle(completed: bool, failed: int, verified: bool, blocking: int) -> dict[str, bool]:
    reasons = {
        "test_run_incomplete": not completed,
        "tests_failed": failed > 0,
        "artifact_unverified": not verified,
        "blocking_findings_exceeded": blocking > 0,
    }
    return {"allow": not any(reasons.values()), **reasons}


class Client:
    def __init__(self, tools: Path, project: Path, workspace: Path | None = None) -> None:
        command = [str(tools), "rpc", "--project", str(project)]
        if workspace is not None:
            command.extend(["--workspace", str(workspace)])
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        self.next_id = 1

    def raw(self, line: str, timeout_seconds: int = 10) -> dict:
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()
        ready, _, _ = select.select([self.process.stdout], [], [], timeout_seconds)
        if not ready:
            raise RuntimeError("RPC response timed out")
        response = self.process.stdout.readline()
        if not response:
            raise RuntimeError(f"RPC endpoint closed: {self.process.stderr.read(350) if self.process.stderr else ''}")
        return json.loads(response)

    def call(self, method: str, params: dict, timeout_seconds: int = 10) -> dict:
        request_id = self.next_id
        self.next_id += 1
        response = self.raw(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}), timeout_seconds)
        if response.get("jsonrpc") != "2.0" or response.get("id") != request_id:
            raise RuntimeError("RPC response ID or version differs from request")
        return response

    def close(self) -> str:
        assert self.process.stdin is not None and self.process.stderr is not None
        self.process.stdin.close()
        if self.process.wait(timeout=10):
            raise RuntimeError(f"RPC process exited unsuccessfully: {self.process.stderr.read()[:350]}")
        return self.process.stderr.read()


def error_code(response: dict, expected: str) -> None:
    error = response.get("error")
    if not isinstance(error, dict) or error.get("data", {}).get("tools_code") != expected:
        raise RuntimeError(f"expected {expected}, received {response}")


def execute(client: Client, handle: str, facts: tuple[bool, int, bool, int], deadline: int = 10_000) -> dict:
    return client.call("sleytools.program.execute", {
        "handle": handle,
        "entrypoint": "evaluate",
        "input": tagged_input(facts[0], str(facts[1]), facts[2], str(facts[3])),
        "deadline_ms": deadline,
    })


def probe(tools: Path, core: Path, artifact: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="sley-rpc-probe-") as temporary:
        root = Path(temporary)
        installed = root / "bin"
        installed.mkdir()
        installed_tools = installed / "sley-tools"
        shutil.copy2(tools, installed_tools)
        shutil.copy2(tools.with_name("sley-tools-native-builder"), installed / "sley-tools-native-builder")
        project = root / "project"
        imported = invoke(installed_tools, "artifact", "import", str(artifact), str(project), "--core", str(core))
        if imported["verification"]["external_assertions_against_native_execution"] != 36:
            raise RuntimeError("copied binary import did not produce executable native program")

        client = Client(installed_tools, project)
        try:
            hello = client.call("sleytools.hello", {})["result"]
            if hello["core"]["binary_sha256"] != hashlib.sha256(core.read_bytes()).hexdigest() or hello["limits"]["max_inflight"] != 1:
                raise RuntimeError("RPC hello differs from selected core or bounded contract")
            opened = client.call("sleytools.workspace.open", {})["result"]
            handle = opened["handle"]
            worker_pid = opened["worker_pid"]
            if len(handle) != 64 or not worker_pid:
                raise RuntimeError("RPC did not issue an opaque handle and native worker")
            inspected = client.call("sleytools.program.inspect", {"handle": handle})["result"]
            if inspected["accepted_state_root"] != opened["state_root"]:
                raise RuntimeError("RPC inspector is not pinned to opened root")

            count = 0
            for completed in (False, True):
                for failed in (0, 1, 2**32 - 1):
                    for verified in (False, True):
                        for blocking in (0, 1, 2, 2**32 - 1):
                            facts = (completed, failed, verified, blocking)
                            response = execute(client, handle, facts)
                            if "error" in response:
                                raise RuntimeError(f"native RPC execution failed at {facts}: {response['error']}")
                            value = response["result"]["value"]
                            observed = {name: field["value"] for name, field in value["fields"].items()}
                            if observed != oracle(*facts) or response["result"]["metadata"]["worker_pid"] != worker_pid:
                                raise RuntimeError(f"native RPC result or worker reuse differs from oracle at {facts}")
                            count += 1
            if count != 48:
                raise RuntimeError("RPC truth-table matrix is incomplete")
            tested = client.call("sleytools.tests.run", {"handle": handle})["result"]
            if tested["external_assertions_against_native_execution"] != 36 or "NOT_RUN" not in tested["native_core_test_admission"]:
                raise RuntimeError("RPC tests misreport external assertions or native admission")
            rejection = execute(client, handle, (True, 0, True, 1))
            if rejection["result"]["value"]["fields"]["allow"]["value"] is not False:
                raise RuntimeError("a computed rejection did not return successful RPC result")

            base = {"handle": handle, "entrypoint": "evaluate", "input": tagged_input(True, "0", True, "0")}
            for bad_input in [
                tagged_input(True, 0, True, "0"),
                tagged_input(True, "00", True, "0"),
                tagged_input(True, "-1", True, "0"),
                tagged_input(True, str(2**32), True, "0"),
                tagged_input(True, True, True, "0"),
            ]:
                bad = client.call("sleytools.program.execute", {**base, "input": bad_input})
                error_code(bad, "ST_INPUT_INVALID")
            error_code(client.call("sleytools.program.execute", {**base, "handle": "0" * 64}), "ST_INPUT_INVALID")
            error_code(client.call("sleytools.operation.status", {}), "ST_INPUT_INVALID")
            malformed = client.raw("{broken")
            if malformed.get("error", {}).get("code") != -32700:
                raise RuntimeError("malformed JSON was not a JSON-RPC parse error")
            duplicate = client.raw('{"jsonrpc":"2.0","id":999,"method":"sleytools.hello","params":{},"params":{}}')
            if duplicate.get("error", {}).get("code") != -32700:
                raise RuntimeError("duplicate JSON-RPC field was not rejected")
            nested = {}
            for _ in range(18):
                nested = {"nested": nested}
            deep = client.raw(json.dumps({"jsonrpc": "2.0", "id": 1000, "method": "sleytools.hello", "params": nested}))
            error_code(deep, "ST_RESOURCE_LIMIT")

            os.kill(worker_pid, signal.SIGSTOP)
            timeout = execute(client, handle, (True, 0, True, 0), deadline=50)
            error_code(timeout, "ST_RESOURCE_LIMIT")
            reopened = client.call("sleytools.workspace.open", {})["result"]
            if reopened["handle"] == handle or reopened["worker_pid"] == worker_pid:
                raise RuntimeError("explicit reopen reused a stopped worker or handle")
            error_code(execute(client, handle, (True, 0, True, 0)), "ST_INPUT_INVALID")
            handle = reopened["handle"]
            worker_pid = reopened["worker_pid"]
            if "error" in execute(client, handle, (True, 0, True, 0)):
                raise RuntimeError("new worker did not recover read-only execution")

            os.kill(worker_pid, signal.SIGKILL)
            lost = execute(client, handle, (True, 0, True, 0))
            error_code(lost, "ST_TRANSPORT_FAILED")
            reopened = client.call("sleytools.workspace.open", {})["result"]
            handle = reopened["handle"]
            worker_pid = reopened["worker_pid"]
            if "error" in execute(client, handle, (True, 0, True, 0)):
                raise RuntimeError("explicit reopen did not recover after endpoint loss")

            invoke(installed_tools, "edit", "--project", str(project), "--max-blocking-findings", "2")
            stale = execute(client, handle, (True, 0, True, 0))
            error_code(stale, "ST_STALE_BASE")
            error_code(client.call("sleytools.workspace.open", {}), "ST_STALE_BASE")
        finally:
            stderr = client.close()
        if stderr:
            raise RuntimeError(f"RPC wrote diagnostics on stderr during normal probe: {stderr[:250]}")

        oversized = subprocess.Popen(
            [str(installed_tools), "rpc", "--project", str(project)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        output, errors = oversized.communicate("x" * 65_540 + "\n", timeout=10)
        if oversized.returncode != 0 or errors or len(output.splitlines()) != 1:
            raise RuntimeError("oversized RPC request did not fail cleanly")
        error_code(json.loads(output), "ST_RESOURCE_LIMIT")
        return {
            "contract": "sley-tools.rpc-probe.v1",
            "result": "PASS",
            "core_binary_sha256": hashlib.sha256(core.read_bytes()).hexdigest(),
            "truth_table_cases": count,
            "single_worker_reused": True,
            "copied_binaries_without_source_checkout": True,
            "typed_input_negatives": 5,
            "computed_rejection_is_success": True,
            "deadline_stopped_worker": True,
            "worker_loss_reported": True,
            "explicit_reopen_rotated_handle": True,
            "stale_root_rejected": True,
            "oversized_request_rejected": True,
            "duplicate_and_deep_requests_rejected": True,
            "stdout_protocol_only": True,
            "native_test_admission": "NOT_RUN: production native executor unavailable",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = probe(args.tools.resolve(), args.core.resolve(), args.artifact.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: native JSON-RPC execution, worker reuse, and failure paths passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"RPC probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
