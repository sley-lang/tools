#!/usr/bin/env python3
"""Build one local native catalog manifest and artifact without overwrites."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def invoke(*args: str) -> dict:
    result = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError(f"{args[0]} {args[1]} exited {result.returncode}: {result.stderr[:350]}")
    return json.loads(result.stdout)


def build(tools: Path, core: Path, catalog: Path, template: str = "release-readiness") -> dict:
    manifest_path = catalog / "program.json"
    artifact_path = catalog / "artifact/native-program.json"
    if manifest_path.exists() or artifact_path.exists():
        raise RuntimeError("catalog manifest or artifact already exists; refusing overwrite")
    catalog.mkdir(parents=True, exist_ok=True)
    artifact_path.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sley-catalog-build-") as temporary:
        source = Path(temporary) / "source"
        imported = Path(temporary) / "imported"
        created = invoke(str(tools), "init", str(source), "--template", template, "--core", str(core))
        source_state = json.loads((source / ".sley-tools/state.json").read_text(encoding="utf-8"))
        exported = invoke(str(tools), "artifact", "export", "--project", str(source), "--output", str(artifact_path))
        raw = artifact_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != exported["sha256"]:
            raise RuntimeError("catalog artifact digest differs from export report")
        manifest = created["program"]
        manifest["program_version"] = "0.1.0"
        manifest["status"] = "portable_native_artifact"
        manifest["artifact"] = {
            "path": "artifact/native-program.json",
            "sha256": exported["sha256"],
            "contract": "sley-tools.native-program-artifact.v0",
        }
        with manifest_path.open("x", encoding="utf-8") as target:
            target.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        replay = invoke(str(tools), "artifact", "import", str(artifact_path), str(imported), "--core", str(core))
        replay_test = invoke(str(tools), "test", "--project", str(imported))
        expected_assertions = 36 if template == "release-readiness" else 12
        if replay_test["external_assertions_against_native_execution"] != expected_assertions:
            raise RuntimeError("catalog artifact did not reproduce the initial native program")
        imported_state = json.loads((imported / ".sley-tools/state.json").read_text(encoding="utf-8"))
        if source_state["workspace_id"] == imported_state["workspace_id"]:
            raise RuntimeError("catalog replay reused source workspace")
        return {
            "contract": f"sley-tools.catalog-{template}.v1",
            "result": "PASS",
            "artifact_sha256": exported["sha256"],
            "artifact_bytes": len(raw),
            "artifact_object_count": exported["object_count"],
            "core_binary_sha256": hashlib.sha256(core.read_bytes()).hexdigest(),
            "source_state_root": source_state["state_root"],
            "source_transaction_id": source_state["transaction_id"],
            "source_receipt_id": source_state["commit_receipt_id"],
            "imported_state_root": imported_state["state_root"],
            "imported_external_assertions": replay["verification"]["external_assertions_against_native_execution"],
            "fresh_workspace_authority": True,
            "native_test_admission": "NOT_RUN: production native executor unavailable",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--template", choices=["release-readiness", "resource-admission"], default="release-readiness")
    args = parser.parse_args()
    if args.evidence.exists():
        raise RuntimeError("catalog evidence already exists; refusing overwrite")
    report = build(args.tools.resolve(), args.core.resolve(), args.catalog.resolve(), args.template)
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    with args.evidence.open("x", encoding="utf-8") as target:
        target.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.catalog}/program.json and native artifact; fresh import passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"catalog build failed: {error}", file=sys.stderr)
        raise SystemExit(1)
