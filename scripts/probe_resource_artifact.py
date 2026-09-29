#!/usr/bin/env python3
"""Verify Resource Admission transfer, fresh authority, exact replay, and rejection paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/python"))
from sley_tools import open_runtime  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(tools: Path, *args: str, expected_exit: int = 0) -> dict:
    result = subprocess.run([str(tools), *args], capture_output=True, text=True, timeout=60, check=False)
    if result.returncode != expected_exit:
        raise RuntimeError(f"{' '.join(args[:2])} exited {result.returncode}, expected {expected_exit}: {result.stderr[:400]}")
    return json.loads(result.stdout if expected_exit == 0 else result.stderr)


def state(project: Path) -> dict:
    return json.loads((project / ".sley-tools/state.json").read_text(encoding="utf-8"))


def matrix(tools: Path, core: Path, project: Path, cases: list[dict]) -> list[dict]:
    observed = []
    with open_runtime(tools=tools, core=core) as runtime:
        program = runtime.open_project(project)
        workers = set()
        for case in cases:
            inputs = {name: int(value) for name, value in case["input"].items()}
            result = program.call("evaluate", inputs=inputs)
            expected = {name: int(value) if name.endswith("_memory_mib") else value for name, value in case["expected"].items()}
            if result.value != expected or result.metadata["state_root"] != program.state_root:
                raise RuntimeError(f"imported native result differs from independent case {case['id']}")
            observed.append(result.value)
            workers.add(result.metadata["worker_pid"])
        if len(workers) != 1:
            raise RuntimeError("imported artifact did not reuse one native worker")
    return observed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    tools, core, artifact, fixtures_path = (item.resolve() for item in (args.tools, args.core, args.artifact, args.fixtures))
    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
    if fixtures.get("contract") != "sley-tools.resource-admission-fixtures.v1" or len(fixtures.get("cases", [])) != 49:
        raise RuntimeError("Resource Admission fixture contract differs")
    raw = artifact.read_bytes()
    payload = json.loads(raw)
    if payload.get("contract") != "sley-tools.native-program-artifact.v0" or payload.get("program_id") != "resource-admission" or len(payload.get("objects", [])) != 22:
        raise RuntimeError("catalog artifact has the wrong native profile")
    if {kind: sum(item["kind"] == kind for item in payload["objects"]) for kind in range(4, 10)} != {4: 1, 5: 1, 6: 4, 7: 1, 8: 14, 9: 1}:
        raise RuntimeError("catalog artifact contains an unexpected native inventory")
    if any(token in raw for token in (b"principal_id", b"policy_root", b"workspace_id", str(artifact.parent).encode())):
        raise RuntimeError("artifact contains source authority or local path")
    with tempfile.TemporaryDirectory(prefix="sley-resource-artifact-probe-") as work:
        base = Path(work)
        bin_dir = base / "bin"
        bin_dir.mkdir()
        copied_tools = bin_dir / "sley-tools"
        shutil.copy2(tools, copied_tools)
        shutil.copy2(tools.with_name("sley-tools-native-builder"), bin_dir / "sley-tools-native-builder")
        first, second, third = (base / name for name in ("first", "second", "third"))
        for destination in (first, second):
            report = invoke(copied_tools, "artifact", "import", str(artifact), str(destination), "--core", str(core))
            if report["verification"]["external_assertions_against_native_execution"] != 12:
                raise RuntimeError("fresh Resource Admission import lacked native assertions")
            if (destination / "artifact/native-program.json").read_bytes() != raw:
                raise RuntimeError("imported project did not preserve source artifact exactly")
        a, b = state(first), state(second)
        for identity in ("workspace_id", "principal_id", "state_root", "function_id"):
            if a[identity] == b[identity]:
                raise RuntimeError(f"independent imports reused {identity}")
        first_results = matrix(copied_tools, core, first, fixtures["cases"])
        if first_results != matrix(copied_tools, core, second, fixtures["cases"]):
            raise RuntimeError("fresh imports differ in native typed behavior")
        with open_runtime(tools=copied_tools, core=core) as runtime:
            loaded = runtime.load(artifact=artifact)
            wide = loaded.call("evaluate", inputs={
                "requested_workers": 1, "available_workers": 1,
                "requested_memory_mib": 2**53 + 1, "available_memory_mib": 2**53,
            })
            if wide.value["requested_memory_mib"] != 2**53 + 1 or wide.value["allow"] is not False:
                raise RuntimeError("Python SDK artifact load lost exact signed execution")
        node = subprocess.run([
            "node", str(Path(__file__).with_name("probe_resource_typescript.mjs")),
            f"tools={copied_tools}", f"core={core}", f"artifact={artifact}", f"fixtures={fixtures_path}",
        ], capture_output=True, text=True, timeout=60, check=False)
        if node.returncode or json.loads(node.stdout).get("result") != "PASS":
            raise RuntimeError(f"TypeScript imported-artifact replay failed: {node.stderr[:350]}")
        second_artifact = base / "second-generation.json"
        exported = invoke(copied_tools, "artifact", "export", "--project", str(first), "--output", str(second_artifact))
        if exported["object_count"] != 22 or exported["sha256"] != sha256(second_artifact):
            raise RuntimeError("rebased export has wrong native inventory or digest")
        invoke(copied_tools, "artifact", "import", str(second_artifact), str(third), "--core", str(core))
        if invoke(copied_tools, "test", "--project", str(third))["external_assertions_against_native_execution"] != 12:
            raise RuntimeError("second-generation Resource Admission artifact failed replay")
        changed_type = json.loads(raw)
        changed_type["result_fields"][-1]["type"] = "Bool"
        wrong_type = base / "wrong-type.json"
        wrong_type.write_text(json.dumps(changed_type), encoding="utf-8")
        rejection = invoke(copied_tools, "artifact", "import", str(wrong_type), str(base / "wrong-type-project"), "--core", str(core), expected_exit=4)
        if rejection["code"] != "ST_IMPORT_UNSUPPORTED":
            raise RuntimeError("import accepted false signed result metadata")
        damaged = json.loads(raw)
        stored = damaged["objects"][0]["stored_hex"]
        damaged["objects"][0]["stored_hex"] = ("0" if stored[0] != "0" else "1") + stored[1:]
        tampered = base / "tampered.json"
        tampered.write_text(json.dumps(damaged), encoding="utf-8")
        rejection = invoke(copied_tools, "artifact", "import", str(tampered), str(base / "tampered-project"), "--core", str(core), expected_exit=4)
        if rejection["code"] != "ST_IMPORT_UNSUPPORTED":
            raise RuntimeError("import accepted tampered native object")
        report = {
            "contract": "sley-tools.resource-artifact-probe.v1", "result": "PASS",
            "core_binary_sha256": sha256(core), "artifact_sha256": sha256(artifact),
            "fixtures_sha256": sha256(fixtures_path), "native_object_count": 22,
            "fresh_workspace_and_principal": True, "source_authority_excluded": True,
            "imported_python_cases_per_workspace": 49, "imported_typescript_cases": 49,
            "typed_source_import_equivalent": True, "second_generation_import_passed": True,
            "both_sdks_loaded_native_artifact": True,
            "tampered_object_and_false_type_rejected": True,
            "native_test_admission": "NOT_RUN: production native executor unavailable",
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: Resource Admission native transfer and exact SDK replay passed")


if __name__ == "__main__":
    main()
