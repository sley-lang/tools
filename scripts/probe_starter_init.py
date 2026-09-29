#!/usr/bin/env python3
"""Exercise a fresh persistent Starter project through sley-tools init."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)


def probe(tools: Path, core: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="sley-tools-starter-") as temporary:
        project = Path(temporary) / "my-first-program"
        invocation = [
            str(tools), "init", str(project), "--template", "release-readiness",
            "--core", str(core),
        ]
        created = command(invocation)
        if created.returncode != 0:
            raise RuntimeError(f"init exited {created.returncode}: {created.stderr[:300]}")
        result = json.loads(created.stdout)
        manifest = json.loads((project / "program.json").read_text(encoding="utf-8"))
        state = json.loads((project / ".sley-tools/state.json").read_text(encoding="utf-8"))
        status = json.loads((project / ".sley-tools/status.json").read_text(encoding="utf-8"))
        configuration = json.loads((project / "config.local.json").read_text(encoding="utf-8"))
        if result["contract"] != "sley-tools.init.v0":
            raise RuntimeError("init omitted its declared contract")
        if status["state"] != "READY_EXTERNAL_ASSERTIONS_ONLY" or status["native_commit_count"] != 1:
            raise RuntimeError("init did not report its one accepted native commit")
        if status["native_test_admission"] != "PENDING_CORE_EXECUTOR":
            raise RuntimeError("init falsely claimed native core test execution")
        if manifest["status"] != "local_workspace_only" or manifest["artifact"] is not None:
            raise RuntimeError("init falsely claimed a portable artifact")
        if manifest["entrypoint"]["state_root"] != state["state_root"] or manifest["entrypoint"]["function_id"] != state["function_id"]:
            raise RuntimeError("project manifest disagrees with committed local state")
        if len(state["commit_receipt_id"]) != 64:
            raise RuntimeError("project stored candidate diagnostics instead of the commit receipt identity")
        if configuration["core_binary_sha256"] != hashlib.sha256(core.read_bytes()).hexdigest():
            raise RuntimeError("trusted local core selection has the wrong digest")
        if json.loads((project / "inputs/ready.json").read_text(encoding="utf-8")) != {
            "test_run_completed": True, "tests_failed": 0,
            "artifact_verified": True, "blocking_findings": 0,
        }:
            raise RuntimeError("ready input differs from documented program contract")
        if "/workspace/" not in (project / ".gitignore").read_text(encoding="utf-8"):
            raise RuntimeError("local mutable workspace is not excluded from version control")

        inspected = command([str(tools), "inspect", "--project", str(project)])
        if inspected.returncode != 0 or json.loads(inspected.stdout)["root_verified_against_local_core_repository"] is not True:
            raise RuntimeError("inspect did not verify accepted root")
        ready_run = command([str(tools), "run", "--project", str(project), "--input", str(project / "inputs/ready.json")])
        if ready_run.returncode != 0:
            raise RuntimeError(f"ready input did not execute: {ready_run.stderr[:300]}")
        ready = json.loads(ready_run.stdout)
        rejected_input = project / "inputs/rejected.json"
        rejected_input.write_text(json.dumps({
            "test_run_completed": False, "tests_failed": 1,
            "artifact_verified": False, "blocking_findings": 1,
        }), encoding="utf-8")
        rejected_run = command([str(tools), "run", "--project", str(project), "--input", str(rejected_input)])
        if rejected_run.returncode != 0:
            raise RuntimeError("a native allow=false decision was misreported as a failed execution")
        rejected = json.loads(rejected_run.stdout)
        if ready["result"]["allow"] is not True or ready["reason_codes"]:
            raise RuntimeError("ready input did not produce native approval")
        if rejected["result"]["allow"] is not False or rejected["reason_codes"] != [
            "test_run_incomplete", "tests_failed", "artifact_unverified", "blocking_findings_exceeded"
        ]:
            raise RuntimeError("multiple native rejection reasons were lost or reordered")
        checked = command([str(tools), "run", "--project", str(project), "--input", str(rejected_input), "--check"])
        if checked.returncode != 1 or json.loads(checked.stdout)["result"]["allow"] is not False:
            raise RuntimeError("check-style exit did not distinguish a computed rejection")
        tested = command([str(tools), "test", "--project", str(project)])
        if tested.returncode != 0:
            raise RuntimeError(f"external assertions failed: {tested.stderr[:300]}")
        test_report = json.loads(tested.stdout)
        if test_report["external_assertions_against_native_execution"] != 36 or not test_report["native_core_test_admission"].startswith("NOT_RUN"):
            raise RuntimeError("test command conflated external assertions with native test admission")
        invalid_input = project / "inputs/invalid.json"
        invalid_input.write_text(json.dumps({
            "test_run_completed": True, "tests_failed": -1,
            "artifact_verified": True, "blocking_findings": 0,
        }), encoding="utf-8")
        invalid_run = command([str(tools), "run", "--project", str(project), "--input", str(invalid_input)])
        if invalid_run.returncode != 2 or json.loads(invalid_run.stderr)["code"] != "ST_INPUT_INVALID":
            raise RuntimeError("negative count was not rejected as invalid input")

        duplicate = command(invocation)
        if duplicate.returncode != 2 or json.loads(duplicate.stderr)["code"] != "ST_INPUT_INVALID":
            raise RuntimeError("init overwrote an existing project")
        after = json.loads((project / ".sley-tools/state.json").read_text(encoding="utf-8"))
        if after != state:
            raise RuntimeError("duplicate init changed accepted project state")

        unsupported = Path(temporary) / "unsupported-core-project"
        invalid_core = command([
            str(tools), "init", str(unsupported), "--template", "release-readiness",
            "--core", str(Path(__file__).resolve()),
        ])
        if invalid_core.returncode != 3 or unsupported.exists():
            raise RuntimeError("incompatible core created a project")

        return {
            "contract": "sley-tools.starter-init-probe.v1",
            "result": "PASS",
            "core_binary_sha256": configuration["core_binary_sha256"],
            "program_id": manifest["program_id"],
            "function_id": state["function_id"],
            "committed_state_root": state["state_root"],
            "candidate_id": state["candidate_id"],
            "commit_receipt_id": state["commit_receipt_id"],
            "native_commit_count": status["native_commit_count"],
            "ready_observation_id": ready["observation_id"],
            "multi_reason_observation_id": rejected["observation_id"],
            "external_assertions_against_native_execution": test_report["external_assertions_against_native_execution"],
            "computed_rejection_exit": rejected_run.returncode,
            "check_rejection_exit": checked.returncode,
            "invalid_count_exit": invalid_run.returncode,
            "duplicate_init_exit": duplicate.returncode,
            "incompatible_core_exit": invalid_core.returncode,
            "portable_artifact": "NOT_YET_CREATED",
            "native_test_admission": status["native_test_admission"],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = probe(args.tools.resolve(), args.core.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: fresh project, native calls, no-overwrite, and incompatible-core checks passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Starter init probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
