#!/usr/bin/env python3
"""Exercise the reusable Review Action from a separate caller workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(caller: Path, report_dir: Path, before: str, after: str, cases: str) -> tuple[int, dict]:
    report_dir.mkdir()
    environment = os.environ.copy()
    environment.update({
        "GITHUB_WORKSPACE": str(caller),
        "SLEY_ACTION_ROOT": str(SOURCE / "actions/check"),
        "SLEY_REPORT_DIR": str(report_dir),
        "SLEY_BEFORE": before,
        "SLEY_AFTER": after,
        "SLEY_CASES": cases,
        "SLEY_CORE_ARCHIVE": "sley-2.1.1-linux-x86_64.tar.gz",
        "GITHUB_STEP_SUMMARY": str(report_dir / "job-summary.md"),
    })
    result = subprocess.run([sys.executable, str(SOURCE / "actions/check/check.py")],
                            cwd=caller, env=environment, capture_output=True, text=True,
                            timeout=600, check=False)
    (report_dir / "runner.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    (report_dir / "exit-code").write_text(f"{result.returncode}\n", encoding="utf-8")
    return result.returncode, json.loads((report_dir / "status.json").read_text(encoding="utf-8"))


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-archive", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="sley-action-probe-") as temporary:
        root = Path(temporary)
        caller = root / "caller"
        caller.mkdir()
        shutil.copy2(args.core_archive, caller / "sley-2.1.1-linux-x86_64.tar.gz")
        shutil.copytree(SOURCE / "evidence/review/fixtures/release-readiness/before",
                        caller / "release/before")
        shutil.copytree(SOURCE / "evidence/review/fixtures/release-readiness/after",
                        caller / "release/after")
        shutil.copy2(SOURCE / "programs/release-readiness/tests/bridge-cases.json",
                     caller / "release/cases.json")
        shutil.copytree(SOURCE / "programs/range-predicate", caller / "graph/before")
        shutil.copytree(SOURCE / "programs/range-predicate", caller / "graph/after")
        shutil.copy2(SOURCE / "programs/range-predicate/tests/cases.json",
                     caller / "graph/cases.json")

        release_dir = root / "release-report"
        code, status = invoke(caller, release_dir, "release/before/program.json",
                              "release/after/program.json", "release/cases.json")
        require(code == 0 and status.get("result") == "PASS_BOUNDED_REVIEW",
                f"fixed-program Action failed before report creation: {status.get('reason', status)}")
        report = json.loads((release_dir / "report.json").read_text(encoding="utf-8"))
        verified = json.loads((release_dir / "verification.json").read_text(encoding="utf-8"))
        require(code == 0 and status["result"] == "PASS_BOUNDED_REVIEW"
                and verified["result"] == "PASS"
                and verified["report_sha256"] == digest(release_dir / "report.json")
                and report["behavioral_evidence"]["case_count"] == 48
                and report["behavioral_evidence"]["changed_case_count"] == 24
                and len(report["structural_change"]["changed"]) == 1,
                "fixed-program Action runner failed its full review")
        require(len(json.loads((release_dir / "manifest-validation.json").read_text())) == 2,
                "fixed-program Action omitted manifest schema validation")
        require((release_dir / "summary.md").read_bytes() == (release_dir / "job-summary.md").read_bytes(),
                "fixed-program Action omitted or changed the GitHub job summary")

        graph_dir = root / "graph-report"
        code, graph_status = invoke(caller, graph_dir, "graph/before/program.json",
                                    "graph/after/program.json", "graph/cases.json")
        require(code == 0 and graph_status.get("result") == "PASS_BOUNDED_REVIEW",
                f"graph Action failed before report creation: {graph_status.get('reason', graph_status)}")
        graph = json.loads((graph_dir / "report.json").read_text(encoding="utf-8"))
        graph_verified = json.loads((graph_dir / "verification.json").read_text(encoding="utf-8"))
        require(code == 0 and graph_status["result"] == "PASS_BOUNDED_REVIEW"
                and graph["contract"] == "sley-tools.review.v1"
                and graph["behavioral_evidence"]["case_count"] == 11
                and graph["behavioral_evidence"]["changed_case_count"] == 0
                and graph_verified["result"] == "PASS"
                and graph_verified["report_sha256"] == digest(graph_dir / "report.json"),
                "graph Action runner failed its native no-change review")

        manifest = caller / "release/after/program.json"
        changed = json.loads(manifest.read_text(encoding="utf-8"))
        changed["artifact"]["sha256"] = "0" * 64
        manifest.write_text(json.dumps(changed), encoding="utf-8")
        tampered_dir = root / "tampered-report"
        code, tampered = invoke(caller, tampered_dir, "release/before/program.json",
                                "release/after/program.json", "release/cases.json")
        require(code == 1 and tampered["result"] == "FAIL"
                and "SHA-256 differs" in tampered["reason"]
                and not (tampered_dir / "report.json").exists(),
                "Action accepted a stale manifest or created a false report")

        (caller / "linked.json").symlink_to(caller / "release/before/program.json")
        symlink_dir = root / "symlink-report"
        code, symlinked = invoke(caller, symlink_dir, "linked.json",
                                 "release/after/program.json", "release/cases.json")
        require(code == 1 and symlinked["result"] == "FAIL"
                and "symlinked action input" in symlinked["reason"],
                "Action accepted a symlinked caller input")

        result = {
            "contract": "sley-tools.review-action-local-probe.v1",
            "result": "PASS",
            "scope": "external caller workspace; local Action runner, not a GitHub-hosted job",
            "action_metadata_sha256": digest(SOURCE / "actions/check/action.yml"),
            "action_runner_sha256": digest(SOURCE / "actions/check/check.py"),
            "probe_sha256": digest(Path(__file__)),
            "core_archive_sha256": digest(args.core_archive),
            "core_binary_sha256": status["core_binary_sha256"],
            "tools_binary_sha256": status["tools_binary_sha256"],
            "release_review": {
                "report_sha256": digest(release_dir / "report.json"),
                "case_count": 48, "changed_case_count": 24,
                "exact_changed_entities": 1, "manifest_schema_checks": 2,
                "report_recomputed": True,
            },
            "graph_review": {
                "report_sha256": digest(graph_dir / "report.json"),
                "case_count": 11, "changed_case_count": 0,
                "report_recomputed": True,
            },
            "stale_manifest_rejected_without_report": True,
            "symlinked_input_rejected": True,
            "job_summary_written": True,
            "native_core_test_admission": status["native_core_test_admission"],
            "github_hosted_action_run": "NOT_RUN",
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {args.output}: fixed and graph Action checks passed; tamper and symlink rejected")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"local Review Action probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
