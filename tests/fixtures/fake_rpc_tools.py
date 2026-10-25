#!/usr/bin/env python3
"""Minimal stand-in for `sley-tools rpc` used by SDK lifecycle tests.

It answers the three requests an SDK sends while opening a project, then
ignores stdin EOF and SIGTERM, like a stuck backend. Its own file doubles as
the selected "core" binary so the hello digest check passes. With
FAKE_RPC_NULL_ERROR_DATA=1 the inspect request fails with `"data": null`.
With FAKE_RPC_NON_OBJECT_FRAME=JSON the inspect response frame is that JSON
text instead, such as `null` or `[]`.
"""

import hashlib
import json
import os
import signal
import sys
import time
from pathlib import Path

INSPECTION = {
    "accepted_state_root": "0" * 64,
    "program": {
        "program_id": "release-readiness",
        "entrypoint": {"name": "evaluate", "function_id": "1" * 64},
        "result_type_id": "2" * 64,
        "result_fields": [{"name": "allow"}],
    },
}


def main() -> None:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    results = {
        "sleytools.hello": {"contract": "sley-tools.rpc.v0", "core": {"binary_sha256": digest}},
        "sleytools.workspace.open": {"handle": "h" * 64},
        "sleytools.program.inspect": INSPECTION,
    }
    for line in sys.stdin:
        request = json.loads(line)
        response = {"jsonrpc": "2.0", "id": request["id"], "result": results[request["method"]]}
        if request["method"] == "sleytools.program.inspect" and os.environ.get("FAKE_RPC_NULL_ERROR_DATA"):
            response = {"jsonrpc": "2.0", "id": request["id"],
                        "error": {"code": -32000, "message": "inspect refused", "data": None}}
        frame = json.dumps(response)
        if request["method"] == "sleytools.program.inspect" and "FAKE_RPC_NON_OBJECT_FRAME" in os.environ:
            frame = os.environ["FAKE_RPC_NON_OBJECT_FRAME"]
        sys.stdout.write(frame + "\n")
        sys.stdout.flush()
    time.sleep(120)


if __name__ == "__main__":
    main()
