#!/usr/bin/env python3
"""Run the N10 native validator on one raw JSON document."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sley_tools.schema_importer import validate_project  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="sley-n10-example-") as scratch:
        project = Path(scratch) / "validator"
        subprocess.run(
            [str(args.tools.resolve()), "artifact", "import",
             str((args.package / "artifact/native-graph.json").resolve()),
             str(project), "--core", str(args.core.resolve())],
            check=True, capture_output=True, text=True, timeout=120,
        )
        schema_dir = project / "schema"
        schema_dir.mkdir()
        shutil.copyfile(args.package / "schema/source.json", schema_dir / "source.json")
        mapping = json.loads((args.package / "schema/mapping.json").read_text())
        state = json.loads((project / ".sley-tools/state.json").read_text())
        manifest = json.loads((project / "program.json").read_text())
        mapping["native_function_entity_id"] = state["function_id"]
        mapping["native_result_type_entity_id"] = manifest["result_type_id"]
        mapping["native_constant_entity_ids"] = state["constant_ids"]
        for property_mapping in mapping["properties"]:
            if property_mapping["type"] == "integer":
                property_mapping["native_bound_entities"] = {
                    "minimum": state["constant_ids"][property_mapping["minimum_constant"]],
                    "maximum": state["constant_ids"][property_mapping["maximum_constant"]],
                }
        (schema_dir / "mapping.json").write_text(json.dumps(mapping, sort_keys=True) + "\n")
        report = validate_project(project, args.input.read_bytes(), args.tools.resolve())
        print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
