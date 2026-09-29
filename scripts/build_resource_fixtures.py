#!/usr/bin/env python3
"""Build independent exact-integer Resource Admission cases for both SDKs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    wide = 2**53 + 1
    maximum = 2**63 - 1
    pairs = ((0, 0), (1, 1), (1, 2), (2, 1), (wide, wide - 1), (maximum, maximum), (maximum, maximum - 1))
    cases = []
    for workers in pairs:
        for memory in pairs:
            requested_workers, available_workers = workers
            requested_memory, available_memory = memory
            insufficient_workers = requested_workers > available_workers
            insufficient_memory = requested_memory > available_memory
            cases.append({
                "id": f"case-{len(cases) + 1:02d}",
                "input": {
                    "requested_workers": str(requested_workers),
                    "available_workers": str(available_workers),
                    "requested_memory_mib": str(requested_memory),
                    "available_memory_mib": str(available_memory),
                },
                "expected": {
                    "allow": not (insufficient_workers or insufficient_memory),
                    "invalid_request": False,
                    "invalid_capacity": False,
                    "insufficient_workers": insufficient_workers,
                    "insufficient_memory": insufficient_memory,
                    "requested_memory_mib": str(requested_memory),
                    "available_memory_mib": str(available_memory),
                },
            })
    payload = {"contract": "sley-tools.resource-admission-fixtures.v1", "cases": cases}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        destination.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.output}: {len(cases)} exact-integer cases")


if __name__ == "__main__":
    main()
