#!/usr/bin/env python3
"""Python SDK lifecycle and error-mapping checks that need no Sley core."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

root = Path(__file__).resolve().parent.parent
package = root / "packages/python"
fake = root / "tests/fixtures/fake_rpc_tools.py"
sys.path.insert(0, str(package))
from sley_tools import CallResult, Runtime, SleyToolsError  # noqa: E402


def expect_error(code: str, action) -> SleyToolsError:
    try:
        action()
    except SleyToolsError as error:
        if error.code != code:
            raise AssertionError(f"expected {code}, got {error.code}: {error}") from error
        return error
    raise AssertionError(f"expected {code}, but the call succeeded")


def stuck_rpc_is_stopped_on_close(project: Path) -> None:
    runtime = Runtime(tools=fake, core=fake)
    program = runtime.open_project(project)
    assert program.name == "release-readiness"
    process = runtime._process
    started = time.monotonic()
    runtime.close()
    elapsed = time.monotonic() - started
    assert process.poll() is not None, "RPC process survived close()"
    assert elapsed < 10, f"close() took {elapsed:.1f} s"


def missing_tools_is_a_transport_error(project: Path) -> None:
    runtime = Runtime(tools=project / "missing-sley-tools", core=fake)
    expect_error("ST_TRANSPORT_FAILED", lambda: runtime.open_project(project))
    assert runtime._process is None


def null_rpc_error_data_is_mapped(project: Path) -> None:
    os.environ["FAKE_RPC_NULL_ERROR_DATA"] = "1"
    try:
        runtime = Runtime(tools=fake, core=fake)
        error = expect_error("ST_TRANSPORT_FAILED", lambda: runtime.open_project(project))
        assert str(error) == "inspect refused"
    finally:
        del os.environ["FAKE_RPC_NULL_ERROR_DATA"]


def non_object_cli_failure_is_mapped() -> None:
    result = subprocess.CompletedProcess(["sley-tools"], 4, "", "[1, 2]")
    expect_error("ST_TRANSPORT_FAILED", lambda: Runtime(tools=fake, core=fake)._raise_cli(result))


def call_result_is_immutable_value() -> None:
    result = CallResult(True, {"state_root": "0" * 64})
    assert result == CallResult(True, {"state_root": "0" * 64})
    assert result != CallResult(False, {"state_root": "0" * 64})
    assert repr(result) == "CallResult(value=True, metadata={'state_root': '" + "0" * 64 + "'})"
    for mutate in (lambda: setattr(result, "value", False), lambda: delattr(result, "metadata")):
        try:
            mutate()
        except AttributeError:
            pass
        else:
            raise AssertionError("CallResult accepted mutation")
    match result:
        case CallResult(value, metadata):
            assert value is True and metadata["state_root"] == "0" * 64


def import_stays_light() -> None:
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); import sley_tools; "
             "print(' '.join(sorted({'dataclasses', 'hashlib', 'shutil', 'tempfile', 'typing'} & set(sys.modules))))")
    loaded = subprocess.run([sys.executable, "-I", "-c", probe, str(package)],
                            capture_output=True, text=True, check=True, timeout=30).stdout.strip()
    assert loaded == "", f"import sley_tools loaded {loaded}"


def main() -> None:
    os.chmod(fake, 0o755)
    with tempfile.TemporaryDirectory(prefix="sley-python-lifecycle-") as temporary:
        project = Path(temporary)
        checks = [
            lambda: stuck_rpc_is_stopped_on_close(project),
            lambda: missing_tools_is_a_transport_error(project),
            lambda: null_rpc_error_data_is_mapped(project),
            non_object_cli_failure_is_mapped,
            call_result_is_immutable_value,
            import_stays_light,
        ]
        for check in checks:
            check()
    print(f"python sdk lifecycle: {len(checks)} checks passed")


if __name__ == "__main__":
    main()
