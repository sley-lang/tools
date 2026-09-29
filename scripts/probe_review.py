#!/usr/bin/env python3
"""Exercise bounded native artifact review, report replay, and tamper refusal."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from validate_program_manifest import validate


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(tools: Path, *args: str, expected_exit: int = 0) -> dict:
    result = subprocess.run([str(tools), *args], capture_output=True, text=True, timeout=120, check=False)
    if result.returncode != expected_exit:
        raise RuntimeError(f"{' '.join(args[:2])} exited {result.returncode}, expected {expected_exit}: {result.stderr[:500]}")
    return json.loads(result.stdout if expected_exit == 0 else result.stderr)


def copy_package(project: Path, destination: Path) -> Path:
    destination.mkdir()
    (destination / "artifact").mkdir()
    manifest = json.loads((project / "program.json").read_text(encoding="utf-8"))
    shutil.copy2(project / "program.json", destination / "program.json")
    for binding in ("artifact", "imported_source_artifact"):
        if manifest.get(binding):
            relative = manifest[binding]["path"]
            shutil.copy2(project / relative, destination / relative)
    return destination / "program.json"


def review_one(tools: Path, core: Path, artifact: Path, fixtures: Path, schema: Path,
               base: Path, program: str) -> dict:
    base.mkdir()
    project = base / "project"
    invoke(tools, "artifact", "import", str(artifact), str(project), "--core", str(core))
    invoke(tools, "artifact", "export", "--project", str(project),
           "--output", str(project / "artifact/before-review.json"), "--bind-manifest")
    before = copy_package(project, base / "before")
    if program == "release-readiness":
        edit = invoke(tools, "edit", "--project", str(project), "--max-blocking-findings", "2")
    else:
        edit = invoke(tools, "edit", "--project", str(project), "--require-memory-spare", "true")
    invoke(tools, "artifact", "export", "--project", str(project),
           "--output", str(project / "artifact/after-review.json"), "--bind-manifest")
    after = copy_package(project, base / "after")
    if validate(before, schema)["result"] != "PASS" or validate(after, schema)["result"] != "PASS":
        raise RuntimeError(f"{program} review package failed manifest schema validation")
    report_path = base / "review.json"
    generated = invoke(tools, "review", "--before", str(before), "--after", str(after),
                       "--core", str(core), "--cases", str(fixtures), "--output", str(report_path))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if generated["report_sha256"] != digest(report_path) or generated["changed_case_count"] < 1:
        raise RuntimeError(f"{program} review omitted a changed native case or report digest")
    structure = report["structural_change"]
    expected_shared = 20 if program == "release-readiness" else 22
    if structure["shared_entity_ids"] != expected_shared or len(structure["changed"]) != 1 or structure["added"] or structure["removed"]:
        raise RuntimeError(f"{program} review did not identify the one native object replacement")
    if report["interface_change"]["changed"] is not False or report["identity"]["before"]["artifact_sha256"] == report["identity"]["after"]["artifact_sha256"]:
        raise RuntimeError(f"{program} review misreported interface or exact artifact identities")
    policy = report["profile_interpretation"]
    if program == "release-readiness":
        if policy["before"]["max_blocking_findings"] != 0 or policy["after"]["max_blocking_findings"] != 2:
            raise RuntimeError("review missed Release Readiness threshold change")
    elif policy["before"]["require_memory_spare"] is not False or policy["after"]["require_memory_spare"] is not True:
        raise RuntimeError("review missed Resource Admission comparison change")
    for side in ("before", "after"):
        if report["validation"][side]["result"] != "PASS" or report["identity"][side]["manifest_sha256"] != digest(base / side / "program.json"):
            raise RuntimeError(f"{program} review omitted native validation or manifest identity")
    if report["validation"]["native_core_test_admission"] != "NOT_RUN: production native executor unavailable":
        raise RuntimeError(f"{program} review overstated native core tests")
    verified = invoke(tools, "review", "verify", str(report_path), "--before", str(before),
                      "--after", str(after), "--core", str(core), "--cases", str(fixtures))
    if verified["result"] != "PASS" or verified["report_sha256"] != digest(report_path):
        raise RuntimeError(f"{program} report did not reproduce from current inputs")
    falsified = base / "falsified-report.json"
    forged = json.loads(report_path.read_text(encoding="utf-8"))
    forged["behavioral_evidence"]["changed_case_count"] += 1
    falsified.write_text(json.dumps(forged), encoding="utf-8")
    failure = invoke(tools, "review", "verify", str(falsified), "--before", str(before),
                     "--after", str(after), "--core", str(core), "--cases", str(fixtures), expected_exit=4)
    if failure["code"] != "ST_EXECUTION_FAILED":
        raise RuntimeError(f"{program} falsified report passed verification")
    stale_manifest = base / "stale"
    shutil.copytree(base / "before", stale_manifest)
    stale = json.loads((stale_manifest / "program.json").read_text(encoding="utf-8"))
    stale["artifact"]["sha256"] = "0" * 64
    (stale_manifest / "program.json").write_text(json.dumps(stale), encoding="utf-8")
    failure = invoke(tools, "review", "--before", str(stale_manifest / "program.json"),
                     "--after", str(after), "--core", str(core), "--cases", str(fixtures),
                     "--output", str(base / "stale-report.json"), expected_exit=2)
    if failure["code"] != "ST_INPUT_INVALID" or (base / "stale-report.json").exists():
        raise RuntimeError(f"{program} stale manifest was accepted")
    false_interface = base / "false-interface"
    shutil.copytree(base / "before", false_interface)
    interface = json.loads((false_interface / "program.json").read_text(encoding="utf-8"))
    first_input = next(iter(interface["inputs"]))
    interface["inputs"][first_input] = "Bool" if interface["inputs"][first_input] != "Bool" else "SInt64"
    (false_interface / "program.json").write_text(json.dumps(interface), encoding="utf-8")
    failure = invoke(tools, "review", "--before", str(false_interface / "program.json"),
                     "--after", str(after), "--core", str(core), "--cases", str(fixtures),
                     "--output", str(base / "false-interface-report.json"), expected_exit=2)
    if failure["code"] != "ST_INPUT_INVALID" or (base / "false-interface-report.json").exists():
        raise RuntimeError(f"{program} false manifest input type was accepted")
    altered = base / "altered"
    shutil.copytree(base / "before", altered)
    bound = json.loads((altered / "program.json").read_text(encoding="utf-8"))["artifact"]["path"]
    selected = altered / bound
    selected.write_bytes(selected.read_bytes() + b" ")
    failure = invoke(tools, "review", "--before", str(altered / "program.json"),
                     "--after", str(after), "--core", str(core), "--cases", str(fixtures),
                     "--output", str(base / "altered-report.json"), expected_exit=2)
    if failure["code"] != "ST_INPUT_INVALID" or (base / "altered-report.json").exists():
        raise RuntimeError(f"{program} altered artifact was accepted")
    return {"program_id": program, "source_artifact_sha256": digest(artifact),
            "fixtures_sha256": digest(fixtures), "schema_sha256": digest(schema),
            "before_artifact_sha256": report["identity"]["before"]["artifact_sha256"],
            "after_artifact_sha256": report["identity"]["after"]["artifact_sha256"],
            "report_sha256": digest(report_path), "edited_candidate_id": edit["candidate_id"],
            "shared_entity_ids": expected_shared, "changed_entity_versions": 1,
            "executed_cases": report["behavioral_evidence"]["case_count"],
            "changed_cases": report["behavioral_evidence"]["changed_case_count"],
            "same_interface": True, "manifest_validation_passed": True,
            "report_recomputed": True, "falsified_report_rejected": True,
            "stale_manifest_rejected": True, "false_interface_rejected": True,
            "altered_artifact_rejected": True,
            "native_core_test_admission": "NOT_RUN: production native executor unavailable"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--release-artifact", required=True, type=Path)
    parser.add_argument("--release-fixtures", required=True, type=Path)
    parser.add_argument("--resource-artifact", required=True, type=Path)
    parser.add_argument("--resource-fixtures", required=True, type=Path)
    parser.add_argument("--schema", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    tools, core, schema = args.tools.resolve(), args.core.resolve(), args.schema.resolve()
    with tempfile.TemporaryDirectory(prefix="sley-review-probe-") as temporary:
        root = Path(temporary)
        programs = [
            review_one(tools, core, args.release_artifact.resolve(), args.release_fixtures.resolve(), schema,
                       root / "release", "release-readiness"),
            review_one(tools, core, args.resource_artifact.resolve(), args.resource_fixtures.resolve(), schema,
                       root / "resource", "resource-admission"),
        ]
    report = {"contract": "sley-tools.review-probe.v1", "result": "PASS",
              "core_binary_sha256": digest(core), "tools_binary_sha256": digest(tools),
              "builder_binary_sha256": digest(tools.with_name("sley-tools-native-builder")),
              "programs": programs, "scope": "bounded local artifact review; CI Action and native core tests pending"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: both native worked edits reviewed and tamper checks passed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"review probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
