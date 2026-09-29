#!/usr/bin/env python3
"""Verify N10's portable native validator, raw adapter, and bound edit."""

from __future__ import annotations

import argparse
import decimal
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.validators import extend
from validate_program_manifest import validate

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/python"))
from sley_tools import schema_importer as importer  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(tools: Path, *args: object) -> dict:
    result = subprocess.run([str(tools), *map(str, args)], capture_output=True,
                            text=True, timeout=120, check=False)
    if result.returncode:
        raise RuntimeError(f"N10 CLI failed: {result.stderr[:500]}")
    return json.loads(result.stdout)


def reference(schema: dict, raw: bytes) -> bool:
    checker = Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda _checker, value: type(value) is int or (
            isinstance(value, decimal.Decimal) and value.is_finite()
            and value == value.to_integral_value()),
    )
    validator = extend(Draft202012Validator, type_checker=checker)
    value = json.loads(raw, parse_int=decimal.Decimal, parse_float=decimal.Decimal)
    return validator(schema).is_valid(value)


def attach_mapping(package: Path, project: Path) -> dict:
    schema_dir = project / "schema"
    schema_dir.mkdir()
    shutil.copyfile(package / "schema/source.json", schema_dir / "source.json")
    mapping = json.loads((package / "schema/mapping.json").read_text())
    state = json.loads((project / ".sley-tools/state.json").read_text())
    manifest = json.loads((project / "program.json").read_text())
    mapping["native_function_entity_id"] = state["function_id"]
    mapping["native_result_type_entity_id"] = manifest["result_type_id"]
    mapping["native_constant_entity_ids"] = state["constant_ids"]
    for entry in mapping["properties"]:
        if entry["type"] == "integer":
            entry["native_bound_entities"] = {
                "minimum": state["constant_ids"][entry["minimum_constant"]],
                "maximum": state["constant_ids"][entry["maximum_constant"]],
            }
    (schema_dir / "mapping.json").write_text(json.dumps(mapping, sort_keys=True) + "\n")
    return mapping


