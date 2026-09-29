#!/usr/bin/env python3
"""Record the independent Release Readiness threshold-zero behavior matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = []
    for completed in (False, True):
        for failed in (0, 1, 2**32 - 1):
            for verified in (False, True):
                for blocking in (0, 1, 2, 2**32 - 1):
                    reasons = {
                        "test_run_incomplete": not completed,
                        "tests_failed": failed > 0,
                        "artifact_unverified": not verified,
                        "blocking_findings_exceeded": blocking > 0,
                    }
                    cases.append({
                        "id": f"case-{len(cases) + 1:02d}",
                        "input": {
                            "test_run_completed": completed,
                            "tests_failed": failed,
                            "artifact_verified": verified,
                            "blocking_findings": blocking,
                        },
                        "expected": {"allow": not any(reasons.values()), **reasons},
                    })
    payload = {
        "contract": "sley-tools.release-readiness-fixtures.v1",
        "program_id": "release-readiness",
        "max_blocking_findings": 0,
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        destination.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.output}: {len(cases)} independent behavior cases")


if __name__ == "__main__":
    main()
