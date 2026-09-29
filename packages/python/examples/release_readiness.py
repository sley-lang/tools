"""Run the checked-in native Release Readiness artifact from Python."""

from __future__ import annotations

import argparse
import json

from sley_tools import open_runtime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True)
    parser.add_argument("--core", required=True)
    parser.add_argument("--artifact", required=True)
    args = parser.parse_args()
    with open_runtime(tools=args.tools, core=args.core) as runtime:
        program = runtime.load(artifact=args.artifact)
        result = program.call("evaluate", inputs={
            "test_run_completed": True,
            "tests_failed": 0,
            "artifact_verified": True,
            "blocking_findings": 1,
        })
        print(json.dumps({"result": result.value, "observation_id": result.metadata["observation_id"]}, sort_keys=True))


if __name__ == "__main__":
    main()
