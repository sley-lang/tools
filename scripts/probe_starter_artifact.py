#!/usr/bin/env python3
"""Verify accepted native object export and authority-free fresh import."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def invoke(tools: Path, *args: str, expect: int = 0) -> dict:
    result = subprocess.run([str(tools), *args], capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != expect:
        raise RuntimeError(f"{' '.join(args[:2])}: exit {result.returncode}, expected {expect}: {result.stderr[:350]}")
    content = result.stdout if expect == 0 else result.stderr
    return json.loads(content)


def state(project: Path) -> dict:
    return json.loads((project / ".sley-tools/state.json").read_text(encoding="utf-8"))


def matrix(tools: Path, project: Path) -> list[dict]:
    observations = []
    for completed in [False, True]:
        for failures in [0, 1, 2**32 - 1]:
            for verified in [False, True]:
                for blocking in [0, 1, 2, 3, 2**32 - 1]:
                    facts = {
                        "test_run_completed": completed,
                        "tests_failed": failures,
                        "artifact_verified": verified,
                        "blocking_findings": blocking,
                    }
                    input_path = project / "inputs/probe.json"
                    input_path.write_text(json.dumps(facts), encoding="utf-8")
                    observed = invoke(tools, "run", "--project", str(project), "--input", str(input_path))
                    observations.append(observed["result"])
    return observations


def probe(tools: Path, core: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="sley-artifact-probe-") as temporary:
        root = Path(temporary)
        source, destination, third = (root / name for name in ("source", "destination", "third"))
        package, second_package = root / "program.json", root / "second-program.json"
        invoke(tools, "init", str(source), "--template", "release-readiness", "--core", str(core))
        invoke(tools, "edit", "--project", str(source), "--max-blocking-findings", "2")
        source_state = state(source)
        exported = invoke(tools, "artifact", "export", "--project", str(source), "--output", str(package))
        raw = package.read_bytes()
        payload = json.loads(raw)
        if exported["sha256"] != hashlib.sha256(raw).hexdigest() or exported["object_count"] != 20:
            raise RuntimeError("export report differs from exact package bytes")
        if sorted(payload) != sorted([
            "contract", "program_id", "core_commit", "schema_epoch_id", "source_state_root",
            "source_function_id", "source_threshold_id", "source_threshold_object_id",
            "threshold", "result_fields", "objects", "test_evidence",
        ]):
            raise RuntimeError("artifact has an unexpected top-level field")
        if any(term in raw for term in [b"principal_id", b"policy_root", b"workspace_id", b"core_binary_sha256", str(root).encode()]):
            raise RuntimeError("artifact contains workspace authority or a local path")
        if payload["threshold"] != 2 or payload["source_state_root"] != source_state["state_root"]:
            raise RuntimeError("artifact does not represent edited accepted source")
        if {kind: sum(item["kind"] == kind for item in payload["objects"]) for kind in range(4, 10)} != {4: 1, 5: 1, 6: 4, 7: 1, 8: 11, 9: 2}:
            raise RuntimeError("artifact contains an authority or unexpected entity kind")
        if invoke(tools, "artifact", "export", "--project", str(source), "--output", str(package), expect=2)["code"] != "ST_INPUT_INVALID":
            raise RuntimeError("artifact export overwrote an existing file")

        imported = invoke(tools, "artifact", "import", str(package), str(destination), "--core", str(core))
        destination_state = state(destination)
        if source_state["workspace_id"] == destination_state["workspace_id"] or source_state["principal_id"] == destination_state["principal_id"]:
            raise RuntimeError("import reused source workspace authority")
        if source_state["function_id"] == destination_state["function_id"] or source_state["state_root"] == destination_state["state_root"]:
            raise RuntimeError("import did not rebase native program identities")
        if destination_state["threshold"] != 2 or destination_state["commit_history"][0]["kind"] != "artifact_import":
            raise RuntimeError("import did not commit edited native graph")
        if imported["verification"]["external_assertions_against_native_execution"] != 60:
            raise RuntimeError("fresh import lacked native execution assertions")
        if (destination / "artifact/native-program.json").read_bytes() != raw:
            raise RuntimeError("fresh project did not preserve exact imported artifact")
        imported_manifest = json.loads((destination / "program.json").read_text())
        if imported_manifest["artifact"] is not None or imported_manifest["imported_source_artifact"]["sha256"] != exported["sha256"]:
            raise RuntimeError("rebased import incorrectly claims source artifact as current root")
        source_matrix, imported_matrix = matrix(tools, source), matrix(tools, destination)
        if source_matrix != imported_matrix:
            raise RuntimeError("fresh import does not reproduce source native behavior")
        if invoke(tools, "artifact", "import", str(package), str(destination), "--core", str(core), expect=2)["code"] != "ST_INPUT_INVALID":
            raise RuntimeError("artifact import overwrote existing project")

        tampered = root / "tampered.json"
        damaged = json.loads(raw)
        first = damaged["objects"][0]["stored_hex"]
        damaged["objects"][0]["stored_hex"] = ("0" if first[0] != "0" else "1") + first[1:]
        tampered.write_text(json.dumps(damaged), encoding="utf-8")
        bad_project = root / "tampered-project"
        refusal = invoke(tools, "artifact", "import", str(tampered), str(bad_project), "--core", str(core), expect=4)
        if refusal["code"] != "ST_IMPORT_UNSUPPORTED" or ("digest" not in refusal["message"].lower() and "magic" not in refusal["message"].lower()):
            raise RuntimeError("tampered native object was not rejected by its core codec")
        if json.loads((bad_project / ".sley-tools/status.json").read_text())["state"] != "INCOMPLETE":
            raise RuntimeError("rejected import was marked ready")
        duplicate = root / "duplicate-field.json"
        duplicate.write_bytes(raw.replace(b"{", b'{"contract":"duplicate",', 1))
        duplicate_refusal = invoke(tools, "artifact", "import", str(duplicate), str(root / "duplicate-project"), "--core", str(core), expect=4)
        if duplicate_refusal["code"] != "ST_IMPORT_UNSUPPORTED" or "duplicate field" not in duplicate_refusal["message"]:
            raise RuntimeError("duplicate artifact metadata was not rejected")
        unknown = root / "unknown-field.json"
        unknown.write_bytes(raw.replace(b"{", b'{"extra":"value",', 1))
        unknown_refusal = invoke(tools, "artifact", "import", str(unknown), str(root / "unknown-project"), "--core", str(core), expect=4)
        if unknown_refusal["code"] != "ST_IMPORT_UNSUPPORTED" or "unknown field" not in unknown_refusal["message"]:
            raise RuntimeError("unknown artifact metadata was not rejected")

        changed = invoke(tools, "edit", "--project", str(destination), "--max-blocking-findings", "1")
        manifest = json.loads((destination / "program.json").read_text())
        if manifest["artifact"] is not None or manifest["imported_source_artifact"]["sha256"] != exported["sha256"]:
            raise RuntimeError("editing an imported program left a stale current-artifact claim")
        if changed["new_threshold"] != 1:
            raise RuntimeError("edited import did not change threshold")
        exported_again = invoke(tools, "artifact", "export", "--project", str(destination), "--output", str(second_package))
        invoke(tools, "artifact", "import", str(second_package), str(third), "--core", str(core))
        if state(third)["threshold"] != 1 or invoke(tools, "test", "--project", str(third))["external_assertions_against_native_execution"] != 48:
            raise RuntimeError("second-generation native artifact failed to replay")
        return {
            "contract": "sley-tools.starter-artifact-probe.v1",
            "result": "PASS",
            "core_binary_sha256": hashlib.sha256(core.read_bytes()).hexdigest(),
            "source_root": source_state["state_root"],
            "imported_root": destination_state["state_root"],
            "source_artifact_sha256": exported["sha256"],
            "second_artifact_sha256": exported_again["sha256"],
            "native_object_count": len(payload["objects"]),
            "source_import_matrix_cases": len(source_matrix),
            "fresh_workspace_and_principal": True,
            "source_authority_excluded": True,
            "tampered_object_rejected": True,
            "duplicate_and_unknown_fields_rejected": True,
            "imported_edit_reexport_reimport_passed": True,
            "native_test_admission": "NOT_RUN: production native executor unavailable",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", type=Path, required=True)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = probe(args.tools.resolve(), args.core.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: native artifact, fresh authority, replay, and tamper rejection passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"artifact probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
