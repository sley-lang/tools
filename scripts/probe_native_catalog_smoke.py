#!/usr/bin/env python3
"""Import every catalog artifact and run its declared cases with the selected core.

This checks package integrity and external assertions against native execution.
It does not claim core-owned native TestCase admission or an independent oracle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REQUIRED_IDS = {f"N{number:02d}" for number in range(1, 11)}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    def unique(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON field {key!r} in {path}")
            result[key] = value
        return result

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def catalog_file(relative: str) -> Path:
    selected = ROOT / relative
    path = selected.resolve()
    if selected.is_symlink() or not path.is_relative_to(ROOT) or not path.is_file():
        raise ValueError(f"catalog path is not a regular file within the repository: {relative}")
    return path


def invoke(tools: Path, *args: str) -> dict:
    result = subprocess.run([str(tools), *args], capture_output=True, text=True,
                            timeout=120, check=False)
    if result.returncode:
        raise RuntimeError(f"sley-tools {args[0]} failed ({result.returncode}): "
                           f"{result.stderr.strip()[:500]}")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError(f"sley-tools {args[0]} returned a non-object response")
    return value


def probe(tools: Path, core: Path, catalog: Path) -> dict:
    tools = tools.resolve(strict=True)
    core = core.resolve(strict=True)
    core_hash = sha256(core)
    entries = read_json(catalog)["programs"]
    if not isinstance(entries, list):
        raise ValueError("program catalog has no programs list")
    ids = [entry["id"] for entry in entries]
    program_ids = [entry["program_id"] for entry in entries]
    if any(not isinstance(identifier, str) or not re.fullmatch(r"N[0-9]{2}", identifier)
           for identifier in ids):
        raise ValueError("program catalog contains an invalid native seed ID")
    if len(ids) != len(set(ids)) or len(program_ids) != len(set(program_ids)):
        raise ValueError("program catalog contains duplicate IDs")
    if not REQUIRED_IDS.issubset(ids):
        raise ValueError(f"program catalog is missing initial native seeds: {sorted(REQUIRED_IDS - set(ids))}")

    checked = []
    with tempfile.TemporaryDirectory(prefix="sley-tools-catalog-smoke-") as temporary:
        for entry in entries:
            package = entry["package"]
            manifest_path = catalog_file(f"{package}/program.json")
            artifact_path = catalog_file(entry["artifact"])
            manifest = read_json(manifest_path)
            if manifest["program_id"] != entry["program_id"]:
                raise ValueError(f"{entry['id']}: catalog and manifest program IDs disagree")
            if manifest["status"] != "portable_native_artifact":
                raise ValueError(f"{entry['id']}: no portable native artifact")
            if manifest["core"]["binary_sha256"] != core_hash:
                raise ValueError(f"{entry['id']}: selected core differs from manifest")
            if (manifest_path.parent / manifest["artifact"]["path"]).resolve() != artifact_path:
                raise ValueError(f"{entry['id']}: catalog and manifest artifact paths disagree")
            if sha256(artifact_path) != manifest["artifact"]["sha256"]:
                raise ValueError(f"{entry['id']}: native artifact digest differs from manifest")

            project = Path(temporary) / entry["id"]
            imported = invoke(tools, "artifact", "import", str(artifact_path), str(project),
                              "--core", str(core))
            program = imported["program"]
            expected_import_contract = ("sley-tools.artifact-import.v0"
                                        if manifest["artifact"]["contract"] == "sley-tools.native-program-artifact.v0"
                                        else "sley-tools.artifact-import.v1")
            if (imported["contract"] != expected_import_contract
                    or program["program_id"] != entry["program_id"]
                    or program["core"]["binary_sha256"] != core_hash):
                raise ValueError(f"{entry['id']}: imported program identity differs from catalog")
            tested = invoke(tools, "test", "--project", str(project))
            count = tested["external_assertions_against_native_execution"]
            if (tested["contract"] != "sley-tools.test.v0"
                    or tested["result"] != "PASS"
                    or tested["program_id"] != entry["program_id"]
                    or tested["state_root"] != program["entrypoint"]["state_root"]
                    or type(count) is not int or count < 1):
                raise ValueError(f"{entry['id']}: imported native program did not pass declared cases")
            checked.append({"id": entry["id"], "program_id": entry["program_id"],
                            "artifact_sha256": manifest["artifact"]["sha256"],
                            "accepted_root": tested["state_root"],
                            "external_assertions": count,
                            "native_core_test_admission": tested["native_core_test_admission"]})
            print(f"{entry['id']} {entry['program_id']}: {count} external assertions passed")

    return {"contract": "sley-tools.native-catalog-smoke.v1", "result": "PASS",
            "catalog_sha256": sha256(catalog), "tools_sha256": sha256(tools),
            "core_sha256": core_hash, "programs": checked,
            "external_assertions": sum(item["external_assertions"] for item in checked)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--catalog", type=Path, default=ROOT / "programs/catalog.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = probe(args.tools, args.core, args.catalog.resolve(strict=True))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"PASS: {len(report['programs'])} native programs, "
          f"{report['external_assertions']} external assertions")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, RuntimeError,
            subprocess.TimeoutExpired) as error:
        print(f"native catalog smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1)
