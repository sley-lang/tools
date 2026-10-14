#!/usr/bin/env python3
"""Rebuild every catalog package's native artifact on the selected core.

Use this when the pinned Sley core changes. Each program is rebuilt from its
checked-in source (a Starter template, or its bound graph recipe and declared
cases) in a fresh workspace, its declared cases run against native execution,
and its artifact is exported. The package manifest keeps every curated field;
only the core binding, the new native identities, and the artifact digest are
replaced. The rebuild must reproduce the manifest's declared interface (inputs,
output, result fields and recipe digest) or the package is left untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from build_release_readiness_catalog import build as build_template
from probe_generated_validator_catalog import attach_mapping
from validate_program_manifest import validate


ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = {"release-readiness", "resource-admission"}
# Fields a rebuild must reproduce exactly; anything else not replaced below is curated.
INTERFACE = ("contract", "program_id", "inputs", "output", "result_fields",
             "tools_contract", "authoring_profile", "graph_recipe")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(tools: Path, *args: object) -> dict:
    result = subprocess.run([str(tools), *map(str, args)], capture_output=True, text=True,
                            timeout=300, check=False)
    if result.returncode:
        raise RuntimeError(f"sley-tools {args[0]} exited {result.returncode}: {result.stderr[:500]}")
    return json.loads(result.stdout)


def write_like(path: Path, value: dict) -> None:
    """Rewrite a JSON file in the layout it already uses (indented or compact)."""
    raw = path.read_text(encoding="utf-8")
    text = (json.dumps(value, indent=2, sort_keys=True) if "\n  " in raw
            else json.dumps(value, sort_keys=True, separators=(",", ":")))
    path.write_text(text + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")


def rebuild(tools: Path, core: Path, package: Path,
            work: Path) -> tuple[dict, bytes, int, dict | None, dict | None]:
    """Return the rebuilt manifest, artifact bytes, assertion count, schema mapping,
    and (for Starter templates) the catalog build report."""
    manifest = json.loads((package / "program.json").read_text(encoding="utf-8"))
    program_id = manifest["program_id"]
    if program_id in TEMPLATES:
        built = work / "catalog"
        report = build_template(tools, core, built, program_id)
        rebuilt = json.loads((built / "program.json").read_text(encoding="utf-8"))
        artifact = (built / "artifact/native-program.json").read_bytes()
        return rebuilt, artifact, report["imported_external_assertions"], None, report
    project = work / "project"
    created = invoke(tools, "init", project, "--graph", package / "build/graph.json",
                     "--tests", package / "tests/cases.json", "--core", core)
    output = work / "native-graph.json"
    exported = invoke(tools, "artifact", "export", "--project", project, "--output", output)
    artifact = output.read_bytes()
    if hashlib.sha256(artifact).hexdigest() != exported["sha256"]:
        raise RuntimeError(f"{program_id}: exported artifact digest differs from export report")
    # Schema-imported programs bind source properties to native entity IDs.
    mapping = (attach_mapping(package, project)
               if (package / "schema/mapping.json").is_file() else None)
    return (created["program"], artifact,
            exported["external_assertions_against_native_execution"], mapping, None)


def rebind(tools: Path, core: Path, package: Path, schema: Path,
           evidence: Path | None) -> dict:
    manifest_path = package / "program.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact_path = package / manifest["artifact"]["path"]
    previous_artifact = digest(artifact_path)
    with tempfile.TemporaryDirectory(prefix="sley-catalog-rebind-") as temporary:
        rebuilt, artifact, assertions, mapping, build_report = rebuild(tools, core, package, Path(temporary))
        differing = [key for key in INTERFACE if manifest.get(key) != rebuilt.get(key)]
        if differing:
            raise RuntimeError(f"{manifest['program_id']}: rebuild changed declared {differing}")
        payload = json.loads(artifact)
        if (payload["core_commit"] != rebuilt["core"]["commit"]
                or payload["source_state_root"] != rebuilt["entrypoint"]["state_root"]
                or payload["source_function_id"] != rebuilt["entrypoint"]["function_id"]):
            raise RuntimeError(f"{manifest['program_id']}: artifact identity differs from rebuild")
    updated = dict(manifest)
    for key in ("core", "entrypoint", "result_type_id", "source_provenance"):
        updated[key] = rebuilt[key]
    updated["artifact"] = dict(manifest["artifact"], sha256=hashlib.sha256(artifact).hexdigest())
    artifact_path.write_bytes(artifact)
    write_like(manifest_path, updated)
    if mapping is not None:
        write_like(package / "schema/mapping.json", mapping)
    if validate(manifest_path, schema)["result"] != "PASS":
        raise RuntimeError(f"{manifest['program_id']}: rebound manifest failed validation")
    if build_report is not None and evidence is not None:
        evidence.parent.mkdir(parents=True, exist_ok=True)
        with evidence.open("x", encoding="utf-8") as target:
            target.write(json.dumps(build_report, indent=2, sort_keys=True) + "\n")
    with tempfile.TemporaryDirectory(prefix="sley-catalog-replay-") as temporary:
        project = Path(temporary) / "replay"
        invoke(tools, "artifact", "import", artifact_path, project, "--core", core)
        tested = invoke(tools, "test", "--project", project)
    if tested["result"] != "PASS" or tested["external_assertions_against_native_execution"] != assertions:
        raise RuntimeError(f"{manifest['program_id']}: rebound artifact did not replay its cases")
    return {"program_id": manifest["program_id"], "previous_artifact_sha256": previous_artifact,
            "artifact_sha256": updated["artifact"]["sha256"],
            "manifest_sha256": digest(manifest_path),
            "state_root": updated["entrypoint"]["state_root"],
            "external_assertions": assertions}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--catalog", type=Path, default=ROOT / "programs/catalog.json")
    parser.add_argument("--schema", type=Path, default=ROOT / "schemas/program-v0.schema.json")
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    parser.add_argument("--template-evidence", type=Path, metavar="PATTERN",
                        help="write each Starter template's catalog build report to PATTERN, "
                             "where {program_id} names the program")
    args = parser.parse_args()
    tools, core = args.tools.resolve(strict=True), args.core.resolve(strict=True)
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    programs = []
    for entry in catalog["programs"]:
        evidence = (Path(str(args.template_evidence).format(program_id=entry["program_id"]))
                    if args.template_evidence else None)
        programs.append(rebind(tools, core, ROOT / entry["package"], args.schema.resolve(),
                               evidence))
        print(f"{entry['id']} {entry['program_id']}: rebound, "
              f"{programs[-1]['external_assertions']} external assertions replayed")
    report = {"contract": "sley-tools.catalog-rebind.v1", "result": "PASS",
              "core_binary_sha256": digest(core), "tools_binary_sha256": digest(tools),
              "programs": programs,
              "external_assertions": sum(item["external_assertions"] for item in programs)}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"PASS: {len(programs)} catalog packages rebound to core {report['core_binary_sha256'][:12]}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"catalog rebind failed: {error}", file=sys.stderr)
        raise SystemExit(1)
