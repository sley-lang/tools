#!/usr/bin/env python3
"""Transfer the minimal Review bundle and verify replay on a second host."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import socket
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from probe_review_isolated import check_files, digest


REMOTE_SCRIPT = r'''
set -euo pipefail
review_dir=$(mktemp -d /tmp/sley-review-host-XXXXXXXX)
trap 'rm -rf "$review_dir"' EXIT
tar -xzf - -C "$review_dir"
python3 "$review_dir/scripts/replay_review_packages.py" \
  --tools "$review_dir/bin/sley-tools" \
  --core-root "$review_dir/core" \
  --fixtures "$review_dir/fixtures" \
  --release-cases "$review_dir/cases/release.json" \
  --resource-cases "$review_dir/cases/resource.json" \
  --output "$review_dir/result.json" >&2
cat "$review_dir/result.json"
'''


def add_checked(archive: tarfile.TarFile, source: Path, name: str) -> None:
    if source.is_symlink() or not (source.is_file() or source.is_dir()):
        raise RuntimeError(f"Review bundle source is not regular: {source}")
    if source.is_dir():
        for item in source.rglob("*"):
            if item.is_symlink() or not (item.is_file() or item.is_dir()):
                raise RuntimeError(f"Review bundle contains a non-regular entry: {item}")
    archive.add(source, arcname=name, recursive=True)


def probe(args: argparse.Namespace) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", args.host):
        raise RuntimeError("host must be a configured SSH alias or hostname")
    tools = args.tools.resolve()
    core_root = args.core_root.resolve()
    fixtures = args.fixtures.resolve()
    release_cases = args.release_cases.resolve()
    resource_cases = args.resource_cases.resolve()
    expected = json.loads(args.isolated_evidence.read_text(encoding="utf-8"))
    manifest = json.loads((fixtures / "manifest.json").read_text(encoding="utf-8"))
    check_files(fixtures, manifest)
    if expected["result"] != "PASS" or expected["contract"] != "sley-tools.review-isolated-probe.v1":
        raise RuntimeError("local isolated Review evidence did not pass")
    if (expected["copied_tools_binary_sha256"] != digest(tools)
            or expected["copied_builder_binary_sha256"] != digest(tools.with_name("sley-tools-native-builder"))
            or expected["copied_core_binary_sha256"] != digest(core_root / "bin/sley")
            or expected["fixture_manifest_sha256"] != digest(fixtures / "manifest.json")
            or expected["release_cases_sha256"] != digest(release_cases)
            or expected["resource_cases_sha256"] != digest(resource_cases)):
        raise RuntimeError("selected Review bundle differs from local isolated evidence")
    if expected["copied_core_binary_sha256"] != manifest["core_binary_sha256"]:
        raise RuntimeError("Review fixture core binding differs")
    companion = tools.with_name("sley-tools-native-builder")
    scripts = Path(__file__).resolve().parent
    with tempfile.TemporaryFile(mode="w+b") as bundle:
        with tarfile.open(fileobj=bundle, mode="w:gz") as archive:
            for source, name in (
                (tools, "bin/sley-tools"),
                (companion, "bin/sley-tools-native-builder"),
                (core_root, "core"),
                (fixtures, "fixtures"),
                (release_cases, "cases/release.json"),
                (resource_cases, "cases/resource.json"),
                (scripts / "replay_review_packages.py", "scripts/replay_review_packages.py"),
                (scripts / "probe_review_isolated.py", "scripts/probe_review_isolated.py"),
            ):
                add_checked(archive, source, name)
        bundle.seek(0)
        result = subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
             "-o", "ServerAliveInterval=20", args.host, "bash -c " + shlex.quote(REMOTE_SCRIPT)],
            stdin=bundle, capture_output=True, timeout=360, check=False,
        )
    if result.returncode:
        raise RuntimeError(f"remote Review replay exited {result.returncode}: {result.stderr.decode(errors='replace')[-900:]}")
    remote = json.loads(result.stdout)
    if (remote.get("contract") != "sley-tools.review-host-replay.v1"
            or remote.get("result") != "PASS" or remote.get("platform") != "Linux"
            or remote.get("machine") != "x86_64" or remote.get("host_name") == socket.gethostname()):
        raise RuntimeError("remote Review replay did not identify a distinct compatible host")
    for key, value in (
        ("tools_binary_sha256", expected["copied_tools_binary_sha256"]),
        ("builder_binary_sha256", expected["copied_builder_binary_sha256"]),
        ("core_binary_sha256", expected["copied_core_binary_sha256"]),
        ("fixture_manifest_sha256", expected["fixture_manifest_sha256"]),
        ("release_cases_sha256", expected["release_cases_sha256"]),
        ("resource_cases_sha256", expected["resource_cases_sha256"]),
        ("native_core_test_admission", expected["native_core_test_admission"]),
    ):
        if remote.get(key) != value:
            raise RuntimeError(f"remote Review replay differs on {key}")
    if remote.get("programs") != expected["programs"]:
        raise RuntimeError("remote Review reports differ from local verified reports")
    return {
        "contract": "sley-tools.review-separate-host-probe.v1", "result": "PASS",
        "local_host": socket.gethostname(), "remote_alias": args.host,
        "transfer": "minimal copied tools/core/fixtures/cases/scripts bundle over SSH",
        "source_checkout_transferred": False, "network_isolation_claimed": False,
        "remote": remote,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--tools", type=Path, required=True)
    parser.add_argument("--core-root", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--release-cases", type=Path, required=True)
    parser.add_argument("--resource-cases", type=Path, required=True)
    parser.add_argument("--isolated-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = probe(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: both native reviews reproduced on {report['remote']['host_name']}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"remote Review probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
