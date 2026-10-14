#!/usr/bin/env python3
"""Artifacts exported for the Sley 2.0.1 core import onto the pinned core.

The fixtures are the RC5 catalog artifacts for Release Readiness (fixed-object
profile) and Range Predicate (graph profile). Import must rebase them into a
fresh workspace, re-run every declared case on the selected core, and refuse
an artifact that names any other core commit.

Usage: test_legacy_artifact_import.py TOOLS CORE
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


FIXTURES = Path(__file__).resolve().parent / "fixtures/legacy-2.0.1"
LEGACY_COMMIT = "c748dda18c1276687cc3b0d1ef9b7f257eb09b24"
CASES = (
    ("release-readiness.native-program.json", "release-readiness",
     "19e829206cb66469d103982490ffec339f466408e02da6dc7f7ecd1981dcbecb", 36),
    ("range-predicate.native-graph.json", "range-predicate",
     "1a4610d7f749e5efeb013fa5e49d5c14bcf6eecc257dd9176ec9ce3c94f764cd", 11),
)


def run(*command: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(item) for item in command], capture_output=True, text=True,
                          timeout=120, check=False)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: test_legacy_artifact_import.py TOOLS CORE")
    tools, core = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    with tempfile.TemporaryDirectory(prefix="sley-legacy-import-") as temporary:
        scratch = Path(temporary)
        for name, program_id, digest, assertions in CASES:
            artifact = FIXTURES / name
            assert hashlib.sha256(artifact.read_bytes()).hexdigest() == digest, name
            document = json.loads(artifact.read_text(encoding="utf-8"))
            assert document["core_commit"] == LEGACY_COMMIT, name

            imported = run(tools, "artifact", "import", artifact, scratch / program_id, "--core", core)
            assert imported.returncode == 0, (name, imported.stderr)
            report = json.loads(imported.stdout)
            program = report["program"]
            assert program["program_id"] == program_id, report
            assert program["core"]["commit"] != LEGACY_COMMIT, report
            assert program["entrypoint"]["state_root"] != document["source_state_root"], report
            tested = run(tools, "test", "--project", scratch / program_id)
            assert tested.returncode == 0, (name, tested.stderr)
            result = json.loads(tested.stdout)
            assert result["result"] == "PASS", result
            assert result["external_assertions_against_native_execution"] == assertions, result

            document["core_commit"] = "f" * 40
            other = scratch / f"other-{name}"
            other.write_text(json.dumps(document), encoding="utf-8")
            refused = run(tools, "artifact", "import", other, scratch / f"refused-{program_id}",
                          "--core", core)
            assert refused.returncode != 0, (name, refused.stdout)
            assert not (scratch / f"refused-{program_id}" / "program.json").exists(), name
    print("legacy artifact import: PASS (2 profiles imported and tested, 2 unknown commits refused)")


if __name__ == "__main__":
    main()
