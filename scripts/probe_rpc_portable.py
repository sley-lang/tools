#!/usr/bin/env python3
"""Exercise workspace-scoped RPC package export, import, and native Review."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from probe_rpc import Client, error_code, invoke


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def success(reply: dict) -> dict:
    if "error" in reply or not isinstance(reply.get("result"), dict):
        raise RuntimeError(f"RPC call failed: {reply}")
    return reply["result"]


def root(project: Path) -> str:
    return json.loads((project / ".sley-tools/state.json").read_text())["state_root"]


def probe(tools: Path, core: Path, examples: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="sley-tools-rpc-portable-") as temporary:
        workspace = Path(temporary)
        project = workspace / "fixed"
        initial_cases = examples / "fixed-limit-cases.json"
        updated_cases = examples / "fixed-limit-after-cases.json"
        invoke(tools, "init", str(project), "--graph", str(examples / "fixed-limit.json"),
               "--tests", str(initial_cases), "--core", str(core))
        original_root = root(project)
        client = Client(tools, project, workspace)
        needed = {"sleytools.artifact.export", "sleytools.artifact.import",
                  "sleytools.review.compare"}
        if not needed.issubset(success(client.call("sleytools.hello", {}))["methods"]):
            raise RuntimeError("scoped RPC does not advertise portable methods")
        handle = success(client.call("sleytools.workspace.open", {}))["handle"]
        before = success(client.call("sleytools.artifact.export",
                                     {"handle": handle, "package": "before"}))
        if before["source_state_root"] != original_root or root(project) != original_root:
            raise RuntimeError("RPC export changed the accepted root")
        error_code(client.call("sleytools.artifact.export",
                               {"handle": handle, "package": "../escape"}), "ST_INPUT_INVALID")
        error_code(client.call("sleytools.artifact.export",
                               {"handle": handle, "package": "before"}), "ST_INPUT_INVALID")
        if (workspace / "escape").exists():
            raise RuntimeError("out-of-scope RPC export wrote a path")
        replay = success(client.call("sleytools.artifact.import",
                                     {"handle": handle, "package": "before",
                                      "destination": "replay"}))
        if replay["import"]["verification"]["result"] != "PASS":
            raise RuntimeError("RPC package import did not pass native assertions")
        retried = success(client.call("sleytools.artifact.import",
                                      {"handle": handle, "package": "before",
                                       "destination": "replay"}))
        if (not retried["reconciled"] or retried["operation_id"] != replay["operation_id"]
                or root(workspace / "replay") != retried["import"]["status"]["state_root"]):
            raise RuntimeError("RPC import did not reconcile a lost successful reply")
        record_path = workspace / ".rpc-import-replay.json"
        operation = json.loads(record_path.read_text())
        operation["phase"] = "started"
        operation.pop("accepted_root", None)
        operation.pop("result", None)
        record_path.write_text(json.dumps(operation) + "\n")
        recovered = success(client.call("sleytools.artifact.import",
                                        {"handle": handle, "package": "before",
                                         "destination": "replay"}))
        if (not recovered["reconciled"] or recovered["operation_id"] != replay["operation_id"]
                or recovered["import"]["status"]["state_root"] != root(workspace / "replay")
                or json.loads(record_path.read_text())["phase"] != "completed"):
            raise RuntimeError("RPC import did not recover a lost completion record")
        test_replay = invoke(tools, "test", "--project", str(workspace / "replay"))
        if test_replay["external_assertions_against_native_execution"] != 4:
            raise RuntimeError("imported package did not reproduce initial cases")
        (workspace / "linked").symlink_to(workspace / "before", target_is_directory=True)
        error_code(client.call("sleytools.artifact.import",
                               {"handle": handle, "package": "linked",
                                "destination": "must-not-exist"}), "ST_INPUT_INVALID")
        if (workspace / "must-not-exist").exists():
            raise RuntimeError("symlinked package created a destination")
        client.close()

        imported_client = Client(tools, workspace / "replay", workspace)
        imported_handle = success(imported_client.call("sleytools.workspace.open", {}))["handle"]
        reexport = success(imported_client.call("sleytools.artifact.export",
                                                {"handle": imported_handle, "package": "reexport"}))
        imported_client.close()
        if (not (workspace / "reexport/artifact/source-native-graph.json").is_file()
                or reexport["source_state_root"] != root(workspace / "replay")):
            raise RuntimeError("RPC reexport lost imported source provenance")

        value = workspace / "new-limit.json"
        value.write_text('"12"\n')
        invoke(tools, "propose", "--project", str(project), "--constant", "limit",
               "--value-file", str(value), "--tests", str(updated_cases))
        invoke(tools, "validate", "--project", str(project))
        invoke(tools, "commit", "--project", str(project))
        edited_root = root(project)
        if edited_root == original_root:
            raise RuntimeError("native graph edit did not change the accepted root")
        client = Client(tools, project, workspace)
        handle = success(client.call("sleytools.workspace.open", {}))["handle"]
        after = success(client.call("sleytools.artifact.export",
                                    {"handle": handle, "package": "after"}))
        if after["source_state_root"] != edited_root or root(project) != edited_root:
            raise RuntimeError("edited RPC export changed the accepted root")
        cases = json.loads(initial_cases.read_text())
        review = success(client.call("sleytools.review.compare",
                                     {"handle": handle, "before": "before", "after": "after",
                                      "cases": cases}))
        if (review["result"] != "PASS_BOUNDED_LOCAL"
                or review["behavioral_evidence"]["changed_case_count"] != 1
                or len(review["structural_change"]["changed"]) != 1):
            raise RuntimeError("RPC Review did not identify the native boundary change")
        shutil.copytree(workspace / "after", workspace / "tampered")
        with (workspace / "tampered/artifact/native-rpc-export.json").open("ab") as file:
            file.write(b"\n")
        error_code(client.call("sleytools.artifact.import",
                               {"handle": handle, "package": "tampered",
                                "destination": "tampered-replay"}), "ST_INPUT_INVALID")
        error_code(client.call("sleytools.review.compare",
                               {"handle": handle, "before": "before", "after": "tampered",
                                "cases": cases}), "ST_INPUT_INVALID")
        if (workspace / "tampered-replay").exists() or root(project) != edited_root:
            raise RuntimeError("tampered package changed a workspace")
        client.close()

        release = workspace / "release"
        invoke(tools, "init", str(release), "--template", "release-readiness", "--core", str(core))
        release_client = Client(tools, release, workspace)
        release_handle = success(release_client.call("sleytools.workspace.open", {}))["handle"]
        success(release_client.call("sleytools.artifact.export",
                                    {"handle": release_handle, "package": "release_before"}))
        release_client.close()
        invoke(tools, "edit", "--project", str(release), "--max-blocking-findings", "1")
        release_client = Client(tools, release, workspace)
        release_handle = success(release_client.call("sleytools.workspace.open", {}))["handle"]
        success(release_client.call("sleytools.artifact.export",
                                    {"handle": release_handle, "package": "release_after"}))
        release_cases = json.loads((examples.parents[1] / "programs/release-readiness/tests/bridge-cases.json").read_text())
        release_review = success(release_client.call("sleytools.review.compare",
                                                     {"handle": release_handle, "before": "release_before",
                                                      "after": "release_after", "cases": release_cases},
                                                     timeout_seconds=120))
        if (release_review["result"] != "PASS_BOUNDED_LOCAL"
                or release_review["behavioral_evidence"]["changed_case_count"] != 12):
            raise RuntimeError("RPC fixed-program Review disagreed with declared cases")
        release_replay = success(release_client.call("sleytools.artifact.import",
                                                    {"handle": release_handle,
                                                     "package": "release_after",
                                                     "destination": "release_replay"}))
        if release_replay["import"]["verification"]["result"] != "PASS":
            raise RuntimeError("RPC fixed-program import did not pass assertions")
        release_client.close()

        unscoped = Client(tools, project)
        if needed.intersection(success(unscoped.call("sleytools.hello", {}))["methods"]):
            raise RuntimeError("unscoped RPC advertised package methods")
        unscoped_handle = success(unscoped.call("sleytools.workspace.open", {}))["handle"]
        error_code(unscoped.call("sleytools.artifact.export",
                                 {"handle": unscoped_handle, "package": "unscoped"}),
                   "ST_FEATURE_UNSUPPORTED")
        unscoped.close()
        if (workspace / "unscoped").exists():
            raise RuntimeError("unscoped RPC wrote a package")

        wrong = subprocess.run([str(tools), "rpc", "--project", str(project),
                                "--workspace", str(workspace / "before")],
                               input="", capture_output=True, text=True, timeout=10)
        if wrong.returncode != 2 or wrong.stdout:
            raise RuntimeError("RPC accepted a workspace that does not contain the project")
        return {
            "contract": "sley-tools.rpc-portable-probe.v0",
            "result": "PASS",
            "tools_binary_sha256": sha256(tools),
            "core_binary_sha256": sha256(core),
            "initial_root": original_root,
            "edited_root": edited_root,
            "before_manifest_sha256": before["manifest_sha256"],
            "after_manifest_sha256": after["manifest_sha256"],
            "review_changed_cases": review["behavioral_evidence"]["changed_case_count"],
            "review_changed_native_objects": len(review["structural_change"]["changed"]),
            "release_review_changed_cases": release_review["behavioral_evidence"]["changed_case_count"],
            "replay_external_assertions": test_replay["external_assertions_against_native_execution"],
            "imported_project_reexported": True,
            "lost_import_reply_and_completion_record_recovered": True,
            "scope_and_tamper_refusals": True,
            "unscoped_methods_unavailable": True,
            "native_core_test_admission": "NOT_RUN: production native executor unavailable",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--examples", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = probe(args.tools.resolve(), args.core.resolve(), args.examples.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
