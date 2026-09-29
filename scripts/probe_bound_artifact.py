#!/usr/bin/env python3
"""Verify a worked native edit can produce a manifest-bound portable package."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from validate_program_manifest import validate


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(tools: Path, *args: str, exit_code: int = 0) -> dict:
    result = subprocess.run([str(tools), *args], capture_output=True, text=True, timeout=60, check=False)
    if result.returncode != exit_code:
        raise RuntimeError(f"{' '.join(args[:2])} exited {result.returncode}, expected {exit_code}: {result.stderr[:450]}")
    return json.loads(result.stdout if exit_code == 0 else result.stderr)


def package_one(tools: Path, core: Path, source: Path, schema: Path, root: Path, program: str) -> dict:
    project = root / program
    replay = root / f"{program}-replay"
    invoke(tools, "artifact", "import", str(source), str(project), "--core", str(core))
    if program == "release-readiness":
        edited = invoke(tools, "edit", "--project", str(project), "--max-blocking-findings", "2")
        input_value = {"test_run_completed": True, "tests_failed": 0, "artifact_verified": True, "blocking_findings": 2}
    else:
        edited = invoke(tools, "edit", "--project", str(project), "--require-memory-spare", "true")
        input_value = {"requested_workers": 1, "available_workers": 1,
                       "requested_memory_mib": 4096, "available_memory_mib": 4096}
    manifest = project / "program.json"
    before = sha256(manifest)
    outside = root / f"{program}-unbound.json"
    rejected = invoke(tools, "artifact", "export", "--project", str(project),
                      "--output", str(outside), "--bind-manifest", exit_code=2)
    if rejected.get("code") != "ST_INPUT_INVALID" or outside.exists() or sha256(manifest) != before:
        raise RuntimeError(f"{program} accepted an out-of-project manifest binding")
    native = project / "artifact/edited-native-program.json"
    exported = invoke(tools, "artifact", "export", "--project", str(project),
                      "--output", str(native), "--bind-manifest")
    checked = validate(manifest, schema)
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    state = json.loads((project / ".sley-tools/state.json").read_text(encoding="utf-8"))
    artifact_data = json.loads(native.read_text(encoding="utf-8"))
    if checked["result"] != "PASS" or exported["manifest_bound"] is not True or exported["manifest_sha256"] != sha256(manifest):
        raise RuntimeError(f"{program} bound manifest failed schema or digest verification")
    if manifest_data["artifact"]["sha256"] != sha256(native) or manifest_data["artifact"]["path"] != "artifact/edited-native-program.json":
        raise RuntimeError(f"{program} manifest did not bind the newly exported artifact")
    if manifest_data["entrypoint"]["state_root"] != state["state_root"] or artifact_data["source_state_root"] != state["state_root"]:
        raise RuntimeError(f"{program} accepted root differs between state, manifest, and artifact")
    if manifest_data["entrypoint"]["function_id"] != artifact_data["source_function_id"]:
        raise RuntimeError(f"{program} function identity differs between manifest and artifact")
    stable = sha256(manifest)
    duplicate = invoke(tools, "artifact", "export", "--project", str(project),
                       "--output", str(native), "--bind-manifest", exit_code=2)
    if duplicate.get("code") != "ST_INPUT_INVALID" or sha256(manifest) != stable:
        raise RuntimeError(f"{program} overwrote an existing artifact or its manifest")
    imported = invoke(tools, "artifact", "import", str(native), str(replay), "--core", str(core))
    assert imported["verification"]["result"] == "PASS"
    case = replay / "inputs/review-boundary.json"
    case.write_text(json.dumps(input_value), encoding="utf-8")
    observed = invoke(tools, "run", "--project", str(replay), "--input", str(case))
    expected = program == "release-readiness"
    if observed["result"]["allow"] is not expected or observed["state_root"] != imported["program"]["entrypoint"]["state_root"]:
        raise RuntimeError(f"{program} edited native behavior did not survive package replay")
    return {
        "program_id": program, "source_artifact_sha256": sha256(source),
        "edited_candidate_id": edited["candidate_id"], "edited_root": edited["new_root"],
        "bound_artifact_sha256": sha256(native), "bound_manifest_sha256": sha256(manifest),
        "manifest_schema_result": checked["result"],
        "boundary_allow_after_replay": observed["result"]["allow"],
        "out_of_project_binding_rejected": True, "existing_file_refused": True,
        "authority_rebased_import_passed": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--release-artifact", required=True, type=Path)
    parser.add_argument("--resource-artifact", required=True, type=Path)
    parser.add_argument("--schema", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    tools, core = args.tools.resolve(), args.core.resolve()
    with tempfile.TemporaryDirectory(prefix="sley-bound-artifact-probe-") as temporary:
        root = Path(temporary)
        programs = [
            package_one(tools, core, args.release_artifact.resolve(), args.schema.resolve(), root, "release-readiness"),
            package_one(tools, core, args.resource_artifact.resolve(), args.schema.resolve(), root, "resource-admission"),
        ]
        link_project = root / "symlink-project"
        invoke(tools, "init", str(link_project), "--template", "release-readiness", "--core", str(core))
        outside = root / "outside"
        outside.mkdir()
        (link_project / "artifact").symlink_to(outside, target_is_directory=True)
        initial_manifest = sha256(link_project / "program.json")
        refused = invoke(tools, "artifact", "export", "--project", str(link_project),
                         "--output", str(link_project / "artifact/escape.json"), "--bind-manifest", exit_code=2)
        if refused.get("code") != "ST_INPUT_INVALID" or (outside / "escape.json").exists() or sha256(link_project / "program.json") != initial_manifest:
            raise RuntimeError("manifest binding followed a symlink artifact directory")
    result = {"contract": "sley-tools.bound-artifact-probe.v1", "result": "PASS",
              "core_binary_sha256": sha256(core), "schema_sha256": sha256(args.schema.resolve()),
              "programs": programs, "symlink_artifact_directory_rejected": True,
              "native_test_admission": "NOT_RUN: production native executor unavailable"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: both edited native artifacts are manifest-bound and replayable")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"bound artifact probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
