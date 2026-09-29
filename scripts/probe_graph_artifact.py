#!/usr/bin/env python3
"""Rebase exact native graph objects, including an edited Data Pipe predicate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from probe_data_pipe_stream import oracle
from validate_program_manifest import validate


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(tools: Path, *args: object, reject: str | None = None) -> dict:
    result = subprocess.run([str(tools), *map(str, args)], capture_output=True,
                            text=True, timeout=120, check=False)
    if reject is None:
        if result.returncode:
            raise RuntimeError(f"{' '.join(map(str, args[:2]))} failed: {result.stderr[:600]}")
        return json.loads(result.stdout)
    if result.returncode == 0:
        raise RuntimeError(f"{' '.join(map(str, args[:2]))} unexpectedly succeeded")
    error = json.loads(result.stderr)
    if error.get("code") != reject:
        raise RuntimeError(f"expected {reject}, got {error}")
    return error


def create(tools: Path, core: Path, root: Path, examples: Path, name: str) -> Path:
    project = root / name
    invoke(tools, "init", project, "--graph", examples / f"{name}.json",
           "--tests", examples / f"{name}-cases.json", "--core", core)
    return project


def roundtrip(tools: Path, core: Path, schema: Path, root: Path,
              examples: Path, name: str) -> dict:
    source = create(tools, core, root, examples, name)
    artifact = root / f"{name}.native.json"
    exported = invoke(tools, "artifact", "export", "--project", source, "--output", artifact)
    imported = invoke(tools, "artifact", "import", artifact,
                      root / f"{name}-replay", "--core", core)
    replay = root / f"{name}-replay"
    tested = invoke(tools, "test", "--project", replay)
    if (imported["contract"] != "sley-tools.artifact-import.v1"
        or tested["result"] != "PASS"
        or exported["source_state_root"] == imported["program"]["entrypoint"]["state_root"]
        or validate(replay / "program.json", schema)["result"] != "PASS"):
        raise RuntimeError(f"{name} graph object replay or manifest validation failed")
    return {"program_id": name, "artifact_sha256": digest(artifact),
            "source_root": exported["source_state_root"],
            "replayed_root": imported["program"]["entrypoint"]["state_root"],
            "object_count": exported["object_count"],
            "replayed_assertions": tested["external_assertions_against_native_execution"]}


def edited_selector(tools: Path, core: Path, schema: Path, root: Path,
                    examples: Path, sample: Path, package: Path) -> dict:
    comparator = create(tools, core, root, examples, "benchmark-comparator")
    selector = create(tools, core, root, examples, "benchmark-selector")
    reducer = create(tools, core, root, examples, "benchmark-summary-reducer")
    value_file = root / "include-all.json"
    value_file.write_text("true\n", encoding="utf-8")
    accepted = invoke(tools, "propose", "--project", selector,
                      "--constant", "include_nonregressions", "--value-file", value_file,
                      "--tests", examples / "benchmark-selector-include-all-cases.json")
    invoke(tools, "validate", "--project", selector)
    committed = invoke(tools, "commit", "--project", selector)
    if accepted["candidate_id"] != committed["candidate_id"]:
        raise RuntimeError("predicate edit lost its exact accepted candidate")
    artifact = selector / "artifact/edited-native-graph.json"
    exported = invoke(tools, "artifact", "export", "--project", selector,
                      "--output", artifact, "--bind-manifest")
    source_manifest = validate(selector / "program.json", schema)
    if (source_manifest["result"] != "PASS" or exported["manifest_bound"] is not True
        or exported["manifest_sha256"] != digest(selector / "program.json")):
        raise RuntimeError("edited selector artifact is not bound to its manifest")
    duplicate = invoke(tools, "artifact", "export", "--project", selector,
                       "--output", artifact, "--bind-manifest", reject="ST_INPUT_INVALID")
    if duplicate["code"] != "ST_INPUT_INVALID":
        raise RuntimeError("existing graph artifact was overwritten")
    replay = root / "selector-replay"
    imported = invoke(tools, "artifact", "import", artifact, replay, "--core", core)
    tested = invoke(tools, "test", "--project", replay)
    replay_manifest = validate(replay / "program.json", schema)
    if (tested["result"] != "PASS" or tested["external_assertions_against_native_execution"] != 9
        or replay_manifest["result"] != "PASS"
        or imported["program"]["entrypoint"]["state_root"] == committed["new_root"]
        or digest(replay / "artifact/source-native-graph.json") != digest(artifact)):
        raise RuntimeError("edited selector did not rebase into a fresh native workspace")
    rows = [json.loads(line) for line in sample.read_text(encoding="utf-8").splitlines()]
    expected = oracle(rows, include_all=True)
    command = [sys.executable, "-m", "sley_tools.data_pipe", "--tools", str(tools),
               "--core", str(core), "--comparator-project", str(comparator),
               "--selector-project", str(replay), "--reducer-project", str(reducer),
               "--input", str(sample), "--batch-size", "2"]
    env = {**os.environ, "PYTHONPATH": str(package)}
    pipe = subprocess.run(command, capture_output=True, env=env, timeout=180, check=False)
    if pipe.returncode or pipe.stdout != expected:
        raise RuntimeError(f"unchanged Data Pipe wrapper lost imported predicate: {pipe.stderr[-500:]!r}")
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    altered = json.loads(payload["recipe_json"])
    changed = next(item for item in altered["constants"]
                   if item["name"] == "include_nonregressions")
    changed["value"] = False
    payload["recipe_json"] = json.dumps(altered, separators=(",", ":"))
    payload["recipe_sha256"] = hashlib.sha256(payload["recipe_json"].encode()).hexdigest()
    wrong_recipe = root / "wrong-recipe.native.json"
    wrong_recipe.write_text(json.dumps(payload), encoding="utf-8")
    invoke(tools, "artifact", "import", wrong_recipe, root / "wrong-recipe-replay",
           "--core", core, reject="ST_IMPORT_UNSUPPORTED")
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    stored = payload["objects"][0]["stored_hex"]
    payload["objects"][0]["stored_hex"] = stored[:-1] + ("0" if stored[-1] != "0" else "1")
    wrong_object = root / "wrong-object.native.json"
    wrong_object.write_text(json.dumps(payload), encoding="utf-8")
    invoke(tools, "artifact", "import", wrong_object, root / "wrong-object-replay",
           "--core", core, reject="ST_IMPORT_UNSUPPORTED")
    value_file.write_text("false\n", encoding="utf-8")
    invoke(tools, "propose", "--project", replay,
           "--constant", "include_nonregressions", "--value-file", value_file,
           "--tests", examples / "benchmark-selector-cases.json")
    invoke(tools, "validate", "--project", replay)
    reedited = invoke(tools, "commit", "--project", replay)
    if invoke(tools, "test", "--project", replay)["result"] != "PASS":
        raise RuntimeError("imported graph could not be edited through the native lifecycle")
    second_artifact = root / "re-edited-selector.native.json"
    invoke(tools, "artifact", "export", "--project", replay, "--output", second_artifact)
    second_replay = root / "second-selector-replay"
    invoke(tools, "artifact", "import", second_artifact, second_replay, "--core", core)
    if invoke(tools, "test", "--project", second_replay)["result"] != "PASS":
        raise RuntimeError("imported graph could not be re-exported after a native edit")
    return {"accepted_candidate_id": committed["candidate_id"],
            "source_root": committed["new_root"],
            "replayed_root": imported["program"]["entrypoint"]["state_root"],
            "artifact_sha256": digest(artifact),
            "object_count": exported["object_count"],
            "replayed_assertions": tested["external_assertions_against_native_execution"],
            "unchanged_host_wrapper_selected_rows": len(rows),
            "mismatched_recipe_rejected": True, "corrupt_native_object_rejected": True,
            "existing_artifact_refused": True,
            "imported_graph_reedited_root": reedited["new_root"],
            "reexported_artifact_sha256": digest(second_artifact),
            "reexported_graph_replayed": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "builder", "core", "schema", "examples", "sample", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    paths = {name: getattr(args, name).resolve() for name in
             ("tools", "builder", "core", "schema", "examples", "sample", "output")}
    package = Path(__file__).resolve().parents[1] / "packages/python"
    with tempfile.TemporaryDirectory(prefix="sley-graph-artifact-") as temporary:
        root = Path(temporary)
        profiles = [roundtrip(paths["tools"], paths["core"], paths["schema"], root,
                              paths["examples"], name)
                    for name in ("range-predicate", "numeric-echo", "benchmark-summary-reducer")]
        pipeline_root = root / "pipeline"
        pipeline_root.mkdir()
        selector = edited_selector(paths["tools"], paths["core"], paths["schema"], pipeline_root,
                                   paths["examples"], paths["sample"], package)
    report = {"contract": "sley-tools.graph-artifact-probe.v1", "result": "PASS",
              "tools_binary_sha256": digest(paths["tools"]),
              "builder_binary_sha256": digest(paths["builder"]),
              "core_binary_sha256": digest(paths["core"]),
              "schema_sha256": digest(paths["schema"]),
              "sample_sha256": digest(paths["sample"]),
              "profiles": profiles, "edited_selector": selector,
              "native_core_test_admission": "NOT_RUN: production native executor unavailable"}
    paths["output"].parent.mkdir(parents=True, exist_ok=True)
    paths["output"].write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {paths['output']}: native graph objects replayed in fresh workspaces")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"graph artifact probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
