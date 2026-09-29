#!/usr/bin/env python3
"""Probe real TestCase construction and the selected core's native test route."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from probe_genesis import checked, decode_bytes, encode_record, encode_uvar, record_fields, request, run, send, uvar


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(command: list[str]) -> dict:
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError(f"{command[1]} exited {result.returncode}: {result.stderr[:300]}")
    return json.loads(result.stdout)


def failure_symbol(response: dict) -> str:
    if response["kind"] != "response" or not response["flags"]["failed"]:
        raise RuntimeError("native test route unexpectedly succeeded; inspect its result before reporting support")
    return decode_bytes(record_fields(bytes.fromhex(response["body"]), 6)[1]).decode("ascii")


def validation_decision(response_body: str) -> int:
    data = bytes.fromhex(response_body)
    if data[:8] != b"SLEYCRS1":
        raise RuntimeError("candidate.validate returned an unknown result envelope")
    _, offset = uvar(data, 8)
    length, offset = uvar(data, offset)
    payload = data[offset:offset + length]
    if len(payload) != length:
        raise RuntimeError("candidate.validate returned a truncated result")
    decision_field = record_fields(payload, 13)[5]
    decision, end = uvar(decision_field, 0)
    if end != len(decision_field):
        raise RuntimeError("candidate.validate returned malformed decision")
    return decision


def probe(tools: Path, builder: Path, core: Path, route_core: Path, spec: Path, cases: Path) -> dict:
    fixture = json.loads(cases.read_text(encoding="utf-8"))
    case = fixture["cases"][0]
    if fixture["contract"] != "sley-tools.graph-cases.v0" or case["id"] != "zero":
        raise RuntimeError("native test probe fixture changed unexpectedly")
    with tempfile.TemporaryDirectory(prefix="sley-tools-native-test-route-") as temporary:
        base = Path(temporary)
        project = base / "fixed"
        invoke([str(tools), "init", str(project), "--graph", str(spec), "--tests", str(cases), "--core", str(core)])
        state = json.loads((project / ".sley-tools/state.json").read_text(encoding="utf-8"))
        original_root = state["state_root"]
        public = invoke([str(tools), "test", "native-candidate", "--project", str(project), "--case", case["id"]])
        if (public["contract"] != "sley-tools.native-test-candidate.v0"
                or public["phase"] != "STATICALLY_VALID"
                or public["base_root"] != original_root
                or public["target_function_id"] != state["function_id"]
                or public["native_core_test_admission"] != "NOT_RUN: production native executor unavailable"
                or public["native_test_execution"] != "NOT_RUN"
                or public["declared_limits"]["memory_bytes"] != 16 * 1024 * 1024
                or public["declared_limits"]["memory_bytes"] > public["grant_ceilings"]["max_memory_bytes"]
                or public["native_plan_selected_test_count"] != 1
                or public["native_plan_preflight"] != "CORE_LIBRARY_ONLY_NO_EXECUTION"
                or public["accepted_root_unchanged"] is not True):
            raise RuntimeError("public CLI promoted static TestCase validation to native execution")
        missing = subprocess.run([str(tools), "test", "native-candidate", "--project", str(project),
                                  "--case", "absent"], capture_output=True, text=True, timeout=30, check=False)
        if missing.returncode != 2 or json.loads(missing.stderr).get("code") != "ST_INPUT_INVALID":
            raise RuntimeError("public CLI accepted an absent native test case")
        template = base / "release-readiness"
        invoke([str(tools), "init", str(template), "--template", "release-readiness", "--core", str(core)])
        wrong_profile = subprocess.run([str(tools), "test", "native-candidate", "--project",
                                        str(template), "--case", case["id"]], capture_output=True,
                                       text=True, timeout=30, check=False)
        if wrong_profile.returncode != 2 or json.loads(wrong_profile.stderr).get("code") != "ST_INPUT_INVALID":
            raise RuntimeError("public CLI accepted a native TestCase probe outside graph projects")
        if list((project / ".sley-tools").glob("handshake-native-test-*")):
            raise RuntimeError("public CLI left native test handshake files in the project")
        repository = project / "workspace"
        args = [
            str(builder), "construct-graph-native-test", str(repository), str(spec),
            json.dumps(case, sort_keys=True, separators=(",", ":")),
            *[state[name] for name in ("workspace_id", "principal_id", "state_root", "policy_root", "transaction_id", "schema_epoch")],
            state["function_id"],
        ]
        constructed = invoke(args)
        if (constructed["contract"] != "sley-tools.graph-native-test-construction-probe.v0"
                or constructed["target_function_id"] != state["function_id"]
                or constructed["case_id"] != case["id"]
                or constructed["declared_limits"] != public["declared_limits"]
                or constructed["grant_ceilings"] != public["grant_ceilings"]
                or constructed["native_plan_selected_test_count"] != 1
                or constructed["native_plan_preflight"] != "CORE_LIBRARY_ONLY_NO_EXECUTION"
                or len(constructed["native_plan_id"]) != 64):
            raise RuntimeError("native TestCase builder did not bind the intended function and case")
        for changed in ({"expected": "yes"}, {"input": []}):
            invalid = dict(case, **changed)
            bad = subprocess.run(args[:4] + [json.dumps(invalid)] + args[5:], capture_output=True,
                                 text=True, timeout=30, check=False)
            if bad.returncode == 0:
                raise RuntimeError(f"native TestCase builder accepted invalid {next(iter(changed))}")
        stale = subprocess.run(args[:7] + ["0" * 64] + args[8:], capture_output=True,
                               text=True, timeout=30, check=False)
        if stale.returncode == 0:
            raise RuntimeError("native TestCase builder accepted a stale base root")

        hello = json.loads(run(
            [str(route_core), "frame", "decode", "--protocol-profile", "v3-capable", "--expected-version", "1"],
            run([str(route_core), "hello", "--protocol-profile", "v3-capable"]),
        ))
        report = base / "handshake.json"
        run([str(route_core), "serve", "--repository", str(base / "handshake"), "--json",
             "--protocol-profile", "v3-capable", "--report", str(report)],
            json.dumps(hello).encode() + b"\n")
        handshake = json.loads(report.read_text(encoding="utf-8"))["handshake_id"]
        process = subprocess.Popen(
            [str(route_core), "serve", "--repository", str(repository), "--json", "--protocol-profile", "v3-capable"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        pending = bytearray()
        try:
            if send(process, pending, hello)["kind"] != "hello":
                raise RuntimeError("core refused the v3 hello")
            session = checked(send(process, pending, request("session.open", handshake, 0, None, 3)), "session.open")
            before = record_fields(bytes.fromhex(checked(send(
                process, pending, request("workspace.open", "", 1, session, 3)), "workspace.open")), 8)
            if before[1].hex() != original_root:
                raise RuntimeError("fresh accepted workspace root differs from toolkit state")
            stored = checked(send(process, pending, request(
                "candidate.create", constructed["candidate_record_hex"], 2, session, 3)), "candidate.create")
            if stored != constructed["candidate_stored_hex"]:
                raise RuntimeError("core candidate bytes differ from public builder bytes")
            validation_request = encode_record([
                bytes.fromhex(state["transaction_id"]), bytes.fromhex(state["principal_id"]),
                encode_uvar(constructed["now_unix_millis"]), bytes.fromhex(stored),
            ]).hex()
            decision = validation_decision(checked(send(process, pending, request(
                "candidate.validate", validation_request, 3, session, 3)), "candidate.validate"))
            if decision != 1:
                raise RuntimeError(f"core rejected the native TestCase candidate: decision {decision}")
            affected_request = encode_record([
                bytes.fromhex(stored), bytes.fromhex(constructed["native_execution_profile_id"]),
                bytes(range(16)),
            ]).hex()
            symbol = failure_symbol(send(process, pending, request(
                "tests.affected", affected_request, 4, session, 3)))
            if symbol != "NATIVE_EXECUTOR_UNAVAILABLE":
                raise RuntimeError(f"native test route changed: {symbol}")
            after = record_fields(bytes.fromhex(checked(send(
                process, pending, request("workspace.open", "", 5, session, 3)), "workspace.open")), 8)
            if after[1].hex() != original_root or after[0] != before[0]:
                raise RuntimeError("probe unexpectedly changed the accepted workspace head")
            checked(send(process, pending, request("session.close", "", 6, session, 3)), "session.close")
            assert process.stdin is not None
            process.stdin.close()
            if process.wait(timeout=30):
                raise RuntimeError("core v3 endpoint exited unsuccessfully")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        if json.loads((project / ".sley-tools/state.json").read_text(encoding="utf-8")) != state:
            raise RuntimeError("probe changed the toolkit's accepted project state")
    core_digest = sha256(core)
    route_digest = sha256(route_core)
    return {
        "contract": "sley-tools.f0-native-test-route-probe.v1",
        "result": "PASS",
        "passed_scope": "TestCase construction, core static validation, and honest native-route failure",
        "core_binary_sha256": core_digest,
        "route_core_binary_sha256": route_digest,
        "route_core_matches_project_core": route_digest == core_digest,
        "tools_binary_sha256": sha256(tools),
        "builder_binary_sha256": sha256(builder),
        "source_sha256": {str(path.relative_to(Path(__file__).resolve().parents[1])): sha256(path)
                          for path in (spec, cases, Path(__file__).resolve())},
        "candidate_id": constructed["candidate_id"],
        "cli_candidate_id": public["candidate_id"],
        "cli_static_candidate_validation": True,
        "declared_limits": constructed["declared_limits"],
        "grant_ceilings": constructed["grant_ceilings"],
        "native_plan_id": constructed["native_plan_id"],
        "native_plan_selected_test_count": constructed["native_plan_selected_test_count"],
        "native_plan_preflight": constructed["native_plan_preflight"],
        "test_entity_id": constructed["test_entity_id"],
        "case_id": case["id"],
        "native_execution_profile_id": constructed["native_execution_profile_id"],
        "candidate_validation_decision": decision,
        "native_test_route": "v3 tests.affected",
        "native_test_route_failure": symbol,
        "native_test_admission": "NOT_RUN: production native executor unavailable",
        "accepted_root_unchanged": True,
        "negative_builder_checks": ["expected type", "input arity", "stale base root"],
        "negative_public_checks": ["absent case", "non-graph project"],
        "compatibility_status": "requires_core_change",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "builder", "core", "spec", "cases", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--route-core", type=Path,
                        help="optional development core for v3 test route; --core remains the pinned project creator")
    args = parser.parse_args()
    result = probe(args.tools.resolve(), args.builder.resolve(), args.core.resolve(),
                   (args.route_core or args.core).resolve(), args.spec.resolve(), args.cases.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: native TestCase validated; v3 test execution unavailable")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"native TestCase probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
