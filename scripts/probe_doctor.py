#!/usr/bin/env python3
"""Verify doctor against the selected release and incompatible inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def invoke(tools: Path, core: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(tools), "doctor", "--core", str(core)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def probe(tools: Path, core: Path) -> dict:
    success = invoke(tools, core)
    if success.returncode != 0:
        raise RuntimeError(f"doctor rejected selected core: {success.stderr[:200]}")
    report = json.loads(success.stdout)
    if report["contract"] != "sley-tools.doctor.v1" or report["status"] != "compatible":
        raise RuntimeError("doctor did not report the selected compatible core")
    digest = hashlib.sha256(core.read_bytes()).hexdigest()
    if report["core"]["binary_sha256"] != digest:
        raise RuntimeError("doctor reported the wrong core binary digest")
    if set(report["profiles"]) != {"legacy", "v2-capable", "v3-capable"}:
        raise RuntimeError("doctor omitted a supported protocol profile")
    if any(profile["extended_execute_offered"] for profile in report["profiles"].values()):
        raise RuntimeError("doctor falsely advertised CLI extended execution")
    if not report["profiles"]["v3-capable"]["native_test_methods_offered"]:
        raise RuntimeError("doctor omitted the v3 native test method offer")
    if "NATIVE_EXECUTOR_UNAVAILABLE" not in report["tested_release_observations"]["native_test_execution"]:
        raise RuntimeError("doctor promoted native test method offer to execution support")

    incompatible = invoke(tools, Path(__file__).resolve())
    if incompatible.returncode != 3:
        raise RuntimeError("doctor did not distinguish incompatible core")
    incompatible_report = json.loads(incompatible.stdout)
    if incompatible_report["status"] != "incompatible" or incompatible_report["profiles"] is not None:
        raise RuntimeError("doctor executed or advertised an incompatible core")

    missing = invoke(tools, core.parent / "does-not-exist")
    if missing.returncode != 2:
        raise RuntimeError("doctor did not distinguish invalid path")
    missing_error = json.loads(missing.stderr)
    if missing_error["code"] != "ST_INPUT_INVALID":
        raise RuntimeError("doctor returned wrong error for invalid path")

    return {
        "contract": "sley-tools.doctor-probe.v1",
        "result": "PASS",
        "tested_core_binary_sha256": digest,
        "profile_names": sorted(report["profiles"]),
        "compatible_exit": success.returncode,
        "incompatible_exit": incompatible.returncode,
        "invalid_path_exit": missing.returncode,
        "native_test_offer_distinguished_from_execution": True,
        "extended_execute_not_offered": True,
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
    print(f"wrote {args.output}: doctor identity and negative-path checks passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"doctor probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
