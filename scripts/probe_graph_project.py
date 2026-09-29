#!/usr/bin/env python3
"""Exercise public native graph project creation and its bounded CLI contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from validate_program_manifest import validate


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def call(tools: Path, *args: str, code: str | None = None) -> dict:
    result = subprocess.run([str(tools), *args], capture_output=True, text=True,
                            timeout=120, check=False)
    if code is None:
        if result.returncode:
            raise RuntimeError(f"{' '.join(args[:2])} failed: {result.stderr[:350]}")
        return json.loads(result.stdout)
    if not result.returncode:
        raise RuntimeError(f"{' '.join(args[:2])} unexpectedly succeeded")
    error = json.loads(result.stderr)
    if error.get("code") != code:
        raise RuntimeError(f"expected {code}, received {error}")
    return error


def profile(tools: Path, core: Path, schema: Path, root: Path,
            spec: Path, cases: Path) -> dict:
    project = root / spec.stem
    initialized = call(tools, "init", str(project), "--graph", str(spec),
                       "--tests", str(cases), "--core", str(core))
    if initialized["contract"] != "sley-tools.graph-init.v0":
        raise RuntimeError("graph init returned the wrong contract")
    manifest = json.loads((project / "program.json").read_text())
    status = json.loads((project / ".sley-tools/status.json").read_text())
    if manifest["authoring_profile"] != "graph_v0" or status["native_commit_count"] != 1:
        raise RuntimeError("graph project has no single accepted native commit")
    checked_manifest = validate(project / "program.json", schema)
    if checked_manifest["artifacts"]["graph_recipe"]["sha256"] != digest(spec):
        raise RuntimeError("graph project recipe binding differs from source")
    fixture = json.loads(cases.read_text())
    tested = call(tools, "test", "--project", str(project))
    if tested["result"] != "PASS" or tested["external_assertions_against_native_execution"] != len(fixture["cases"]):
        raise RuntimeError("graph test did not run every external assertion")
    if tested["cases_sha256"] != digest(cases) or not tested["native_core_test_admission"].startswith("NOT_RUN"):
        raise RuntimeError("graph test evidence or native admission status differs")
    observations = []
    for row in fixture["cases"]:
        input_path = root / f"{spec.stem}-{row['id']}.json"
        input_path.write_text(json.dumps(row["input"]))
        observed = call(tools, "run", "--project", str(project), "--input", str(input_path))
        if observed["result"] != row["expected"] or observed["state_root"] != manifest["entrypoint"]["state_root"]:
            raise RuntimeError(f"graph run differs from independent case {row['id']}")
        observations.append({"id": row["id"], "value_hash": observed["value_hash"]})
    inspection = call(tools, "inspect", "--project", str(project))
    if inspection["accepted_state_root"] != manifest["entrypoint"]["state_root"]:
        raise RuntimeError("inspect lost accepted graph identity")
    call(tools, "init", str(project), "--graph", str(spec), "--tests", str(cases),
         "--core", str(core), code="ST_INPUT_INVALID")
    call(tools, "run", "--project", str(project), "--input", str(project / "inputs/example.json"),
         "--check", code="ST_FEATURE_UNSUPPORTED")
    artifact = root / f"{spec.stem}-native.json"
    exported = call(tools, "artifact", "export", "--project", str(project), "--output",
                    str(artifact))
    if exported["source_state_root"] != manifest["entrypoint"]["state_root"] or exported["sha256"] != digest(artifact):
        raise RuntimeError("graph artifact export lost the accepted native root")
    call(tools, "artifact", "export", "--project", str(project), "--output",
         str(artifact), code="ST_INPUT_INVALID")
    bad_input = root / f"{spec.stem}-bad-input.json"
    bad_input.write_text(json.dumps(["01"] * len(json.loads(spec.read_text())["parameters"])))
    call(tools, "run", "--project", str(project), "--input", str(bad_input), code="ST_INPUT_INVALID")
    bad_cases = root / f"{spec.stem}-bad-cases.json"
    changed = json.loads(cases.read_text())
    changed["cases"][0]["expected"] = "wrong-type"
    bad_cases.write_text(json.dumps(changed))
    missing = root / f"{spec.stem}-invalid-cases"
    call(tools, "init", str(missing), "--graph", str(spec), "--tests", str(bad_cases),
         "--core", str(core), code="ST_INPUT_INVALID")
    if missing.exists():
        raise RuntimeError("invalid graph cases created a partial project")
    project_cases = project / "tests/cases.json"
    expected = fixture["cases"][0]["expected"]
    if isinstance(expected, bool):
        changed["cases"][0]["expected"] = not expected
    else:
        changed["cases"][0]["expected"] = expected.copy()
        first = next(iter(expected))
        changed["cases"][0]["expected"][first] = not expected[first]
    project_cases.write_text(json.dumps(changed))
    call(tools, "test", "--project", str(project), code="ST_EXECUTION_FAILED")
    project_cases.write_bytes(cases.read_bytes())
    recipe = project / "build/graph.json"
    recipe.write_bytes(recipe.read_bytes() + b" ")
    call(tools, "test", "--project", str(project), code="ST_STALE_BASE")
    return {"program_id": spec.stem, "state_root": manifest["entrypoint"]["state_root"],
            "function_id": manifest["entrypoint"]["function_id"],
            "candidate_id": initialized["candidate_id"],
            "commit_receipt_id": initialized["commit_receipt_id"],
            "artifact_sha256": digest(artifact), "artifact_object_count": exported["object_count"],
            "spec_sha256": digest(spec), "cases_sha256": digest(cases),
            "external_assertions": len(observations), "observations": observations,
            "negative_paths": ["existing_directory", "unsupported_check", "existing_artifact_refused",
                               "noncanonical_input", "wrong_case_type_before_creation",
                               "false_expected_result", "stale_recipe"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "builder", "core", "schema", "spec", "cases",
                 "unsigned-spec", "unsigned-cases", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    paths = {name.replace("_", "-"): getattr(args, name).resolve() for name in
             ("tools", "builder", "core", "schema", "spec", "cases", "unsigned_spec",
              "unsigned_cases", "output")}
    with tempfile.TemporaryDirectory(prefix="sley-graph-project-") as temporary:
        root = Path(temporary)
        profiles = [profile(paths["tools"], paths["core"], paths["schema"], root,
                            paths["spec"], paths["cases"]),
                    profile(paths["tools"], paths["core"], paths["schema"], root,
                            paths["unsigned-spec"], paths["unsigned-cases"])]
    evidence = {"contract": "sley-tools.graph-project-probe.v1", "result": "PASS",
                "tools_binary_sha256": digest(paths["tools"]),
                "builder_binary_sha256": digest(paths["builder"]),
                "core_binary_sha256": digest(paths["core"]),
                "schema_sha256": digest(paths["schema"]),
                "profiles": profiles,
                "native_core_test_admission": "NOT_RUN: production native executor unavailable"}
    paths["output"].parent.mkdir(parents=True, exist_ok=True)
    paths["output"].write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"wrote {paths['output']}: two public graph projects, twelve native case runs")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"graph project probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
