"""Call a locally created Resource Admission Sley program."""

import argparse

from sley_tools import open_runtime


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--tools", required=True)
parser.add_argument("--core", required=True)
parser.add_argument("--artifact", required=True)
args = parser.parse_args()

with open_runtime(tools=args.tools, core=args.core) as runtime:
    program = runtime.load(artifact=args.artifact)
    result = program.call("evaluate", inputs={
        "requested_workers": 2,
        "available_workers": 4,
        "requested_memory_mib": 2**53 + 1,
        "available_memory_mib": 2**53 + 1,
    })
    print(result.value)