def compare(tools: Path, project: Path, schema: dict, cases: list[dict],
            alignment: str) -> tuple[str, list[dict]]:
    roots: set[str] = set()
    observations = []
    for row in cases:
        raw = row["raw"].encode()
        expected = reference(schema, raw)
        if expected is not row["valid"]:
            raise RuntimeError(f"N10 raw fixture contradicts exact JSON Schema at {row['id']}")
        observed = importer.validate_project(project, raw, tools)
        if (observed["valid"] is not expected
                or (observed["typed_payload"] is None) == expected
                or observed["source_schema_alignment"] != alignment):
            raise RuntimeError(f"N10 native/reference mismatch at {row['id']}: {observed}")
        roots.add(observed["accepted_state_root"])
        observations.append({"case": row["id"], "valid": expected,
                             "reasons": observed["reasons"]})
    if len(roots) != 1:
        raise RuntimeError("N10 read-only cases changed the accepted native root")
    return roots.pop(), observations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "core", "schema", "catalog", "package", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    paths = {name: getattr(args, name).resolve() for name in
             ("tools", "core", "schema", "catalog", "package", "output")}
    package = paths["package"]
    manifest_path = package / "program.json"
    artifact_path = package / "artifact/native-graph.json"
    recipe_path = package / "build/graph.json"
    typed_path = package / "tests/cases.json"
    raw_path = package / "tests/raw-cases.json"
    edited_path = package / "tests/max-12-cases.json"
    source_path = package / "schema/source.json"
    mapping_path = package / "schema/mapping.json"
    example_path = ROOT / "packages/python/examples/generated_payload_validator.py"
    if validate(manifest_path, paths["schema"])["result"] != "PASS":
        raise RuntimeError("N10 manifest did not validate")
    manifest = json.loads(manifest_path.read_text())
    artifact = json.loads(artifact_path.read_text())
    recipe = json.loads(recipe_path.read_text())
    mapping = json.loads(mapping_path.read_text())
    catalog = json.loads(paths["catalog"].read_text())
    entry = next((row for row in catalog["programs"] if row["id"] == "N10"), None)
    raw_document = json.loads(raw_path.read_text())
    cases = raw_document["cases"]
    original_schema = json.loads(source_path.read_text())
    selected = importer.select_schema(importer.parse_json(source_path.read_bytes()))
    compiled, compiled_mapping = importer.compile_graph(selected, "generated-payload-validator")
    count_mapping = next(row for row in mapping["properties"] if row["name"] == "count")
    if (manifest["program_id"] != "generated-payload-validator"
            or manifest["status"] != "portable_native_artifact"
            or manifest["artifact"]["sha256"] != digest(artifact_path)
            or artifact["source_state_root"] != manifest["entrypoint"]["state_root"]
            or artifact["source_function_id"] != mapping["native_function_entity_id"]
            or digest(recipe_path) != mapping["initial_recipe_sha256"]
            or digest(source_path) != mapping["source_schema_sha256"]
            or compiled != recipe
            or compiled_mapping["properties"] != [{k: v for k, v in row.items()
                if k != "native_bound_entities"} for row in mapping["properties"]]
            or count_mapping["maximum_constant"] != "p1_maximum"
            or count_mapping["native_bound_entities"]["maximum"]
               != mapping["native_constant_entity_ids"]["p1_maximum"]
            or {row["name"]: row["value"] for row in recipe["constants"]}.get("p1_maximum") != "10"
            or raw_document["contract"] != "sley-tools.raw-schema-cases.v1"
            or len(cases) != 16 or len({row["id"] for row in cases}) != len(cases)
            or entry is None or entry["program_id"] != manifest["program_id"]
            or entry["artifact"] != artifact_path.relative_to(ROOT).as_posix()
            or entry["verified_behavior"] != paths["output"].relative_to(ROOT).as_posix()
            or entry["worked_modification"] != paths["output"].relative_to(ROOT).as_posix()
            or entry["integration_examples"] != [example_path.relative_to(ROOT).as_posix()]):
        raise RuntimeError("N10 package, mapping, or catalog binding differs")

    with tempfile.TemporaryDirectory(prefix="sley-n10-catalog-") as temporary:
        scratch = Path(temporary)
        authored = scratch / "authored"
        invoke(paths["tools"], "init", authored, "--graph", recipe_path,
               "--tests", typed_path, "--core", paths["core"])
        if invoke(paths["tools"], "test", "--project", authored)["result"] != "PASS":
            raise RuntimeError("N10 checked-in recipe did not author a working native graph")
        imported = scratch / "imported"
        import_report = invoke(paths["tools"], "artifact", "import", artifact_path,
                               imported, "--core", paths["core"])
        imported_mapping = attach_mapping(package, imported)
        if (import_report["program"]["entrypoint"]["state_root"] == artifact["source_state_root"]
                or imported_mapping["native_function_entity_id"]
                   == mapping["native_function_entity_id"]
                or invoke(paths["tools"], "test", "--project", imported)["result"] != "PASS"):
            raise RuntimeError("N10 portable native import failed to rebase and test")
        initial_root, initial_observations = compare(
            paths["tools"], imported, original_schema, cases, "initial")
        reasons = {row["case"]: row["reasons"] for row in initial_observations}
        if ("p1_missing" not in reasons["missing-count"]
                or "extra_properties" not in reasons["extra-field"]
                or "p1_outside_native_range" not in reasons["outside-signed-range"]
                or "p1_out_of_bounds" not in reasons["above-maximum"]):
            raise RuntimeError("N10 native reason flags do not identify schema violations")
        for raw in (b'{"active":true,"count":1,"count":2}', b'{"active":'):
            try:
                importer.validate_project(imported, raw, paths["tools"])
            except importer.SchemaImportError:
                pass
            else:
                raise RuntimeError("N10 accepted malformed or duplicate-key input")
        input_file = scratch / "example-input.json"
        input_file.write_text('{"active":true,"count":10}\n')
        example = subprocess.run(
            [sys.executable, str(example_path), "--tools", str(paths["tools"]),
             "--core", str(paths["core"]), "--package", str(package),
             "--input", str(input_file)], capture_output=True, text=True,
            timeout=120, check=False,
        )
        if example.returncode or json.loads(example.stdout)["valid"] is not True:
            raise RuntimeError(f"N10 raw-input integration example failed: {example.stderr[:300]}")
        value_file = scratch / "new-maximum.json"
        value_file.write_text('"12"\n')
        proposed = invoke(paths["tools"], "propose", "--project", imported,
                          "--constant", "p1_maximum", "--value-file", value_file,
                          "--tests", edited_path)
        if invoke(paths["tools"], "inspect", "--project", imported)["accepted_state_root"] != initial_root:
            raise RuntimeError("N10 proposal changed accepted state before commit")
        validated = invoke(paths["tools"], "validate", "--project", imported)
        committed = invoke(paths["tools"], "commit", "--project", imported)
        tested = invoke(paths["tools"], "test", "--project", imported)
        if (not proposed["candidate_id"] == validated["candidate_id"] == committed["candidate_id"]
                or committed["old_root"] != initial_root
                or committed["new_root"] == initial_root or tested["result"] != "PASS"):
            raise RuntimeError("N10 maximum edit did not follow native proposal lifecycle")
        edited_schema = json.loads(source_path.read_text())
        edited_schema["properties"]["count"]["maximum"] = 12
        edited_cases = [row for row in cases if row["id"] != "above-maximum"] + [
            {"id": "new-interior", "raw": '{"active":true,"count":11}', "valid": True},
            {"id": "new-maximum", "raw": '{"active":true,"count":12}', "valid": True},
            {"id": "still-excluded", "raw": '{"active":true,"count":13}', "valid": False},
        ]
        edited_root, edited_observations = compare(
            paths["tools"], imported, edited_schema, edited_cases, "native_edited")
        if edited_root != committed["new_root"] or reference(original_schema, b'{"active":true,"count":11}'):
            raise RuntimeError("N10 edit did not change the declared native bound")
        edited_artifact = imported / "artifact/edited-native-graph.json"
        invoke(paths["tools"], "artifact", "export", "--project", imported,
               "--output", edited_artifact, "--bind-manifest")
        replay = scratch / "replay"
        replay_report = invoke(paths["tools"], "artifact", "import", edited_artifact,
                               replay, "--core", paths["core"])
        attach_mapping(package, replay)
        replay_root, _ = compare(paths["tools"], replay, edited_schema,
                                 edited_cases, "native_edited")
        if replay_root == edited_root or replay_report["verification"]["result"] != "PASS":
            raise RuntimeError("N10 edited native artifact did not rebase and replay")

    evidence = {
        "contract": "sley-tools.generated-validator-catalog-probe.v1", "result": "PASS",
        "core_binary_sha256": digest(paths["core"]),
        "tools_binary_sha256": digest(paths["tools"]),
        "schema_sha256": digest(paths["schema"]),
        "catalog_sha256": digest(paths["catalog"]),
        "manifest_sha256": digest(manifest_path),
        "artifact_sha256": digest(artifact_path),
        "recipe_sha256": digest(recipe_path),
        "typed_cases_sha256": digest(typed_path),
        "raw_cases_sha256": digest(raw_path),
        "edited_cases_sha256": digest(edited_path),
        "source_schema_sha256": digest(source_path),
        "mapping_sha256": digest(mapping_path),
        "python_example_sha256": digest(example_path),
        "probe_sha256": digest(Path(__file__)),
        "source_root": artifact["source_state_root"],
        "imported_root": initial_root, "edited_root": edited_root,
        "replay_root": replay_root,
        "edit_candidate_id": committed["candidate_id"],
        "edit_receipt_id": committed["receipt_id"],
        "native_object_count": len(artifact["objects"]),
        "fresh_authoring_cases": len(json.loads(typed_path.read_text())["cases"]),
        "independently_checked_initial_cases": len(initial_observations),
        "independently_checked_edited_cases": len(edited_observations),
        "malformed_inputs_rejected": 2,
        "python_native_example_passed": True,
        "edited_artifact_replayed": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
        "initial_observations": initial_observations,
    }
    paths["output"].parent.mkdir(parents=True, exist_ok=True)
    paths["output"].write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    main()
