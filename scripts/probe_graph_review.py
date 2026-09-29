#!/usr/bin/env python3
"""Review native edits to schema- and Python-imported graph artifacts."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from validate_program_manifest import validate

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/python"))
from sley_tools.python_importer import import_project  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(tools: Path, *args: object, reject: str | None = None) -> dict:
    command = [str(tools), *map(str, args)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120,
                            check=False)
    if reject is not None:
        if result.returncode == 0:
            raise RuntimeError(f"invalid Review command succeeded: {command[1:3]}")
        failure = json.loads(result.stderr)
        if failure.get("code") != reject:
            raise RuntimeError(f"Review rejection differs: {failure}")
        return failure
    if result.returncode:
        raise RuntimeError(f"Review command failed: {result.stderr[:600]}")
    return json.loads(result.stdout)


def snapshot(project: Path, destination: Path) -> Path:
    destination.mkdir()
    (destination / "artifact").mkdir()
    manifest = json.loads((project / "program.json").read_text())
    shutil.copy2(project / "program.json", destination / "program.json")
    if manifest.get("graph_recipe"):
        recipe = manifest["graph_recipe"]["path"]
        (destination / recipe).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(project / recipe, destination / recipe)
    for binding in ("artifact", "imported_source_artifact"):
        if manifest.get(binding):
            relative = manifest[binding]["path"]
            shutil.copy2(project / relative, destination / relative)
    return destination / "program.json"


def edit(tools: Path, project: Path, scratch: Path, constant: str,
         value: str, changed_cases: Path) -> dict:
    old_root = invoke(tools, "inspect", "--project", project)["accepted_state_root"]
    value_file = scratch / f"{constant}-new-value.json"
    value_file.write_text(json.dumps(value) + "\n")
    proposal = invoke(tools, "propose", "--project", project, "--constant", constant,
                      "--value-file", value_file, "--tests", changed_cases)
    if invoke(tools, "inspect", "--project", project)["accepted_state_root"] != old_root:
        raise RuntimeError("graph proposal changed accepted root before commit")
    validation = invoke(tools, "validate", "--project", project)
    commit = invoke(tools, "commit", "--project", project)
    tested = invoke(tools, "test", "--project", project)
    if (not proposal["candidate_id"] == validation["candidate_id"] == commit["candidate_id"]
            or commit["old_root"] != old_root or commit["new_root"] == old_root
            or tested["result"] != "PASS"):
        raise RuntimeError("graph edit failed the native proposal lifecycle")
    return commit


def review_pair(tools: Path, core: Path, schema: Path, project: Path,
                scratch: Path, fixtures: Path, changed_cases: Path,
                constant: str, before_value: str, after_value: str) -> dict:
    scratch.mkdir()
    before_artifact = project / "artifact/review-before.json"
    invoke(tools, "artifact", "export", "--project", project,
           "--output", before_artifact, "--bind-manifest")
    before = snapshot(project, scratch / "before")
    commit = edit(tools, project, scratch, constant, after_value, changed_cases)
    after_artifact = project / "artifact/review-after.json"
    invoke(tools, "artifact", "export", "--project", project,
           "--output", after_artifact, "--bind-manifest")
    after = snapshot(project, scratch / "after")
    if validate(before, schema)["result"] != "PASS" or validate(after, schema)["result"] != "PASS":
        raise RuntimeError("graph Review package manifest failed schema validation")
    report_path = scratch / "review.json"
    generated = invoke(tools, "review", "--before", before, "--after", after,
                       "--core", core, "--cases", fixtures, "--output", report_path)
    report = json.loads(report_path.read_text())
    policy = report["profile_interpretation"]
    structural = report["structural_change"]
    if (generated["contract"] != "sley-tools.review-result.v1"
            or report["contract"] != "sley-tools.review.v1"
            or generated["report_sha256"] != digest(report_path)
            or generated["changed_case_count"] < 1
            or policy["before"]["named_constants"][constant] != before_value
            or policy["after"]["named_constants"][constant] != after_value
            or len(structural["changed"]) != 1 or structural["added"] or structural["removed"]
            or report["interface_change"]["changed"] is not False
            or report["behavioral_evidence"]["baseline_fixture_matches"] is not True
            or report["validation"]["native_core_test_admission"]
               != "NOT_RUN: production native executor unavailable"):
        raise RuntimeError("graph Review omitted exact edit, policy, or behavioral evidence")
    verified = invoke(tools, "review", "verify", report_path, "--before", before,
                      "--after", after, "--core", core, "--cases", fixtures)
    if (verified["contract"] != "sley-tools.review-verification.v1"
            or verified["result"] != "PASS"
            or verified["report_sha256"] != digest(report_path)):
        raise RuntimeError("graph Review report failed recomputation")
    falsified = scratch / "falsified.json"
    forged = copy.deepcopy(report)
    forged["behavioral_evidence"]["changed_case_count"] += 1
    falsified.write_text(json.dumps(forged))
    invoke(tools, "review", "verify", falsified, "--before", before, "--after", after,
           "--core", core, "--cases", fixtures, reject="ST_EXECUTION_FAILED")
    stale = scratch / "stale"
    shutil.copytree(scratch / "before", stale)
    stale_manifest = json.loads((stale / "program.json").read_text())
    stale_manifest["artifact"]["sha256"] = "0" * 64
    (stale / "program.json").write_text(json.dumps(stale_manifest))
    invoke(tools, "review", "--before", stale / "program.json", "--after", after,
           "--core", core, "--cases", fixtures, "--output", scratch / "stale-report.json",
           reject="ST_INPUT_INVALID")
    false_interface = scratch / "false-interface"
    shutil.copytree(scratch / "before", false_interface)
    false_manifest = json.loads((false_interface / "program.json").read_text())
    first = next(iter(false_manifest["inputs"]))
    false_manifest["inputs"][first] = "Bool" if false_manifest["inputs"][first] != "Bool" else "SInt64"
    (false_interface / "program.json").write_text(json.dumps(false_manifest))
    invoke(tools, "review", "--before", false_interface / "program.json", "--after", after,
           "--core", core, "--cases", fixtures, "--output", scratch / "false-interface-report.json",
           reject="ST_INPUT_INVALID")
    altered = scratch / "altered"
    shutil.copytree(scratch / "before", altered)
    artifact_relative = json.loads((altered / "program.json").read_text())["artifact"]["path"]
    altered_artifact = altered / artifact_relative
    altered_artifact.write_bytes(altered_artifact.read_bytes() + b" ")
    invoke(tools, "review", "--before", altered / "program.json", "--after", after,
           "--core", core, "--cases", fixtures, "--output", scratch / "altered-report.json",
           reject="ST_INPUT_INVALID")
    altered_recipe = scratch / "altered-recipe"
    shutil.copytree(scratch / "before", altered_recipe)
    recipe_file = altered_recipe / "build/graph.json"
    recipe_file.write_bytes(recipe_file.read_bytes() + b" ")
    invoke(tools, "review", "--before", altered_recipe / "program.json", "--after", after,
           "--core", core, "--cases", fixtures, "--output", scratch / "altered-recipe-report.json",
           reject="ST_INPUT_INVALID")
    replay = scratch / "replay"
    imported = invoke(tools, "artifact", "import", after_artifact, replay, "--core", core)
    if (imported["verification"]["result"] != "PASS"
            or imported["program"]["entrypoint"]["state_root"] == commit["new_root"]):
        raise RuntimeError("reviewed graph artifact failed authority-rebased replay")
    return {
        "program_id": report["identity"]["program_id"],
        "before_manifest_sha256": digest(before), "after_manifest_sha256": digest(after),
        "before_artifact_sha256": digest(before_artifact),
        "after_artifact_sha256": digest(after_artifact),
        "fixtures_sha256": digest(fixtures), "report_sha256": digest(report_path),
        "old_root": commit["old_root"], "new_root": commit["new_root"],
        "replay_root": imported["program"]["entrypoint"]["state_root"],
        "candidate_id": commit["candidate_id"], "receipt_id": commit["receipt_id"],
        "native_changed_entities": len(structural["changed"]),
        "executed_cases": report["behavioral_evidence"]["case_count"],
        "changed_cases": report["behavioral_evidence"]["changed_case_count"],
        "verified_report": True, "falsified_report_rejected": True,
        "stale_manifest_rejected": True, "false_interface_rejected": True,
        "altered_artifact_rejected": True, "altered_recipe_rejected": True,
        "edited_artifact_replayed": True,
    }


def schema_cases(package: Path, destination: Path) -> None:
    document = json.loads((package / "tests/cases.json").read_text())
    changed = json.loads((package / "tests/max-12-cases.json").read_text())
    for row in changed["cases"]:
        if row["id"] in ("new-interior", "new-maximum"):
            baseline = copy.deepcopy(row)
            baseline["expected"]["valid"] = False
            baseline["expected"]["p1_out_of_bounds"] = True
            document["cases"].append(baseline)
    destination.write_text(json.dumps(document, sort_keys=True) + "\n")


def primitive_graph(tools: Path, core: Path, schema: Path, scratch: Path) -> dict:
    scratch.mkdir()
    recipe = ROOT / "examples/graph/unsigned-limit.json"
    cases = ROOT / "examples/graph/unsigned-limit-cases.json"
    project = scratch / "project"
    invoke(tools, "init", project, "--graph", recipe, "--tests", cases, "--core", core)
    artifact = project / "artifact/primitive-native.json"
    invoke(tools, "artifact", "export", "--project", project,
           "--output", artifact, "--bind-manifest")
    before = snapshot(project, scratch / "before")
    after = snapshot(project, scratch / "after")
    if validate(before, schema)["result"] != "PASS":
        raise RuntimeError("primitive graph package did not validate")
    report_path = scratch / "primitive-review.json"
    summary = invoke(tools, "review", "--before", before, "--after", after,
                     "--core", core, "--cases", cases, "--output", report_path)
    report = json.loads(report_path.read_text())
    if (summary["contract"] != "sley-tools.review-result.v1"
            or report["contract"] != "sley-tools.review.v1"
            or summary["changed_case_count"] != 0
            or report["interface_change"]["before"]["output"]["type"] != "Bool"
            or report["structural_change"]["changed"]
            or report["profile_interpretation"]["before"] != {"named_constants": {}}
            or invoke(tools, "review", "verify", report_path, "--before", before,
                      "--after", after, "--core", core, "--cases", cases)["result"] != "PASS"):
        raise RuntimeError("primitive graph Review lost its typed Boolean result")
    false_cases = json.loads(cases.read_text())
    false_cases["cases"][0]["expected"] = False
    false_path = scratch / "false-cases.json"
    false_path.write_text(json.dumps(false_cases))
    invoke(tools, "review", "--before", before, "--after", after,
           "--core", core, "--cases", false_path, "--output", scratch / "false-review.json",
           reject="ST_EXECUTION_FAILED")
    wrong_shape = json.loads(cases.read_text())
    wrong_shape["cases"][0]["input"] = {"count": "0", "limit": "0"}
    wrong_path = scratch / "wrong-shape.json"
    wrong_path.write_text(json.dumps(wrong_shape))
    invoke(tools, "review", "--before", before, "--after", after,
           "--core", core, "--cases", wrong_path, "--output", scratch / "wrong-review.json",
           reject="ST_INPUT_INVALID")
    return {"program_id": "unsigned-limit", "artifact_sha256": digest(artifact),
            "recipe_sha256": digest(recipe), "cases_sha256": digest(cases),
            "report_sha256": digest(report_path), "executed_cases": 4,
            "no_change_report_verified": True, "false_baseline_rejected": True,
            "wrong_shape_rejected": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "core", "schema", "validator-package", "python-source",
                 "python-domain", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    tools, core = args.tools.resolve(), args.core.resolve()
    package = args.validator_package.resolve()
    with tempfile.TemporaryDirectory(prefix="sley-graph-review-") as temporary:
        scratch = Path(temporary)
        validator = scratch / "validator"
        invoke(tools, "artifact", "import", package / "artifact/native-graph.json",
               validator, "--core", core)
        validator_fixtures = scratch / "validator-review-cases.json"
        schema_cases(package, validator_fixtures)
        validator_result = review_pair(
            tools, core, args.schema.resolve(), validator, scratch / "validator-review",
            validator_fixtures, package / "tests/max-12-cases.json",
            "p1_maximum", "10", "12")
        python_project = scratch / "python-tier"
        import_project(args.python_source.resolve(), args.python_domain.resolve(),
                       python_project, tools, core)
        mapping = json.loads((python_project / "python-source/mapping.json").read_text())
        threshold = next(item for item in mapping["constant_spans"] if item["line"] == 2)
        constant = threshold["constant"]
        python_fixtures = scratch / "python-review-cases.json"
        shutil.copy2(python_project / "tests/cases.json", python_fixtures)
        changed = json.loads(python_fixtures.read_text())
        next(item for item in changed["cases"] if item["id"] == "second")["expected"]["value"] = {"ok": "0"}
        changed_path = scratch / "python-edited-cases.json"
        changed_path.write_text(json.dumps(changed, sort_keys=True) + "\n")
        source_digest = digest(python_project / "python-source/source.py")
        python_result = review_pair(
            tools, core, args.schema.resolve(), python_project, scratch / "python-review",
            python_fixtures, changed_path, constant, "25", "26")
        if source_digest != digest(python_project / "python-source/source.py"):
            raise RuntimeError("native Review workflow rewrote the original Python source")
        primitive_result = primitive_graph(tools, core, args.schema.resolve(), scratch / "primitive")
    report = {
        "contract": "sley-tools.graph-review-probe.v1", "result": "PASS",
        "tools_binary_sha256": digest(tools), "core_binary_sha256": digest(core),
        "schema_sha256": digest(args.schema.resolve()),
        "validator_artifact_sha256": digest(package / "artifact/native-graph.json"),
        "python_source_sha256": digest(args.python_source.resolve()),
        "python_domain_sha256": digest(args.python_domain.resolve()),
        "probe_sha256": digest(Path(__file__)),
        "programs": [validator_result, python_result],
        "primitive_graph": primitive_result,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"wrote {output}: both imported graph edits reviewed and replayed")


if __name__ == "__main__":
    main()
