#!/usr/bin/env python3
"""Validate catalog, local, and imported manifests against the published schema."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator

from validate_program_manifest import validate


def invoke(tools: Path, *args: str) -> dict:
    result = subprocess.run([str(tools), *args], capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args[:2])}: {result.returncode}: {result.stderr[:300]}")
    return json.loads(result.stdout)


def probe(tools: Path, core: Path, catalog: Path, schema_path: Path, resource_catalog: Path | None = None) -> dict:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    catalog_manifest = catalog / "program.json"
    catalog_report = validate(catalog_manifest, schema_path)
    resource_catalog_report = validate(resource_catalog / "program.json", schema_path) if resource_catalog else None
    catalog_data = json.loads(catalog_manifest.read_text(encoding="utf-8"))
    for bad, reason in [
        ({**catalog_data, "purpose": ""}, "empty purpose"),
        ({key: value for key, value in catalog_data.items() if key != "source_provenance"}, "missing provenance"),
        ({**catalog_data, "artifact": {**catalog_data["artifact"], "path": "../secret"}}, "traversal path"),
        ({**catalog_data, "artifact": None}, "portable manifest without artifact"),
    ]:
        if Draft202012Validator(schema).is_valid(bad):
            raise RuntimeError(f"schema accepted {reason}")
    with tempfile.TemporaryDirectory(prefix="sley-manifest-probe-") as temporary:
        root = Path(temporary)
        local, imported, resource_local = root / "local", root / "imported", root / "resource-local"
        invoke(tools, "init", str(local), "--template", "release-readiness", "--core", str(core))
        local_report = validate(local / "program.json", schema_path)
        invoke(tools, "init", str(resource_local), "--template", "resource-admission", "--core", str(core))
        resource_report = validate(resource_local / "program.json", schema_path)
        wrong_type = json.loads((resource_local / "program.json").read_text(encoding="utf-8"))
        wrong_type["result_fields"][-1]["type"] = "Bool"
        wrong_type_path = resource_local / "wrong-type.json"
        wrong_type_path.write_text(json.dumps(wrong_type), encoding="utf-8")
        try:
            validate(wrong_type_path, schema_path)
        except RuntimeError as error:
            if "result field type" not in str(error):
                raise
        else:
            raise RuntimeError("manifest validator accepted a result type mismatch")
        invoke(tools, "artifact", "import", str(catalog / "artifact/native-program.json"), str(imported), "--core", str(core))
        imported_report = validate(imported / "program.json", schema_path)
        resource_imported_report = None
        resource_edited_report = None
        if resource_catalog:
            destination = root / "resource-imported"
            invoke(tools, "artifact", "import", str(resource_catalog / "artifact/native-program.json"), str(destination), "--core", str(core))
            resource_imported_report = validate(destination / "program.json", schema_path)
            invoke(tools, "edit", "--project", str(destination), "--require-memory-spare", "true")
            resource_edited_report = validate(destination / "program.json", schema_path)
        invoke(tools, "edit", "--project", str(imported), "--max-blocking-findings", "2")
        edited_report = validate(imported / "program.json", schema_path)
        bad_dir = root / "bad"
        (bad_dir / "artifact").mkdir(parents=True)
        shutil.copyfile(catalog / "artifact/native-program.json", bad_dir / "artifact/native-program.json")
        bad_manifest = json.loads(catalog_manifest.read_text(encoding="utf-8"))
        bad_manifest["artifact"]["sha256"] = "0" * 64
        (bad_dir / "program.json").write_text(json.dumps(bad_manifest), encoding="utf-8")
        try:
            validate(bad_dir / "program.json", schema_path)
        except RuntimeError as error:
            if "SHA-256" not in str(error):
                raise
        else:
            raise RuntimeError("manifest validator accepted a false artifact checksum")
        duplicate_manifest = bad_dir / "duplicate.json"
        duplicate_manifest.write_bytes(catalog_manifest.read_bytes().replace(b"{", b'{"contract":"duplicate",', 1))
        try:
            validate(duplicate_manifest, schema_path)
        except ValueError as error:
            if "duplicate JSON field" not in str(error):
                raise
        else:
            raise RuntimeError("manifest validator accepted a duplicate field")
    report = {
        "contract": "sley-tools.program-schema-probe.v1",
        "result": "PASS",
        "core_binary_sha256": hashlib.sha256(core.read_bytes()).hexdigest(),
        "catalog_manifest_sha256": catalog_report["manifest_sha256"],
        "catalog_artifact_sha256": catalog_report["artifacts"]["artifact"]["sha256"],
        "schema_sha256": catalog_report["schema_sha256"],
        "local_manifest_valid": local_report["result"] == "PASS",
        "resource_local_manifest_valid": resource_report["result"] == "PASS",
        "validator_rejected_result_type_mismatch": True,
        "imported_manifest_valid": imported_report["result"] == "PASS",
        "edited_import_manifest_valid": edited_report["result"] == "PASS",
        "schema_rejected_missing_fields_traversal_and_unbound_portable_artifact": True,
        "validator_rejected_false_artifact_checksum": True,
        "validator_rejected_duplicate_json_field": True,
    }
    if resource_catalog_report and resource_imported_report and resource_edited_report:
        report.update({
            "resource_catalog_manifest_sha256": resource_catalog_report["manifest_sha256"],
            "resource_catalog_artifact_sha256": resource_catalog_report["artifacts"]["artifact"]["sha256"],
            "resource_imported_manifest_valid": resource_imported_report["result"] == "PASS",
            "resource_edited_import_manifest_valid": resource_edited_report["result"] == "PASS",
        })
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", type=Path, required=True)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--resource-catalog", type=Path)
    parser.add_argument("--schema", type=Path, default=Path(__file__).resolve().parent.parent / "schemas/program-v0.schema.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = probe(args.tools.resolve(), args.core.resolve(), args.catalog.resolve(), args.schema.resolve(), args.resource_catalog.resolve() if args.resource_catalog else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: catalog/local/imported manifest schema and negative checks passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"manifest schema probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
