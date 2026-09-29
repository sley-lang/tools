#!/usr/bin/env python3
"""Validate one v0 program manifest and its referenced native artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator


def strict_json(raw: bytes) -> dict:
    def unique(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON field: {key}")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique)


def validate(manifest_path: Path, schema_path: Path) -> dict:
    schema_bytes = schema_path.read_bytes()
    schema = strict_json(schema_bytes)
    Draft202012Validator.check_schema(schema)
    manifest_bytes = manifest_path.read_bytes()
    manifest = strict_json(manifest_bytes)
    problems = sorted(Draft202012Validator(schema).iter_errors(manifest), key=lambda error: list(map(str, error.absolute_path)))
    if problems:
        location = "/".join(map(str, problems[0].absolute_path)) or "root"
        raise RuntimeError(f"manifest schema violation at {location}: {problems[0].message}")
    if manifest["source_provenance"]["core_commit"] != manifest["core"]["commit"]:
        raise RuntimeError("source provenance and selected core commits differ")
    field_names = [field["name"] for field in manifest["result_fields"]]
    member_ids = [field["member_id"] for field in manifest["result_fields"]]
    if manifest["output"]["type"] == "Record":
        if len(set(field_names)) != len(field_names) or len(set(member_ids)) != len(member_ids) or set(field_names) != set(manifest["output"]["fields"]):
            raise RuntimeError("native result field identities disagree with output schema")
        for field in manifest["result_fields"]:
            if field.get("type", "Bool") != manifest["output"]["fields"][field["name"]]:
                raise RuntimeError("native result field type differs from output schema")
    elif manifest["result_fields"] or manifest["result_type_id"] is not None:
        raise RuntimeError("primitive native result has record identities")
    root = manifest_path.parent.resolve()
    checked = {}
    recipe = manifest.get("graph_recipe")
    expected_artifact_contract = ("sley-tools.native-graph-artifact.v1" if recipe is not None
                                  else "sley-tools.native-program-artifact.v0")
    if recipe is not None:
        selected = root / recipe["path"]
        path = selected.resolve()
        if selected.is_symlink() or not path.is_relative_to(root) or not path.is_file():
            raise RuntimeError("graph recipe path is not a regular file within the program directory")
        raw = path.read_bytes()
        if len(raw) > 65_536 or hashlib.sha256(raw).hexdigest() != recipe["sha256"]:
            raise RuntimeError("graph recipe size or SHA-256 differs from manifest")
        payload = strict_json(raw)
        if payload.get("contract") != "sley-tools.graph-authoring.v0" or payload.get("program_id") != manifest["program_id"]:
            raise RuntimeError("graph recipe contract or program ID differs from manifest")
        checked["graph_recipe"] = {"path": recipe["path"], "sha256": recipe["sha256"], "bytes": len(raw)}
    for key in ("artifact", "imported_source_artifact"):
        item = manifest.get(key)
        if item is None:
            continue
        if item["contract"] != expected_artifact_contract:
            raise RuntimeError(f"{key} contract differs from the program profile")
        selected = root / item["path"]
        path = selected.resolve()
        if selected.is_symlink() or not path.is_relative_to(root) or not path.is_file():
            raise RuntimeError(f"{key} path is not a regular file within the program directory")
        raw = path.read_bytes()
        limit = (8_388_608 if item["contract"] == "sley-tools.native-graph-artifact.v1"
                 else 1_048_576)
        if len(raw) > limit or hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise RuntimeError(f"{key} size or SHA-256 differs from manifest")
        payload = strict_json(raw)
        if payload.get("contract") != item["contract"] or payload.get("program_id") != manifest["program_id"]:
            raise RuntimeError(f"{key} native contract or program ID differs from manifest")
        if key == "artifact" and (
            payload.get("source_state_root") != manifest["entrypoint"]["state_root"]
            or payload.get("source_function_id") != manifest["entrypoint"]["function_id"]
            or payload.get("result_fields") != manifest["result_fields"]
            or payload.get("core_commit") != manifest["core"]["commit"]
        ):
            raise RuntimeError("current native artifact identity differs from manifest")
        checked[key] = {"path": item["path"], "sha256": item["sha256"], "bytes": len(raw)}
    if manifest["status"] == "portable_native_artifact" and "artifact" not in checked:
        raise RuntimeError("portable program manifest has no current native artifact")
    return {
        "contract": "sley-tools.program-manifest-validation.v1",
        "result": "PASS",
        "schema": schema_path.name,
        "schema_sha256": hashlib.sha256(schema_bytes).hexdigest(),
        "program_id": manifest["program_id"],
        "program_version": manifest["program_version"],
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "artifacts": checked,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--schema", type=Path, default=Path(__file__).resolve().parent.parent / "schemas/program-v0.schema.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate(args.manifest.resolve(), args.schema.resolve())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"manifest validation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
