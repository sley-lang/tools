#!/usr/bin/env python3
"""Check the packaged core report parser against Sley's independent golden."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

test_dir = Path(__file__).resolve().parent
source_scripts = test_dir.parent / "scripts"
package_scripts = test_dir / "payload/scripts"
sys.path.insert(0, str(source_scripts if source_scripts.is_dir() else package_scripts))
from probe_authorized_native_test import inspected_report_summary


def uvar(value: int) -> bytes:
    encoded = bytearray()
    while value >= 128:
        encoded.append((value & 127) | 128)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def inspect(builder: Path, stored: bytes) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="sley-report-golden-") as temporary:
        path = Path(temporary) / "report.bin"
        path.write_bytes(stored)
        return subprocess.run([str(builder), "inspect-native-test-report", str(path)],
                              capture_output=True, text=True, timeout=30, check=False)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: test_native_report_inspector.py BUILDER")
    builder = Path(sys.argv[1]).resolve()
    fixture = json.loads((Path(__file__).parent / "fixtures/native-test-report-one.json").read_text())
    record = bytes.fromhex(fixture["record_hex"])
    preimage = b"SLEYNTS1" + uvar(1) + uvar(len(record)) + record
    stored = preimage + bytes.fromhex(fixture["report_id"])
    result = inspect(builder, stored)
    if result.returncode:
        raise RuntimeError(f"independent golden was refused: {result.stderr[-300:]}")
    summary = json.loads(result.stdout)
    if (summary["contract"] != "sley-tools.native-test-report-inspection.v0"
            or any(summary[name] != fixture[name] for name in
                   ("report_id", "plan_id", "schema_epoch", "proposed_root"))
            or len(summary["entries"]) != 1
            or summary["entries"][0]["test_entity_id"] != fixture["test_entity_id"]
            or summary["entries"][0]["comparison_tag"] != fixture["comparison_tag"]
            or [summary[name] for name in
                ("match_count", "mismatch_count", "rejected_count")] != [1, 0, 0]):
        raise RuntimeError("independent golden parsed with the wrong identity or result")
    integrated = inspected_report_summary(
        stored, builder, bytes.fromhex(fixture["report_id"]),
        bytes.fromhex(fixture["plan_id"]), bytes.fromhex(fixture["test_entity_id"]),
        bytes.fromhex(fixture["schema_epoch"]),
    )
    if integrated.get("core_parser_verified") is not True:
        raise RuntimeError("receiver probe did not use the core report parser")
    try:
        inspected_report_summary(
            stored, builder, bytes.fromhex(fixture["report_id"]),
            bytes(32), bytes.fromhex(fixture["test_entity_id"]),
            bytes.fromhex(fixture["schema_epoch"]),
        )
    except RuntimeError:
        pass
    else:
        raise RuntimeError("receiver probe accepted a report for another plan")
    tampered = bytearray(stored)
    tampered[-33] ^= 1
    if inspect(builder, bytes(tampered)).returncode == 0:
        raise RuntimeError("tampered report passed the core digest check")
    try:
        inspected_report_summary(
            bytes(tampered), builder, bytes.fromhex(fixture["report_id"]),
            bytes.fromhex(fixture["plan_id"]), bytes.fromhex(fixture["test_entity_id"]),
            bytes.fromhex(fixture["schema_epoch"]),
        )
    except RuntimeError:
        pass
    else:
        raise RuntimeError("receiver probe accepted a tampered report")
    if inspect(builder, stored + b"extra").returncode == 0:
        raise RuntimeError("trailing report bytes were accepted")
    if inspect(builder, b"x" * (4 * 1024 * 1024 + 1)).returncode == 0:
        raise RuntimeError("oversized report was accepted")
    print("native report inspector: independent golden and three refusals passed")


if __name__ == "__main__":
    main()
