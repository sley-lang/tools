"""Call the checked-in native Quota Remaining utility from Python."""

from __future__ import annotations

import argparse
import json

from sley_tools import open_runtime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True)
    parser.add_argument("--core", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--limit", required=True, type=int)
    parser.add_argument("--used", required=True, type=int)
    args = parser.parse_args()
    with open_runtime(tools=args.tools, core=args.core) as runtime:
        program = runtime.load(artifact=args.artifact)
        result = program.call("evaluate", inputs={"limit": args.limit, "used": args.used})
        print(json.dumps({"result": result.value, "state_root": result.metadata["state_root"]}, sort_keys=True))


if __name__ == "__main__":
    main()
