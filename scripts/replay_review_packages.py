#!/usr/bin/env python3
"""Replay both checked-in native Review packages from copied inputs on one host."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from probe_review_isolated import check_files, digest


def invoke(tools: Path, *args: str) -> dict:
    result = subprocess.run([str(tools), *args], capture_output=True, text=True, timeout=150, check=False)
    if result.returncode:
        raise RuntimeError(f"review command exited {result.returncode}: {result.stderr[:500]}")
    if result.stderr:
        raise RuntimeError(f"review command wrote unexpected stderr: {result.stderr[:300]}")
    return json.loads(result.stdout)


def replay(tools: Path, core_root: Path, fixtures: Path, release_cases: Path,
           resource_cases: Path) -> dict:
    manifest_path = fixtures / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    check_files(fixtures, manifest)
    core = core_root / "bin/sley"
    if manifest["core_binary_sha256"] != digest(core):
        raise RuntimeError("review fixture core digest differs from copied core")
    doctor = invoke(tools, "doctor", "--core", str(core))
    if doctor["status"] != "compatible" or doctor["core"]["binary_sha256"] != digest(core):
        raise RuntimeError("copied core is incompatible on this host")
    programs = []
    with tempfile.TemporaryDirectory(prefix="sley-review-replay-") as temporary:
        for program, case_path, count, entities in (
            ("release-readiness", release_cases, 48, 20),
            ("resource-admission", resource_cases, 49, 22),
        ):
            before = fixtures / program / "before/program.json"
            after = fixtures / program / "after/program.json"
            report_path = Path(temporary) / f"{program}.json"
            args = ("--before", str(before), "--after", str(after),
                    "--core", str(core), "--cases", str(case_path))
            generated = invoke(tools, "review", *args, "--output", str(report_path))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report_sha = digest(report_path)
            if generated["report_sha256"] != report_sha or report["behavioral_evidence"]["case_count"] != count:
                raise RuntimeError(f"{program} review omitted report identity or cases")
            if report["structural_change"]["shared_entity_ids"] != entities or len(report["structural_change"]["changed"]) != 1:
                raise RuntimeError(f"{program} review did not show the exact native edit")
            if report["validation"]["native_core_test_admission"] != "NOT_RUN: production native executor unavailable":
                raise RuntimeError(f"{program} review overstated native core tests")
            verified = invoke(tools, "review", "verify", str(report_path), *args)
            if verified["result"] != "PASS" or verified["report_sha256"] != report_sha:
                raise RuntimeError(f"{program} report failed recomputation")
            programs.append({
                "program_id": program, "case_count": count,
                "changed_case_count": report["behavioral_evidence"]["changed_case_count"],
                "shared_entity_ids": entities, "changed_entity_versions": 1,
                "before_artifact_sha256": report["identity"]["before"]["artifact_sha256"],
                "after_artifact_sha256": report["identity"]["after"]["artifact_sha256"],
                "report_sha256": report_sha, "verified_from_copied_inputs": True,
            })
    return {
        "contract": "sley-tools.review-host-replay.v1", "result": "PASS",
        "host_name": platform.node(), "platform": platform.system(), "machine": platform.machine(),
        "tools_binary_sha256": digest(tools),
        "builder_binary_sha256": digest(tools.with_name("sley-tools-native-builder")),
        "core_binary_sha256": digest(core),
        "fixture_manifest_sha256": digest(manifest_path),
        "release_cases_sha256": digest(release_cases),
        "resource_cases_sha256": digest(resource_cases), "programs": programs,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", type=Path, required=True)
    parser.add_argument("--core-root", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--release-cases", type=Path, required=True)
    parser.add_argument("--resource-cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = replay(args.tools.resolve(), args.core_root.resolve(), args.fixtures.resolve(),
                    args.release_cases.resolve(), args.resource_cases.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: both native reviews replayed on {report['host_name']}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Review host replay failed: {error}", file=sys.stderr)
        raise SystemExit(1)
