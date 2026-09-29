#!/usr/bin/env python3
"""Create immutable same-workspace before/after packages for native Review replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from validate_program_manifest import validate


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(tools: Path, *args: str) -> dict:
    result = subprocess.run([str(tools), *args], capture_output=True, text=True, timeout=60, check=False)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args[:2])} exited {result.returncode}: {result.stderr[:450]}")
    return json.loads(result.stdout)


def copy_package(project: Path, target: Path, schema: Path) -> dict:
    target.mkdir(parents=True)
    (target / "artifact").mkdir()
    manifest = json.loads((project / "program.json").read_text(encoding="utf-8"))
    shutil.copy2(project / "program.json", target / "program.json")
    files = {"program.json": digest(target / "program.json")}
    for binding in ("artifact", "imported_source_artifact"):
        selected = manifest.get(binding)
        if selected:
            path = selected["path"]
            parts = Path(path).parts
            if len(parts) != 2 or parts[0] != "artifact" or parts[1] in (".", ".."):
                raise RuntimeError("review package contains a nonlocal artifact path")
            shutil.copy2(project / path, target / path)
            files[path] = digest(target / path)
    if validate(target / "program.json", schema)["result"] != "PASS":
        raise RuntimeError("review package failed manifest schema or artifact binding")
    for relative in files:
        raw = (target / relative).read_bytes()
        if b"/home/" in raw or b"/tmp/" in raw:
            raise RuntimeError("review package retained a machine-local path")
    return {"files": files, "state_root": manifest["entrypoint"]["state_root"],
            "function_id": manifest["entrypoint"]["function_id"]}


def build_one(tools: Path, core: Path, artifact: Path, schema: Path, output: Path, program: str,
              work: Path) -> dict:
    project = work / program
    invoke(tools, "artifact", "import", str(artifact), str(project), "--core", str(core))
    invoke(tools, "artifact", "export", "--project", str(project),
           "--output", str(project / "artifact/before-review.json"), "--bind-manifest")
    before = copy_package(project, output / program / "before", schema)
    if program == "release-readiness":
        edited = invoke(tools, "edit", "--project", str(project), "--max-blocking-findings", "2")
    else:
        edited = invoke(tools, "edit", "--project", str(project), "--require-memory-spare", "true")
    invoke(tools, "artifact", "export", "--project", str(project),
           "--output", str(project / "artifact/after-review.json"), "--bind-manifest")
    after = copy_package(project, output / program / "after", schema)
    before_artifact = json.loads((output / program / "before/artifact/before-review.json").read_text(encoding="utf-8"))
    after_artifact = json.loads((output / program / "after/artifact/after-review.json").read_text(encoding="utf-8"))
    old = {item["entity_id"]: item["object_id"] for item in before_artifact["objects"]}
    new = {item["entity_id"]: item["object_id"] for item in after_artifact["objects"]}
    if old.keys() != new.keys() or sum(old[entity] != new[entity] for entity in old) != 1:
        raise RuntimeError(f"{program} review fixture did not preserve one exact native edit")
    return {"program_id": program, "source_artifact_sha256": digest(artifact),
            "edited_candidate_id": edited["candidate_id"],
            "changed_entity_versions": 1, "before": before, "after": after}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--release-artifact", required=True, type=Path)
    parser.add_argument("--resource-artifact", required=True, type=Path)
    parser.add_argument("--schema", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists():
        raise RuntimeError(f"review fixtures already exist: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    tools, core, schema = args.tools.resolve(), args.core.resolve(), args.schema.resolve()
    build_root = Path(tempfile.mkdtemp(prefix=".sley-review-build-", dir=output.parent))
    try:
        with tempfile.TemporaryDirectory(prefix="sley-review-authoring-") as temporary:
            work = Path(temporary)
            programs = [
                build_one(tools, core, args.release_artifact.resolve(), schema, build_root,
                          "release-readiness", work),
                build_one(tools, core, args.resource_artifact.resolve(), schema, build_root,
                          "resource-admission", work),
            ]
        report = {"contract": "sley-tools.review-fixtures.v1", "core_binary_sha256": digest(core),
                  "schema_sha256": digest(schema), "programs": programs,
                  "scope": "same-workspace native before/after review packages; no workspace authority"}
        (build_root / "manifest.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(build_root, output)
        print(f"created {output}: two manifest-validated native review pairs")
    finally:
        if build_root.exists():
            shutil.rmtree(build_root)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"review fixture build failed: {error}", file=sys.stderr)
        raise SystemExit(1)
