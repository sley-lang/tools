#!/usr/bin/env python3
"""Replay checked-in native reviews with copied binaries and no source mount."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def isolated(root: Path, *command: str, timeout: int = 120) -> str:
    args = [
        "bwrap", "--unshare-net", "--die-with-parent", "--clearenv",
        "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev",
        "--tmpfs", "/tmp", "--bind", str(root), "/work",
        "--setenv", "HOME", "/tmp", "--setenv", "PATH", "/usr/bin",
        "--chdir", "/work", *command,
    ]
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"isolated {' '.join(command[:2])} exited {result.returncode}: {result.stderr[:550]}")
    if result.stderr:
        raise RuntimeError(f"isolated command wrote unexpected stderr: {result.stderr[:300]}")
    return result.stdout.strip()


def check_files(root: Path, manifest: dict) -> None:
    programs = manifest.get("programs")
    if manifest.get("contract") != "sley-tools.review-fixtures.v1" or not isinstance(programs, list) or len(programs) != 2:
        raise RuntimeError("checked-in Review fixture manifest is incomplete")
    expected_files = {"manifest.json"}
    if root.is_symlink():
        raise RuntimeError("checked-in Review fixture root is a symlink")
    if {entry["program_id"] for entry in programs} != {"release-readiness", "resource-admission"}:
        raise RuntimeError("checked-in Review fixture programs differ")
    for entry in programs:
        program = entry["program_id"]
        if program not in ("release-readiness", "resource-admission") or entry["changed_entity_versions"] != 1:
            raise RuntimeError("Review fixture manifest names an unsupported program")
        if (root / program).is_symlink():
            raise RuntimeError(f"checked-in Review fixture contains a symlink directory: {program}")
        for side in ("before", "after"):
            package = entry[side]
            package_root = root / program / side
            if package_root.is_symlink() or (package_root / "artifact").is_symlink():
                raise RuntimeError(f"checked-in Review fixture contains a symlink directory: {program}/{side}")
            for relative, expected in package["files"].items():
                parts = Path(relative).parts
                if relative != "program.json" and (len(parts) != 2 or parts[0] != "artifact" or parts[1] in (".", "..")):
                    raise RuntimeError("checked-in Review fixture contains an unsafe file path")
                selected = root / program / side / relative
                if not selected.is_file() or selected.is_symlink() or digest(selected) != expected:
                    raise RuntimeError(f"checked-in Review fixture differs: {program}/{side}/{relative}")
                expected_files.add(f"{program}/{side}/{relative}")
            document = json.loads((root / program / side / "program.json").read_text(encoding="utf-8"))
            if document["entrypoint"]["state_root"] != package["state_root"] or document["entrypoint"]["function_id"] != package["function_id"]:
                raise RuntimeError(f"checked-in Review fixture identity differs: {program}/{side}")
    actual_files = set()
    for selected in root.rglob("*"):
        if selected.is_symlink():
            raise RuntimeError(f"checked-in Review fixture contains a symlink: {selected.relative_to(root)}")
        if selected.is_file():
            actual_files.add(selected.relative_to(root).as_posix())
    if actual_files != expected_files:
        raise RuntimeError("checked-in Review fixture contains unlisted or missing files")


def probe(tools: Path, core_root: Path, fixtures: Path, release_cases: Path, resource_cases: Path) -> dict:
    fixture_manifest = fixtures / "manifest.json"
    manifest = json.loads(fixture_manifest.read_text(encoding="utf-8"))
    check_files(fixtures, manifest)
    core = core_root / "bin/sley"
    if manifest["core_binary_sha256"] != digest(core):
        raise RuntimeError("Review fixtures were authored with a different core binary")
    with tempfile.TemporaryDirectory(prefix="sley-review-isolated-") as temporary:
        root = Path(temporary)
        (root / "bin").mkdir()
        (root / "cases").mkdir()
        (root / "reports").mkdir()
        shutil.copy2(tools, root / "bin/sley-tools")
        shutil.copy2(tools.with_name("sley-tools-native-builder"), root / "bin/sley-tools-native-builder")
        shutil.copytree(core_root, root / "core")
        shutil.copytree(fixtures, root / "fixtures")
        shutil.copy2(release_cases, root / "cases/release.json")
        shutil.copy2(resource_cases, root / "cases/resource.json")
        if isolated(root, "/usr/bin/test", "!", "-e", "/sandbox/forbidden-workspace"):
            raise RuntimeError("source checkout visibility check returned output")
        doctor = json.loads(isolated(root, "/work/bin/sley-tools", "doctor", "--core", "/work/core/bin/sley"))
        if doctor["status"] != "compatible" or doctor["core"]["binary_sha256"] != digest(core):
            raise RuntimeError("copied core failed identity check in isolated environment")
        results = []
        for program, case_name, count, entities in (("release-readiness", "release", 48, 20),
                                                     ("resource-admission", "resource", 49, 22)):
            folder = f"/work/fixtures/{program}"
            args = ["--before", f"{folder}/before/program.json", "--after", f"{folder}/after/program.json",
                    "--core", "/work/core/bin/sley", "--cases", f"/work/cases/{case_name}.json"]
            output = f"/work/reports/{program}.json"
            generated = json.loads(isolated(root, "/work/bin/sley-tools", "review", *args,
                                            "--output", output))
            report_path = root / "reports" / f"{program}.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if generated["report_sha256"] != digest(report_path) or report["behavioral_evidence"]["case_count"] != count:
                raise RuntimeError(f"isolated {program} report omitted exact digest or full case matrix")
            if report["structural_change"]["shared_entity_ids"] != entities or len(report["structural_change"]["changed"]) != 1:
                raise RuntimeError(f"isolated {program} report lost one native entity edit")
            if report["validation"]["native_core_test_admission"] != "NOT_RUN: production native executor unavailable":
                raise RuntimeError(f"isolated {program} report overstated native core tests")
            verified = json.loads(isolated(root, "/work/bin/sley-tools", "review", "verify", output, *args))
            if verified["result"] != "PASS" or verified["report_sha256"] != digest(report_path):
                raise RuntimeError(f"isolated {program} report failed exact replay")
            results.append({"program_id": program, "case_count": count,
                            "changed_case_count": report["behavioral_evidence"]["changed_case_count"],
                            "shared_entity_ids": entities, "changed_entity_versions": 1,
                            "report_sha256": digest(report_path),
                            "before_artifact_sha256": report["identity"]["before"]["artifact_sha256"],
                            "after_artifact_sha256": report["identity"]["after"]["artifact_sha256"],
                            "verified_from_copied_inputs": True})
    return {"contract": "sley-tools.review-isolated-probe.v1", "result": "PASS",
            "environment": "bubblewrap new mount and network namespaces on the same Linux host",
            "source_checkout_mounted": False, "network_namespace_isolated": True,
            "copied_tools_binary_sha256": digest(tools), "copied_core_binary_sha256": digest(core),
            "copied_builder_binary_sha256": digest(tools.with_name("sley-tools-native-builder")),
            "fixture_manifest_sha256": digest(fixture_manifest), "release_cases_sha256": digest(release_cases),
            "resource_cases_sha256": digest(resource_cases), "programs": results,
            "native_core_test_admission": "NOT_RUN: production native executor unavailable"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--core-root", required=True, type=Path)
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--release-cases", required=True, type=Path)
    parser.add_argument("--resource-cases", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = probe(args.tools.resolve(), args.core_root.resolve(), args.fixtures.resolve(),
                   args.release_cases.resolve(), args.resource_cases.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: both checked-in native reviews replayed outside the source checkout")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"isolated Review probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
