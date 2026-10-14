#!/usr/bin/env python3
"""Record the executable identity and existing-artifact part of Sley F0.

This intentionally does not infer construction, commit, or native-test support
from method names. Those operations need their own real authoring probe.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import subprocess
import sys
import tarfile
from pathlib import Path


PROFILES = ("legacy", "v2-capable", "v3-capable")
EXPECTED_ARCHIVE_SHA256 = "b0898ba7404a1656db2f83f448ea96d2c1175346f35e50b135bedc96e3810bfd"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_native_test_route_evidence(report: dict, path: Path, tools: Path, builder: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": "sley-tools.f0-native-test-route-probe.v1",
        "result": "PASS",
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "case_id": "zero",
        "candidate_validation_decision": 1,
        "native_test_route": "v3 tests.affected",
        "native_test_route_failure": "NATIVE_EXECUTOR_UNAVAILABLE",
        "native_test_admission": "NOT_RUN: production native executor unavailable",
        "accepted_root_unchanged": True,
        "cli_static_candidate_validation": True,
        "native_plan_selected_test_count": 1,
        "native_plan_preflight": "CORE_LIBRARY_ONLY_NO_EXECUTION",
        "compatibility_status": "requires_core_change",
        "negative_builder_checks": ["expected type", "input arity", "stale base root"],
        "negative_public_checks": ["absent case", "non-graph project"],
    }
    if any(observed.get(key) != value for key, value in expected.items()):
        raise RuntimeError("native TestCase route evidence differs from the selected core or probe contract")
    if observed.get("builder_binary_sha256") != sha256(builder) or observed.get("tools_binary_sha256") != sha256(tools):
        raise RuntimeError("native TestCase route evidence used different tools binaries")
    declared = observed.get("declared_limits", {})
    ceilings = observed.get("grant_ceilings", {})
    if (declared.get("memory_bytes") != 16 * 1024 * 1024
            or any(type(declared.get(name)) is not int or declared[name] > ceilings.get(cap, -1)
                   for name, cap in (("fuel", "max_fuel"), ("memory_bytes", "max_memory_bytes"),
                                     ("output_bytes", "max_output_bytes"),
                                     ("effect_count", "max_effect_count")))):
        raise RuntimeError("native TestCase declared limits exceed accepted grant ceilings")
    for source in ("scripts/probe_native_test_route.py", "examples/graph/fixed-limit.json",
                   "examples/graph/fixed-limit-cases.json"):
        if observed.get("source_sha256", {}).get(source) != sha256(Path(source)):
            raise RuntimeError(f"native TestCase route evidence differs from {source}")
    if any(not isinstance(observed.get(key), str) or len(observed[key]) != 64 for key in (
        "candidate_id", "cli_candidate_id", "test_entity_id", "native_execution_profile_id",
        "native_plan_id"
    )):
        raise RuntimeError("native TestCase route evidence omitted exact identities")
    report["capabilities"]["native_test_case_candidate_static_validation"] = {
        "status": "supported",
        "evidence": path.as_posix(),
        "scope": "fresh Fixed Limit project; public CLI and direct core both validate real TestCase candidates, invalid case and builder inputs refused, accepted root unchanged; no native test executed",
    }
    report["capabilities"]["native_test_plan_preflight_core_library"] = {
        "status": "supported",
        "evidence": path.as_posix(),
        "scope": "selected core library derives a one-TestCase N4 plan from fresh validator-owned output and the accepted grant; planning is read-only and no worker ran",
    }
    report["capabilities"]["native_test_admission_selected_core"] = {
        "status": "requires_core_change",
        "evidence": path.as_posix(),
        "scope": "v3 tests.affected refuses the valid TestCase candidate with NATIVE_EXECUTOR_UNAVAILABLE; production executor/supervisor wiring is required",
    }
    report["gaps"]["native_test_admission"] = {
        "status": "requires_core_change",
        "observed": "candidate.validate accepts a new TestCase; v3 tests.affected returns NATIVE_EXECUTOR_UNAVAILABLE",
        "source": f"selected Sley 2.1.1 standalone CLI, {path.as_posix()}",
        "bounded_next_step": "wire and verify the core-owned production executor/supervisor, then rerun F0 native test admission and commit from a fresh workspace",
    }


def command_json(command: list[str], timeout: int = 120) -> dict:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{command[1]} failed with exit {result.returncode}: {result.stderr.strip()[:500]}")
    return json.loads(result.stdout)


def probe(artifact_root: Path, archive: Path | None, genesis_evidence: Path | None, release_readiness_evidence: Path | None, doctor_evidence: Path | None, starter_init_evidence: Path | None, starter_edit_evidence: Path | None, starter_artifact_evidence: Path | None, catalog_dir: Path | None, catalog_evidence: Path | None, schema_evidence: Path | None, schema_file: Path | None) -> dict:
    manifest = json.loads((artifact_root / "MANIFEST.json").read_text(encoding="utf-8"))
    binary = artifact_root / "bin/sley"
    expected_binary = next(item for item in manifest["files"] if item["path"] == "bin/sley")
    actual_binary_hash = sha256(binary)
    if actual_binary_hash != expected_binary["sha256"]:
        raise RuntimeError("release binary does not match MANIFEST.json")
    if archive is not None and sha256(archive) != EXPECTED_ARCHIVE_SHA256:
        raise RuntimeError("archive SHA-256 differs from the selected public 2.1.1 release")
    if manifest["commit"] != "af34be33eb1ae913dddd92aa7f4f275aac0e45ee":
        raise RuntimeError("artifact commit differs from selected public 2.1.1 release")

    profiles = {}
    for profile in PROFILES:
        flags = [] if profile == "legacy" else ["--protocol-profile", profile]
        version = command_json([str(binary), "version", *flags])
        hello = command_json([str(binary), "hello", "--json", *flags])
        methods = command_json([str(binary), "methods", *flags])
        offered = set(hello["methods"])
        listed = {item["name"] for item in methods["methods"] if not item["reserved"]}
        if offered != listed:
            raise RuntimeError(f"{profile}: hello and method table disagree")
        profiles[profile] = {
            "version": version,
            "protocol_versions": hello["protocol_versions"],
            "schema_epochs": hello["schema_epochs"],
            "features": hello["features"],
            "limits": hello["limits"],
            "offered_methods": sorted(offered),
            "reserved_methods": sorted(item["name"] for item in methods["methods"] if item["reserved"]),
        }

    demo = command_json([sys.executable, str(artifact_root / "demo/run_demo.py")])
    if demo.get("result") != "PASS":
        raise RuntimeError(f"packaged demo failed: {demo.get('problems')}")
    report = {
        "contract": "sley-tools.compatibility.v1",
        "selected_core": {
            "release": "v2.1.1",
            "commit": manifest["commit"],
            "artifact": manifest["artifact"],
            "archive_sha256": EXPECTED_ARCHIVE_SHA256 if archive is not None else None,
            "binary_sha256": actual_binary_hash,
            "platform": platform.machine() + "-" + platform.system().lower(),
            "target": manifest["target"],
        },
        "profiles": profiles,
        "capabilities": {
            "release_fixture_import_query_execute_export_reimport": {
                "status": "supported",
                "evidence": "packaged release demo passed against exact selected binary",
                "scope": "existing release fixture only; no new authoring or caller-supplied inputs",
            },
        },
        "f0": {
            "status": "NOT_RUN",
            "remaining": [
                "scoped workspace authority and initialization",
                "new native function construction and authorized commit",
                "two distinct caller-supplied inputs",
                "targeted behavior edit and rejection with unchanged root",
                "native test admission and execution",
                "portable transfer of the newly constructed artifact",
            ],
        },
        "gaps": {},
        "interpretation": "Method offers and the packaged fixture demo do not establish new-program authoring or native test admission. Each capability is limited to its validated linked evidence; evidence omitted from this invocation is not verified by this report.",
    }
    if genesis_evidence is not None:
        genesis = json.loads(genesis_evidence.read_text(encoding="utf-8"))
        if genesis.get("contract") != "sley-tools.f0-genesis-probe.v1" or genesis.get("result") != "PASS" or not genesis.get("workspace_open_exact_identity_match"):
            raise RuntimeError("genesis evidence did not pass exact identity verification")
        if genesis.get("binary_sha256") != actual_binary_hash:
            raise RuntimeError("genesis probe used a different core binary")
        if genesis.get("schema_epoch") not in profiles["legacy"]["schema_epochs"]:
            raise RuntimeError("genesis schema epoch differs from selected release")
        report["capabilities"]["fresh_scoped_workspace_genesis"] = {
            "status": "supported",
            "evidence": genesis_evidence.as_posix(),
            "scope": "new local workspace via public codecs and SMP1",
        }
        if genesis.get("candidate_stored_matches_core") and len(genesis.get("executions", [])) == 2 and genesis.get("root_unchanged_after_rejection") and len(genesis.get("edited_executions", [])) == 2:
            report["capabilities"]["new_function_commit_execute_edit_reject"] = {
                "status": "supported",
                "evidence": genesis_evidence.as_posix(),
                "scope": "Bool identity function and BoolNot edit; outputs proven by VM report hashes",
            }
            for item in (
                "new native function construction and authorized commit",
                "two distinct caller-supplied inputs",
                "targeted behavior edit and rejection with unchanged root",
            ):
                report["f0"]["remaining"].remove(item)
        if genesis.get("fresh_import_reproduced_report_ids"):
            report["capabilities"]["new_artifact_export_import"] = {
                "status": "supported",
                "evidence": genesis_evidence.as_posix(),
                "scope": "accepted edited function exported and reproduced in a fresh workspace",
            }
            report["f0"]["remaining"].remove("portable transfer of the newly constructed artifact")
        if genesis.get("native_tests_selected_v3_result") == "NATIVE_EXECUTOR_UNAVAILABLE":
            report["capabilities"]["native_tests_via_unconfigured_release_cli"] = {
                "status": "unsupported",
                "evidence": genesis_evidence.as_posix(),
                "scope": "v3 tests.selected with a valid body on standalone 2.1.1 CLI; a provisioned public-library executor remains to be probed",
            }
            report["gaps"]["native_test_admission"] = {
                "status": "requires_core_change",
                "observed": "NATIVE_EXECUTOR_UNAVAILABLE from v3 tests.selected on the selected release binary",
                "source": "sley-lang/sley at af34be3, packaging/sley-test-supervisor/README.md: root supervisor event loop and execution dispatch pending",
                "bounded_next_step": "Complete and verify the core-owned production executor/supervisor path, then rerun F0 native test admission from a fresh workspace; keep any local tools-side tests labeled external assertions",
            }
        report["capabilities"]["typed_output_via_release_smp1_execute"] = {
            "status": "unsupported",
            "evidence": genesis_evidence.as_posix(),
            "scope": "SMP1 execute returns a report with a value hash, not the typed value",
        }
        report["f0"]["status"] = "IN_PROGRESS"
        report["f0"]["remaining"].remove("scoped workspace authority and initialization")
    if release_readiness_evidence is not None:
        readiness = json.loads(release_readiness_evidence.read_text(encoding="utf-8"))
        if readiness.get("contract") != "sley-tools.release-readiness-probe.v1" or readiness.get("result") != "PASS":
            raise RuntimeError("Release Readiness evidence did not pass")
        if readiness.get("core_binary_sha256") != actual_binary_hash:
            raise RuntimeError("Release Readiness authoring used a different release binary")
        if len(readiness.get("initial_cases", [])) != 18 or len(readiness.get("edited_cases", [])) != 40:
            raise RuntimeError("Release Readiness evidence has incomplete external assertions")
        fields = readiness.get("result_fields", [])
        if [field.get("name") for field in fields] != [
            "allow", "test_run_incomplete", "tests_failed", "artifact_unverified", "blocking_findings_exceeded"
        ] or len({field.get("member_id") for field in fields}) != 5:
            raise RuntimeError("Release Readiness evidence has no stable native result field mapping")
        report["capabilities"]["native_release_readiness_via_public_vm_library"] = {
            "status": "supported",
            "evidence": release_readiness_evidence.as_posix(),
            "scope": "SMP1 constructs/commits; public core VM library loads accepted head and executes extended_v1; 58 typed outputs match independent oracle",
        }
        report["capabilities"]["extended_execute_via_release_cli"] = {
            "status": "unsupported",
            "evidence": release_readiness_evidence.as_posix(),
            "scope": "released CLI omits extended_execute feature from all offered hellos; restricted profile reports VM_LOWER_OPCODE_UNSUPPORTED for this graph",
        }
    if doctor_evidence is not None:
        doctor = json.loads(doctor_evidence.read_text(encoding="utf-8"))
        if doctor.get("contract") != "sley-tools.doctor-probe.v1" or doctor.get("result") != "PASS":
            raise RuntimeError("doctor probe did not pass")
        if doctor.get("tested_core_binary_sha256") != actual_binary_hash:
            raise RuntimeError("doctor probe used a different core binary")
        if (doctor.get("compatible_exit"), doctor.get("incompatible_exit"), doctor.get("invalid_path_exit")) != (0, 3, 2):
            raise RuntimeError("doctor probe exit behavior differs from contract")
        report["capabilities"]["tools_doctor_identity_and_negative_paths"] = {
            "status": "supported",
            "evidence": doctor_evidence.as_posix(),
            "scope": "local sley-tools doctor against exact release binary, incompatible file, and missing path; optional runtimes reported",
        }
    if starter_init_evidence is not None:
        starter = json.loads(starter_init_evidence.read_text(encoding="utf-8"))
        if starter.get("contract") != "sley-tools.starter-init-probe.v1" or starter.get("result") != "PASS":
            raise RuntimeError("Starter fresh-project probe did not pass")
        if starter.get("core_binary_sha256") != actual_binary_hash:
            raise RuntimeError("Starter probe used a different core binary")
        if (starter.get("native_commit_count"), starter.get("external_assertions_against_native_execution")) != (1, 36):
            raise RuntimeError("Starter probe did not execute its native project and external assertions")
        if (starter.get("computed_rejection_exit"), starter.get("check_rejection_exit"), starter.get("invalid_count_exit")) != (0, 1, 2):
            raise RuntimeError("Starter run exit behavior differs from contract")
        report["capabilities"]["starter_local_init_run_test_inspect"] = {
            "status": "supported",
            "evidence": starter_init_evidence.as_posix(),
            "scope": "fresh local project with one native commit; public init/run/test/inspect; external assertions only; this probe does not cover artifact transfer",
        }
    if starter_edit_evidence is not None:
        edited = json.loads(starter_edit_evidence.read_text(encoding="utf-8"))
        if edited.get("contract") != "sley-tools.starter-edit-probe.v1" or edited.get("result") != "PASS":
            raise RuntimeError("Starter worked-edit probe did not pass")
        if edited.get("core_binary_sha256") != actual_binary_hash:
            raise RuntimeError("Starter worked edit used a different core binary")
        if (edited.get("threshold_before"), edited.get("threshold_after"), edited.get("edited_external_assertions")) != (0, 2, 60):
            raise RuntimeError("Starter worked edit did not prove its threshold and assertions")
        if (edited.get("before_allow_at_one"), edited.get("after_allow_at_one")) != (False, True):
            raise RuntimeError("Starter worked edit did not change its native boundary")
        if not edited.get("root_unchanged_after_stale_rejection") or not edited.get("accepted_copy_executable_after_rejection"):
            raise RuntimeError("Starter stale rejection did not preserve the accepted copy")
        if edited.get("stale_rejection_symbol") != "CANDIDATE_VALIDATION_STALE_ENTITY":
            raise RuntimeError("Starter stale rejection did not use an exact entity precondition")
        report["capabilities"]["starter_local_worked_edit_and_stale_rejection"] = {
            "status": "supported",
            "evidence": starter_edit_evidence.as_posix(),
            "scope": "public threshold edit changed native behavior; 60 edited external assertions; stale exact-object proposal rejected without changing accepted root",
        }
    if starter_artifact_evidence is not None:
        artifact = json.loads(starter_artifact_evidence.read_text(encoding="utf-8"))
        if artifact.get("contract") != "sley-tools.starter-artifact-probe.v1" or artifact.get("result") != "PASS":
            raise RuntimeError("Starter native artifact probe did not pass")
        if artifact.get("core_binary_sha256") != actual_binary_hash:
            raise RuntimeError("Starter artifact probe used a different core binary")
        if (artifact.get("native_object_count"), artifact.get("source_import_matrix_cases")) != (20, 60):
            raise RuntimeError("Starter artifact probe did not prove full native object graph and behavior matrix")
        if not all(artifact.get(field) for field in (
            "fresh_workspace_and_principal", "source_authority_excluded",
            "tampered_object_rejected", "duplicate_and_unknown_fields_rejected",
            "imported_edit_reexport_reimport_passed",
        )):
            raise RuntimeError("Starter artifact probe missed fresh authority, tamper, or replay checks")
        report["capabilities"]["starter_native_object_artifact_rebase"] = {
            "status": "supported",
            "evidence": starter_artifact_evidence.as_posix(),
            "scope": "20 accepted native object records exported without workspace grants; fresh workspace and principal rebase; 60 source/import semantic comparisons; tampered object rejection; edit/reexport/reimport",
        }
    if any(item is not None for item in (catalog_dir, catalog_evidence, schema_evidence, schema_file)):
        if any(item is None for item in (catalog_dir, catalog_evidence, schema_evidence, schema_file)):
            raise RuntimeError("catalog directory, catalog evidence, schema evidence, and schema file must be supplied together")
        catalog = json.loads(catalog_evidence.read_text(encoding="utf-8"))
        schema_probe = json.loads(schema_evidence.read_text(encoding="utf-8"))
        manifest_path = catalog_dir / "program.json"
        native_path = catalog_dir / "artifact/native-program.json"
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        native_data = json.loads(native_path.read_text(encoding="utf-8"))
        if catalog.get("contract") != "sley-tools.catalog-release-readiness.v1" or catalog.get("result") != "PASS":
            raise RuntimeError("catalog build evidence did not pass")
        if schema_probe.get("contract") != "sley-tools.program-schema-probe.v1" or schema_probe.get("result") != "PASS":
            raise RuntimeError("program manifest schema evidence did not pass")
        if catalog.get("core_binary_sha256") != actual_binary_hash or schema_probe.get("core_binary_sha256") != actual_binary_hash:
            raise RuntimeError("catalog or schema probe used a different core binary")
        if catalog.get("artifact_sha256") != sha256(native_path) or schema_probe.get("catalog_artifact_sha256") != sha256(native_path):
            raise RuntimeError("catalog artifact digest differs from recorded evidence")
        if schema_probe.get("catalog_manifest_sha256") != sha256(manifest_path) or schema_probe.get("schema_sha256") != sha256(schema_file):
            raise RuntimeError("manifest or schema differs from validation evidence")
        if manifest_data.get("entrypoint", {}).get("state_root") != catalog.get("source_state_root") or native_data.get("source_state_root") != catalog.get("source_state_root"):
            raise RuntimeError("catalog accepted root identity differs among artifact, manifest, and evidence")
        if (catalog.get("artifact_object_count"), catalog.get("imported_external_assertions")) != (20, 36):
            raise RuntimeError("catalog artifact did not prove native graph and fresh replay")
        if not catalog.get("fresh_workspace_authority") or not all(schema_probe.get(field) for field in (
            "local_manifest_valid", "resource_local_manifest_valid", "validator_rejected_result_type_mismatch", "imported_manifest_valid", "edited_import_manifest_valid", "resource_edited_import_manifest_valid",
            "schema_rejected_missing_fields_traversal_and_unbound_portable_artifact",
            "validator_rejected_false_artifact_checksum",
            "validator_rejected_duplicate_json_field",
        )):
            raise RuntimeError("catalog authority or manifest schema checks are incomplete")
        report["capabilities"]["catalog_native_artifact_and_manifest_schema"] = {
            "status": "supported",
            "evidence": [catalog_evidence.as_posix(), schema_evidence.as_posix()],
            "scope": "checked-in Release Readiness native object artifact and schema-validated manifest; 36 fresh-import assertions; malformed manifest and checksum rejection",
        }
    return report


def add_bridge_evidence(report: dict, artifact: Path, fixtures: Path, rpc_path: Path, python_path: Path, typescript_path: Path, soak_path: Path) -> None:
    expected = {
        rpc_path: "sley-tools.rpc-probe.v1",
        python_path: "sley-tools.python-sdk-probe.v1",
        typescript_path: "sley-tools.typescript-sdk-probe.v1",
        soak_path: "sley-tools.bridge-soak-probe.v1",
    }
    evidence = {}
    for path, contract in expected.items():
        observed = json.loads(path.read_text(encoding="utf-8"))
        if observed.get("contract") != contract or observed.get("result") != "PASS":
            raise RuntimeError(f"Bridge evidence did not pass: {path}")
        if observed.get("core_binary_sha256") != report["selected_core"]["binary_sha256"]:
            raise RuntimeError(f"Bridge evidence used a different core binary: {path}")
        if not observed.get("copied_binaries_without_source_checkout") or observed.get("native_test_admission") != "NOT_RUN: production native executor unavailable":
            raise RuntimeError(f"Bridge evidence has incomplete source isolation or native-test status: {path}")
        evidence[contract] = observed
    rpc = evidence["sley-tools.rpc-probe.v1"]
    if rpc.get("truth_table_cases") != 48 or not all(rpc.get(field) for field in (
        "single_worker_reused", "deadline_stopped_worker", "worker_loss_reported",
        "stale_root_rejected", "duplicate_and_deep_requests_rejected", "oversized_request_rejected",
        "explicit_reopen_rotated_handle", "stdout_protocol_only",
    )):
        raise RuntimeError("RPC evidence lacks required native calls or failure paths")
    for contract in ("sley-tools.python-sdk-probe.v1", "sley-tools.typescript-sdk-probe.v1"):
        sdk = evidence[contract]
        if sdk.get("artifact_sha256") != sha256(artifact) or sdk.get("fixtures_sha256") != sha256(fixtures):
            raise RuntimeError(f"Bridge SDK evidence differs from checked-in artifact or fixtures: {contract}")
        if sdk.get("native_cases") != 48 or sdk.get("typed_input_negative_cases") != 8 or not all(sdk.get(field) for field in (
            "one_worker_for_matrix", "i64_wire_limits_exact", "worker_loss_reported", "runtime_closed_process",
        )):
            raise RuntimeError(f"Bridge SDK evidence lacks native calls or failure paths: {contract}")
    soak = evidence["sley-tools.bridge-soak-probe.v1"]
    if soak.get("artifact_sha256") != sha256(artifact) or soak.get("fixtures_sha256") != sha256(fixtures):
        raise RuntimeError("Bridge soak evidence differs from checked-in artifact or fixtures")
    for adapter in ("python", "typescript"):
        sample = soak.get(adapter, {})
        baseline = sample.get("baseline_fds", {})
        rows = sample.get("fd_samples", [])
        if sample.get("calls") != 256 or sample.get("max_allowed_fd_drift") != 2 or not all(sample.get(name) for name in ("worker_reused", "root_pinned", "processes_exited")):
            raise RuntimeError(f"Bridge {adapter} soak has incomplete native process coverage")
        if not isinstance(rows, list) or [row.get("calls") for row in rows] != [64, 128, 192, 256]:
            raise RuntimeError(f"Bridge {adapter} soak has incomplete descriptor samples")
        for name in ("client", "rpc", "worker"):
            if type(baseline.get(name)) is not int or baseline[name] < 1 or any(type(row.get(name)) is not int or row[name] > baseline[name] + 2 for row in rows):
                raise RuntimeError(f"Bridge {adapter} soak grew {name} descriptors")
    report["capabilities"]["bridge_rpc_and_python_typescript_release_readiness"] = {
        "status": "supported",
        "evidence": [rpc_path.as_posix(), python_path.as_posix(), typescript_path.as_posix(), soak_path.as_posix()],
        "scope": "process-scoped RPC; both SDKs call the checked-in Release Readiness artifact across 48 shared cases and 256-call bounded soaks; typed negatives, worker and descriptor stability/cleanup, deadline and stale-root paths",
    }


def add_bridge_cancellation_evidence(report: dict, path: Path, tools: Path,
                                     artifact: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": "sley-tools.bridge-read-cancellation-probe.v1", "result": "PASS",
        "tools_binary_sha256": sha256(tools),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "artifact_sha256": sha256(artifact),
        "python_sdk_sha256": sha256(Path("packages/python/sley_tools/runtime.py")),
        "typescript_sdk_sha256": sha256(Path("packages/typescript/src/index.js")),
        "typescript_types_sha256": sha256(Path("packages/typescript/src/index.d.ts")),
        "python_probe_sha256": sha256(Path("scripts/probe_bridge_cancellation.py")),
        "typescript_probe_sha256": sha256(Path("scripts/probe_bridge_cancellation.mjs")),
        "pre_dispatch_preserved_session": True,
        "invalid_cancel_input_rejected": True,
        "write_cancel_rejected": True,
        "inflight_cancel_stopped_rpc_and_worker": True,
        "read_only_calls_only": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("Bridge cancellation evidence differs from selected SDKs or native inputs")
    report["capabilities"]["bridge_read_only_call_cancellation"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "Python threading.Event and TypeScript AbortSignal cancel read-only native calls; a pre-dispatch cancel preserves the session, while an in-flight cancel stops the local RPC process and native worker; write cancellation uses the separate operation lifecycle and does not accept SDK read signals",
    }


def add_bridge_timing_evidence(report: dict, path: Path, tools: Path,
                               artifact: Path, fixtures: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    extended = observed.get("measurement_profile")
    if extended not in (None, "five_cold_and_sampled_rss_v1"):
        raise RuntimeError("Bridge timing evidence has an unknown measurement profile")
    if extended and (observed.get("probe_checkout_dirty") is not False
                     or not isinstance(observed.get("probe_checkout_commit"), str)
                     or len(observed["probe_checkout_commit"]) != 40):
        raise RuntimeError("Bridge timing evidence was not produced from a clean, identified probe checkout")
    expected = {
        "contract": "sley-tools.bridge-timing-probe.v1", "result": "PASS",
        "tools_binary_sha256": sha256(tools),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "artifact_sha256": sha256(artifact), "fixtures_sha256": sha256(fixtures),
        "python_sdk_sha256": sha256(Path("packages/python/sley_tools/runtime.py")),
        "typescript_sdk_sha256": sha256(Path("packages/typescript/src/index.js")),
        "python_probe_sha256": sha256(Path("scripts/probe_bridge_timing.py")),
        "typescript_probe_sha256": sha256(Path("scripts/probe_bridge_timing.mjs")),
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("Bridge timing evidence differs from selected binaries, SDKs, or fixtures")
    if observed.get("sample_order") != "48 fixture cases in file order; no warmup discarded":
        raise RuntimeError("Bridge timing sample boundary changed")
    for adapter in ("python", "typescript"):
        section = observed.get(adapter, {})
        if section.get("fixture_cases_checked") != 48 or section.get("worker_reused") is not True:
            raise RuntimeError(f"Bridge {adapter} timing omitted correctness or worker reuse")
        if (type(section.get("cold_load_ms")) not in (float, int)
                or not math.isfinite(section["cold_load_ms"]) or section["cold_load_ms"] <= 0):
            raise RuntimeError(f"Bridge {adapter} cold load is invalid")
        for name in ("warm_client_call", "rpc_dispatch_to_worker_result"):
            sample = section.get(name, {})
            if sample.get("samples") != 48 or any(type(sample.get(key)) not in (float, int)
                    or not math.isfinite(sample[key])
                    for key in ("min_ms", "median_ms", "p95_ms", "max_ms")):
                raise RuntimeError(f"Bridge {adapter} {name} has invalid samples")
            if not 0 <= sample["min_ms"] <= sample["median_ms"] <= sample["p95_ms"] <= sample["max_ms"]:
                raise RuntimeError(f"Bridge {adapter} {name} order is invalid")
        if extended:
            raw = section.get("cold_load_samples_ms", [])
            cold = section.get("cold_load_summary", {})
            if (not isinstance(raw, list) or len(raw) != 5
                    or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in raw)
                    or cold.get("samples") != 5):
                raise RuntimeError(f"Bridge {adapter} repeated cold loads are incomplete")
            ordered = sorted(raw)
            if any(not math.isclose(cold.get(name, -1), value, rel_tol=1e-9)
                   for name, value in (("min_ms", ordered[0]), ("median_ms", ordered[2]),
                                       ("p95_ms", ordered[4]), ("max_ms", ordered[4]))):
                raise RuntimeError(f"Bridge {adapter} cold summary differs from samples")
            elapsed = section.get("batch_elapsed_seconds")
            rate = section.get("batch_calls_per_second")
            if (type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed <= 0
                    or type(rate) not in (int, float) or not math.isclose(rate, 48 / elapsed, rel_tol=1e-9)):
                raise RuntimeError(f"Bridge {adapter} batch throughput is invalid")
            rows = section.get("rss_samples", [])
            if not isinstance(rows, list) or [row.get("after_calls") for row in rows] != [1, 12, 24, 36, 48]:
                raise RuntimeError(f"Bridge {adapter} RSS samples are incomplete")
            peaks = []
            for row in rows:
                values = [row.get(name) for name in ("client_kib", "rpc_kib", "worker_kib")]
                if any(type(value) is not int or value <= 0 for value in values):
                    raise RuntimeError(f"Bridge {adapter} RSS sample is invalid")
                peaks.append(sum(values))
            if section.get("sampled_peak_client_rpc_worker_rss_kib") != max(peaks):
                raise RuntimeError(f"Bridge {adapter} sampled peak differs from RSS samples")
            limits = section.get("declared_rpc_limits", {})
            if not isinstance(limits, dict) or limits.get("max_inflight") != 1 or limits.get("max_deadline_ms") != 60_000:
                raise RuntimeError(f"Bridge {adapter} omitted declared RPC limits")
    unavailable = observed.get("unavailable_subdivisions", {})
    if (set(unavailable) != {"encoding_ms", "core_execution_ms", "decoding_ms"}
            or any(not isinstance(reason, str) or not reason for reason in unavailable.values())):
        raise RuntimeError("Bridge timing omitted unavailable subdivisions")
    scope = ("local Python and TypeScript Release Readiness artifact load and 48 correctness-checked post-open calls each; "
             "client wall and coarse RPC durations, median and p95; encoding, core VM execution, and decoding subdivisions unavailable; "
             + ("five fresh cold loads and five sampled process RSS observations per SDK" if extended
                else "one cold-load sample per SDK"))
    report["capabilities"]["bridge_local_cold_and_warm_timing"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": scope,
    }


def add_write_operation_evidence(report: dict, candidate_path: Path, operation_path: Path,
                                 sdk_path: Path, tools: Path, artifact: Path) -> None:
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    operation = json.loads(operation_path.read_text(encoding="utf-8"))
    sdk = json.loads(sdk_path.read_text(encoding="utf-8"))
    for observed, contract, probe in (
        (candidate, "sley-tools.candidate-reconcile-probe.v1", "scripts/probe_candidate_reconcile.py"),
        (operation, "sley-tools.operation-status-probe.v1", "scripts/probe_operation_status.py"),
    ):
        if (observed.get("contract") != contract or observed.get("result") != "PASS"
                or observed.get("tools_binary_sha256") != sha256(tools)
                or observed.get("core_binary_sha256") != report["selected_core"]["binary_sha256"]
                or observed.get("probe_sha256") != sha256(Path(probe))):
            raise RuntimeError(f"write operation evidence differs from pinned binary or probe: {contract}")
    for name in ("uncommitted_outcome_unknown", "verified_committed_receipt",
                 "stale_local_state_recovered", "unknown_candidate_not_claimed_complete",
                 "malformed_id_rejected"):
        if candidate.get(name) is not True:
            raise RuntimeError(f"candidate reconciliation did not verify {name}")
    for name in ("precommit_validated", "commit_bound_to_operation",
                 "completed_verified_by_core", "stale_project_cli_recovered",
                 "stale_project_rpc_recovered", "partial_project_file_rpc_recovered",
                 "cancelled_before_commit_blocked_rpc_and_cli",
                 "completed_cancel_did_not_claim_rollback"):
        if operation.get(name) is not True:
            raise RuntimeError(f"operation status did not verify {name}")
    if not all(isinstance(operation.get(name), str) and len(operation[name]) == 64
               for name in ("operation_id", "candidate_id", "verified_receipt_id")):
        raise RuntimeError("operation status omitted exact identities")
    expected_sdk = {
        "contract": "sley-tools.sdk-commit-recovery-probe.v1", "result": "PASS",
        "tools_binary_sha256": sha256(tools),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "artifact_sha256": sha256(artifact),
        "python_sdk_sha256": sha256(Path("packages/python/sley_tools/runtime.py")),
        "typescript_sdk_sha256": sha256(Path("packages/typescript/src/index.js")),
        "python_probe_sha256": sha256(Path("scripts/probe_sdk_commit_recovery.py")),
        "typescript_probe_sha256": sha256(Path("scripts/probe_sdk_commit_recovery.mjs")),
        "python_predispatch_unknown_preserved": True,
        "python_completed_commit_identified_and_preserved": True,
        "python_cancel_respected_commit_boundary": True,
    }
    if any(sdk.get(name) != value for name, value in expected_sdk.items()) or any(
            sdk.get("typescript", {}).get(name) is not True for name in (
                "predispatch_unknown_preserved", "completed_commit_identified_and_preserved",
                "cancel_respected_commit_boundary")):
        raise RuntimeError("SDK commit recovery evidence differs from current clients or native behavior")
    report["capabilities"]["verified_ordinary_candidate_reconciliation"] = {
        "status": "supported", "evidence": candidate_path.as_posix(),
        "scope": "verified ordinary Sley receipt ancestry identifies one committed candidate after local project metadata becomes stale; unobserved candidates remain outcome_unknown, never retry-safe",
    }
    report["capabilities"]["bridge_durable_write_operation_status"] = {
        "status": "supported", "evidence": [operation_path.as_posix(), sdk_path.as_posix()],
        "scope": "durable operation ID before ordinary commit; core-verified status survives stale or partial local metadata; both SDKs preserve workspace on lost replies and reconcile completed versus unknown; cancellation before commit blocks RPC and CLI writes, while in-flight cancellation cannot preempt a locked commit",
    }


def add_resource_evidence(report: dict, fixtures: Path, evidence_path: Path, artifact_evidence_path: Path, edit_evidence_path: Path, catalog_path: Path, resource_dir: Path, catalog_evidence_path: Path, schema_evidence_path: Path) -> None:
    observed = json.loads(evidence_path.read_text(encoding="utf-8"))
    artifact_probe = json.loads(artifact_evidence_path.read_text(encoding="utf-8"))
    edit_probe = json.loads(edit_evidence_path.read_text(encoding="utf-8"))
    catalog_probe = json.loads(catalog_evidence_path.read_text(encoding="utf-8"))
    schema_probe = json.loads(schema_evidence_path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    manifest_path = resource_dir / "program.json"
    artifact_path = resource_dir / "artifact/native-program.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    entry = next((item for item in catalog.get("programs", []) if item.get("id") == "N02"), None)
    if observed.get("contract") != "sley-tools.resource-admission-probe.v1" or observed.get("result") != "PASS":
        raise RuntimeError("Resource Admission native/SDK evidence did not pass")
    if observed.get("core_binary_sha256") != report["selected_core"]["binary_sha256"] or observed.get("fixtures_sha256") != sha256(fixtures):
        raise RuntimeError("Resource Admission evidence used a different core or fixture set")
    if (observed.get("external_native_assertions"), observed.get("python_native_cases"), observed.get("typescript_native_cases")) != (12, 49, 49):
        raise RuntimeError("Resource Admission evidence lacks required native assertions")
    if not all(observed.get(field) for field in (
        "copied_binaries_without_core_source_checkout", "fresh_native_commit", "python_and_typescript_one_worker_each",
        "signed_limits_and_over_safe_integer_exact", "check_exit_on_computed_rejection",
    )) or observed.get("typed_input_negatives_per_sdk") != 5:
        raise RuntimeError("Resource Admission evidence lacks exact-integer or process checks")
    if catalog.get("contract") != "sley-tools.program-catalog.v0" or entry is None or entry.get("program_id") != "resource-admission" or entry.get("artifact") != artifact_path.as_posix() or entry.get("verified_behavior") != artifact_evidence_path.as_posix() or entry.get("worked_modification") != edit_evidence_path.as_posix():
        raise RuntimeError("Resource Admission catalog entry misstates its current package status")
    if catalog_probe.get("contract") != "sley-tools.catalog-resource-admission.v1" or catalog_probe.get("result") != "PASS" or catalog_probe.get("core_binary_sha256") != report["selected_core"]["binary_sha256"]:
        raise RuntimeError("Resource Admission catalog build evidence differs")
    if (catalog_probe.get("artifact_object_count"), catalog_probe.get("imported_external_assertions")) != (22, 12) or not catalog_probe.get("fresh_workspace_authority"):
        raise RuntimeError("Resource Admission catalog lacked fresh native import checks")
    if catalog_probe.get("artifact_sha256") != sha256(artifact_path) or manifest["artifact"]["sha256"] != sha256(artifact_path):
        raise RuntimeError("Resource Admission catalog artifact digest differs")
    if manifest["entrypoint"]["state_root"] != artifact["source_state_root"] or artifact["source_state_root"] != catalog_probe["source_state_root"]:
        raise RuntimeError("Resource Admission catalog accepted root differs")
    if schema_probe.get("resource_catalog_manifest_sha256") != sha256(manifest_path) or schema_probe.get("resource_catalog_artifact_sha256") != sha256(artifact_path) or not schema_probe.get("resource_imported_manifest_valid") or not schema_probe.get("resource_edited_import_manifest_valid"):
        raise RuntimeError("Resource Admission catalog or imported manifest was not schema-validated")
    if artifact_probe.get("contract") != "sley-tools.resource-artifact-probe.v1" or artifact_probe.get("result") != "PASS" or artifact_probe.get("core_binary_sha256") != report["selected_core"]["binary_sha256"]:
        raise RuntimeError("Resource Admission transfer evidence did not pass")
    if artifact_probe.get("artifact_sha256") != sha256(artifact_path) or artifact_probe.get("fixtures_sha256") != sha256(fixtures) or artifact_probe.get("native_object_count") != 22:
        raise RuntimeError("Resource Admission transfer evidence differs from catalog files")
    if not all(artifact_probe.get(field) for field in (
        "fresh_workspace_and_principal", "source_authority_excluded", "typed_source_import_equivalent",
        "second_generation_import_passed", "tampered_object_and_false_type_rejected", "both_sdks_loaded_native_artifact",
    )) or (artifact_probe.get("imported_python_cases_per_workspace"), artifact_probe.get("imported_typescript_cases")) != (49, 49):
        raise RuntimeError("Resource Admission transfer or signed SDK replay is incomplete")
    if edit_probe.get("contract") != "sley-tools.resource-edit-probe.v1" or edit_probe.get("result") != "PASS" or edit_probe.get("core_binary_sha256") != report["selected_core"]["binary_sha256"] or edit_probe.get("source_artifact_sha256") != sha256(artifact_path):
        raise RuntimeError("Resource Admission worked edit evidence differs from selected artifact/core")
    if edit_probe.get("initial_root") == edit_probe.get("edited_root") or edit_probe.get("old_memory_object_id") == edit_probe.get("new_memory_object_id") or not all(len(edit_probe.get(name, "")) == 64 for name in ("candidate_id", "receipt_id")):
        raise RuntimeError("Resource Admission worked edit lacks exact native identities")
    if edit_probe.get("exact_fit_before") is not True or edit_probe.get("exact_fit_after") is not False or edit_probe.get("edited_external_assertions") != 12 or edit_probe.get("boundary_cases") != 4:
        raise RuntimeError("Resource Admission worked edit lacks intended boundary evidence")
    if edit_probe.get("stale_rejection_symbol") != "STALE_ROOT" or not edit_probe.get("root_unchanged_after_stale_rejection") or not edit_probe.get("edited_artifact_reimport_passed") or not edit_probe.get("reverse_edit_passed"):
        raise RuntimeError("Resource Admission stale rejection or edited replay is unproved")
    report["capabilities"]["resource_admission_native_and_wide_sdk"] = {
        "status": "supported",
        "evidence": [evidence_path.as_posix(), artifact_evidence_path.as_posix(), edit_evidence_path.as_posix(), catalog_evidence_path.as_posix(), schema_evidence_path.as_posix()],
        "scope": "fresh native Resource Admission graph and checked-in 22-object artifact; authority-rebased imports, 49 exact signed cases in each SDK, tamper rejection, second-generation replay, exact native memory-policy edit, core stale rejection, and edited-artifact replay",
    }


def add_bound_artifact_evidence(report: dict, path: Path, schema: Path, release_artifact: Path, resource_artifact: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    if observed.get("contract") != "sley-tools.bound-artifact-probe.v1" or observed.get("result") != "PASS":
        raise RuntimeError("manifest-bound artifact evidence did not pass")
    if observed.get("core_binary_sha256") != report["selected_core"]["binary_sha256"] or observed.get("schema_sha256") != sha256(schema):
        raise RuntimeError("manifest-bound artifact evidence used a different core or schema")
    if not observed.get("symlink_artifact_directory_rejected") or observed.get("native_test_admission") != "NOT_RUN: production native executor unavailable":
        raise RuntimeError("manifest-bound artifact evidence omits path safety or native test status")
    programs = observed.get("programs")
    if not isinstance(programs, list) or len(programs) != 2:
        raise RuntimeError("manifest-bound artifact evidence omits a native program")
    for entry, program_id, source, boundary_allow in zip(programs, ("release-readiness", "resource-admission"), (release_artifact, resource_artifact), (True, False), strict=True):
        if entry.get("program_id") != program_id or entry.get("source_artifact_sha256") != sha256(source):
            raise RuntimeError(f"bound artifact evidence differs from {program_id} source")
        if any(len(entry.get(name, "")) != 64 for name in ("edited_candidate_id", "edited_root", "bound_artifact_sha256", "bound_manifest_sha256")):
            raise RuntimeError(f"bound artifact evidence omits {program_id} identities")
        if entry.get("manifest_schema_result") != "PASS" or entry.get("boundary_allow_after_replay") is not boundary_allow or not all(entry.get(name) for name in ("out_of_project_binding_rejected", "existing_file_refused", "authority_rebased_import_passed")):
            raise RuntimeError(f"bound artifact evidence lacks {program_id} integrity or replay checks")
    report["capabilities"]["manifest_bound_native_artifact_after_edit"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "edited Release Readiness and Resource Admission artifacts exported under project artifact directories, bound to validated manifests, refused unsafe/duplicate outputs, and reimported with changed native boundaries",
    }


def add_review_evidence(report: dict, path: Path, tools: Path, schema: Path, release_artifact: Path, resource_artifact: Path, release_fixtures: Path, resource_fixtures: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    if observed.get("contract") != "sley-tools.review-probe.v1" or observed.get("result") != "PASS":
        raise RuntimeError("bounded local Review evidence did not pass")
    if (observed.get("core_binary_sha256") != report["selected_core"]["binary_sha256"]
            or observed.get("tools_binary_sha256") != sha256(tools)
            or observed.get("builder_binary_sha256") != sha256(tools.with_name("sley-tools-native-builder"))):
        raise RuntimeError("bounded Review evidence used a different core or tools binary")
    programs = observed.get("programs")
    if not isinstance(programs, list) or len(programs) != 2:
        raise RuntimeError("bounded Review evidence omits a native worked edit")
    selected = (("release-readiness", release_artifact, release_fixtures, 48, 20),
                ("resource-admission", resource_artifact, resource_fixtures, 49, 22))
    for entry, (program_id, artifact, fixtures, count, entities) in zip(programs, selected, strict=True):
        if entry.get("program_id") != program_id or entry.get("source_artifact_sha256") != sha256(artifact) or entry.get("fixtures_sha256") != sha256(fixtures) or entry.get("schema_sha256") != sha256(schema):
            raise RuntimeError(f"bounded Review inputs differ from {program_id} catalog")
        if entry.get("executed_cases") != count or not isinstance(entry.get("changed_cases"), int) or entry["changed_cases"] < 1 or entry.get("shared_entity_ids") != entities or entry.get("changed_entity_versions") != 1:
            raise RuntimeError(f"bounded Review did not prove the {program_id} native change")
        if any(len(entry.get(name, "")) != 64 for name in ("before_artifact_sha256", "after_artifact_sha256", "report_sha256", "edited_candidate_id")) or entry["before_artifact_sha256"] == entry["after_artifact_sha256"]:
            raise RuntimeError(f"bounded Review omits exact {program_id} identities")
        if not all(entry.get(name) for name in ("same_interface", "manifest_validation_passed", "report_recomputed", "falsified_report_rejected", "stale_manifest_rejected", "false_interface_rejected", "altered_artifact_rejected")) or entry.get("native_core_test_admission") != "NOT_RUN: production native executor unavailable":
            raise RuntimeError(f"bounded Review omits {program_id} integrity or test status")
    report["capabilities"]["bounded_local_native_artifact_review"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "local review of both worked native edits: exact one-object changes, 48/49 before-and-after cases, fresh core-validated imports, reproducible reports, and manifest/artifact/report tamper rejection; GitHub-hosted Action run pending",
    }


def add_graph_review_evidence(report: dict, path: Path, tools: Path, schema: Path,
                              validator_artifact: Path, python_source: Path,
                              python_domain: Path, probe: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": "sley-tools.graph-review-probe.v1", "result": "PASS",
        "tools_binary_sha256": sha256(tools),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "schema_sha256": sha256(schema),
        "validator_artifact_sha256": sha256(validator_artifact),
        "python_source_sha256": sha256(python_source),
        "python_domain_sha256": sha256(python_domain),
        "probe_sha256": sha256(probe),
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("graph Review evidence differs from selected tools, core, or sources")
    programs = observed.get("programs")
    if not isinstance(programs, list) or len(programs) != 2:
        raise RuntimeError("graph Review evidence omits an importer edit")
    for entry, program_id, cases, changed in zip(
        programs, ("generated-payload-validator", "python-tier"), (7, 8), (2, 1), strict=True
    ):
        if (entry.get("program_id") != program_id
                or entry.get("native_changed_entities") != 1
                or entry.get("executed_cases") != cases
                or entry.get("changed_cases") != changed
                or not all(entry.get(name) is True for name in (
                    "verified_report", "falsified_report_rejected",
                    "stale_manifest_rejected", "false_interface_rejected",
                    "altered_artifact_rejected", "altered_recipe_rejected",
                    "edited_artifact_replayed"))
                or any(not isinstance(entry.get(name), str) or len(entry[name]) != 64
                       for name in ("old_root", "new_root", "replay_root", "candidate_id",
                                    "receipt_id", "before_artifact_sha256",
                                    "after_artifact_sha256", "report_sha256"))
                or len({entry[name] for name in ("old_root", "new_root", "replay_root")}) != 3):
            raise RuntimeError(f"graph Review omitted exact {program_id} edit or integrity proof")
    primitive = observed.get("primitive_graph", {})
    if (primitive.get("program_id") != "unsigned-limit"
            or primitive.get("executed_cases") != 4
            or primitive.get("recipe_sha256") != sha256(Path("examples/graph/unsigned-limit.json"))
            or primitive.get("cases_sha256") != sha256(Path("examples/graph/unsigned-limit-cases.json"))
            or not all(primitive.get(name) is True for name in (
                "no_change_report_verified", "false_baseline_rejected",
                "wrong_shape_rejected"))):
        raise RuntimeError("graph Review evidence omits primitive result or false-baseline rejection")
    report["capabilities"]["bounded_native_graph_artifact_review"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "graph_v0 Review of schema-generated and Python-imported native edits with exact one-object changes, 7/8 baseline cases and 2/1 changed cases, report recomputation, changed recipe and artifact rejection, replay, and primitive Boolean no-change review; finite cases and local profile only, GitHub-hosted Action run and native core tests pending",
    }


def add_review_action_evidence(report: dict, path: Path, metadata: Path,
                               runner: Path, probe: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": "sley-tools.review-action-local-probe.v1", "result": "PASS",
        "action_metadata_sha256": sha256(metadata),
        "action_runner_sha256": sha256(runner), "probe_sha256": sha256(probe),
        "core_archive_sha256": report["selected_core"]["archive_sha256"],
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
        "github_hosted_action_run": "NOT_RUN",
        "stale_manifest_rejected_without_report": True,
        "symlinked_input_rejected": True,
        "job_summary_written": True,
    }
    if any(observed.get(key) != value for key, value in expected.items()):
        raise RuntimeError("local Review Action evidence differs from source, core, or checks")
    if not isinstance(observed.get("tools_binary_sha256"), str) or len(observed["tools_binary_sha256"]) != 64:
        raise RuntimeError("local Review Action omitted the built tools binary identity")
    fixed = observed.get("release_review", {})
    graph = observed.get("graph_review", {})
    if (fixed.get("case_count"), fixed.get("changed_case_count"),
        fixed.get("exact_changed_entities"), fixed.get("manifest_schema_checks"),
        fixed.get("report_recomputed")) != (48, 24, 1, 2, True):
        raise RuntimeError("local Review Action omitted the worked fixed-program edit")
    if (graph.get("case_count"), graph.get("changed_case_count"),
        graph.get("report_recomputed")) != (11, 0, True):
        raise RuntimeError("local Review Action omitted the graph package")
    if any(not isinstance(item.get("report_sha256"), str) or len(item["report_sha256"]) != 64
           for item in (fixed, graph)):
        raise RuntimeError("local Review Action omitted report identities")
    report["capabilities"]["local_ci_action_native_review_runner"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "reusable composite Action runner exercised from an external caller workspace: hash-locked Sley core, both manifest schema validations, 48-case native fixed-program edit review, 11-case native graph review, report recomputation, and stale-manifest/symlink rejection; hosted execution is separate evidence and native core tests remain pending",
    }


def add_review_action_hosted_evidence(report: dict, path: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": "sley-tools.review-action-hosted.v1", "result": "PASS",
        "run_event": "workflow_dispatch", "run_conclusion": "success",
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "manifest_schema_checks": 2, "case_count": 48,
        "changed_case_count": 24, "exact_changed_entities": 1,
        "report_recomputed": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
        "pull_request_event": "NOT_RUN",
    }
    if any(observed.get(key) != value for key, value in expected.items()):
        raise RuntimeError("hosted Review Action evidence differs from selected core or bounded check")
    run_id = observed.get("run_id")
    if (not isinstance(run_id, int) or run_id <= 0
            or observed.get("run_url") != f"https://github.com/sley-lang/tools/actions/runs/{run_id}"
            or not isinstance(observed.get("artifact_id"), int) or observed["artifact_id"] <= 0
            or observed.get("artifact_name") != "sley-native-review-smoke"):
        raise RuntimeError("hosted Review Action evidence omitted the GitHub run or artifact identity")
    hashes = ("run_head_sha", "status_sha256", "report_sha256", "verification_sha256",
              "manifest_validation_sha256", "hosted_tools_binary_sha256")
    for name in hashes:
        value = observed.get(name)
        length = 40 if name == "run_head_sha" else 64
        if not isinstance(value, str) or len(value) != length or any(
                character not in "0123456789abcdef" for character in value):
            raise RuntimeError(f"hosted Review Action evidence omitted {name}")
    artifact_digest = observed.get("artifact_digest")
    if (not isinstance(artifact_digest, str) or not artifact_digest.startswith("sha256:")
            or len(artifact_digest) != 71 or any(
                character not in "0123456789abcdef" for character in artifact_digest[7:])):
        raise RuntimeError("hosted Review Action evidence omitted the uploaded artifact digest")
    report["capabilities"]["github_hosted_bounded_review_smoke"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "GitHub-hosted workflow_dispatch on the checked-in Release Readiness fixture: pinned Sley core, two manifest schema validations, one native object edit, 48 cases with 24 changed, report recomputation, and uploaded artifact; pull_request evidence is recorded separately and native core tests remain pending",
    }


def add_review_action_pr_hosted_evidence(report: dict, path: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": "sley-tools.review-action-pr-hosted.v1",
        "result": "PASS_BOUNDED_REVIEW",
        "repository": "sley-lang/tools",
        "workflow_event": "pull_request",
        "workflow_conclusion": "success",
        "workflow_permissions": {"contents": "read"},
        "review_action_commit": "5c60a1499343f648918715026b3fb349810582af",
        "uploaded_artifact_name": "sley-native-review-pr",
        "uploaded_artifact_downloaded": True,
        "uploaded_exit_code": 0,
        "manifest_schema_validation": "PASS_BOTH",
        "core_archive_sha256": EXPECTED_ARCHIVE_SHA256,
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "executed_cases": 48,
        "changed_cases": 24,
        "exact_changed_entity_versions": 1,
        "report_recomputed_from_inputs": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(key) != value for key, value in expected.items()):
        raise RuntimeError("hosted PR Review evidence differs from the selected core or bounded check")
    run_id = observed.get("workflow_run_id")
    if (not isinstance(run_id, int) or run_id <= 0
            or observed.get("workflow_run_url") !=
            f"https://github.com/sley-lang/tools/actions/runs/{run_id}"
            or not isinstance(observed.get("pull_request"), int)
            or observed["pull_request"] <= 0):
        raise RuntimeError("hosted PR Review evidence omitted the run or pull request identity")
    for name in ("pull_request_head_sha", "pull_request_base_sha", "before_manifest_sha256",
                 "after_manifest_sha256", "before_artifact_sha256", "after_artifact_sha256",
                 "tools_binary_sha256", "report_sha256"):
        value = observed.get(name)
        length = 40 if name in ("pull_request_head_sha", "pull_request_base_sha") else 64
        if not isinstance(value, str) or len(value) != length or any(
                character not in "0123456789abcdef" for character in value):
            raise RuntimeError(f"hosted PR Review evidence omitted {name}")
    names = ("report.json", "status.json", "verification.json", "manifest-validation.json")
    hashes = observed.get("uploaded_files_sha256")
    if not isinstance(hashes, dict) or set(hashes) != set(names):
        raise RuntimeError("hosted PR Review evidence omitted downloaded files")
    folder = path.parent / "action-pr-hosted"
    if any(hashes[name] != sha256(folder / name) for name in names):
        raise RuntimeError("downloaded hosted PR Review artifact differs from its evidence")
    rendered = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
    verification = json.loads((folder / "verification.json").read_text(encoding="utf-8"))
    manifests = json.loads((folder / "manifest-validation.json").read_text(encoding="utf-8"))
    if (rendered.get("result") != "PASS_BOUNDED_LOCAL"
            or rendered.get("identity", {}).get("before", {}).get("artifact_sha256") !=
            observed["before_artifact_sha256"]
            or rendered.get("identity", {}).get("after", {}).get("artifact_sha256") !=
            observed["after_artifact_sha256"]
            or rendered.get("behavioral_evidence", {}).get("case_count") != 48
            or rendered.get("behavioral_evidence", {}).get("changed_case_count") != 24
            or len(rendered.get("structural_change", {}).get("changed", [])) != 1
            or rendered.get("validation", {}).get("native_core_test_admission") !=
            expected["native_core_test_admission"]):
        raise RuntimeError("downloaded hosted PR Review report omitted the native edit")
    if (status.get("result") != "PASS_BOUNDED_REVIEW"
            or status.get("report_sha256") != observed["report_sha256"]
            or verification.get("result") != "PASS"
            or verification.get("report_recomputed_from_inputs") is not True
            or verification.get("report_sha256") != observed["report_sha256"]):
        raise RuntimeError("downloaded hosted PR Review status or recomputation differs")
    if (not isinstance(manifests, list) or len(manifests) != 2
            or [item.get("manifest_sha256") for item in manifests] !=
            [observed["before_manifest_sha256"], observed["after_manifest_sha256"]]
            or any(item.get("result") != "PASS" for item in manifests)):
        raise RuntimeError("downloaded hosted PR Review omitted manifest validation")
    report["capabilities"]["github_hosted_pull_request_native_review"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "read-only GitHub pull_request on a fresh manifest-bound Release Readiness artifact pair: immutable hosted-tested Action, pinned Sley core, both schemas validated, one native entity edit, 48 cases with 24 changed, downloaded report and recomputation verified; native core tests and public Action release remain pending",
    }


def add_review_action_pr_rejection_evidence(report: dict, path: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": "sley-tools.review-action-pr-rejection-hosted.v1",
        "result": "EXPECTED_INTEGRITY_REFUSAL",
        "repository": "sley-lang/tools",
        "workflow_event": "pull_request",
        "workflow_conclusion": "failure",
        "workflow_permissions": {"contents": "read"},
        "review_action_commit": "5c60a1499343f648918715026b3fb349810582af",
        "uploaded_artifact_name": "sley-native-review-pr",
        "uploaded_artifact_downloaded": True,
        "uploaded_exit_code": 1,
        "diagnostic_result": "FAIL",
        "diagnostic_reason": "manifest validation failed: artifact size or SHA-256 differs from manifest",
        "report_generated": False,
        "refusal_phase": "manifest validation before native execution",
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(key) != value for key, value in expected.items()):
        raise RuntimeError("hosted PR integrity refusal differs from its bounded check")
    run_id = observed.get("workflow_run_id")
    if (not isinstance(run_id, int) or run_id <= 0
            or observed.get("workflow_run_url") !=
            f"https://github.com/sley-lang/tools/actions/runs/{run_id}"
            or not isinstance(observed.get("pull_request"), int)
            or observed["pull_request"] <= 0):
        raise RuntimeError("hosted PR integrity refusal omitted the run identity")
    for name in ("pull_request_head_sha", "pull_request_base_sha",
                 "submitted_artifact_sha256", "stale_manifest_artifact_sha256"):
        value = observed.get(name)
        length = 40 if name in ("pull_request_head_sha", "pull_request_base_sha") else 64
        if not isinstance(value, str) or len(value) != length or any(
                character not in "0123456789abcdef" for character in value):
            raise RuntimeError(f"hosted PR integrity refusal omitted {name}")
    if observed["submitted_artifact_sha256"] == observed["stale_manifest_artifact_sha256"]:
        raise RuntimeError("hosted PR integrity refusal has no stale artifact")
    names = ("status.json", "runner.log", "summary.md")
    hashes = observed.get("uploaded_files_sha256")
    if not isinstance(hashes, dict) or set(hashes) != set(names):
        raise RuntimeError("hosted PR integrity refusal omitted diagnostics")
    folder = path.parent / "action-pr-rejection-hosted"
    if any(hashes[name] != sha256(folder / name) for name in names):
        raise RuntimeError("downloaded hosted PR rejection diagnostics differ from evidence")
    if (folder / "report.json").exists():
        raise RuntimeError("hosted PR rejection claimed no report but one was copied")
    status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
    log = (folder / "runner.log").read_text(encoding="utf-8")
    summary = (folder / "summary.md").read_text(encoding="utf-8")
    if (status.get("result") != "FAIL"
            or expected["diagnostic_reason"] not in status.get("reason", "")
            or expected["diagnostic_reason"] not in log
            or "Result: **FAIL**" not in summary):
        raise RuntimeError("downloaded hosted PR rejection lost the useful failure reason")
    report["capabilities"]["github_hosted_pull_request_integrity_refusal"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "read-only GitHub pull_request refuses a parseable altered native artifact with a stale manifest binding; CI fails and uploads status, log, and summary; validation stopped before report generation or native execution",
    }


def add_authoring_evidence(report: dict, path: Path, tools: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    if observed.get("contract") != "sley-tools.authoring-lifecycle-probe.v1" or observed.get("result") != "PASS":
        raise RuntimeError("explicit authoring lifecycle evidence did not pass")
    if observed.get("core_binary_sha256") != report["selected_core"]["binary_sha256"] or observed.get("tools_binary_sha256") != sha256(tools):
        raise RuntimeError("explicit authoring lifecycle used a different core or tools binary")
    if observed.get("native_core_test_admission") != "NOT_RUN: production native executor unavailable":
        raise RuntimeError("explicit authoring lifecycle overstated native test admission")
    programs = observed.get("programs")
    if not isinstance(programs, list) or len(programs) != 2:
        raise RuntimeError("explicit authoring lifecycle omitted a native program")
    for entry, program, before, after, count in zip(programs,
                                                   ("release-readiness", "resource-admission"),
                                                   (False, True), (True, False), (60, 12), strict=True):
        if entry.get("program_id") != program or entry.get("before_allow") is not before or entry.get("after_allow") is not after or entry.get("external_assertions") != count:
            raise RuntimeError(f"explicit authoring lifecycle did not change {program} boundary")
        if entry.get("initial_root") == entry.get("committed_root") or any(len(entry.get(name, "")) != 64 for name in ("candidate_id", "initial_root", "committed_root")):
            raise RuntimeError(f"explicit authoring lifecycle lacks {program} exact identities")
        if entry.get("changed_entity_versions") != 1 or any(len(entry.get(name, "")) != 64 for name in ("before_artifact_sha256", "after_artifact_sha256")) or entry["before_artifact_sha256"] == entry["after_artifact_sha256"]:
            raise RuntimeError(f"explicit authoring lifecycle lacks the exact {program} native object change")
        if not all(entry.get(name) for name in ("commit_before_validation_rejected", "duplicate_proposal_rejected", "exact_candidate_committed")):
            raise RuntimeError(f"explicit authoring lifecycle skipped {program} candidate checks")
        if program == "release-readiness" and not entry.get("concurrent_project_write_rejected"):
            raise RuntimeError("explicit authoring lifecycle did not reject a busy project write")
    negative = observed.get("negative_paths", {})
    if not all(negative.get(name) for name in ("tamper_rejected", "stale_base_rejected", "accepted_root_preserved_after_rejections")) or any(len(negative.get(name, "")) != 64 for name in ("tampered_candidate_id", "stale_candidate_id")):
        raise RuntimeError("explicit authoring lifecycle lacks tamper or stale rejection")
    report["capabilities"]["bounded_explicit_native_edit_lifecycle"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "CLI Release Readiness and Resource Admission typed one-object proposals: separate core validation, exact candidate revalidation and commit, one changed entity version each, changed native boundaries, 60/12 external assertions, busy-write/tamper/stale/duplicate/commit-before-validation rejection; general graph builders and native core tests pending",
    }


def add_rpc_authoring_evidence(report: dict, paths: tuple[Path, Path, Path], tools: Path) -> None:
    contracts = ("sley-tools.rpc-authoring-probe.v1", "sley-tools.python-authoring-probe.v1",
                 "sley-tools.typescript-authoring-probe.v1")
    for path, contract in zip(paths, contracts, strict=True):
        observed = json.loads(path.read_text(encoding="utf-8"))
        if observed.get("contract") != contract or observed.get("result") != "PASS":
            raise RuntimeError(f"authoring bridge evidence did not pass: {path}")
        if observed.get("core_binary_sha256") != report["selected_core"]["binary_sha256"] or observed.get("tools_binary_sha256") != sha256(tools):
            raise RuntimeError(f"authoring bridge used a different core or tools binary: {path}")
        if observed.get("native_core_test_admission") != "NOT_RUN: production native executor unavailable":
            raise RuntimeError(f"authoring bridge overstated native test admission: {path}")
        programs = observed.get("programs")
        if not isinstance(programs, list) or len(programs) != 2:
            raise RuntimeError(f"authoring bridge omitted a worked program: {path}")
        for entry, program, before, after, count in zip(programs,
                                                        ("release-readiness", "resource-admission"),
                                                        (False, True), (True, False), (60, 12), strict=True):
            if (entry.get("program_id"), entry.get("before_allow"), entry.get("after_allow"),
                    entry.get("external_assertions")) != (program, before, after, count):
                raise RuntimeError(f"authoring bridge did not change {program} native boundary: {path}")
            if entry.get("initial_root") == entry.get("committed_root") or any(len(entry.get(name, "")) != 64 for name in ("candidate_id", "initial_root", "committed_root")):
                raise RuntimeError(f"authoring bridge omitted {program} exact identities: {path}")
            required = ("typed_rejection", "wrong_candidate_rejected", "commit_before_validation_rejected", "discard_preserved_root")
            if not all(entry.get(name) for name in required) or not (entry.get("old_handle_rejected") or entry.get("handle_rotated")):
                raise RuntimeError(f"authoring bridge omitted {program} lifecycle checks: {path}")
        if contract == contracts[0]:
            stale = observed.get("stale", {})
            if not all(stale.get(name) for name in ("stale_validation_rejected", "old_rpc_session_rejected", "accepted_root_preserved")):
                raise RuntimeError("RPC authoring evidence omitted stale-session rejection")
    report["capabilities"]["bounded_rpc_sdk_native_edit_lifecycle"] = {
        "status": "supported", "evidence": [path.as_posix() for path in paths],
        "scope": "two typed native one-object edits over local RPC, Python SDK, and TypeScript SDK; separate validation, exact candidate commit, changed native boundaries, 60/12 external assertions, handle rotation, discard and stale-session rejection; general graph builders and native core tests pending",
    }


def add_graph_builder_evidence(report: dict, path: Path, tools: Path, builder: Path, spec: Path, unsigned_spec: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    if observed.get("contract") != "sley-tools.graph-builder-probe.v1" or observed.get("result") != "PASS":
        raise RuntimeError("typed graph builder evidence did not pass")
    expected_hashes = {"tools_binary_sha256": sha256(tools),
                       "builder_binary_sha256": sha256(builder),
                       "core_binary_sha256": report["selected_core"]["binary_sha256"],
                       "spec_sha256": sha256(spec),
                       "unsigned_spec_sha256": sha256(unsigned_spec)}
    if any(observed.get(name) != digest for name, digest in expected_hashes.items()):
        raise RuntimeError("typed graph builder evidence used different tools, core, or graph spec")
    graph = observed.get("graph", {})
    if graph.get("initial_root") == graph.get("accepted_root") or any(len(graph.get(name, "")) != 64 for name in ("initial_root", "accepted_root", "candidate_id", "function_id")):
        raise RuntimeError("typed graph builder lacks an exact core candidate or accepted root")
    if graph.get("entity_count") != 20 or graph.get("operation_count") != 10 or len(graph.get("cases", [])) != 8:
        raise RuntimeError("typed graph builder did not execute the checked native function matrix")
    for case in graph["cases"]:
        values = [int(value) for value in case["input"]]
        value, lower, upper = values
        invalid = lower > upper
        expected = {"within_range": not invalid and lower <= value <= upper,
                    "invalid_bounds": invalid,
                    "below_lower": not invalid and value < lower,
                    "above_upper": not invalid and value > upper}
        if case.get("expected") != expected or len(case.get("value_hash", "")) != 64:
            raise RuntimeError("typed graph builder evidence differs from independent Range Predicate oracle")
    negatives = graph.get("negative_construction", {})
    if not all(negatives.get(name) for name in ("wrong_operand_type", "unknown_branch_target", "cycle", "duplicate_operation", "wrong_constant_type")) or not graph.get("noncanonical_input_rejected"):
        raise RuntimeError("typed graph builder omitted construction or input rejection")
    unsigned = graph.get("unsigned_graph", {})
    if unsigned.get("accepted_root") == graph.get("accepted_root") or any(len(unsigned.get(name, "")) != 64 for name in ("accepted_root", "candidate_id", "function_id")):
        raise RuntimeError("typed graph builder omitted the second accepted native function")
    if unsigned.get("entity_count") != 5 or unsigned.get("operation_count") != 1 or len(unsigned.get("cases", [])) != 4 or not unsigned.get("negative_input_rejected"):
        raise RuntimeError("typed graph builder omitted primitive result or unsigned input checks")
    for case in unsigned["cases"]:
        count, limit = (int(value) for value in case["input"])
        if case.get("expected") is not (count <= limit) or len(case.get("value_hash", "")) != 64:
            raise RuntimeError("typed unsigned graph evidence differs from independent oracle")
    if observed.get("native_core_test_admission") != "NOT_RUN: production native executor unavailable":
        raise RuntimeError("typed graph builder overstated native test admission")
    report["capabilities"]["typed_graph_lowering_core_candidate_and_vm"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "backend graph-authoring v0 lowers named parameters, constants, Boolean/integer comparisons, records, and acyclic branches to real candidates; Range Predicate and unsigned primitive-result functions are core-validated, committed, and executed on twelve cases with five construction rejections; native core tests pending",
    }


def add_graph_project_evidence(report: dict, path: Path, tools: Path, builder: Path,
                               schema: Path, spec: Path, cases: Path,
                               unsigned_spec: Path, unsigned_cases: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    if observed.get("contract") != "sley-tools.graph-project-probe.v1" or observed.get("result") != "PASS":
        raise RuntimeError("public graph project evidence did not pass")
    for name, expected in (("tools_binary_sha256", sha256(tools)),
                           ("builder_binary_sha256", sha256(builder)),
                           ("core_binary_sha256", report["selected_core"]["binary_sha256"]),
                           ("schema_sha256", sha256(schema))):
        if observed.get(name) != expected:
            raise RuntimeError(f"public graph project {name} differs from selected artifact")
    profiles = observed.get("profiles", [])
    if len(profiles) != 2:
        raise RuntimeError("public graph project evidence omitted a native program")
    for item, recipe, fixture, count in zip(profiles, (spec, unsigned_spec),
                                             (cases, unsigned_cases), (8, 4), strict=True):
        if (item.get("program_id") != recipe.stem or item.get("spec_sha256") != sha256(recipe)
            or item.get("cases_sha256") != sha256(fixture)
            or item.get("external_assertions") != count or len(item.get("observations", [])) != count):
            raise RuntimeError("public graph project lost selected source or native case coverage")
        if any(len(item.get(name, "")) != 64 for name in
               ("state_root", "function_id", "candidate_id", "commit_receipt_id", "artifact_sha256")):
            raise RuntimeError("public graph project omitted accepted native identity")
        if item.get("artifact_object_count", 0) < 1:
            raise RuntimeError("public graph project omitted portable native objects")
        if any(len(case.get("value_hash", "")) != 64 for case in item["observations"]):
            raise RuntimeError("public graph project omitted native value identity")
        if set(item.get("negative_paths", [])) != {"existing_directory", "unsupported_check",
                                                   "existing_artifact_refused", "noncanonical_input",
                                                   "wrong_case_type_before_creation",
                                                   "false_expected_result", "stale_recipe"}:
            raise RuntimeError("public graph project omitted a negative path")
    if observed.get("native_core_test_admission") != "NOT_RUN: production native executor unavailable":
        raise RuntimeError("public graph project overstated native core test admission")
    report["capabilities"]["public_native_graph_project_cli"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "two independent graph recipes initialized as accepted native projects; CLI run, inspect, and twelve external test assertions; recipe binding and bounded negative paths; native core tests and catalog admission pending",
    }


def add_graph_edit_evidence(report: dict, path: Path, tools: Path, builder: Path,
                            schema: Path, spec: Path, initial: Path, updated: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    if observed.get("contract") != "sley-tools.graph-edit-probe.v1" or observed.get("result") != "PASS":
        raise RuntimeError("native graph edit evidence did not pass")
    expected = {"tools_binary_sha256": sha256(tools),
                "builder_binary_sha256": sha256(builder),
                "core_binary_sha256": report["selected_core"]["binary_sha256"],
                "schema_sha256": sha256(schema), "spec_sha256": sha256(spec),
                "initial_cases_sha256": sha256(initial), "updated_cases_sha256": sha256(updated)}
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("native graph edit used different tools, core, schema, or cases")
    edit = observed.get("edit", {})
    if (edit.get("initial_root") == edit.get("accepted_root")
        or edit.get("old_object_id") == edit.get("new_object_id")
        or any(len(edit.get(name, "")) != 64 for name in
               ("initial_root", "accepted_root", "candidate_id", "receipt_id",
                "constant_id", "old_object_id", "new_object_id"))
        or edit.get("changed_case_count") != 2 or edit.get("external_assertions") != 6):
        raise RuntimeError("native graph edit lacks exact one-object or boundary evidence")
    if set(edit.get("negative_paths", [])) != {"invalid_typed_value", "tampered_candidate_value",
                                               "stale_proposal_root", "duplicate_validation",
                                               "tampered_validated_proposal", "discard"}:
        raise RuntimeError("native graph edit omitted proposal rejection or discard")
    failed = observed.get("false_oracle", {})
    if (failed.get("old_root") == failed.get("accepted_root")
        or failed.get("reported_code") != "ST_EXECUTION_FAILED"
        or failed.get("external_assertions") != "FAIL"
        or failed.get("project_remains_runnable") is not True):
        raise RuntimeError("native graph edit hid an accepted commit behind a false oracle")
    if observed.get("native_core_test_admission") != "NOT_RUN: production native executor unavailable":
        raise RuntimeError("native graph edit overstated core test admission")
    report["capabilities"]["targeted_native_graph_constant_edit_cli"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "Fixed Limit named constant proposal, separate core validation, exact old-object native replacement, two changed boundary cases and six postcommit external assertions; false oracle reports accepted commit; native core tests pending",
    }


def add_graph_opcode_evidence(report: dict, path: Path, tools: Path, builder: Path,
                              spec: Path, initial: Path, updated: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": "sley-tools.graph-opcode-edit-probe.v1", "result": "PASS",
        "tools_binary_sha256": sha256(tools), "builder_binary_sha256": sha256(builder),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "spec_sha256": sha256(spec), "initial_cases_sha256": sha256(initial),
        "updated_cases_sha256": sha256(updated),
        "python_sdk_sha256": sha256(Path("packages/python/sley_tools/runtime.py")),
        "python_builder_sha256": sha256(Path("packages/python/sley_tools/graph_builder.py")),
        "typescript_sdk_sha256": sha256(Path("packages/typescript/src/index.js")),
        "typescript_types_sha256": sha256(Path("packages/typescript/src/index.d.ts")),
        "typescript_type_test_sha256": sha256(Path("packages/typescript/tests/types.ts")),
        "python_probe_sha256": sha256(Path("scripts/probe_graph_opcode_edit.py")),
        "typescript_probe_sha256": sha256(Path("scripts/probe_graph_typescript_opcode.mjs")),
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("graph opcode evidence differs from selected binaries, sources, or fixtures")
    cli, python, typed = (observed.get(name, {}) for name in ("cli", "python", "typescript"))
    if (cli.get("old_root") == cli.get("new_root")
            or cli.get("old_object_id") == cli.get("new_object_id")
            or any(not isinstance(cli.get(name), str) or len(cli[name]) != 64 for name in
                   ("old_root", "new_root", "candidate_id", "operation_id",
                    "old_object_id", "new_object_id"))
            or cli.get("changed_case_count") != 1 or cli.get("native_cases") != 5
            or any(cli.get(name) is not True for name in
                   ("tampered_value_rejected", "stale_base_rejected",
                    "tampered_object_rejected", "cross_profile_rejected"))):
        raise RuntimeError("CLI graph opcode evidence omitted one-object native edit or rejection")
    for name, entry in (("Python", python), ("TypeScript", typed)):
        if (entry.get("old_root") == entry.get("new_root")
                or any(not isinstance(entry.get(key), str) or len(entry[key]) != 64
                       for key in ("old_root", "new_root", "candidate_id"))
                or entry.get("changed_case_count") != 1 or entry.get("native_cases") != 5
                or entry.get("handle_rotated") is not True):
            raise RuntimeError(f"{name} graph opcode evidence omitted native edit or reopen")
    if typed.get("contract") != "sley-tools.graph-typescript-opcode-probe.v1" or typed.get("result") != "PASS":
        raise RuntimeError("TypeScript graph opcode probe did not pass")
    families = observed.get("additional_families", {})
    expected_families = {"boolean": (4, 2), "equality": (3, 3),
                         "checked_binary": (4, 4), "checked_unary": (4, 3)}
    if set(families) != set(expected_families):
        raise RuntimeError("graph opcode evidence omitted compatible operation families")
    for name, (cases, changed) in expected_families.items():
        entry = families[name]
        if (entry.get("native_cases") != cases or entry.get("changed_case_count") != changed
                or entry.get("old_root") == entry.get("new_root")
                or any(not isinstance(entry.get(key), str) or len(entry[key]) != 64
                       for key in ("old_root", "new_root", "operation_id"))):
            raise RuntimeError(f"native {name} opcode family differs from its oracle")
    report["capabilities"]["targeted_native_graph_opcode_edit"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "compatible graph opcode replacement by block/operation label through CLI, RPC, Python, and TypeScript; exact one-entity precondition, separate core validation and commit, five native boundary assertions per adapter plus Boolean, equality, checked binary, and checked unary matrices; stale/tamper/cross-profile rejection; native core tests pending",
    }


def add_graph_operand_evidence(report: dict, path: Path, tools: Path, spec: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": "sley-tools.graph-operand-probe.v1",
        "tools_sha256": sha256(tools),
        "core_sha256": report["selected_core"]["binary_sha256"],
        "source_spec_sha256": sha256(spec),
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("graph operand evidence differs from selected binaries or source graph")
    if any(not isinstance(observed.get(name), str) or len(observed[name]) != 64
           for name in ("initial_spec_sha256", "initial_cases_sha256", "corrected_cases_sha256")):
        raise RuntimeError("graph operand evidence omitted generated fixture identities")
    cli, python, typed = (observed.get(name, {}) for name in ("cli", "python", "typescript"))
    for name, entry in (("CLI", cli), ("Python", python), ("TypeScript", typed)):
        if (entry.get("old_root") == entry.get("new_root")
                or entry.get("native_cases") != 7
                or any(not isinstance(entry.get(key), str) or len(entry[key]) != 64
                       for key in ("old_root", "new_root", "candidate_id"))):
            raise RuntimeError(f"{name} operand evidence omitted accepted native behavior")
    if (cli.get("changed_case_count") != 2
            or cli.get("old_object_id") == cli.get("new_object_id")
            or any(not isinstance(cli.get(key), str) or len(cli[key]) != 64
                   for key in ("operation_id", "old_object_id", "new_object_id",
                               "type_mismatch_candidate"))
            or any(cli.get(key) is not True for key in
                   ("type_mismatch_rejected", "forward_reference_rejected",
                    "tampered_proposal_rejected"))):
        raise RuntimeError("CLI operand evidence omitted exact edit or rejection")
    if any(entry.get("invalid_reference_rejected") is not True for entry in (python, typed)):
        raise RuntimeError("SDK operand evidence omitted invalid-reference rejection")
    profiles = observed.get("additional_profiles", {})
    for name, changed in (("checked_unary", 4), ("earlier_operation", 1)):
        entry = profiles.get(name, {})
        if (entry.get("native_cases") != 4 or entry.get("changed_case_count") != changed
                or not isinstance(entry.get("candidate_id"), str)
                or len(entry["candidate_id"]) != 64):
            raise RuntimeError(f"graph operand evidence omitted {name} native cases")
    report["capabilities"]["targeted_native_graph_operand_edit"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "one accepted native operation operand reference replaced through CLI, RPC, Python, and TypeScript; seven independent Range Predicate assertions per adapter plus checked unary and earlier-operation matrices; exact one-object candidate, separate core validation and commit, type/forward-reference/tamper rejection; MCP covered in reference-client probe; native core tests pending",
    }


def add_graph_branch_evidence(report: dict, path: Path, tools: Path, builder: Path,
                              spec: Path, cases: Path, updated: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": "sley-tools.graph-branch-edit-probe.v1", "result": "PASS",
        "tools_binary_sha256": sha256(tools), "builder_binary_sha256": sha256(builder),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "spec_sha256": sha256(spec), "initial_cases_sha256": sha256(cases),
        "updated_cases_sha256": sha256(updated),
        "python_probe_sha256": sha256(Path("scripts/probe_graph_branch_edit.py")),
        "typescript_probe_sha256": sha256(Path("scripts/probe_graph_typescript_branch.mjs")),
        "python_sdk_sha256": sha256(Path("packages/python/sley_tools/runtime.py")),
        "typescript_sdk_sha256": sha256(Path("packages/typescript/src/index.js")),
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("graph branch evidence differs from selected binaries, sources, or fixtures")
    # The branch probe never starts the MCP server. Its recorded server hash
    # is historical metadata; the MCP reference probe verifies that source.
    if not isinstance(observed.get("mcp_server_sha256"), str) or len(observed["mcp_server_sha256"]) != 64:
        raise RuntimeError("graph branch evidence omitted its historical MCP source identity")
    cli, python, typed = (observed.get(name, {}) for name in ("cli", "python", "typescript"))
    if (cli.get("old_root") == cli.get("new_root")
            or cli.get("native_cases") != 8 or cli.get("changed_case_count") != 8
            or cli.get("invalid_edits_rejected") != 3
            or cli.get("tampered_proposals_rejected") != 3
            or any(not isinstance(cli.get(key), str) or len(cli[key]) != 64
                   for key in ("old_root", "new_root", "candidate_id"))
            or python.get("native_cases") != 8
            or typed.get("contract") != "sley-tools.graph-typescript-branch-probe.v1"
            or typed.get("result") != "PASS" or typed.get("native_cases") != 8):
        raise RuntimeError("graph branch evidence omitted native behavior or rejection")
    jump = observed.get("unconditional", {})
    if (jump.get("old_root") == jump.get("new_root")
            or jump.get("native_cases") != 4 or jump.get("changed_case_count") != 2
            or any(not isinstance(jump.get(key), str) or len(jump[key]) != 64
                   for key in ("old_root", "new_root", "candidate_id"))):
        raise RuntimeError("unconditional native branch edit omitted changed behavior")
    report["capabilities"]["targeted_native_graph_branch_edit"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "one accepted native Block terminator target replacement through CLI, RPC, Python, and TypeScript; conditional and unconditional edits, exact old-object precondition, separate validation and commit, eight postcommit native executions per adapter, malformed/unreachable/tampered/stale rejection; MCP exercised in its separate reference-client probe; block arguments and native core tests pending",
    }


def add_graph_sdk_evidence(report: dict, path: Path, tools: Path, builder: Path,
                           spec: Path, cases: Path, unsigned_spec: Path,
                           unsigned_cases: Path, numeric_spec: Path,
                           numeric_cases: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    if observed.get("contract") != "sley-tools.graph-sdk-probe.v1" or observed.get("result") != "PASS":
        raise RuntimeError("graph SDK evidence did not pass")
    expected = {
        "tools_binary_sha256": sha256(tools), "builder_binary_sha256": sha256(builder),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "recipes_sha256": [sha256(spec), sha256(unsigned_spec), sha256(numeric_spec)],
        "cases_sha256": [sha256(cases), sha256(unsigned_cases), sha256(numeric_cases)],
        "python_native_cases": 15, "typescript_native_cases": 15,
        "record_and_primitive_results": True, "exact_signed_boundaries": True,
        "signed_and_unsigned_record_results": True,
        "one_worker_per_project_and_sdk": True, "typed_input_rejection": True,
        "tampered_recipe_rejected": True,
        "tampered_project_bindings_rejected": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("graph SDK evidence differs from the selected binaries or cases")
    report["capabilities"]["rpc_python_typescript_native_graph_execution"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "three accepted graph projects opened through local RPC and both SDKs; fifteen native VM cases per SDK, signed boundaries, primitive and mixed numeric record results, typed input and changed project-binding rejection, and worker reuse; SDK artifact transfer and native core tests pending",
    }


def add_graph_sdk_authoring_evidence(report: dict, path: Path, tools: Path, builder: Path,
                                     spec: Path, initial: Path, updated: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": "sley-tools.graph-sdk-authoring-probe.v1", "result": "PASS",
        "tools_binary_sha256": sha256(tools), "builder_binary_sha256": sha256(builder),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "spec_sha256": sha256(spec), "initial_cases_sha256": sha256(initial),
        "updated_cases_sha256": sha256(updated),
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("graph SDK authoring evidence differs from selected binaries or fixtures")
    for name in ("python", "typescript"):
        result = observed.get(name, {})
        if (result.get("old_root") == result.get("new_root")
                or any(not isinstance(result.get(field), str) or len(result[field]) != 64
                       for field in ("old_root", "new_root", "candidate_id"))
                or result.get("changed_case_count") != 2):
            raise RuntimeError(f"graph {name} authoring omitted a changed native identity")
    false_oracle = observed.get("false_oracle", {})
    if (false_oracle.get("old_root") == false_oracle.get("accepted_root")
            or false_oracle.get("status") != "FAIL"
            or false_oracle.get("project_remains_runnable") is not True):
        raise RuntimeError("graph SDK authoring hid a false postcommit oracle")
    report["capabilities"]["targeted_native_graph_constant_edit_sdk"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "Fixed Limit named constant edit through RPC, Python, and TypeScript; separate native validation, accepted one-object commit, changed boundaries, SDK reopen, and six external assertions; false expectations report the new accepted root; generic graph operations and native core tests pending",
    }


def add_graph_sdk_creation_evidence(report: dict, path: Path, tools: Path, builder: Path,
                                    range_spec: Path, range_cases: Path, fixed_spec: Path,
                                    fixed_cases: Path, numeric_spec: Path, numeric_cases: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": "sley-tools.graph-sdk-creation-probe.v1", "result": "PASS",
        "tools_binary_sha256": sha256(tools), "builder_binary_sha256": sha256(builder),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "recipes_sha256": {"range": sha256(range_spec), "fixed": sha256(fixed_spec),
                            "numeric": sha256(numeric_spec)},
        "cases_sha256": {"range": sha256(range_cases), "fixed": sha256(fixed_cases),
                          "numeric": sha256(numeric_cases)},
        "sdk_sources_sha256": {name: sha256(path) for name, path in {
            "python_builder": Path("packages/python/sley_tools/graph_builder.py"),
            "python_runtime": Path("packages/python/sley_tools/runtime.py"),
            "typescript_sdk": Path("packages/typescript/src/index.js"),
            "typescript_types": Path("packages/typescript/src/index.d.ts"),
            "python_probe": Path("scripts/probe_graph_sdk_creation.py"),
            "typescript_probe": Path("scripts/probe_graph_typescript_creation.mjs"),
        }.items()},
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("graph SDK creation evidence differs from selected binaries or fixtures")
    python = observed.get("python", {})
    typed = observed.get("typescript", {})
    if (python.get("program") != "range-predicate" or python.get("native_cases") != 8
            or not isinstance(python.get("accepted_root"), str) or len(python["accepted_root"]) != 64
            or python.get("fluent_recipe_matched") is not True or python.get("fluent_rejections") != 3
            or python.get("invalid_input_rejected") is not True
            or python.get("existing_project_rejected") is not True):
        raise RuntimeError("Python graph creation evidence omitted native behavior or rejection")
    if (typed.get("contract") != "sley-tools.graph-typescript-creation-probe.v1"
            or typed.get("result") != "PASS" or typed.get("fixed_native_cases") != 4
            or typed.get("numeric_native_cases") != 3
            or typed.get("fluent_recipe_matched") is not True or typed.get("fluent_rejections") != 3
            or any(not isinstance(typed.get(name), str) or len(typed[name]) != 64
                   for name in ("fixed_root", "numeric_root"))
            or typed.get("invalid_input_rejected") is not True
            or typed.get("existing_project_rejected") is not True):
        raise RuntimeError("TypeScript graph creation evidence omitted native behavior or rejection")
    report["capabilities"]["sdk_native_graph_project_creation"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "Python and TypeScript fluent builders produce checked-in branching and primitive recipes, initialize fresh native graph projects through the shared Rust builder, and run eight branching, four primitive, and three exact numeric native cases; six malformed builder uses, invalid inputs, and existing destinations rejected; SDK artifact transfer and native core tests pending",
    }


def add_mcp_evidence(report: dict, path: Path, tools: Path, server: Path,
                     probe_entry: Path, sdk_entry: Path, package_lock: Path,
                     review_fixtures: Path, review_cases: Path,
                     comparator_spec: Path, comparator_cases: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": "sley-tools.mcp-reference-probe.v1", "result": "PASS",
        "sdk_server_version": "2.1.0", "sdk_client_version": "2.1.0",
        "negotiated_protocol_eras": ["legacy", "modern"],
        "stdout_protocol_only": True,
        "raw_stdio_messages": 3,
        "tools_binary_sha256": sha256(tools),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "server_entry_sha256": sha256(server),
        "probe_entry_sha256": sha256(probe_entry),
        "tools_sdk_entry_sha256": sha256(sdk_entry),
        "package_lock_sha256": sha256(package_lock),
        "review_before_manifest_sha256": sha256(review_fixtures / "release-readiness/before/program.json"),
        "review_after_manifest_sha256": sha256(review_fixtures / "release-readiness/after/program.json"),
        "review_cases_sha256": sha256(review_cases),
        "comparator_spec_sha256": sha256(comparator_spec),
        "comparator_cases_sha256": sha256(comparator_cases),
        "opcode_cases_sha256": sha256(Path("examples/graph/fixed-limit-exclusive-cases.json")),
        "configured_workspace_scope": True,
        "out_of_scope_startup_rejected": True,
        "program_edits_opt_in": True,
        "unauthorized_write_rejected": True,
        "conflicting_change_rejected": True,
        "rejected_change_root_unchanged": True,
        "original_candidate_committed_after_rejection": True,
        "workspace_creation_scoped": True,
        "created_native_template_project": True,
        "configured_native_review_verified": True,
        "execution_may_persist_reports": True,
        "resources_discovered": 5,
        "tools_discovered_full": 9,
        "native_boundary_changed": True,
        "resource_i64_exact": True,
        "graph_constant_edit": True,
        "graph_opcode_edit": True,
        "graph_operand_edit": True,
        "graph_branch_edit": True,
        "graph_numeric_exact": True,
        "checked_arithmetic_result": True,
        "external_assertions": 106,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
        "tool_error_code_preserved": True,
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("MCP reference-client evidence differs from selected binaries or adapter sources")
    roots = (observed.get("before_root"), observed.get("after_root"))
    if roots[0] == roots[1] or any(not isinstance(root, str) or len(root) != 64
                                  or any(character not in "0123456789abcdef" for character in root)
                                  for root in roots):
        raise RuntimeError("MCP reference-client evidence omitted changed native root identity")
    report["capabilities"]["local_mcp_adapter"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "official MCP SDK stdio reference client on eight fresh Sley projects; three raw stdout messages parsed as JSON-RPC, nine tools on the fully configured template project, graph opcode, operand, and branch edit tools on graph projects, five resources, two protocol eras, scoped creation/review, rejected conflicting edit with unchanged accepted root and successful recovery, exact i64 and checked arithmetic results, and 106 external assertions; live agent task and native core tests pending",
    }


def add_mcp_launcher_evidence(report: dict, path: Path, tools: Path, server: Path,
                              probe_entry: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": "sley-tools.mcp-launcher-probe.v1", "result": "PASS",
        "tools_binary_sha256": sha256(tools),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "adapter_sha256": sha256(server),
        "probe_entry_sha256": sha256(probe_entry),
        "missing_adapter_rejected": True,
        "missing_adapter_file_rejected": True,
        "incomplete_review_rejected": True,
        "tools_override_rejected": True,
        "native_boundary_changed": True,
        "protocol_client_connected": True,
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("MCP launcher evidence differs from selected binaries or protocol probe")
    roots = (observed.get("old_root"), observed.get("new_root"))
    if roots[0] == roots[1] or any(not isinstance(root, str) or len(root) != 64
                                  or any(character not in "0123456789abcdef" for character in root)
                                  for root in roots):
        raise RuntimeError("MCP launcher evidence omitted the changed accepted native root")
    report["capabilities"]["local_mcp_cli_launcher"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "sley-tools mcp replaces its process with an explicitly selected installed Node adapter, keeps stdout protocol-only, connects an official MCP client, and carries one native Release Readiness proposal through validation, commit, and changed execution; missing adapter and malformed options refuse before launch",
    }


def add_checked_graph_evidence(report: dict, path: Path, tools: Path, builder: Path,
                               spec: Path, cases: Path, schema: Path,
                               python_sdk: Path, typescript_sdk: Path,
                               typescript_probe: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": "sley-tools.checked-graph-probe.v1", "result": "PASS",
        "tools_binary_sha256": sha256(tools), "builder_binary_sha256": sha256(builder),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "spec_sha256": sha256(spec), "cases_sha256": sha256(cases),
        "schema_sha256": sha256(schema), "python_sdk_sha256": sha256(python_sdk),
        "typescript_sdk_sha256": sha256(typescript_sdk),
        "typescript_probe_sha256": sha256(typescript_probe),
        "probe_entry_sha256": sha256(Path(__file__).with_name("probe_checked_graph.py")),
        "oracle_verified_cases": 9, "checked_overflow_observed": True,
        "checked_add_native_cases": 3, "checked_mul_neg_result_native_cases": 4,
        "checked_operation_entities_verified": True,
        "invalid_negative_flag_observed": True, "missing_measurement_rejected": True,
        "native_cases_by_adapter": {"cli": 9, "python": 9, "typescript": 9},
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("checked graph evidence differs from selected native inputs or adapters")
    roots = observed.get("native_roots", {})
    if set(roots) != {"cli", "python", "typescript"} or len(set(roots.values())) != 3 or any(
            not isinstance(root, str) or len(root) != 64
            or any(character not in "0123456789abcdef" for character in root)
            for root in roots.values()):
        raise RuntimeError("checked graph evidence omitted three distinct accepted native roots")
    add_root = observed.get("checked_add_root")
    if (not isinstance(add_root, str) or len(add_root) != 64
            or any(character not in "0123456789abcdef" for character in add_root)
            or add_root in roots.values()):
        raise RuntimeError("checked addition evidence omitted its distinct accepted native root")
    advanced_root = observed.get("checked_mul_neg_result_root")
    if (not isinstance(advanced_root, str) or len(advanced_root) != 64
            or any(character not in "0123456789abcdef" for character in advanced_root)
            or advanced_root in roots.values() or advanced_root == add_root):
        raise RuntimeError("checked multiplication, negation, and result evidence omitted its distinct native root")
    report["capabilities"]["native_checked_benchmark_comparator_witness"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "fresh native Benchmark Comparator graph with exact checked signed deltas, independent regression/negative-input flags, explicit overflow result, and missing-measurement rejection; nine oracle-checked cases through CLI, Python, and TypeScript, plus three native checked-addition cases and four checked multiplication/negation/result-construction cases; catalog admission and native core tests pending",
    }


def add_data_pipe_native_evidence(report: dict, path: Path, tools: Path, builder: Path,
                                  schema: Path, selector: Path, selector_cases: Path,
                                  selector_after_cases: Path, reducer: Path,
                                  reducer_cases: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": "sley-tools.data-pipe-native-probe.v1", "result": "PASS",
        "tools_binary_sha256": sha256(tools), "builder_binary_sha256": sha256(builder),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "schema_sha256": sha256(schema),
        "python_sdk_sha256": sha256(Path("packages/python/sley_tools/runtime.py")),
        "probe_entry_sha256": sha256(Path("scripts/probe_data_pipe_native.py")),
        "selector_spec_sha256": sha256(selector),
        "selector_cases_sha256": sha256(selector_cases),
        "selector_after_cases_sha256": sha256(selector_after_cases),
        "reducer_spec_sha256": sha256(reducer),
        "reducer_cases_sha256": sha256(reducer_cases),
        "selector_native_cases": 9, "reducer_native_cases": 10,
        "selector_changed_cases": 2,
        "selected_before": ["mixed-cpu-regression", "zero-baselines", "max-increase",
                            "archive-only-regression"],
        "selected_after": ["all-zero", "mixed-cpu-regression", "zero-baselines",
                           "max-increase", "max-decrease", "archive-only-regression"],
        "partition_shapes": [[5], [2, 3], [1, 1, 3]],
        "partition_summary": [5, 155, 275, 397],
        "empty_identity_verified": True, "overflow_arms_verified": 4,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("native Data Pipe evidence differs from selected programs or inputs")
    roots = observed.get("native_roots", {})
    edited = observed.get("edited_selector_root")
    values = [roots.get("selector"), roots.get("reducer"), edited]
    if (set(roots) != {"selector", "reducer"} or len(set(values)) != 3 or any(
            not isinstance(value, str) or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in values)):
        raise RuntimeError("native Data Pipe evidence omitted distinct accepted roots")
    report["capabilities"]["native_data_pipe_selector_reducer_witnesses"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "two independently callable local Sley graphs: selector/projector and checked summary reducer; nine and ten native cases, exact one-constant predicate edit, empty identity, four overflow arms, and three batch partitions; catalog admission and native core tests pending",
    }


def add_data_pipe_stream_evidence(report: dict, path: Path, tools: Path,
                                  comparator: Path, comparator_cases: Path,
                                  selector: Path, selector_cases: Path,
                                  selector_after_cases: Path,
                                  reducer: Path, reducer_cases: Path,
                                  sample: Path, expected_output: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    launcher_profile = observed.get("contract") == "sley-tools.data-pipe-stream-probe.v2"
    expected = {
        "contract": ("sley-tools.data-pipe-stream-probe.v2" if launcher_profile
                     else "sley-tools.data-pipe-stream-probe.v1"), "result": "PASS",
        "tools_binary_sha256": sha256(tools),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "module_sha256": sha256(Path("packages/python/sley_tools/data_pipe.py")),
        "python_sdk_sha256": sha256(Path("packages/python/sley_tools/runtime.py")),
        "package_sha256": sha256(Path("packages/python/pyproject.toml")),
        "probe_entry_sha256": sha256(Path("scripts/probe_data_pipe_stream.py")),
        "comparator_spec_sha256": sha256(comparator),
        "comparator_cases_sha256": sha256(comparator_cases),
        "selector_spec_sha256": sha256(selector),
        "selector_cases_sha256": sha256(selector_cases),
        "selector_after_cases_sha256": sha256(selector_after_cases),
        "reducer_spec_sha256": sha256(reducer),
        "reducer_cases_sha256": sha256(reducer_cases),
        "sample_sha256": sha256(sample), "expected_sha256": sha256(expected_output),
        "sample_records": 4, "sample_selected": 2,
        "installed_entrypoint_verified": True,
        "batch_sizes": [1, 2, 4],
        "predicate_edit_host_reuse": True,
        "empty_native_identity": True, "signed_boundary": True,
        "reducer_overflow": True, "atomic_file_output": True,
        "symlink_output_rejected": True,
        "negative_paths": ["duplicate", "malformed", "missing", "negative", "noncanonical", "unit"],
        "stdout_partial_count": 1, "record_limit_enforced": True,
        "line_and_byte_limits_enforced": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if launcher_profile:
        expected.update({"unified_cli_launcher_verified": True,
                         "missing_adapter_and_duplicate_option_rejected": True,
                         "tools_source_dirty": False})
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("Data Pipe stream evidence differs from selected implementation or inputs")
    if launcher_profile and (not isinstance(observed.get("tools_source_commit"), str)
                             or len(observed["tools_source_commit"]) != 40):
        raise RuntimeError("Data Pipe launcher evidence lacks its source commit")
    roots = observed.get("native_roots", {})
    edited = observed.get("edited_selector_root")
    if (set(roots) != {"comparator", "selector", "reducer"}
            or len(set([*roots.values(), edited])) != 4 or any(
                not isinstance(root, str) or len(root) != 64
                or any(character not in "0123456789abcdef" for character in root)
                for root in [*roots.values(), edited])):
        raise RuntimeError("Data Pipe stream omitted four distinct accepted native roots")
    scope = ("installed Python host command and sley-tools pipe launcher stream fixed-schema JSON Lines through three accepted native Sley graphs; independent four-row sample, three batch sizes, accepted selector predicate edit with unchanged host command, empty identity, exact signed boundary, overflow, duplicate/malformed/missing/unit/negative rejection, stdout partial count, atomic file output, and missing-adapter/duplicate-option refusal verified; broader workloads and native core tests pending"
             if launcher_profile else
             "installed Python host command streams fixed-schema JSON Lines through three accepted native Sley graphs; independent four-row sample, three batch sizes, accepted selector predicate edit with unchanged host command, empty identity, exact signed boundary, overflow, duplicate/malformed/missing/unit/negative rejection, stdout partial count, and atomic file output verified; broader workloads and native core tests pending")
    report["capabilities"]["local_data_pipe_jsonl_stream"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": scope,
    }


def add_data_pipe_performance_evidence(report: dict, path: Path, tools: Path, builder: Path,
                                       comparator: Path, comparator_cases: Path,
                                       selector: Path, selector_cases: Path,
                                       reducer: Path, reducer_cases: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": "sley-tools.data-pipe-performance-probe.v2", "result": "PASS",
        "tools_binary_sha256": sha256(tools), "builder_binary_sha256": sha256(builder),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "module_sha256": sha256(Path("packages/python/sley_tools/data_pipe.py")),
        "python_sdk_sha256": sha256(Path("packages/python/sley_tools/runtime.py")),
        "probe_entry_sha256": sha256(Path("scripts/probe_data_pipe_performance.py")),
        "comparator_spec_sha256": sha256(comparator),
        "comparator_cases_sha256": sha256(comparator_cases),
        "selector_spec_sha256": sha256(selector),
        "selector_cases_sha256": sha256(selector_cases),
        "reducer_spec_sha256": sha256(reducer),
        "reducer_cases_sha256": sha256(reducer_cases),
        "peak_scope": "sampled RSS sum of pipeline process and live descendants on Linux /proc",
        "memory_growth_bound_verified": True,
        "workload": "alternating wall regression and all-metric improvement",
        "selected_output_verified": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("Data Pipe performance evidence differs from the selected implementation")
    roots = observed.get("native_roots", {})
    if (set(roots) != {"comparator", "selector", "reducer"}
            or len(set(roots.values())) != 3 or any(
                not isinstance(root, str) or len(root) != 64
                or any(character not in "0123456789abcdef" for character in root)
                for root in roots.values())):
        raise RuntimeError("Data Pipe performance omitted three accepted native roots")
    inputs, runs = observed.get("inputs"), observed.get("observations")
    if (not isinstance(inputs, list) or not isinstance(runs, list)
            or [entry.get("records") for entry in inputs] != [64, 8192]
            or [entry.get("records") for entry in runs] != [64, 8192]
            or inputs[1]["bytes"] <= 1_048_576
            or inputs[1]["bytes"] < inputs[0]["bytes"] * 100
            or any(not isinstance(item.get("sha256"), str) or len(item["sha256"]) != 64
                   for item in inputs)
            or any(item.get("memory_samples", 0) < 1 or item.get("elapsed_seconds", 0) <= 0
                   or item.get("rows_per_second", 0) <= 0
                   or item.get("sampled_peak_process_tree_kib", 0) <= 0
                   or item.get("selected_records") != item["records"] // 2
                   or item.get("output_bytes", 0) <= 0
                   or not isinstance(item.get("output_sha256"), str)
                   or len(item["output_sha256"]) != 64 for item in runs)
            or runs[1]["output_bytes"] <= 65_536):
        raise RuntimeError("Data Pipe performance did not cover two measured stream sizes")
    small_peak = runs[0]["sampled_peak_process_tree_kib"]
    large_peak = runs[1]["sampled_peak_process_tree_kib"]
    if large_peak > small_peak + 32_768 or large_peak * 2 > small_peak * 3:
        raise RuntimeError("Data Pipe process-tree resident memory grew beyond the stated bound")
    report["capabilities"]["local_data_pipe_bounded_memory_witness"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "same three native graphs streamed 64 and 8192 alternating regression/improvement rows; all selected output and exact native summary verified, throughput and 50 ms sampled process-tree RSS recorded, 128-fold input growth stayed within 32 MiB and 1.5x peak RSS bounds; one mixed fixed-schema workload, native core tests pending",
    }


def add_graph_artifact_evidence(report: dict, path: Path, tools: Path, builder: Path,
                                schema: Path, sample: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": "sley-tools.graph-artifact-probe.v1", "result": "PASS",
        "tools_binary_sha256": sha256(tools), "builder_binary_sha256": sha256(builder),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "schema_sha256": sha256(schema), "sample_sha256": sha256(sample),
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("graph artifact evidence differs from selected binaries or inputs")
    profiles = observed.get("profiles")
    if (not isinstance(profiles, list)
        or [(item.get("program_id"), item.get("replayed_assertions")) for item in profiles]
            != [("range-predicate", 8), ("numeric-echo", 3),
                ("benchmark-summary-reducer", 10)]):
        raise RuntimeError("graph artifact evidence omits an imported native profile")
    edited = observed.get("edited_selector", {})
    if (edited.get("object_count") != 33 or edited.get("replayed_assertions") != 9
        or edited.get("unchanged_host_wrapper_selected_rows") != 4
        or any(edited.get(name) is not True for name in
               ("mismatched_recipe_rejected", "corrupt_native_object_rejected",
                "existing_artifact_refused", "reexported_graph_replayed"))):
        raise RuntimeError("edited native selector artifact evidence is incomplete")
    for item in [*profiles, edited]:
        if item.get("source_root") == item.get("replayed_root") or any(
            not isinstance(item.get(name), str) or len(item[name]) != 64
            or any(character not in "0123456789abcdef" for character in item[name])
            for name in ("source_root", "replayed_root", "artifact_sha256")
        ):
            raise RuntimeError("graph artifact replay did not preserve exact identity evidence")
    report["capabilities"]["portable_native_graph_artifact_rebase"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "exact SCB1 graph objects and bound recipe/cases transferred to fresh authority contexts for branching, primitive-result, and checked reducer programs; edited Data Pipe selector replayed through unchanged host command; source object and recipe mismatch rejection; second native edit and re-export verified; only bounded graph_v0 profile, native core tests pending",
    }


def add_comparator_catalog_evidence(report: dict, path: Path, tools: Path, schema: Path,
                                    catalog_path: Path, package: Path, sample: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    manifest_path = package / "program.json"
    artifact_path = package / "artifact/native-graph.json"
    recipe_path = package / "build/graph.json"
    cases_path = package / "tests/cases.json"
    after_path = package / "tests/allow-corrections-cases.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    entry = next((item for item in catalog.get("programs", []) if item.get("id") == "N03"), None)
    expected = {
        "contract": "sley-tools.comparator-catalog-probe.v1", "result": "PASS",
        "tools_binary_sha256": sha256(tools),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "schema_sha256": sha256(schema), "catalog_sha256": sha256(catalog_path),
        "manifest_sha256": sha256(manifest_path), "artifact_sha256": sha256(artifact_path),
        "recipe_sha256": sha256(recipe_path), "cases_sha256": sha256(cases_path),
        "changed_cases_sha256": sha256(after_path), "sample_sha256": sha256(sample),
        "native_object_count": 34, "independently_checked_cases_before": 12,
        "independently_checked_cases_after": 12, "negative_cases_changed": 6,
        "missing_measurement_rejected": True,
        "unchanged_host_sample_verified_before_and_after": True,
        "edited_artifact_replayed": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("N03 catalog evidence differs from selected package or binaries")
    if (entry is None or entry.get("program_id") != "benchmark-comparator"
        or entry.get("artifact") != artifact_path.as_posix()
        or entry.get("verified_behavior") != path.as_posix()
        or entry.get("worked_modification") != path.as_posix()
        or manifest["artifact"]["sha256"] != sha256(artifact_path)
        or manifest["entrypoint"]["state_root"] != artifact["source_state_root"]
        or observed.get("source_root") != artifact["source_state_root"]):
        raise RuntimeError("N03 catalog entry or native object binding differs")
    if (len({observed.get(name) for name in ("source_root", "imported_root", "edited_root")}) != 3
        or any(not isinstance(observed.get(name), str) or len(observed[name]) != 64 for name in
               ("source_root", "imported_root", "edited_root", "candidate_id", "receipt_id",
                "sample_output_sha256"))):
        raise RuntimeError("N03 catalog evidence omitted native commit or host output identity")
    report["capabilities"]["catalog_benchmark_comparator_native_artifact"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "checked-in N03 manifest and 34-object native artifact; fresh authority-rebased import, twelve independently checked cases before and after a validated negative-input policy edit, six changed negative cases, missing-input rejection, unchanged Data Pipe output for valid rows, and edited artifact replay; local package only, native core tests pending",
    }


def add_reducer_catalog_evidence(report: dict, path: Path, tools: Path, schema: Path,
                                 catalog_path: Path, package: Path, sample: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    manifest_path = package / "program.json"
    artifact_path = package / "artifact/native-graph.json"
    recipe_path = package / "build/graph.json"
    cases_path = package / "tests/cases.json"
    after_path = package / "tests/allow-corrections-cases.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    entry = next((item for item in catalog.get("programs", []) if item.get("id") == "N05"), None)
    expected = {
        "contract": "sley-tools.reducer-catalog-probe.v1", "result": "PASS",
        "tools_binary_sha256": sha256(tools),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "schema_sha256": sha256(schema), "catalog_sha256": sha256(catalog_path),
        "manifest_sha256": sha256(manifest_path), "artifact_sha256": sha256(artifact_path),
        "recipe_sha256": sha256(recipe_path), "cases_sha256": sha256(cases_path),
        "changed_cases_sha256": sha256(after_path), "sample_sha256": sha256(sample),
        "native_object_count": 36, "independently_checked_cases_before": 17,
        "independently_checked_cases_after": 17, "negative_total_cases_changed": 5,
        "missing_summary_input_rejected": True,
        "empty_identity_distinguished_from_zero_valued_record": True,
        "host_partitions_verified_before": [1, 2, 4],
        "unchanged_host_sample_verified_after": True,
        "edited_artifact_replayed": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("N05 catalog evidence differs from selected package or binaries")
    if (entry is None or entry.get("program_id") != "benchmark-summary-reducer"
        or entry.get("artifact") != artifact_path.as_posix()
        or entry.get("verified_behavior") != path.as_posix()
        or entry.get("worked_modification") != path.as_posix()
        or manifest["artifact"]["sha256"] != sha256(artifact_path)
        or manifest["entrypoint"]["state_root"] != artifact["source_state_root"]
        or observed.get("source_root") != artifact["source_state_root"]):
        raise RuntimeError("N05 catalog entry or native object binding differs")
    if (len({observed.get(name) for name in ("source_root", "imported_root", "edited_root")}) != 3
        or any(not isinstance(observed.get(name), str) or len(observed[name]) != 64 for name in
               ("source_root", "imported_root", "edited_root", "candidate_id", "receipt_id",
                "sample_output_sha256"))):
        raise RuntimeError("N05 catalog evidence omitted native commit or host output identity")
    report["capabilities"]["catalog_benchmark_summary_reducer_native_artifact"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "checked-in N05 manifest and 36-object native artifact; fresh authority-rebased import, seventeen independently checked cases before and after a validated negative-total policy edit, five changed negative-total cases, empty identity and overflow, Data Pipe partition agreement at batch sizes 1/2/4, and edited artifact replay; local package only, native core tests pending",
    }


def add_selector_catalog_evidence(report: dict, path: Path, tools: Path, schema: Path,
                                  catalog_path: Path, package: Path, sample: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    manifest_path = package / "program.json"
    artifact_path = package / "artifact/native-graph.json"
    recipe_path = package / "build/graph.json"
    cases_path = package / "tests/cases.json"
    after_path = package / "tests/include-all-cases.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    entry = next((item for item in catalog.get("programs", []) if item.get("id") == "N04"), None)
    expected = {
        "contract": "sley-tools.selector-catalog-probe.v1", "result": "PASS",
        "tools_binary_sha256": sha256(tools),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "schema_sha256": sha256(schema), "catalog_sha256": sha256(catalog_path),
        "manifest_sha256": sha256(manifest_path), "artifact_sha256": sha256(artifact_path),
        "recipe_sha256": sha256(recipe_path), "cases_sha256": sha256(cases_path),
        "changed_cases_sha256": sha256(after_path), "sample_sha256": sha256(sample),
        "native_object_count": 33, "independently_checked_cases_before": 9,
        "independently_checked_cases_after": 9,
        "unchanged_host_selected_rows_after": 4, "edited_artifact_replayed": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("N04 catalog evidence differs from selected package or binaries")
    if (entry is None or entry.get("program_id") != "benchmark-selector"
        or entry.get("artifact") != artifact_path.as_posix()
        or entry.get("verified_behavior") != path.as_posix()
        or entry.get("worked_modification") != path.as_posix()
        or manifest["artifact"]["sha256"] != sha256(artifact_path)
        or manifest["entrypoint"]["state_root"] != artifact["source_state_root"]
        or observed.get("source_root") != artifact["source_state_root"]):
        raise RuntimeError("N04 catalog entry or native object binding differs")
    if (len({observed.get(name) for name in ("source_root", "imported_root", "edited_root")}) != 3
        or any(not isinstance(observed.get(name), str) or len(observed[name]) != 64 for name in
               ("source_root", "imported_root", "edited_root", "candidate_id", "receipt_id"))):
        raise RuntimeError("N04 catalog evidence omitted distinct native commit identities")
    report["capabilities"]["catalog_benchmark_selector_native_artifact"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "checked-in N04 manifest and 33-object native artifact; fresh authority-rebased import, nine independently checked cases before and after a validated native predicate edit, unchanged Data Pipe host command selecting four rows, and edited artifact replay; local package only, native core tests pending",
    }


def add_range_catalog_evidence(report: dict, path: Path, tools: Path, schema: Path,
                               catalog_path: Path, package: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    manifest_path = package / "program.json"
    artifact_path = package / "artifact/native-graph.json"
    recipe_path = package / "build/graph.json"
    cases_path = package / "tests/cases.json"
    excluded_path = package / "tests/excluded-upper-cases.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    entry = next((item for item in catalog.get("programs", []) if item.get("id") == "N07"), None)
    expected = {
        "contract": "sley-tools.range-catalog-probe.v1", "result": "PASS",
        "tools_binary_sha256": sha256(tools),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "schema_sha256": sha256(schema), "catalog_sha256": sha256(catalog_path),
        "manifest_sha256": sha256(manifest_path), "artifact_sha256": sha256(artifact_path),
        "recipe_sha256": sha256(recipe_path), "cases_sha256": sha256(cases_path),
        "excluded_cases_sha256": sha256(excluded_path),
        "python_example_sha256": sha256(package / "examples/python.py"),
        "cli_examples_sha256": {
            "upper_endpoint": sha256(package / "inputs/upper-endpoint.json"),
            "reversed_bounds": sha256(package / "inputs/reversed-bounds.json"),
        },
        "native_object_count": 28, "independently_checked_cases_each_phase": 35,
        "python_native_example_cases": 3,
        "bundled_cli_example_cases": 2,
        "edited_artifact_replayed": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("N07 catalog evidence differs from selected package or binaries")
    if (entry is None or entry.get("program_id") != "range-predicate"
        or entry.get("artifact") != artifact_path.as_posix()
        or entry.get("verified_behavior") != path.as_posix()
        or entry.get("worked_modification") != path.as_posix()
        or manifest["artifact"]["sha256"] != sha256(artifact_path)
        or manifest["entrypoint"]["state_root"] != artifact["source_state_root"]
        or observed.get("source_root") != artifact["source_state_root"]):
        raise RuntimeError("N07 catalog entry or native object binding differs")
    identities = ("source_root", "imported_root", "excluded_root", "corrected_root",
                  "replay_root", "exclusion_candidate_id", "exclusion_receipt_id",
                  "correction_candidate_id", "correction_receipt_id")
    if (any(not isinstance(observed.get(name), str) or len(observed[name]) != 64
            for name in identities)
        or observed["source_root"] == observed["imported_root"]
        or observed["imported_root"] == observed["excluded_root"]
        or observed["corrected_root"] != observed["imported_root"]
        or observed["replay_root"] == observed["corrected_root"]
        or observed["exclusion_candidate_id"] == observed["correction_candidate_id"]):
        raise RuntimeError("N07 catalog evidence omitted boundary correction or fresh replay")
    report["capabilities"]["catalog_range_predicate_native_artifact"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "checked-in N07 manifest and 28-object branching native artifact; fresh authority-rebased import, 35 independently checked cases in each phase, two bundled CLI inputs and three Python SDK calls including an exact large integer, separate validated exclusion and correction commits, restored content root, and corrected artifact replay; local package only, native core tests pending",
    }


def add_clamp_catalog_evidence(report: dict, path: Path, tools: Path, schema: Path,
                               catalog_path: Path, package: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    manifest_path = package / "program.json"
    artifact_path = package / "artifact/native-graph.json"
    recipe_path = package / "build/graph.json"
    cases_path = package / "tests/cases.json"
    upper_cases = package / "tests/upper-6-cases.json"
    invalid_cases = package / "tests/invalid-order-cases.json"
    example = Path("packages/python/examples/bounded_clamp.py")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    entry = next((item for item in catalog.get("programs", []) if item.get("id") == "N08"), None)
    expected = {
        "contract": "sley-tools.clamp-catalog-probe.v1", "result": "PASS",
        "tools_binary_sha256": sha256(tools),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "schema_sha256": sha256(schema), "catalog_sha256": sha256(catalog_path),
        "manifest_sha256": sha256(manifest_path), "artifact_sha256": sha256(artifact_path),
        "recipe_sha256": sha256(recipe_path), "cases_sha256": sha256(cases_path),
        "upper_cases_sha256": sha256(upper_cases), "invalid_cases_sha256": sha256(invalid_cases),
        "python_example_sha256": sha256(example), "native_object_count": 36,
        "fresh_authoring_cases": 12,
        "independently_checked_cases_each_phase": 38,
        "wrong_typed_inputs_rejected": 3, "python_native_example_passed": True,
        "edited_artifact_replayed": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("N08 catalog evidence differs from package, binaries, or independent cases")
    if (entry is None or entry.get("program_id") != "bounded-clamp"
            or entry.get("artifact") != artifact_path.as_posix()
            or entry.get("verified_behavior") != path.as_posix()
            or entry.get("worked_modification") != path.as_posix()
            or entry.get("integration_examples") != [example.as_posix()]
            or manifest["artifact"]["sha256"] != sha256(artifact_path)
            or manifest["entrypoint"]["state_root"] != artifact["source_state_root"]
            or observed.get("source_root") != artifact["source_state_root"]):
        raise RuntimeError("N08 catalog entry or native object binding differs")
    identities = ("source_root", "imported_root", "upper_edited_root", "invalid_root",
                  "restored_lower_root", "corrected_root", "replay_root",
                  "upper_edit_candidate_id", "invalid_edit_candidate_id")
    if (any(not isinstance(observed.get(name), str) or len(observed[name]) != 64
            for name in identities)
            or len({observed[name] for name in ("source_root", "imported_root", "upper_edited_root", "invalid_root")}) != 4
            or observed["corrected_root"] != observed["imported_root"]
            or observed["replay_root"] == observed["corrected_root"]):
        raise RuntimeError("N08 evidence omitted native bound edit, invalid configuration, or replay")
    report["capabilities"]["catalog_bounded_clamp_native_artifact"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "checked-in N08 manifest and 36-object native clamp artifact; fresh import, 38 independently checked signed values per phase, normal upper-bound and invalid-configuration edits, corrected export replay, and a Python SDK example; local package only, native core tests pending",
    }


def add_quota_catalog_evidence(report: dict, path: Path, tools: Path, schema: Path,
                               catalog_path: Path, package: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    manifest_path = package / "program.json"
    artifact_path = package / "artifact/native-graph.json"
    recipe_path = package / "build/graph.json"
    cases_path = package / "tests/cases.json"
    notify_cases = package / "tests/notify-6-cases.json"
    example = Path("packages/python/examples/quota_remaining.py")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    entry = next((item for item in catalog.get("programs", []) if item.get("id") == "N09"), None)
    expected = {
        "contract": "sley-tools.quota-catalog-probe.v1", "result": "PASS",
        "tools_binary_sha256": sha256(tools),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "schema_sha256": sha256(schema), "catalog_sha256": sha256(catalog_path),
        "manifest_sha256": sha256(manifest_path), "artifact_sha256": sha256(artifact_path),
        "recipe_sha256": sha256(recipe_path), "cases_sha256": sha256(cases_path),
        "notify_cases_sha256": sha256(notify_cases), "python_example_sha256": sha256(example),
        "native_object_count": 35, "independently_checked_cases_each_phase": 81,
        "fresh_authoring_cases": 18, "wrong_typed_inputs_rejected": 3,
        "python_native_example_passed": True, "quota_calculation_preserved_on_edit": True,
        "edited_artifact_replayed": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("N09 catalog evidence differs from package, binaries, or independent cases")
    if (entry is None or entry.get("program_id") != "quota-remaining"
            or entry.get("artifact") != artifact_path.as_posix()
            or entry.get("verified_behavior") != path.as_posix()
            or entry.get("worked_modification") != path.as_posix()
            or entry.get("integration_examples") != [example.as_posix()]
            or manifest["artifact"]["sha256"] != sha256(artifact_path)
            or manifest["entrypoint"]["state_root"] != artifact["source_state_root"]
            or observed.get("source_root") != artifact["source_state_root"]):
        raise RuntimeError("N09 catalog entry or native object binding differs")
    identities = ("source_root", "imported_root", "notification_root", "restored_root",
                  "replay_root", "notification_candidate_id", "notification_receipt_id",
                  "restored_candidate_id")
    if (any(not isinstance(observed.get(name), str) or len(observed[name]) != 64
            for name in identities)
            or len({observed[name] for name in ("source_root", "imported_root", "notification_root")}) != 3
            or observed["restored_root"] != observed["imported_root"]
            or observed["replay_root"] == observed["restored_root"]):
        raise RuntimeError("N09 evidence omitted native edit, restoration, or replay")
    report["capabilities"]["catalog_quota_remaining_native_artifact"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "checked-in N09 manifest and 35-object native quota artifact; fresh authoring and import, 81 independently checked signed pairs per phase, negative-input guard before checked subtraction, normal notification edit preserving quota semantics, and corrected replay; local package only, native core tests pending",
    }


def add_generated_validator_catalog_evidence(report: dict, path: Path, tools: Path,
                                              schema: Path, catalog_path: Path,
                                              package: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    manifest_path = package / "program.json"
    artifact_path = package / "artifact/native-graph.json"
    recipe_path = package / "build/graph.json"
    typed_path = package / "tests/cases.json"
    raw_path = package / "tests/raw-cases.json"
    edited_path = package / "tests/max-12-cases.json"
    source_path = package / "schema/source.json"
    mapping_path = package / "schema/mapping.json"
    example_path = Path("packages/python/examples/generated_payload_validator.py")
    probe_path = Path("scripts/probe_generated_validator_catalog.py")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    entry = next((item for item in catalog.get("programs", []) if item.get("id") == "N10"), None)
    expected = {
        "contract": "sley-tools.generated-validator-catalog-probe.v1", "result": "PASS",
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "tools_binary_sha256": sha256(tools),
        "schema_sha256": sha256(schema), "catalog_sha256": sha256(catalog_path),
        "manifest_sha256": sha256(manifest_path), "artifact_sha256": sha256(artifact_path),
        "recipe_sha256": sha256(recipe_path), "typed_cases_sha256": sha256(typed_path),
        "raw_cases_sha256": sha256(raw_path), "edited_cases_sha256": sha256(edited_path),
        "source_schema_sha256": sha256(source_path), "mapping_sha256": sha256(mapping_path),
        "python_example_sha256": sha256(example_path), "probe_sha256": sha256(probe_path),
        "native_object_count": 51, "fresh_authoring_cases": 5,
        "independently_checked_initial_cases": 16,
        "independently_checked_edited_cases": 18, "malformed_inputs_rejected": 2,
        "python_native_example_passed": True, "edited_artifact_replayed": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(key) != value for key, value in expected.items()):
        raise RuntimeError("N10 catalog evidence differs from selected package or binaries")
    if (entry is None or entry.get("program_id") != "generated-payload-validator"
            or entry.get("artifact") != artifact_path.as_posix()
            or entry.get("verified_behavior") != path.as_posix()
            or entry.get("worked_modification") != path.as_posix()
            or entry.get("integration_examples") != [example_path.as_posix()]
            or manifest["artifact"]["sha256"] != sha256(artifact_path)
            or manifest["entrypoint"]["state_root"] != artifact["source_state_root"]
            or observed.get("source_root") != artifact["source_state_root"]):
        raise RuntimeError("N10 catalog entry or accepted native binding differs")
    identities = ("source_root", "imported_root", "edited_root", "replay_root",
                  "edit_candidate_id", "edit_receipt_id")
    if (any(not isinstance(observed.get(key), str) or len(observed[key]) != 64
            for key in identities)
            or len({observed[key] for key in identities[:4]}) != 4):
        raise RuntimeError("N10 evidence omitted authority rebasing, edit, or replay")
    report["capabilities"]["catalog_generated_payload_validator_native_artifact"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "checked-in N10 schema, source-to-entity mapping, and 51-object native validator artifact; fresh authoring and authority-rebased import, 16 exact-number JSON Schema reference cases, malformed-input rejection, normal native upper-bound edit with 18 reference cases, raw Python adapter, and edited artifact replay; local package only, native core tests pending",
    }


def add_tier_catalog_evidence(report: dict, path: Path, tools: Path, schema: Path,
                              catalog_path: Path, package: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    manifest_path = package / "program.json"
    artifact_path = package / "artifact/native-graph.json"
    recipe_path = package / "build/graph.json"
    cases = {name: package / "tests" / f"{name}.json" for name in
             ("cases", "bronze-12-cases", "invalid-order-cases", "disabled-middle-cases")}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    entry = next((item for item in catalog.get("programs", []) if item.get("id") == "N06"), None)
    expected = {
        "contract": "sley-tools.tier-catalog-probe.v1", "result": "PASS",
        "tools_binary_sha256": sha256(tools),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "schema_sha256": sha256(schema), "catalog_sha256": sha256(catalog_path),
        "manifest_sha256": sha256(manifest_path), "artifact_sha256": sha256(artifact_path),
        "recipe_sha256": sha256(recipe_path),
        "cases_sha256": {name: sha256(case) for name, case in cases.items()},
        "native_object_count": 40, "independent_native_cases_each_phase": 14,
        "published_cases_each_policy": 11,
        "missing_input_rejected": True, "unrelated_categories_preserved": True,
        "invalid_configuration_returned_no_category": True,
        "edited_artifact_replayed": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("N06 catalog evidence differs from selected package or binaries")
    if (entry is None or entry.get("program_id") != "tier-classifier"
        or entry.get("artifact") != artifact_path.as_posix()
        or entry.get("verified_behavior") != path.as_posix()
        or entry.get("worked_modification") != path.as_posix()
        or manifest["artifact"]["sha256"] != sha256(artifact_path)
        or manifest["entrypoint"]["state_root"] != artifact["source_state_root"]
        or observed.get("source_root") != artifact["source_state_root"]):
        raise RuntimeError("N06 catalog entry or native object binding differs")
    identities = ("source_root", "imported_root", "threshold_root", "invalid_root",
                  "disabled_root", "restored_root", "replay_root",
                  "threshold_candidate_id", "threshold_receipt_id",
                  "invalid_candidate_id", "invalid_receipt_id",
                  "disabled_candidate_id", "disabled_receipt_id")
    if (any(not isinstance(observed.get(name), str) or len(observed[name]) != 64
            for name in identities)
        or len({observed[name] for name in
                ("source_root", "imported_root", "threshold_root", "invalid_root", "disabled_root")}) != 5
        or observed["restored_root"] != observed["imported_root"]
        or observed["replay_root"] == observed["imported_root"]):
        raise RuntimeError("N06 evidence omitted threshold, invalid, or branch state")
    report["capabilities"]["catalog_tier_classifier_native_artifact"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "checked-in N06 manifest and 40-object branching native artifact; fresh import, fourteen independently checked signed inputs in each policy, one-threshold edit preserving unrelated tiers, explicit invalid-order domain result, middle-branch repair, and corrected export replay; local package only, native core tests pending",
    }


def add_playground_local_evidence(report: dict, path: Path, tools: Path, server: Path,
                                  html: Path, client: Path, style: Path, probe: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    launcher_profile = observed.get("contract") == "sley-tools.playground-local-probe.v1"
    expected = {
        "contract": ("sley-tools.playground-local-probe.v1" if launcher_profile
                     else "sley-tools.playground-local-probe.v0"), "result": "PASS",
        "tools_binary_sha256": sha256(tools),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "server_sha256": sha256(server), "html_sha256": sha256(html),
        "client_sha256": sha256(client), "style_sha256": sha256(style),
        "probe_script_sha256": sha256(probe),
        "examples": ["release-readiness", "benchmark-comparator", "range-predicate"],
        "origin_and_host_rejection": True, "bounded_request_rejection": True,
        "input_and_change_errors_distinguished": True,
        "separate_session_cookies": True,
        "ui_served": True, "portable_export_replayed": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
        "hosted_ready": False,
    }
    if launcher_profile:
        expected.update({"unified_cli_launcher_verified": True,
                         "missing_adapter_rejected": True,
                         "duplicate_port_rejected": True,
                         "concurrent_session_limit_enforced": True,
                         "tools_source_dirty": False})
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("local Playground evidence differs from selected core, tools, or UI")
    if launcher_profile and (not isinstance(observed.get("tools_source_commit"), str)
                             or len(observed["tools_source_commit"]) != 40):
        raise RuntimeError("local Playground launcher evidence lacks its source commit")
    for name in ("release", "comparator"):
        item = observed.get(name, {})
        before, after = item.get("before_root"), item.get("after_root")
        if before == after or any(
            not isinstance(value, str) or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in (before, after, item.get("candidate_id"), item.get("receipt_id"),
                          item.get("export", {}).get("sha256"),
                          item.get("export", {}).get("imported_root"))
        ):
            raise RuntimeError(f"local Playground {name} omitted native edit or export identity")
        if item["export"]["imported_root"] == after:
            raise RuntimeError(f"local Playground {name} did not rebase its exported artifact")
    if not isinstance(observed.get("range_root"), str) or len(observed["range_root"]) != 64:
        raise RuntimeError("local Playground omitted branching range graph")
    report["capabilities"]["playground_local_native_browser_adapter"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": ("sley-tools playground launches the loopback-only local service with three bundled native programs; typed run, separate proposal/validation/commit, two before/after native edits, session-scoped workspaces with concurrent admission capped at sixteen, Host/Origin rejection, published cases, edited artifact exports imported into fresh workspaces, and missing/duplicate launcher option refusals; hosted worker isolation and native core tests pending"
                  if launcher_profile else
                  "loopback-only local service with three bundled native programs; typed run, separate proposal/validation/commit, two before/after native edits, session-scoped workspaces, Host/Origin rejection, published cases, and edited artifact exports imported into fresh workspaces; hosted worker isolation and native core tests pending"),
    }


def add_playground_browser_current_evidence(report: dict, path: Path, observed: dict,
                                            tools: Path, bundle_builder: Path,
                                            version: int) -> None:
    expected = {
        "contract": f"sley-tools.playground-browser-observation.v{version}", "result": "PASS",
        "tools_binary_sha256": sha256(tools),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "core_archive_sha256": report["selected_core"]["archive_sha256"],
        "bundle_builder_sha256": sha256(bundle_builder),
        "server_sha256": sha256(Path("apps/playground/server.mjs")),
        "client_sha256": sha256(Path("apps/playground/public/app.js")),
        "html_sha256": sha256(Path("apps/playground/public/index.html")),
        "source_checkout_hidden_for_server": True,
        "source_checkout_hidden_for_replay": True, "replay_network_disabled": True,
        "export_private_path_markers_absent": True,
        "before_allow": False, "after_allow": True,
        "browser_console_errors": 0, "browser_console_warnings": 0,
        "commit_outcome_control_present": True,
        "replayed_external_assertions": 48,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
        "hosted_ready": False,
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("current installed Playground browser evidence differs from selected tools or core")
    for name, length in (("tools_source_commit", 40), ("tools_bundle_sha256", 64),
                         ("before_root", 64), ("after_root", 64),
                         ("candidate_id", 64), ("receipt_id", 64),
                         ("download_sha256", 64)):
        value = observed.get(name)
        if not isinstance(value, str) or len(value) != length or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise RuntimeError(f"current installed Playground browser evidence has invalid {name}")
    before, after = observed["before_root"], observed["after_root"]
    directory = Path(f"output/playwright/playground-browser-v{version}")
    filenames = {"before.yml", "proposed.yml", "validated.yml", "after.yml",
                 "accepted-change.png", "sley-release-readiness.tar.gz", "replay.json",
                 "console.txt", "server-mask-check.txt", "replay-mask-check.txt"}
    artifacts = observed.get("artifacts")
    if (not isinstance(artifacts, dict)
            or set(artifacts) != {(directory / name).as_posix() for name in filenames}
            or any(artifacts[(directory / name).as_posix()] != sha256(directory / name)
                   for name in filenames)):
        raise RuntimeError("current installed Playground browser artifacts differ from recorded hashes")
    snapshots = {name: (directory / f"{name}.yml").read_text(encoding="utf-8")
                 for name in ("before", "proposed", "validated", "after")}
    if (before == after or before not in snapshots["before"]
            or before not in snapshots["proposed"]
            or '"allow\\": false' not in snapshots["proposed"]
            or "Proposed. Validate before commit." not in snapshots["proposed"]
            or before not in snapshots["validated"]
            or "Validated by Sley. Ready to commit." not in snapshots["validated"]
            or "Check commit outcome" not in snapshots["after"]
            or f"Root {before} → {after}" not in snapshots["after"]
            or observed["candidate_id"] not in snapshots["after"]
            or observed["receipt_id"] not in snapshots["after"]
            or '"allow\\": true' not in snapshots["after"]):
        raise RuntimeError("current installed Playground browser states omit the native change")
    if "Errors: 0, Warnings: 0" not in (directory / "console.txt").read_text():
        raise RuntimeError("current installed Playground browser console checks differ")
    if (not (directory / "server-mask-check.txt").read_text().startswith("PASS: source checkout")
            or not (directory / "replay-mask-check.txt").read_text().startswith("PASS: source checkout")):
        raise RuntimeError("current installed Playground browser checkout mask checks differ")
    replay = json.loads((directory / "replay.json").read_text(encoding="utf-8"))
    if (replay.get("result") != "PASS" or replay.get("checkout_hidden") is not True
            or replay.get("network_disabled") is not True
            or replay.get("source_state_root") != after
            or replay.get("import", {}).get("source_state_root") != after
            or replay.get("test", {}).get("result") != "PASS"
            or replay.get("test", {}).get("external_assertions_against_native_execution") != 48
            or replay.get("test", {}).get("native_core_test_admission")
                != expected["native_core_test_admission"]
            or replay.get("run", {}).get("result") != observed.get("replayed_native_result")
            or replay.get("run", {}).get("result", {}).get("allow") is not True
            or replay.get("run", {}).get("state_root")
                != replay.get("import", {}).get("program", {}).get("entrypoint", {}).get("state_root")):
        raise RuntimeError("current installed Playground browser export did not replay natively")
    archive = directory / "sley-release-readiness.tar.gz"
    if sha256(archive) != observed["download_sha256"]:
        raise RuntimeError("current installed Playground browser download differs from recorded hash")
    with tarfile.open(archive, "r:gz") as package:
        names = package.getnames()
        if any(name.startswith("/") or ".." in Path(name).parts for name in names):
            raise RuntimeError("current installed Playground browser export contains an unsafe path")
        for member in package.getmembers():
            if not member.isfile():
                continue
            contents = package.extractfile(member)
            data = contents.read() if contents is not None else b""
            if contents is None or any(marker in data for marker in
                                       (b"/home/", b"/tmp/", b"session_cookie",
                                        b"access_token", b"PRIVATE KEY")):
                raise RuntimeError("current installed Playground browser export contains a private marker")
        native = [name for name in names if name.startswith("artifact/playground-")
                  and name.endswith(".json")]
        if len(native) != 1:
            raise RuntimeError("current installed Playground browser export lacks its native artifact")
        stream = package.extractfile(native[0])
        if stream is None or json.load(stream).get("source_state_root") != after:
            raise RuntimeError("current installed Playground browser export has a different root")
    report["capabilities"]["playground_installed_browser_flow_v1"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "real headless Chromium local browser flow launched from a produced tools bundle with checkout masked; native Release Readiness run, separate proposal/validation/commit, downloaded edited artifact imported and replayed by installed CLI in a checkout-masked network-disabled workspace with 48 external assertions; single browser and local mode only, hosted isolation and native core tests pending",
    }


def add_playground_browser_evidence(report: dict, path: Path, tools: Path,
                                    bundle_builder: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    if observed.get("contract") in ("sley-tools.playground-browser-observation.v4",
                                    "sley-tools.playground-browser-observation.v5",
                                    "sley-tools.playground-browser-observation.v6"):
        version = int(observed["contract"].rsplit("v", 1)[1])
        return add_playground_browser_current_evidence(report, path, observed, tools,
                                                       bundle_builder, version)
    current_profile = observed.get("contract") == "sley-tools.playground-browser-observation.v3"
    expected = {
        "contract": ("sley-tools.playground-browser-observation.v3" if current_profile else
                     "sley-tools.playground-browser-observation.v2"), "result": "PASS",
        "tools_binary_sha256": sha256(tools),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "core_archive_sha256": report["selected_core"]["archive_sha256"],
        "bundle_builder_sha256": sha256(bundle_builder),
        "source_checkout_hidden_for_server": True,
        "source_checkout_hidden_for_replay": True,
        "replay_network_disabled": True,
        "export_private_path_markers_absent": True,
        "before_allow": False, "after_allow": True,
        "browser_console_errors": 0, "browser_console_warnings": 0,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
        "hosted_ready": False,
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("installed Playground browser evidence differs from selected tools or core")
    for name, length in (("tools_source_commit", 40), ("tools_bundle_sha256", 64),
                         ("before_root", 64), ("after_root", 64),
                         ("candidate_id", 64), ("receipt_id", 64),
                         ("download_sha256", 64)):
        value = observed.get(name)
        if not isinstance(value, str) or len(value) != length or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise RuntimeError(f"installed Playground browser evidence has invalid {name}")
    before, after = observed["before_root"], observed["after_root"]
    if before == after or observed.get("replayed_import_source_root") != after:
        raise RuntimeError("installed Playground browser edit or export root did not change")
    directory = Path("output/playwright/playground-browser-v3" if current_profile else
                     "output/playwright/playground-browser-v2")
    filenames = {
        "before.yml", "proposed.yml", "validated.yml", "after.yml", "accepted-change.png",
        "sley-release-readiness.tar.gz", "run.json", "test.json", "console.txt",
        "server-mask-check.txt", "replay-mask-check.txt",
    }
    if current_profile:
        filenames.add("replay.json")
    artifacts = observed.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {
        (directory / name).as_posix() for name in filenames
    } or any(artifacts[(directory / name).as_posix()] != sha256(directory / name)
             for name in filenames):
        raise RuntimeError("installed Playground browser artifacts differ from recorded hashes")
    snapshots = {name: (directory / f"{name}.yml").read_text(encoding="utf-8")
                 for name in ("before", "proposed", "validated", "after")}
    if (before not in snapshots["before"]
            or '"allow\\": false' not in snapshots["before"]
            or before not in snapshots["proposed"]
            or "Proposed. Validate before commit." not in snapshots["proposed"]
            or before not in snapshots["validated"]
            or "Validated by Sley. Ready to commit." not in snapshots["validated"]
            or f"Root {before} → {after}" not in snapshots["after"]
            or observed["candidate_id"] not in snapshots["after"]
            or observed["receipt_id"] not in snapshots["after"]
            or '"allow\\": true' not in snapshots["after"]):
        raise RuntimeError("installed Playground browser states omit the native change")
    if "Errors: 0, Warnings: 0" not in (directory / "console.txt").read_text():
        raise RuntimeError("installed Playground browser console checks differ")
    if current_profile:
        if (not (directory / "server-mask-check.txt").read_text().startswith("PASS: source checkout")
                or not (directory / "replay-mask-check.txt").read_text().startswith("PASS: source checkout")):
            raise RuntimeError("installed Playground browser checkout mask checks differ")
    elif ((directory / "server-mask-check.txt").read_text().strip()
          != "browser-server-checkout-masked"
          or (directory / "replay-mask-check.txt").read_text().strip()
          != "replay-checkout-masked-network-disabled"):
        raise RuntimeError("installed Playground browser environment checks differ")
    run = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    test = json.loads((directory / "test.json").read_text(encoding="utf-8"))
    if current_profile:
        replay = json.loads((directory / "replay.json").read_text(encoding="utf-8"))
        if (run.get("native", {}).get("state_root") != before
                or run.get("native", {}).get("result", {}).get("allow") is not False
                or run.get("state", {}).get("last_run", {}).get("inputs") != observed.get("input")
                or test.get("test", {}).get("state_root") != after
                or test.get("test", {}).get("result") != "PASS"
                or test.get("test", {}).get("external_assertions_against_native_execution") != 60
                or replay.get("source_artifact_root") != after
                or replay.get("import", {}).get("source_state_root") != after
                or replay.get("run", {}).get("result") != observed.get("replayed_native_result")
                or replay.get("run", {}).get("result", {}).get("allow") is not True
                or replay.get("test", {}).get("result") != "PASS"
                or replay.get("test", {}).get("external_assertions_against_native_execution") != 60
                or replay.get("test", {}).get("external_assertions_against_native_execution")
                    != observed.get("replayed_external_assertions")
                or replay.get("test", {}).get("native_core_test_admission")
                    != expected["native_core_test_admission"]
                or replay.get("network_disabled") is not True
                or replay.get("source_checkout_hidden") is not True):
            raise RuntimeError("current Playground browser export did not replay natively")
    elif (run.get("result") != observed.get("replayed_native_result")
          or run.get("result", {}).get("allow") is not True
          or test.get("result") != "PASS"
          or test.get("external_assertions_against_native_execution") != 60
          or test.get("external_assertions_against_native_execution")
              != observed.get("replayed_external_assertions")
          or test.get("native_core_test_admission") != expected["native_core_test_admission"]):
        raise RuntimeError("installed Playground browser export did not replay natively")
    archive = directory / "sley-release-readiness.tar.gz"
    if sha256(archive) != observed["download_sha256"]:
        raise RuntimeError("installed Playground browser download differs from recorded hash")
    with tarfile.open(archive, "r:gz") as package:
        names = package.getnames()
        if any(name.startswith("/") or ".." in Path(name).parts for name in names):
            raise RuntimeError("installed Playground browser export contains an unsafe path")
        for member in package.getmembers():
            if not member.isfile():
                continue
            contents = package.extractfile(member)
            data = contents.read() if contents is not None else b""
            if contents is None or any(marker in data for marker in
                                       (b"/home/", b"/tmp/", b"session_cookie",
                                        b"access_token", b"PRIVATE KEY")):
                raise RuntimeError("installed Playground browser export contains a private path or credential marker")
        native = [name for name in names if name.startswith("artifact/playground-")
                  and name.endswith(".json")]
        if len(native) != 1:
            raise RuntimeError("installed Playground browser export lacks its native artifact")
        stream = package.extractfile(native[0])
        if stream is None or json.load(stream).get("source_state_root") != after:
            raise RuntimeError("installed Playground browser export has a different root")
    report["capabilities"]["playground_installed_browser_flow_v1"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "real headless Chromium local browser flow launched from a produced tools bundle with checkout masked; native Release Readiness run, separate proposal/validation/commit, downloaded edited artifact imported and replayed by installed CLI in a checkout-masked network-disabled workspace with 60 external assertions; single browser and local mode only, hosted isolation and native core tests pending",
    }


def add_repair_lab_evidence(report: dict, path: Path, tools: Path, lab: Path,
                            probe: Path, materials: Path, range_artifact: Path,
                            tier_artifact: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    expected_materials = {
        f"{exercise}/{name}": sha256(materials / exercise / name)
        for exercise in ("rl01", "rl02", "rl03", "rl04", "rl05", "rl06", "rl07", "rl08")
        for name in ("TASK.md", "HINTS.md", "SOLUTION.md")
    }
    expected_materials["rl01/starter-recipe.json"] = sha256(
        materials / "rl01/starter-recipe.json")
    for name in ("missing-upper-cases.json", "solution-cases.json"):
        expected_materials[f"rl04/{name}"] = sha256(materials / "rl04" / name)
    expected_materials["rl05/wrong-value.json"] = sha256(
        materials / "rl05/wrong-value.json")
    expected_materials["rl08/input-12.json"] = sha256(
        materials / "rl08/input-12.json")
    expected = {
        "contract": "sley-tools.repair-lab-probe.v6", "result": "PASS",
        "tools_binary_sha256": sha256(tools),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "lab_sha256": sha256(lab), "probe_sha256": sha256(probe),
        "materials_sha256": expected_materials,
        "range_artifact_sha256": sha256(range_artifact),
        "tier_artifact_sha256": sha256(tier_artifact),
        "public_exercises_available": ["rl01", "rl02", "rl03", "rl04", "rl05", "rl06", "rl07", "rl08"],
        "remaining_exercises_pending": [],
        "rl01_imported_wrong_solution_rejected": True,
        "rl02_exclusive_seed_rejected": True,
        "rl03_disabled_seed_rejected": True,
        "rl04_missing_new_case_rejected": True,
        "rl04_missing_boundary_rejected": True,
        "rl04_wrong_expectation_rejected": True,
        "rl04_reset_reexposed_failure": True,
        "rl05_unchanged_seed_rejected": True,
        "rl05_wrong_type_rejected_without_candidate": True,
        "rl05_forged_diagnostic_rejected": True,
        "rl05_reset_confined": True,
        "rl06_advanced_seed_rejected": True,
        "rl06_silent_overwrite_rejected": True,
        "rl06_stale_validation_rejected": True,
        "rl06_forged_reinspection_rejected": True,
        "rl06_reset_confined": True,
        "rl07_limited_seed_rejected": True,
        "rl07_native_permission_denied": True,
        "rl07_commit_without_validation_rejected": True,
        "rl07_artifact_excluded_authority": True,
        "rl07_policy_reuse_explanation_rejected": True,
        "rl07_symlinked_recovered_reset_rejected": True,
        "rl07_reset_confined": True,
        "rl08_unchanged_source_rejected": True,
        "rl08_unbound_source_rejected": True,
        "rl08_forged_destination_run_rejected": True,
        "rl08_symlinked_copied_reset_rejected": True,
        "rl08_reset_confined": True,
        "reset_confined_to_marked_projects": True,
        "unmarked_reset_rejected": True,
        "symlinked_project_reset_rejected": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("Repair Lab evidence differs from the selected tools or materials")
    first, second, third, fourth, fifth, sixth, seventh, eighth = (observed.get(name, {}) for name in
        ("rl01", "rl02", "rl03", "rl04", "rl05", "rl06", "rl07", "rl08"))
    identities = [(first, "accepted_root"), (first, "replay_root"),
                  (first, "artifact_sha256"), (second, "seed_root"),
                  (second, "flaw_root"), (second, "accepted_root"),
                  (second, "correction_candidate_id"),
                  (second, "correction_receipt_id"),
                  (third, "seed_root"), (third, "flaw_root"),
                  (third, "accepted_root"), (third, "correction_candidate_id"),
                  (third, "correction_receipt_id"),
                  (fourth, "accepted_root"), (fourth, "case_file_sha256"),
                  (fourth, "diagnostic_sha256"),
                  (fifth, "seed_root"), (fifth, "accepted_root"),
                  (fifth, "correction_candidate_id"),
                  (fifth, "correction_receipt_id"),
                  (fifth, "diagnostic_sha256"),
                  (sixth, "seed_root"), (sixth, "advanced_root"),
                  (sixth, "accepted_root"), (sixth, "stale_candidate_id"),
                  (sixth, "fresh_candidate_id"), (sixth, "fresh_receipt_id"),
                  (sixth, "stale_diagnostic_sha256"),
                  (sixth, "reinspection_sha256"),
                  (sixth, "fresh_proposal_sha256"),
                  (seventh, "source_root"), (seventh, "source_policy_root"),
                  (seventh, "imported_root"), (seventh, "accepted_root"),
                  (seventh, "destination_policy_root"),
                  (seventh, "artifact_sha256"),
                  (seventh, "permission_diagnostic_sha256"),
                  (seventh, "correction_candidate_id"),
                  (seventh, "correction_receipt_id"),
                  (eighth, "seed_root"), (eighth, "source_root"),
                  (eighth, "destination_root"),
                  (eighth, "source_policy_root"),
                  (eighth, "destination_policy_root"),
                  (eighth, "artifact_sha256"),
                  (eighth, "source_manifest_sha256"),
                  (eighth, "destination_manifest_sha256"),
                  (eighth, "source_run_sha256"),
                  (eighth, "destination_run_sha256")]
    if (any(not isinstance(row.get(name), str) or len(row[name]) != 64
            for row, name in identities)
        or first["accepted_root"] == first["replay_root"]
        or first.get("published_cases", 0) < 8
        or first.get("independent_native_cases") != 16
        or first.get("replayed_native_cases") != 16
        or second["flaw_root"] == second["seed_root"]
        or second["accepted_root"] != second["seed_root"]
        or second.get("published_cases") != 11
        or second.get("independent_native_cases") != 16
        or third["flaw_root"] == third["seed_root"]
        or third["accepted_root"] != third["seed_root"]
        or third.get("published_cases") != 11
        or third.get("independent_native_cases") != 14
        or [third.get(name, {}).get("result", {}).get("tier") for name in
            ("bronze_sample", "middle_sample", "gold_sample")] != ["0", "1", "2"]
        or fourth.get("bound_suite_cases") != 7
        or fourth.get("new_cases") != 3
        or fourth.get("matched_neighbors") != 2
        or not isinstance(fourth.get("failed_case_id"), str)
        or fourth.get("expected", {}).get("within_range") is not True
        or fourth.get("observed", {}).get("within_range") is not False
        or fourth.get("observed", {}).get("excluded_upper") is not True
        or fifth.get("seed_root") == fifth.get("accepted_root")
        or fifth.get("diagnostic_code") != "ST_INPUT_INVALID"
        or fifth.get("diagnostic_category") != "input_shape"
        or fifth.get("published_cases") != 11
        or fifth.get("independent_native_cases") != 14
        or [fifth.get(name, {}).get("result", {}).get("tier") for name in
            ("bronze_sample", "silver_sample", "gold_sample")] != ["0", "1", "2"]
        or sixth.get("seed_root") == sixth.get("advanced_root")
        or sixth.get("advanced_root") == sixth.get("accepted_root")
        or sixth.get("stale_candidate_id") == sixth.get("fresh_candidate_id")
        or sixth.get("published_cases", 0) < 40
        or sixth.get("independent_native_cases") != 12
        or [sixth.get(name, {}).get("result", {}).get("allow") for name in
            ("below_sample", "at_sample", "above_sample")] != [True, True, False]
        or seventh.get("source_root") == seventh.get("imported_root")
        or seventh.get("imported_root") == seventh.get("accepted_root")
        or seventh.get("source_policy_root") == seventh.get("destination_policy_root")
        or seventh.get("source_native_cases") != 12
        or seventh.get("recovered_native_cases") != 12
        or seventh.get("source_at_two", {}).get("result", {}).get("allow") is not False
        or seventh.get("recovered_at_two", {}).get("result", {}).get("allow") is not True
        or eighth.get("seed_root") == eighth.get("source_root")
        or eighth.get("source_root") == eighth.get("destination_root")
        or eighth.get("source_policy_root") == eighth.get("destination_policy_root")
        or eighth.get("source_native_cases") != 14
        or eighth.get("destination_native_cases") != 14
        or eighth.get("source_at_twelve") != {"tier": "0", "invalid_configuration": False}
        or eighth.get("destination_at_twelve") != eighth.get("source_at_twelve")):
        raise RuntimeError("Repair Lab omitted native construction, repair, replay, boundary test, type diagnosis, stale-state recovery, scoped authority, or edited portability")
    report["capabilities"]["repair_lab_rl01_rl08_native"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "eight public local Repair Lab lessons with native construction/edit/run judges, wrong-start rejection, independent native cases, boundary and type diagnostics, stale-state recovery, accepted-policy permission denial and fresh-workspace recovery, bound edited artifact transfer, and workspace-confined reset; independent owner evaluation and native core tests pending",
    }
    report["capabilities"]["scoped_create_only_local_authority"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "Release Readiness template initialization with an accepted create-only grant; inspect reads the accepted principal grant, validation rejects an unauthorized replacement with a core-owned permission diagnostic, and portable import establishes independent normal development authority",
    }


def add_review_isolated_evidence(report: dict, path: Path, fixtures_dir: Path, tools: Path, schema: Path, release_artifact: Path, resource_artifact: Path, release_cases: Path, resource_cases: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    fixture_path = fixtures_dir / "manifest.json"
    fixture_manifest = json.loads(fixture_path.read_text(encoding="utf-8"))
    if observed.get("contract") != "sley-tools.review-isolated-probe.v1" or observed.get("result") != "PASS" or fixture_manifest.get("contract") != "sley-tools.review-fixtures.v1":
        raise RuntimeError("isolated Review or checked-in fixture contract did not pass")
    if observed.get("copied_core_binary_sha256") != report["selected_core"]["binary_sha256"] or fixture_manifest.get("core_binary_sha256") != report["selected_core"]["binary_sha256"] or observed.get("copied_tools_binary_sha256") != sha256(tools) or observed.get("copied_builder_binary_sha256") != sha256(tools.with_name("sley-tools-native-builder")) or fixture_manifest.get("schema_sha256") != sha256(schema):
        raise RuntimeError("isolated Review used a different core, tools binary, or schema")
    if observed.get("fixture_manifest_sha256") != sha256(fixture_path) or observed.get("release_cases_sha256") != sha256(release_cases) or observed.get("resource_cases_sha256") != sha256(resource_cases):
        raise RuntimeError("isolated Review input digest differs from checked-in fixtures")
    if observed.get("source_checkout_mounted") is not False or observed.get("network_namespace_isolated") is not True or observed.get("native_core_test_admission") != "NOT_RUN: production native executor unavailable":
        raise RuntimeError("isolated Review misstates its filesystem, network, or native-test scope")
    programs = observed.get("programs")
    catalog = fixture_manifest.get("programs")
    if not isinstance(programs, list) or not isinstance(catalog, list) or len(programs) != 2 or len(catalog) != 2:
        raise RuntimeError("isolated Review omits a native program")
    selected = (("release-readiness", release_artifact, 48, 20),
                ("resource-admission", resource_artifact, 49, 22))
    for replay, source, (program_id, artifact, count, entities) in zip(programs, catalog, selected, strict=True):
        if replay.get("program_id") != program_id or source.get("program_id") != program_id or source.get("source_artifact_sha256") != sha256(artifact):
            raise RuntimeError(f"isolated Review source differs for {program_id}")
        if source.get("changed_entity_versions") != 1 or replay.get("changed_entity_versions") != 1 or replay.get("case_count") != count or replay.get("shared_entity_ids") != entities or not replay.get("verified_from_copied_inputs") or replay.get("changed_case_count", 0) < 1:
            raise RuntimeError(f"isolated Review did not reproduce {program_id} behavior and structure")
        for side, label in (("before", "before_artifact_sha256"), ("after", "after_artifact_sha256")):
            files = source.get(side, {}).get("files", {})
            if not isinstance(files, dict) or not files:
                raise RuntimeError(f"isolated Review fixture files are absent for {program_id}/{side}")
            if (fixtures_dir / program_id / side).is_symlink() or (fixtures_dir / program_id / side / "artifact").is_symlink():
                raise RuntimeError("checked-in Review fixture contains a symlink directory")
            current_name = json.loads((fixtures_dir / program_id / side / "program.json").read_text(encoding="utf-8"))["artifact"]["path"]
            if replay.get(label) != files.get(current_name):
                raise RuntimeError(f"isolated Review artifact digest differs for {program_id}/{side}")
            for relative, expected in files.items():
                parts = Path(relative).parts
                if relative != "program.json" and (len(parts) != 2 or parts[0] != "artifact" or parts[1] in (".", "..")):
                    raise RuntimeError("checked-in Review fixture contains an unsafe file path")
                selected_file = fixtures_dir / program_id / side / relative
                if not selected_file.is_file() or selected_file.is_symlink() or sha256(selected_file) != expected:
                    raise RuntimeError(f"checked-in Review fixture differs: {program_id}/{side}/{relative}")
                if b"/home/" in selected_file.read_bytes() or b"/tmp/" in selected_file.read_bytes():
                    raise RuntimeError("checked-in Review fixture contains a machine-local path")
        if len(replay.get("report_sha256", "")) != 64:
            raise RuntimeError(f"isolated Review omitted {program_id} report identity")
    report["capabilities"]["same_host_isolated_native_review_replay"] = {
        "status": "supported", "evidence": [path.as_posix(), fixture_path.as_posix()],
        "scope": "same-host Bubblewrap mount/network isolation with copied tools/core and checked-in native packages; both 48/49-case reviews and report verification pass without source checkout mount; GitHub-hosted Action run pending",
    }


def add_review_remote_evidence(report: dict, path: Path, isolated_path: Path, tools: Path, fixtures_dir: Path, release_cases: Path, resource_cases: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    local = json.loads(isolated_path.read_text(encoding="utf-8"))
    remote = observed.get("remote", {})
    if observed.get("contract") != "sley-tools.review-separate-host-probe.v1" or observed.get("result") != "PASS" or remote.get("contract") != "sley-tools.review-host-replay.v1" or remote.get("result") != "PASS":
        raise RuntimeError("separate-host Review replay did not pass")
    if observed.get("local_host") == remote.get("host_name") or not observed.get("local_host") or not remote.get("host_name") or remote.get("platform") != "Linux" or remote.get("machine") != "x86_64":
        raise RuntimeError("separate-host Review replay has no distinct Linux host identity")
    if observed.get("source_checkout_transferred") is not False or observed.get("network_isolation_claimed") is not False:
        raise RuntimeError("separate-host Review replay misstates its transfer or isolation scope")
    selected = (
        ("tools_binary_sha256", sha256(tools)),
        ("builder_binary_sha256", sha256(tools.with_name("sley-tools-native-builder"))),
        ("core_binary_sha256", report["selected_core"]["binary_sha256"]),
        ("fixture_manifest_sha256", sha256(fixtures_dir / "manifest.json")),
        ("release_cases_sha256", sha256(release_cases)),
        ("resource_cases_sha256", sha256(resource_cases)),
    )
    for name, expected in selected:
        if remote.get(name) != expected:
            raise RuntimeError(f"separate-host Review differs on {name}")
    if remote.get("programs") != local.get("programs") or remote.get("native_core_test_admission") != "NOT_RUN: production native executor unavailable":
        raise RuntimeError("separate-host Review behavior or native test status differs")
    report["capabilities"]["separate_host_native_review_replay"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "two-host Linux x86_64 replay with copied tools/core and checked-in native packages; 48/49 cases and report verification match exact local report digests; no source checkout transfer; network isolation not claimed; CI Action pending",
    }


def add_schema_importer_evidence(report: dict, evidence_path: Path, tools: Path,
                                 source: Path, module: Path, probe: Path, lock: Path) -> None:
    observed = json.loads(evidence_path.read_text(encoding="utf-8"))
    expected = {
        "contract": "sley-tools.schema-importer-probe.v1", "result": "PASS",
        "profile": "json-schema-records-v1",
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "tools_binary_sha256": sha256(tools), "schema_sha256": sha256(source),
        "importer_sha256": sha256(module), "probe_sha256": sha256(probe),
        "requirements_dev_sha256": sha256(lock),
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(key) != value for key, value in expected.items()):
        raise RuntimeError("Schema Importer evidence differs from selected core, tools, source, or implementation")
    reference = observed.get("reference", {})
    if (reference.get("library") != "jsonschema" or reference.get("version") != "4.25.1"
            or reference.get("draft") != "2020-12" or "Decimal" not in reference.get("decoder", "")):
        raise RuntimeError("Schema Importer evidence lacks the selected exact-number reference")
    object_cases = observed.get("object_cases", [])
    required = {"valid-min", "valid-max", "integral-decimal", "integral-exponent", "missing", "extra",
                "wrong-root", "boolean-as-integer", "fractional", "string-as-integer", "wrong-boolean",
                "below-min", "above-max", "outside-i64", "zero-huge-exponent"}
    if {case.get("case") for case in object_cases} != required:
        raise RuntimeError("Schema Importer object evidence omits a required boundary or wrong-kind case")
    scalar = observed.get("scalar_profiles", [])
    if {(item.get("profile"), len(item.get("cases", []))) for item in scalar} != {
        ("boolean", 3), ("integer", 9), ("empty-record", 3)
    }:
        raise RuntimeError("Schema Importer evidence omits scalar or empty-record cases")
    maximum = observed.get("maximum_record", {})
    if (maximum.get("parameter_count") != 14 or maximum.get("result_field_count") != 15
            or maximum.get("escaped_pointer") != "/properties/a~1b~0c"
            or {item.get("case") for item in maximum.get("cases", [])} != {
                "valid", "integral-decimal", "missing", "extra", "wide"
            }):
        raise RuntimeError("Schema Importer evidence omits the three-property native limit")
    rejected = {item.get("pointer") for item in observed.get("unsupported_schema_locations", [])}
    if not {"/type", "/pattern", "/minimum", "/maximum", "/required", "/additionalProperties",
            "/properties/x/type", "/$ref", "/$schema", ""}.issubset(rejected):
        raise RuntimeError("Schema Importer evidence omits unsupported-schema locations")
    if set(observed.get("parser_rejections", [])) != {
        "duplicate-key", "malformed", "oversized", "too-deep", "too-many-keys"
    }:
        raise RuntimeError("Schema Importer evidence omits parser failure classes")
    if {(item.get("case"), item.get("code")) for item in observed.get("raw_input_failures", [])} != {
        ("malformed", "ST_INPUT_INVALID"), ("duplicate-key", "ST_INPUT_INVALID"),
        ("oversized", "ST_RESOURCE_LIMIT")
    }:
        raise RuntimeError("Schema Importer evidence misclassifies raw input and resource failures")
    if observed.get("exact_number_sweep_cases") != 512:
        raise RuntimeError("Schema Importer evidence omits exact-number differential cases")
    if observed.get("incompatible_core_rejected") is not True:
        raise RuntimeError("Schema Importer evidence omitted incompatible-core rejection")
    native = observed.get("native", {})
    if (len({native.get("source_state_root"), native.get("edited_state_root"),
             native.get("copied_state_root")}) != 3
            or native.get("normal_constant_edit") != "sley-tools.edit.v0"
            or any(not isinstance(native.get(key), str) or len(native[key]) != 64 for key in (
                "source_artifact_sha256", "edited_artifact_sha256"))):
        raise RuntimeError("Schema Importer evidence omits native artifact transfer or normal editing")
    report["capabilities"]["schema_importer_local_native_records_v1"] = {
        "status": "supported", "evidence": evidence_path.as_posix(),
        "scope": "restricted local draft-2020-12 root Boolean/integer and closed flat records of up to three required Boolean/bounded-i64 properties; exact host JSON projection, native Sley validation/reasons, manifest-bound artifact transfer, normal native bound edit, and independent Decimal reference checks; fresh install and native core tests pending",
    }


def add_python_importer_evidence(report: dict, path: Path, tools: Path, module: Path,
                                 probe: Path, examples: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract": "sley-tools.python-importer-probe.v1", "result": "PASS",
        "tools_sha256": sha256(tools), "core_sha256": report["selected_core"]["binary_sha256"],
        "importer_sha256": sha256(module), "probe_sha256": sha256(probe),
        "differential_cases": 219, "unsafe_arithmetic_rejected": True,
        "source_never_executed_during_import": True, "native_domain_guard_verified": True,
        "missing_domain_rejected": True, "native_run_without_pythonpath": True,
        "named_adapter_native_edit_and_source_binding": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if any(observed.get(name) != value for name, value in expected.items()):
        raise RuntimeError("Python Importer evidence differs from selected tools, core, source, or checks")
    if set(observed.get("unsupported_rejections", [])) != {
        "call", "truthiness", "nested-arithmetic", "missing-return"
    } or set(observed.get("additional_native_syntax_cases", [])) != {
        "boolean-branches", "if-expression", "multiply", "negate"
    }:
        raise RuntimeError("Python Importer evidence omits required syntax boundaries")
    rows = observed.get("examples")
    if not isinstance(rows, list) or [(item.get("name"), item.get("differential_cases")) for item in rows] != [
        ("range", 21), ("clamp", 41), ("quota", 56), ("tier", 101)
    ]:
        raise RuntimeError("Python Importer evidence omits example differential cases")
    for item in rows:
        name = item["name"]
        if (item.get("source_sha256") != sha256(examples / f"{name}.py")
                or item.get("domain_sha256") != sha256(examples / f"{name}.domain.json")
                or item.get("generated_domain_cases") != (4 if name == "quota" else 2)
                or item.get("isolated_reference") != "bubblewrap networkless runtime-only mounts"
                or any(len(item.get(key, "")) != 64 for key in ("accepted_root", "artifact_sha256"))):
            raise RuntimeError(f"Python Importer evidence differs from {name} source or native identity")
    edit = observed.get("native_edit_without_reimport", {})
    if (edit.get("before_root") == edit.get("after_root")
            or any(len(edit.get(key, "")) != 64 for key in ("before_root", "after_root", "artifact_sha256"))):
        raise RuntimeError("Python Importer native edit omitted accepted identity or artifact")
    report["capabilities"]["restricted_python_function_importer_native_v1"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "restricted pure CPython 3.14 int/bool functions with explicit bounded domains; four native Sley graphs, 219 isolated differential cases, native domain guards, unsupported and unsafe arithmetic rejection, normal native constant edit and graph Review; general Python and native core test admission remain open",
    }


def add_fresh_install_evidence(report: dict, path: Path, probe: Path, tools: Path,
                               builder: Path, archive: Path, expected_pipe: Path) -> None:
    observed = json.loads(path.read_text(encoding="utf-8"))
    browser_profile = observed.get("contract") == "sley-tools.fresh-install-probe.v3"
    launcher_profile = browser_profile or observed.get("contract") == "sley-tools.fresh-install-probe.v2"
    expected = {
        "contract": ("sley-tools.fresh-install-probe.v3" if browser_profile else
                     "sley-tools.fresh-install-probe.v2" if launcher_profile else
                     "sley-tools.fresh-install-probe.v1"), "result": "PASS",
        "profile": "local-linux-x86_64-sley-2.1.1",
        "installed_core_binary_sha256": report["selected_core"]["binary_sha256"],
        "installed_tools_binary_sha256": sha256(tools),
        "tools_binary_sha256": sha256(tools),
        "builder_binary_sha256": sha256(builder),
        "core_archive_sha256": sha256(archive),
        "probe_sha256": sha256(probe),
        "data_pipe_expected_output_sha256": sha256(expected_pipe),
        "source_checkout_hidden": True,
        "review_verified": True, "artifact_replay_passed": True,
        "repair_lab_independent_native_cases": 16,
        "schema_import_valid": True, "python_import_value": "63",
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
        "live_agent_task": "NOT_RUN: no agent launched by fresh-install probe",
        "sandbox_stdout": "fresh-install local package scenario passed",
    }
    if launcher_profile:
        expected.update({"data_pipe_unified_cli_launcher": True,
                         "tools_source_dirty": False})
    if any(observed.get(key) != value for key, value in expected.items()):
        raise RuntimeError("fresh-install evidence differs from selected packages, core, or probe")
    if browser_profile:
        playground = observed.get("installed_playground")
        browser_expected = {
            "contract": "sley-tools.playground-local-probe.v1", "result": "PASS",
            "tools_binary_sha256": sha256(tools),
            "core_binary_sha256": report["selected_core"]["binary_sha256"],
            "server_sha256": sha256(Path("apps/playground/server.mjs")),
            "html_sha256": sha256(Path("apps/playground/public/index.html")),
            "client_sha256": sha256(Path("apps/playground/public/app.js")),
            "style_sha256": sha256(Path("apps/playground/public/style.css")),
            "probe_script_sha256": sha256(Path("scripts/probe_playground_local.mjs")),
            "unified_cli_launcher_verified": True,
            "missing_adapter_rejected": True,
            "duplicate_port_rejected": True,
            "portable_export_replayed": True,
            "origin_and_host_rejection": True,
            "hosted_ready": False,
        }
        if not isinstance(playground, dict) or any(
            playground.get(key) != value for key, value in browser_expected.items()
        ):
            raise RuntimeError("fresh-install Playground evidence differs from bundled native browser flow")
    if (observed.get("python_sdk") != {"release_allow": True, "wide_memory": 9007199254740993}
            or observed.get("typescript_sdk") != {
                "release_allow": True, "wide_memory": "9007199254740993"}
            or observed.get("review_changed_cases", 0) < 1
            or observed.get("flagship_new_program_root") == observed.get("flagship_edited_root")
            or observed.get("isolation") !=
               "bubblewrap checkout-masked, private tmpfs, network namespace disabled, new workspace and caches"
            or observed.get("installed_python_package") != "sley_tools-0.0.1 wheel"
            or observed.get("installed_typescript_package") != "@sley-lang/tools@0.0.1 tarball"
            or any(not isinstance(observed.get(name), dict)
                   or observed[name].get("changed_case_count") != 1
                   or not isinstance(observed[name].get("accepted_root"), str)
                   or len(observed[name]["accepted_root"]) != 64
                   for name in ("installed_python_graph_opcode", "installed_typescript_graph_opcode"))
            or any(not isinstance(observed.get(key), str) or len(observed[key]) != 64
                   for key in ("local_tools_package_sha256", "python_wheel_sha256",
                               "typescript_package_sha256", "flagship_new_program_root",
                               "flagship_edited_root"))
            or not isinstance(observed.get("tools_source_commit"), str)
            or len(observed["tools_source_commit"]) != 40):
        raise RuntimeError("fresh-install evidence omits package identities or required scenarios")
    native_candidate = observed.get("installed_native_test_candidate", {})
    if (native_candidate.get("contract") != "sley-tools.native-test-candidate.v0"
            or native_candidate.get("phase") != "STATICALLY_VALID"
            or native_candidate.get("case_id") != "zero"
            or native_candidate.get("accepted_root_unchanged") is not True
            or native_candidate.get("declared_limits", {}).get("memory_bytes") != 16 * 1024 * 1024
            or native_candidate.get("declared_limits", {}).get("memory_bytes", 0) >
               native_candidate.get("grant_ceilings", {}).get("max_memory_bytes", -1)
            or native_candidate.get("native_plan_selected_test_count") != 1
            or native_candidate.get("native_plan_preflight") != "CORE_LIBRARY_ONLY_NO_EXECUTION"
            or native_candidate.get("native_core_test_admission") !=
               "NOT_RUN: production native executor unavailable"
            or native_candidate.get("native_test_execution") != "NOT_RUN"):
        raise RuntimeError("fresh-install evidence promoted static TestCase validation to test admission")
    lab = observed.get("installed_repair_lab")
    exercise_ids = [f"rl{number:02d}" for number in range(1, 9)]
    lab_cases = {"rl01": ("independent_native_cases", 16),
                 "rl02": ("independent_native_cases", 16),
                 "rl03": ("independent_native_cases", 14),
                 "rl04": ("new_cases", 3),
                 "rl05": ("independent_native_cases", 14),
                 "rl06": ("independent_native_cases", 12),
                 "rl07": ("recovered_native_cases", 12),
                 "rl08": ("destination_native_cases", 14)}
    if (not isinstance(lab, dict)
            or lab.get("contract") != "sley-tools.repair-lab-probe.v6"
            or lab.get("result") != "PASS"
            or lab.get("public_exercises_available") != exercise_ids
            or lab.get("remaining_exercises_pending") != []
            or lab.get("tools_binary_sha256") != sha256(tools)
            or lab.get("core_binary_sha256") != report["selected_core"]["binary_sha256"]
            or lab.get("probe_sha256") != sha256(Path("scripts/probe_repair_lab.py"))
            or lab.get("lab_sha256") != sha256(Path("apps/repair-lab/lab.py"))
            or lab.get("native_core_test_admission") !=
               "NOT_RUN: production native executor unavailable"
            or any(not isinstance(lab.get(exercise), dict)
                   or lab[exercise].get(key) != count
                   for exercise, (key, count) in lab_cases.items())
            or any(value is False for value in lab.values() if isinstance(value, bool))):
        raise RuntimeError("fresh-install Repair Lab evidence omitted an installed exercise or failed path")
    schema_edit = observed.get("schema_native_edit", {})
    python_edit = observed.get("python_native_edit", {})
    expected_schema = {"before_count_12_valid": False, "after_count_12_valid": True,
                       "after_count_13_valid": False, "changed_case_count": 1,
                       "external_assertions": 2, "source_alignment": "native_edited",
                       "unsupported_code": "ST_SCHEMA_UNSUPPORTED",
                       "unsupported_pointer": "/pattern"}
    expected_python = {"before_score_25": "1", "after_score_25": "0",
                       "after_score_26": "1", "changed_case_count": 1,
                       "external_assertions": 8, "source_alignment": "native_edited",
                       "unsupported_code": "ST_IMPORT_UNSUPPORTED"}
    if (not isinstance(schema_edit, dict) or not isinstance(python_edit, dict)
            or any(schema_edit.get(key) != value for key, value in expected_schema.items())
            or any(python_edit.get(key) != value for key, value in expected_python.items())
            or any(not isinstance(edit.get(key), str) or len(edit[key]) != 64
                   for edit in (schema_edit, python_edit)
                   for key in ("before_root", "after_root"))
            or any(edit["before_root"] == edit["after_root"]
                   for edit in (schema_edit, python_edit))):
        raise RuntimeError("fresh-install importer evidence omitted native edits or unsupported rejections")
    mcp_package = observed.get("standalone_mcp_package")
    expected_mcp_package = {
        "contract": "sley-tools.mcp-standalone-package.v1", "result": "PASS",
        "filename": "sley-lang-tools-mcp-0.0.1.tgz",
        "package_name": "@sley-lang/tools-mcp", "package_version": "0.0.1",
        "source_server_sha256": sha256(Path("packages/mcp/src/server.mjs")),
        "sdk_source_sha256": sha256(Path("packages/typescript/src/index.js")),
        "source_manifest_sha256": sha256(Path("packages/mcp/package.json")),
        "package_lock_sha256": sha256(Path("packages/mcp/package-lock.json")),
        "npm_lifecycle_scripts_disabled": True,
    }
    if (not isinstance(mcp_package, dict)
            or any(mcp_package.get(key) != value
                   for key, value in expected_mcp_package.items())
            or observed.get("standalone_mcp_package_sha256") != mcp_package.get("archive_sha256")
            or observed.get("mcp_packager_sha256") != sha256(Path("scripts/package_mcp.py"))
            or not isinstance(mcp_package.get("archive_bytes"), int)
            or mcp_package["archive_bytes"] <= 0
            or not isinstance(mcp_package.get("member_count"), int)
            or mcp_package["member_count"] <= 0
            or not isinstance(mcp_package.get("standalone_server_sha256"), str)
            or len(mcp_package["standalone_server_sha256"]) != 64
            or not {"@modelcontextprotocol/core", "@modelcontextprotocol/server", "zod"}.issubset(
                set(mcp_package.get("bundled_dependencies", [])))):
        raise RuntimeError("fresh-install standalone MCP package identity or dependencies differ")
    mcp = observed.get("installed_mcp_reference")
    expected_mcp = {
        "contract": "sley-tools.mcp-reference-probe.v1", "result": "PASS",
        "sdk_server_version": "2.1.0", "sdk_client_version": "2.1.0",
        "negotiated_protocol_eras": ["legacy", "modern"],
        "stdout_protocol_only": True, "raw_stdio_messages": 3,
        "tools_binary_sha256": sha256(tools),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "server_entry_sha256": mcp_package["standalone_server_sha256"],
        "probe_entry_sha256": sha256(Path("packages/mcp/tests/probe.mjs")),
        "tools_sdk_entry_sha256": sha256(Path("packages/typescript/src/index.js")),
        "package_lock_sha256": sha256(Path("packages/mcp/package-lock.json")),
        "resources_discovered": 5, "tools_discovered_full": 9,
        "workspace_creation_scoped": True, "configured_native_review_verified": True,
        "unauthorized_write_rejected": True, "out_of_scope_startup_rejected": True,
        "conflicting_change_rejected": True,
        "rejected_change_root_unchanged": True,
        "original_candidate_committed_after_rejection": True,
        "graph_constant_edit": True, "graph_opcode_edit": True,
        "graph_operand_edit": True, "graph_branch_edit": True,
        "graph_numeric_exact": True, "resource_i64_exact": True,
        "external_assertions": 106,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    if (not isinstance(mcp, dict)
            or any(mcp.get(key) != value for key, value in expected_mcp.items())
            or not isinstance(mcp.get("before_root"), str)
            or not isinstance(mcp.get("after_root"), str)
            or len(mcp["before_root"]) != 64 or len(mcp["after_root"]) != 64
            or mcp["before_root"] == mcp["after_root"]):
        raise RuntimeError("fresh-install MCP evidence omitted the installed native agent edit")
    launcher = observed.get("installed_mcp_launcher", {})
    expected_launcher = {
        "contract": "sley-tools.mcp-launcher-probe.v1", "result": "PASS",
        "tools_binary_sha256": sha256(tools),
        "core_binary_sha256": report["selected_core"]["binary_sha256"],
        "adapter_sha256": mcp_package["standalone_server_sha256"],
        "probe_entry_sha256": sha256(Path("packages/mcp/tests/launcher_probe.mjs")),
        "missing_adapter_rejected": True,
        "missing_adapter_file_rejected": True,
        "incomplete_review_rejected": True,
        "tools_override_rejected": True,
        "native_boundary_changed": True,
        "protocol_client_connected": True,
    }
    if (not isinstance(launcher, dict)
            or any(launcher.get(name) != value for name, value in expected_launcher.items())
            or launcher.get("old_root") == launcher.get("new_root")
            or any(not isinstance(launcher.get(name), str) or len(launcher[name]) != 64
                   for name in ("old_root", "new_root"))):
        raise RuntimeError("fresh-install MCP launcher did not carry an installed native edit")
    scope = ("local tools bundle plus produced Python wheel, TypeScript tarball, and standalone MCP npm tarball installed with verified Sley 2.1.1 archive, checkout hidden and network off; native Starter create/edit/test, static native TestCase candidate validation, Review and replay, exact 2^53+1 SDK calls, installed graph opcode and branch edits, 106-case official MCP reference client including operand edit and rejected-change recovery, plus the CLI MCP launcher against installed standalone server, Data Pipe sample through sley-tools pipe, local Playground run/edit/export through sley-tools playground, all eight Repair Lab exercises, and both native importer edits passed; hosted worker isolation, live agent, native core tests, and public release remain open"
             if browser_profile else
             "local tools bundle plus produced Python wheel, TypeScript tarball, and standalone MCP npm tarball installed with verified Sley 2.1.1 archive, checkout hidden and network off; native Starter create/edit/test, static native TestCase candidate validation, Review and replay, exact 2^53+1 SDK calls, installed graph opcode and branch edits, 106-case official MCP reference client including operand edit and the CLI MCP launcher against installed standalone server, Data Pipe sample through sley-tools pipe, all eight Repair Lab exercises, and both native importer edits passed; live agent, native core tests, and public release remain open"
             if launcher_profile else
             "local tools bundle plus produced Python wheel, TypeScript tarball, and standalone MCP npm tarball installed with verified Sley 2.1.1 archive, checkout hidden and network off; native Starter create/edit/test, static native TestCase candidate validation, Review and replay, exact 2^53+1 SDK calls, installed graph opcode and branch edits, 106-case official MCP reference client including operand edit and the CLI MCP launcher against installed standalone server, Data Pipe sample, all eight Repair Lab exercises, and both native importer edits passed; live agent, native core tests, and public release remain open")
    report["capabilities"]["fresh_install_local_package_scenario_v1"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": scope,
    }
    report["capabilities"]["standalone_local_mcp_package_v1"] = {
        "status": "supported", "evidence": path.as_posix(),
        "scope": "locally produced @sley-lang/tools-mcp@0.0.1 tarball bundles pinned MCP runtime dependencies and the Sley TypeScript SDK; installed outside the checkout with network disabled and passed the 106-assertion official MCP reference-client probe and CLI launcher native edit against the selected Sley 2.1.1 core; npm publication and live agent task remain open",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--genesis-evidence", type=Path)
    parser.add_argument("--native-test-route-evidence", type=Path)
    parser.add_argument("--native-test-tools-binary", type=Path)
    parser.add_argument("--native-test-builder-binary", type=Path)
    parser.add_argument("--release-readiness-evidence", type=Path)
    parser.add_argument("--doctor-evidence", type=Path)
    parser.add_argument("--starter-init-evidence", type=Path)
    parser.add_argument("--starter-edit-evidence", type=Path)
    parser.add_argument("--authoring-evidence", type=Path)
    parser.add_argument("--starter-artifact-evidence", type=Path)
    parser.add_argument("--catalog-dir", type=Path)
    parser.add_argument("--catalog-evidence", type=Path)
    parser.add_argument("--schema-evidence", type=Path)
    parser.add_argument("--schema-file", type=Path)
    parser.add_argument("--bound-artifact-evidence", type=Path)
    parser.add_argument("--review-evidence", type=Path)
    parser.add_argument("--graph-review-evidence", type=Path)
    parser.add_argument("--graph-review-probe", type=Path)
    parser.add_argument("--review-action-evidence", type=Path)
    parser.add_argument("--review-action-hosted-evidence", type=Path)
    parser.add_argument("--review-action-pr-hosted-evidence", type=Path)
    parser.add_argument("--review-action-pr-rejection-evidence", type=Path)
    parser.add_argument("--review-action-metadata", type=Path)
    parser.add_argument("--review-action-runner", type=Path)
    parser.add_argument("--review-action-probe", type=Path)
    parser.add_argument("--review-tools-binary", type=Path)
    parser.add_argument("--review-isolated-evidence", type=Path)
    parser.add_argument("--review-fixtures-dir", type=Path)
    parser.add_argument("--review-remote-evidence", type=Path)
    parser.add_argument("--bridge-rpc-evidence", type=Path)
    parser.add_argument("--bridge-python-evidence", type=Path)
    parser.add_argument("--bridge-typescript-evidence", type=Path)
    parser.add_argument("--bridge-soak-evidence", type=Path)
    parser.add_argument("--bridge-cancellation-evidence", type=Path)
    parser.add_argument("--bridge-timing-evidence", type=Path)
    parser.add_argument("--candidate-reconcile-evidence", type=Path)
    parser.add_argument("--bridge-operation-evidence", type=Path)
    parser.add_argument("--sdk-commit-recovery-evidence", type=Path)
    parser.add_argument("--rpc-authoring-evidence", type=Path)
    parser.add_argument("--python-authoring-evidence", type=Path)
    parser.add_argument("--typescript-authoring-evidence", type=Path)
    parser.add_argument("--graph-builder-evidence", type=Path)
    parser.add_argument("--graph-builder-binary", type=Path)
    parser.add_argument("--graph-spec", type=Path)
    parser.add_argument("--graph-unsigned-spec", type=Path)
    parser.add_argument("--graph-project-evidence", type=Path)
    parser.add_argument("--graph-cases", type=Path)
    parser.add_argument("--graph-unsigned-cases", type=Path)
    parser.add_argument("--graph-edit-evidence", type=Path)
    parser.add_argument("--graph-edit-spec", type=Path)
    parser.add_argument("--graph-edit-initial-cases", type=Path)
    parser.add_argument("--graph-edit-updated-cases", type=Path)
    parser.add_argument("--graph-opcode-evidence", type=Path)
    parser.add_argument("--graph-opcode-updated-cases", type=Path)
    parser.add_argument("--graph-operand-evidence", type=Path)
    parser.add_argument("--graph-branch-evidence", type=Path)
    parser.add_argument("--graph-branch-updated-cases", type=Path)
    parser.add_argument("--graph-sdk-evidence", type=Path)
    parser.add_argument("--graph-sdk-authoring-evidence", type=Path)
    parser.add_argument("--graph-sdk-creation-evidence", type=Path)
    parser.add_argument("--graph-artifact-evidence", type=Path)
    parser.add_argument("--comparator-catalog-evidence", type=Path)
    parser.add_argument("--comparator-catalog-dir", type=Path)
    parser.add_argument("--reducer-catalog-evidence", type=Path)
    parser.add_argument("--reducer-catalog-dir", type=Path)
    parser.add_argument("--selector-catalog-evidence", type=Path)
    parser.add_argument("--selector-catalog-dir", type=Path)
    parser.add_argument("--range-catalog-evidence", type=Path)
    parser.add_argument("--range-catalog-dir", type=Path)
    parser.add_argument("--clamp-catalog-evidence", type=Path)
    parser.add_argument("--clamp-catalog-dir", type=Path)
    parser.add_argument("--quota-catalog-evidence", type=Path)
    parser.add_argument("--quota-catalog-dir", type=Path)
    parser.add_argument("--generated-validator-catalog-evidence", type=Path)
    parser.add_argument("--generated-validator-catalog-dir", type=Path)
    parser.add_argument("--tier-catalog-evidence", type=Path)
    parser.add_argument("--tier-catalog-dir", type=Path)
    parser.add_argument("--playground-local-evidence", type=Path)
    parser.add_argument("--playground-browser-evidence", type=Path)
    parser.add_argument("--playground-server", type=Path)
    parser.add_argument("--playground-html", type=Path)
    parser.add_argument("--playground-js", type=Path)
    parser.add_argument("--playground-css", type=Path)
    parser.add_argument("--playground-probe", type=Path)
    parser.add_argument("--repair-lab-evidence", type=Path)
    parser.add_argument("--repair-lab", type=Path)
    parser.add_argument("--repair-lab-probe", type=Path)
    parser.add_argument("--repair-lab-materials", type=Path)
    parser.add_argument("--repair-lab-range-artifact", type=Path)
    parser.add_argument("--repair-lab-tier-artifact", type=Path)
    parser.add_argument("--mcp-evidence", type=Path)
    parser.add_argument("--mcp-launcher-evidence", type=Path)
    parser.add_argument("--mcp-server", type=Path)
    parser.add_argument("--mcp-probe", type=Path)
    parser.add_argument("--mcp-sdk-entry", type=Path)
    parser.add_argument("--mcp-lock", type=Path)
    parser.add_argument("--benchmark-evidence", type=Path)
    parser.add_argument("--benchmark-spec", type=Path)
    parser.add_argument("--benchmark-cases", type=Path)
    parser.add_argument("--benchmark-typescript-probe", type=Path)
    parser.add_argument("--data-pipe-native-evidence", type=Path)
    parser.add_argument("--selector-spec", type=Path)
    parser.add_argument("--selector-cases", type=Path)
    parser.add_argument("--selector-after-cases", type=Path)
    parser.add_argument("--reducer-spec", type=Path)
    parser.add_argument("--reducer-cases", type=Path)
    parser.add_argument("--data-pipe-stream-evidence", type=Path)
    parser.add_argument("--data-pipe-sample", type=Path)
    parser.add_argument("--data-pipe-expected", type=Path)
    parser.add_argument("--data-pipe-performance-evidence", type=Path)
    parser.add_argument("--graph-fixed-cases", type=Path)
    parser.add_argument("--graph-numeric-spec", type=Path)
    parser.add_argument("--graph-numeric-cases", type=Path)
    parser.add_argument("--bridge-artifact", type=Path)
    parser.add_argument("--bridge-fixtures", type=Path)
    parser.add_argument("--resource-evidence", type=Path)
    parser.add_argument("--resource-artifact-evidence", type=Path)
    parser.add_argument("--resource-edit-evidence", type=Path)
    parser.add_argument("--resource-fixtures", type=Path)
    parser.add_argument("--resource-catalog-dir", type=Path)
    parser.add_argument("--resource-catalog-evidence", type=Path)
    parser.add_argument("--program-catalog", type=Path)
    parser.add_argument("--schema-importer-evidence", type=Path)
    parser.add_argument("--schema-importer-source", type=Path)
    parser.add_argument("--schema-importer-module", type=Path)
    parser.add_argument("--schema-importer-probe", type=Path)
    parser.add_argument("--python-importer-evidence", type=Path)
    parser.add_argument("--python-importer-module", type=Path)
    parser.add_argument("--python-importer-probe", type=Path)
    parser.add_argument("--python-importer-examples", type=Path)
    parser.add_argument("--fresh-install-evidence", type=Path)
    parser.add_argument("--fresh-install-probe", type=Path)
    parser.add_argument("--requirements-dev-lock", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = probe(args.artifact_root.resolve(), args.archive.resolve() if args.archive else None, args.genesis_evidence, args.release_readiness_evidence, args.doctor_evidence, args.starter_init_evidence, args.starter_edit_evidence, args.starter_artifact_evidence, args.catalog_dir, args.catalog_evidence, args.schema_evidence, args.schema_file)
    native_route = (args.native_test_route_evidence, args.native_test_tools_binary,
                    args.native_test_builder_binary)
    if any(item is not None for item in native_route):
        if any(item is None for item in native_route):
            raise RuntimeError("native TestCase route evidence requires the selected tools and builder binaries")
        add_native_test_route_evidence(report, *native_route)
    bridge_args = (args.bridge_artifact, args.bridge_fixtures, args.bridge_rpc_evidence, args.bridge_python_evidence, args.bridge_typescript_evidence, args.bridge_soak_evidence)
    if any(item is not None for item in bridge_args):
        if any(item is None for item in bridge_args):
            raise RuntimeError("Bridge artifact, fixtures, and all four evidence files must be supplied together")
        add_bridge_evidence(report, *bridge_args)
    if args.bridge_cancellation_evidence is not None:
        if args.review_tools_binary is None or args.bridge_artifact is None:
            raise RuntimeError("Bridge cancellation evidence needs the tools binary and artifact")
        add_bridge_cancellation_evidence(report, args.bridge_cancellation_evidence,
                                         args.review_tools_binary, args.bridge_artifact)
    if args.bridge_timing_evidence is not None:
        if any(item is None for item in (args.review_tools_binary, args.bridge_artifact,
                                        args.bridge_fixtures)):
            raise RuntimeError("Bridge timing evidence needs tools binary, artifact, and fixtures")
        add_bridge_timing_evidence(report, args.bridge_timing_evidence,
                                   args.review_tools_binary, args.bridge_artifact, args.bridge_fixtures)
    write_operation = (args.candidate_reconcile_evidence, args.bridge_operation_evidence,
                       args.sdk_commit_recovery_evidence)
    if any(path is not None for path in write_operation):
        if any(path is None for path in write_operation) or args.review_tools_binary is None or args.bridge_artifact is None:
            raise RuntimeError("write operation evidence needs candidate, operation, SDK, tools, and artifact")
        add_write_operation_evidence(report, *write_operation, args.review_tools_binary, args.bridge_artifact)
    resource_specific = (args.resource_fixtures, args.resource_evidence, args.resource_artifact_evidence, args.resource_edit_evidence, args.program_catalog, args.resource_catalog_dir, args.resource_catalog_evidence)
    if any(item is not None for item in resource_specific):
        resource_args = (*resource_specific, args.schema_evidence)
        if any(item is None for item in resource_args):
            raise RuntimeError("Resource Admission fixtures, artifact, catalog, and schema evidence must be supplied together")
        add_resource_evidence(report, *resource_args)
    if args.bound_artifact_evidence is not None:
        if args.schema_file is None or args.bridge_artifact is None or args.resource_catalog_dir is None:
            raise RuntimeError("bound artifact evidence requires schema and both native source artifacts")
        add_bound_artifact_evidence(report, args.bound_artifact_evidence, args.schema_file,
                                    args.bridge_artifact, args.resource_catalog_dir / "artifact/native-program.json")
    if args.review_evidence is not None or args.review_tools_binary is not None:
        if any(item is None for item in (args.review_evidence, args.review_tools_binary, args.schema_file,
                                         args.bridge_artifact, args.resource_catalog_dir, args.bridge_fixtures, args.resource_fixtures)):
            raise RuntimeError("Review evidence requires tools binary, schema, and both native artifact/fixture sets")
        add_review_evidence(report, args.review_evidence, args.review_tools_binary, args.schema_file,
                            args.bridge_artifact, args.resource_catalog_dir / "artifact/native-program.json",
                            args.bridge_fixtures, args.resource_fixtures)
    if args.graph_review_evidence is not None or args.graph_review_probe is not None:
        required = (args.graph_review_evidence, args.graph_review_probe,
                    args.review_tools_binary, args.schema_file,
                    args.generated_validator_catalog_dir, args.python_importer_examples)
        if any(item is None for item in required):
            raise RuntimeError("graph Review evidence needs probe, tools, schema, validator, and Python examples")
        add_graph_review_evidence(
            report, args.graph_review_evidence, args.review_tools_binary,
            args.schema_file,
            args.generated_validator_catalog_dir / "artifact/native-graph.json",
            args.python_importer_examples / "tier.py",
            args.python_importer_examples / "tier.domain.json",
            args.graph_review_probe)
    review_action = (args.review_action_evidence, args.review_action_metadata,
                     args.review_action_runner, args.review_action_probe)
    if any(item is not None for item in review_action):
        if any(item is None for item in review_action) or args.archive is None:
            raise RuntimeError("local Review Action evidence requires metadata, runner, probe, and core archive")
        add_review_action_evidence(report, *review_action)
    if args.review_action_hosted_evidence is not None:
        add_review_action_hosted_evidence(report, args.review_action_hosted_evidence)
    if args.review_action_pr_hosted_evidence is not None:
        add_review_action_pr_hosted_evidence(report, args.review_action_pr_hosted_evidence)
    if args.review_action_pr_rejection_evidence is not None:
        add_review_action_pr_rejection_evidence(report, args.review_action_pr_rejection_evidence)
    if args.authoring_evidence is not None:
        if args.review_tools_binary is None:
            raise RuntimeError("explicit authoring evidence requires the selected tools binary")
        add_authoring_evidence(report, args.authoring_evidence, args.review_tools_binary)
    authoring_bridge = (args.rpc_authoring_evidence, args.python_authoring_evidence,
                        args.typescript_authoring_evidence)
    if any(path is not None for path in authoring_bridge):
        if any(path is None for path in authoring_bridge) or args.review_tools_binary is None:
            raise RuntimeError("RPC, Python, and TypeScript authoring evidence and the tools binary must be supplied together")
        add_rpc_authoring_evidence(report, authoring_bridge, args.review_tools_binary)
    graph_builder = (args.graph_builder_evidence, args.graph_builder_binary, args.graph_spec,
                     args.graph_unsigned_spec)
    if any(item is not None for item in graph_builder):
        if any(item is None for item in graph_builder) or args.review_tools_binary is None:
            raise RuntimeError("graph builder evidence requires graph spec, both binaries, and the selected tools binary")
        add_graph_builder_evidence(report, args.graph_builder_evidence,
                                   args.review_tools_binary, args.graph_builder_binary,
                                   args.graph_spec, args.graph_unsigned_spec)
    if any(item is not None for item in (args.graph_project_evidence, args.graph_cases,
                                          args.graph_unsigned_cases)):
        required = (args.graph_project_evidence, args.graph_cases, args.graph_unsigned_cases,
                    args.review_tools_binary, args.graph_builder_binary, args.schema_file,
                    args.graph_spec, args.graph_unsigned_spec)
        if any(item is None for item in required):
            raise RuntimeError("graph project evidence needs both recipes, case fixtures, binaries, and schema")
        add_graph_project_evidence(report, args.graph_project_evidence, args.review_tools_binary,
                                   args.graph_builder_binary, args.schema_file, args.graph_spec,
                                   args.graph_cases, args.graph_unsigned_spec, args.graph_unsigned_cases)
    graph_edit = (args.graph_edit_evidence, args.graph_edit_spec,
                  args.graph_edit_initial_cases, args.graph_edit_updated_cases)
    if any(item is not None for item in graph_edit):
        if any(item is None for item in (*graph_edit, args.review_tools_binary,
                                         args.graph_builder_binary, args.schema_file)):
            raise RuntimeError("graph edit evidence needs its recipe, cases, binaries, and schema")
        add_graph_edit_evidence(report, args.graph_edit_evidence, args.review_tools_binary,
                                args.graph_builder_binary, args.schema_file,
                                args.graph_edit_spec, args.graph_edit_initial_cases,
                                args.graph_edit_updated_cases)
    if args.graph_opcode_evidence is not None or args.graph_opcode_updated_cases is not None:
        required = (args.graph_opcode_evidence, args.graph_opcode_updated_cases,
                    args.review_tools_binary, args.graph_builder_binary,
                    args.graph_edit_spec, args.graph_edit_initial_cases)
        if any(item is None for item in required):
            raise RuntimeError("graph opcode evidence needs both binaries and Fixed Limit fixtures")
        add_graph_opcode_evidence(report, args.graph_opcode_evidence,
                                  args.review_tools_binary, args.graph_builder_binary,
                                  args.graph_edit_spec, args.graph_edit_initial_cases,
                                  args.graph_opcode_updated_cases)
    if args.graph_operand_evidence is not None:
        if args.review_tools_binary is None or args.graph_spec is None:
            raise RuntimeError("graph operand evidence needs the tools binary and Range Predicate recipe")
        add_graph_operand_evidence(report, args.graph_operand_evidence,
                                   args.review_tools_binary, args.graph_spec)
    if args.graph_branch_evidence is not None or args.graph_branch_updated_cases is not None:
        required = (args.review_tools_binary, args.graph_builder_binary,
                    args.graph_spec, args.graph_cases, args.graph_branch_evidence,
                    args.graph_branch_updated_cases)
        if any(item is None for item in required):
            raise RuntimeError("graph branch evidence needs both binaries and Range Predicate fixtures")
        add_graph_branch_evidence(report, args.graph_branch_evidence,
                                  args.review_tools_binary, args.graph_builder_binary,
                                  args.graph_spec, args.graph_cases,
                                  args.graph_branch_updated_cases)
    if args.graph_sdk_evidence is not None:
        required = (args.review_tools_binary, args.graph_builder_binary, args.graph_spec,
                    args.graph_cases, args.graph_unsigned_spec, args.graph_unsigned_cases,
                    args.graph_numeric_spec, args.graph_numeric_cases)
        if any(item is None for item in required):
            raise RuntimeError("graph SDK evidence needs both binaries, recipes, and case fixtures")
        add_graph_sdk_evidence(report, args.graph_sdk_evidence, args.review_tools_binary,
                               args.graph_builder_binary, args.graph_spec, args.graph_cases,
                               args.graph_unsigned_spec, args.graph_unsigned_cases,
                               args.graph_numeric_spec, args.graph_numeric_cases)
    if args.graph_sdk_authoring_evidence is not None:
        required = (args.review_tools_binary, args.graph_builder_binary,
                    args.graph_edit_spec, args.graph_edit_initial_cases,
                    args.graph_edit_updated_cases)
        if any(item is None for item in required):
            raise RuntimeError("graph SDK authoring evidence needs both binaries and Fixed Limit fixtures")
        add_graph_sdk_authoring_evidence(report, args.graph_sdk_authoring_evidence,
                                         args.review_tools_binary, args.graph_builder_binary,
                                         args.graph_edit_spec, args.graph_edit_initial_cases,
                                         args.graph_edit_updated_cases)
    if args.graph_sdk_creation_evidence is not None:
        required = (args.review_tools_binary, args.graph_builder_binary,
                    args.graph_spec, args.graph_cases, args.graph_edit_spec,
                    args.graph_fixed_cases, args.graph_numeric_spec, args.graph_numeric_cases)
        if any(item is None for item in required):
            raise RuntimeError("graph SDK creation evidence needs both binaries and three recipe/case pairs")
        add_graph_sdk_creation_evidence(report, args.graph_sdk_creation_evidence,
                                        args.review_tools_binary, args.graph_builder_binary,
                                        args.graph_spec, args.graph_cases, args.graph_edit_spec,
                                        args.graph_fixed_cases, args.graph_numeric_spec,
                                        args.graph_numeric_cases)
    if any(item is not None for item in (args.mcp_evidence, args.mcp_server,
                                         args.mcp_probe, args.mcp_sdk_entry, args.mcp_lock)):
        if any(item is None for item in (args.mcp_evidence, args.mcp_server,
                                         args.mcp_probe, args.mcp_sdk_entry, args.mcp_lock,
                                         args.review_tools_binary, args.review_fixtures_dir,
                                         args.bridge_fixtures, args.benchmark_spec,
                                         args.benchmark_cases)):
            raise RuntimeError("MCP evidence needs the tools binary, adapter sources, Review fixtures, and comparator inputs")
        add_mcp_evidence(report, args.mcp_evidence, args.review_tools_binary,
                         args.mcp_server, args.mcp_probe, args.mcp_sdk_entry,
                         args.mcp_lock, args.review_fixtures_dir, args.bridge_fixtures,
                         args.benchmark_spec, args.benchmark_cases)
    if args.mcp_launcher_evidence is not None:
        if args.review_tools_binary is None or args.mcp_server is None:
            raise RuntimeError("MCP launcher evidence needs the tools binary and adapter source")
        add_mcp_launcher_evidence(report, args.mcp_launcher_evidence,
                                  args.review_tools_binary, args.mcp_server,
                                  Path("packages/mcp/tests/launcher_probe.mjs"))
    if args.benchmark_evidence is not None:
        required = (args.review_tools_binary, args.graph_builder_binary,
                    args.benchmark_spec, args.benchmark_cases, args.schema_file,
                    args.benchmark_typescript_probe)
        if any(item is None for item in required):
            raise RuntimeError("checked graph evidence needs binaries, fixtures, schema, and TypeScript probe")
        add_checked_graph_evidence(report, args.benchmark_evidence,
                                   args.review_tools_binary, args.graph_builder_binary,
                                   args.benchmark_spec, args.benchmark_cases,
                                   args.schema_file,
                                   Path("packages/python/sley_tools/runtime.py"),
                                   Path("packages/typescript/src/index.js"),
                                   args.benchmark_typescript_probe)
    if args.data_pipe_native_evidence is not None:
        required = (args.review_tools_binary, args.graph_builder_binary,
                    args.schema_file, args.selector_spec, args.selector_cases,
                    args.selector_after_cases, args.reducer_spec, args.reducer_cases)
        if any(item is None for item in required):
            raise RuntimeError("native Data Pipe evidence needs binaries, schema, and both program fixtures")
        add_data_pipe_native_evidence(report, args.data_pipe_native_evidence,
                                      args.review_tools_binary, args.graph_builder_binary,
                                      args.schema_file, args.selector_spec, args.selector_cases,
                                      args.selector_after_cases, args.reducer_spec,
                                      args.reducer_cases)
    if args.data_pipe_stream_evidence is not None:
        required = (args.review_tools_binary, args.benchmark_spec, args.benchmark_cases,
                    args.selector_spec, args.selector_cases, args.selector_after_cases, args.reducer_spec,
                    args.reducer_cases, args.data_pipe_sample, args.data_pipe_expected)
        if any(item is None for item in required):
            raise RuntimeError("Data Pipe stream evidence needs three native programs and sample files")
        add_data_pipe_stream_evidence(report, args.data_pipe_stream_evidence,
                                      args.review_tools_binary, args.benchmark_spec,
                                      args.benchmark_cases, args.selector_spec,
                                      args.selector_cases, args.selector_after_cases,
                                      args.reducer_spec,
                                      args.reducer_cases, args.data_pipe_sample,
                                      args.data_pipe_expected)
    if args.data_pipe_performance_evidence is not None:
        required = (args.review_tools_binary, args.graph_builder_binary,
                    args.benchmark_spec, args.benchmark_cases, args.selector_spec,
                    args.selector_cases, args.reducer_spec, args.reducer_cases)
        if any(item is None for item in required):
            raise RuntimeError("Data Pipe performance evidence needs three native programs and binaries")
        add_data_pipe_performance_evidence(report, args.data_pipe_performance_evidence,
                                           args.review_tools_binary, args.graph_builder_binary,
                                           args.benchmark_spec, args.benchmark_cases,
                                           args.selector_spec, args.selector_cases,
                                           args.reducer_spec, args.reducer_cases)
    if args.graph_artifact_evidence is not None:
        required = (args.review_tools_binary, args.graph_builder_binary,
                    args.schema_file, args.data_pipe_sample)
        if any(item is None for item in required):
            raise RuntimeError("graph artifact evidence needs binaries, schema, and Data Pipe sample")
        add_graph_artifact_evidence(report, args.graph_artifact_evidence,
                                    args.review_tools_binary, args.graph_builder_binary,
                                    args.schema_file, args.data_pipe_sample)
    if args.comparator_catalog_evidence is not None or args.comparator_catalog_dir is not None:
        required = (args.comparator_catalog_evidence, args.comparator_catalog_dir,
                    args.review_tools_binary, args.schema_file, args.program_catalog,
                    args.data_pipe_sample)
        if any(item is None for item in required):
            raise RuntimeError("N03 catalog evidence needs package, catalog, tools, schema, and sample")
        add_comparator_catalog_evidence(report, args.comparator_catalog_evidence,
                                        args.review_tools_binary, args.schema_file,
                                        args.program_catalog, args.comparator_catalog_dir,
                                        args.data_pipe_sample)
    if args.reducer_catalog_evidence is not None or args.reducer_catalog_dir is not None:
        required = (args.reducer_catalog_evidence, args.reducer_catalog_dir,
                    args.review_tools_binary, args.schema_file, args.program_catalog,
                    args.data_pipe_sample)
        if any(item is None for item in required):
            raise RuntimeError("N05 catalog evidence needs package, catalog, tools, schema, and sample")
        add_reducer_catalog_evidence(report, args.reducer_catalog_evidence,
                                     args.review_tools_binary, args.schema_file,
                                     args.program_catalog, args.reducer_catalog_dir,
                                     args.data_pipe_sample)
    if args.selector_catalog_evidence is not None or args.selector_catalog_dir is not None:
        required = (args.selector_catalog_evidence, args.selector_catalog_dir,
                    args.review_tools_binary, args.schema_file, args.program_catalog,
                    args.data_pipe_sample)
        if any(item is None for item in required):
            raise RuntimeError("N04 catalog evidence needs package, catalog, tools, schema, and sample")
        add_selector_catalog_evidence(report, args.selector_catalog_evidence,
                                      args.review_tools_binary, args.schema_file,
                                      args.program_catalog, args.selector_catalog_dir,
                                      args.data_pipe_sample)
    if args.range_catalog_evidence is not None or args.range_catalog_dir is not None:
        required = (args.range_catalog_evidence, args.range_catalog_dir,
                    args.review_tools_binary, args.schema_file, args.program_catalog)
        if any(item is None for item in required):
            raise RuntimeError("N07 catalog evidence needs package, catalog, tools, and schema")
        add_range_catalog_evidence(report, args.range_catalog_evidence,
                                   args.review_tools_binary, args.schema_file,
                                   args.program_catalog, args.range_catalog_dir)
    if args.clamp_catalog_evidence is not None or args.clamp_catalog_dir is not None:
        required = (args.clamp_catalog_evidence, args.clamp_catalog_dir,
                    args.review_tools_binary, args.schema_file, args.program_catalog)
        if any(item is None for item in required):
            raise RuntimeError("N08 catalog evidence needs package, catalog, tools, and schema")
        add_clamp_catalog_evidence(report, args.clamp_catalog_evidence,
                                   args.review_tools_binary, args.schema_file,
                                   args.program_catalog, args.clamp_catalog_dir)
    if args.quota_catalog_evidence is not None or args.quota_catalog_dir is not None:
        required = (args.quota_catalog_evidence, args.quota_catalog_dir,
                    args.review_tools_binary, args.schema_file, args.program_catalog)
        if any(item is None for item in required):
            raise RuntimeError("N09 catalog evidence needs package, catalog, tools, and schema")
        add_quota_catalog_evidence(report, args.quota_catalog_evidence,
                                   args.review_tools_binary, args.schema_file,
                                   args.program_catalog, args.quota_catalog_dir)
    if (args.generated_validator_catalog_evidence is not None
            or args.generated_validator_catalog_dir is not None):
        required = (args.generated_validator_catalog_evidence,
                    args.generated_validator_catalog_dir, args.review_tools_binary,
                    args.schema_file, args.program_catalog)
        if any(item is None for item in required):
            raise RuntimeError("N10 catalog evidence needs package, catalog, tools, and schema")
        add_generated_validator_catalog_evidence(
            report, args.generated_validator_catalog_evidence,
            args.review_tools_binary, args.schema_file,
            args.program_catalog, args.generated_validator_catalog_dir)
    if args.tier_catalog_evidence is not None or args.tier_catalog_dir is not None:
        required = (args.tier_catalog_evidence, args.tier_catalog_dir,
                    args.review_tools_binary, args.schema_file, args.program_catalog)
        if any(item is None for item in required):
            raise RuntimeError("N06 catalog evidence needs package, catalog, tools, and schema")
        add_tier_catalog_evidence(report, args.tier_catalog_evidence,
                                  args.review_tools_binary, args.schema_file,
                                  args.program_catalog, args.tier_catalog_dir)
    if args.playground_local_evidence is not None:
        required = (args.review_tools_binary, args.playground_server,
                    args.playground_html, args.playground_js, args.playground_css,
                    args.playground_probe)
        if any(item is None for item in required):
            raise RuntimeError("local Playground evidence needs tools binary, server, UI, and probe")
        add_playground_local_evidence(report, args.playground_local_evidence,
                                      args.review_tools_binary, args.playground_server,
                                      args.playground_html, args.playground_js,
                                      args.playground_css, args.playground_probe)
    if args.playground_browser_evidence is not None:
        if args.review_tools_binary is None or args.fresh_install_probe is None:
            raise RuntimeError("browser evidence needs tools binary and bundle builder")
        add_playground_browser_evidence(report, args.playground_browser_evidence,
                                        args.review_tools_binary, args.fresh_install_probe)
    if args.repair_lab_evidence is not None:
        required = (args.review_tools_binary, args.repair_lab,
                    args.repair_lab_probe, args.repair_lab_materials,
                    args.repair_lab_range_artifact, args.repair_lab_tier_artifact)
        if any(item is None for item in required):
            raise RuntimeError("Repair Lab evidence needs tools, lab, probe, materials, and range artifact")
        add_repair_lab_evidence(report, args.repair_lab_evidence,
                                args.review_tools_binary, args.repair_lab,
                                args.repair_lab_probe, args.repair_lab_materials,
                                args.repair_lab_range_artifact,
                                args.repair_lab_tier_artifact)
    if args.review_isolated_evidence is not None or args.review_fixtures_dir is not None:
        if any(item is None for item in (args.review_isolated_evidence, args.review_fixtures_dir, args.review_tools_binary,
                                         args.schema_file, args.bridge_artifact, args.resource_catalog_dir,
                                         args.bridge_fixtures, args.resource_fixtures)):
            raise RuntimeError("isolated Review evidence requires fixture packages, tools binary, schema, and both catalogs")
        add_review_isolated_evidence(report, args.review_isolated_evidence, args.review_fixtures_dir,
                                     args.review_tools_binary, args.schema_file, args.bridge_artifact,
                                     args.resource_catalog_dir / "artifact/native-program.json",
                                     args.bridge_fixtures, args.resource_fixtures)
    if args.review_remote_evidence is not None:
        if any(item is None for item in (args.review_isolated_evidence, args.review_fixtures_dir,
                                         args.review_tools_binary, args.bridge_fixtures, args.resource_fixtures)):
            raise RuntimeError("separate-host Review evidence requires isolated evidence, fixtures, tools, and both case files")
        add_review_remote_evidence(report, args.review_remote_evidence, args.review_isolated_evidence,
                                   args.review_tools_binary, args.review_fixtures_dir,
                                   args.bridge_fixtures, args.resource_fixtures)
    if args.schema_importer_evidence is not None:
        required = (args.review_tools_binary, args.schema_importer_source,
                    args.schema_importer_module, args.schema_importer_probe, args.requirements_dev_lock)
        if any(item is None for item in required):
            raise RuntimeError("Schema Importer evidence needs tools binary, source, module, probe, and dependency lock")
        add_schema_importer_evidence(report, args.schema_importer_evidence,
                                     args.review_tools_binary, args.schema_importer_source,
                                     args.schema_importer_module, args.schema_importer_probe,
                                     args.requirements_dev_lock)
    if args.python_importer_evidence is not None:
        required = (args.review_tools_binary, args.python_importer_module,
                    args.python_importer_probe, args.python_importer_examples)
        if any(item is None for item in required):
            raise RuntimeError("Python Importer evidence needs tools binary, module, probe, and examples")
        add_python_importer_evidence(report, args.python_importer_evidence, args.review_tools_binary,
                                     args.python_importer_module, args.python_importer_probe,
                                     args.python_importer_examples)
    if args.fresh_install_evidence is not None or args.fresh_install_probe is not None:
        required = (args.fresh_install_evidence, args.fresh_install_probe,
                    args.review_tools_binary, args.graph_builder_binary,
                    args.archive, args.data_pipe_expected)
        if any(item is None for item in required):
            raise RuntimeError("fresh-install evidence needs probe, tools, builder, archive, and sample")
        add_fresh_install_evidence(report, args.fresh_install_evidence,
                                   args.fresh_install_probe, args.review_tools_binary,
                                   args.graph_builder_binary, args.archive,
                                   args.data_pipe_expected)
    report["evidence_selection"] = {
        "playground_local": args.playground_local_evidence.as_posix() if args.playground_local_evidence else None,
        "playground_browser": args.playground_browser_evidence.as_posix() if args.playground_browser_evidence else None,
        "repair_lab": args.repair_lab_evidence.as_posix() if args.repair_lab_evidence else None,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: core identity and packaged demo passed; F0 status {report['f0']['status']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
