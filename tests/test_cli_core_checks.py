#!/usr/bin/env python3
"""CLI checks: informational flags, doctor manifest reporting, and that
project commands verify the core by identity without probing optional runtimes.

Usage: test_cli_core_checks.py TOOLS CORE
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(command: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=120, check=False, env=env)


def informational_flags(tools: Path) -> None:
    version = run([str(tools), "--version"])
    assert version.returncode == 0 and version.stdout.startswith("sley-tools 0.0.1 "), version
    help_text = run([str(tools), "--help"])
    assert help_text.returncode == 0, help_text
    assert "\n  sley-tools doctor --core PATH\n" in help_text.stdout, help_text.stdout
    assert all(line.startswith("  sley-tools ") for line in help_text.stdout.splitlines()[1:])
    usage = run([str(tools), "--version", "extra"])
    assert usage.returncode == 2 and json.loads(usage.stderr)["code"] == "ST_INPUT_INVALID", usage


def argument_and_output_edges(tools: Path, core: Path) -> None:
    # A non-UTF-8 argument is an input error with a JSON failure, not a panic.
    for arguments in ([b"doctor", b"--core", b"\xff"], [b"\xff"], [b"--help", b"\xff"]):
        result = subprocess.run([bytes(tools), *arguments], capture_output=True, timeout=60, check=False)
        assert result.returncode == 2, (arguments, result)
        assert json.loads(result.stderr)["code"] == "ST_INPUT_INVALID", (arguments, result)
    # A reader that closes stdout early (`| head`) must not make the CLI panic;
    # the exit status still reports the command's own outcome.
    for arguments, expected in ((["--help"], 0), (["doctor", "--core", str(core)], 0),
                                (["doctor", "--core", str(tools)], 3)):
        reader, writer = os.pipe()
        os.close(reader)
        try:
            result = subprocess.run([str(tools), *arguments], stdout=writer, stderr=subprocess.PIPE,
                                    text=True, timeout=120, check=False)
        finally:
            os.close(writer)
        assert result.returncode == expected, (arguments, result)
        assert "panicked" not in result.stderr and result.stderr == "", (arguments, result.stderr)


def doctor_names_unpinned_release(tools: Path, scratch: Path) -> None:
    release = scratch / "other-core"
    (release / "bin").mkdir(parents=True)
    binary = release / "bin/sley"
    binary.write_bytes(b"not the pinned core\n")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    for listed, matches in ((digest, True), ("0" * 64, False)):
        manifest = {"commit": "f" * 40, "files": [{"path": "bin/sley", "sha256": listed}]}
        (release / "MANIFEST.json").write_text(json.dumps(manifest))
        result = run([str(tools), "doctor", "--core", str(binary)])
        report = json.loads(result.stdout)
        assert result.returncode == 3 and report["status"] == "incompatible", result
        assert report["core"]["manifest_commit"] == "f" * 40, report
        assert report["core"]["manifest_binary_matches"] is matches, report


def project_commands_skip_runtime_probes(tools: Path, core: Path, scratch: Path) -> None:
    shims = scratch / "shims"
    shims.mkdir()
    log = scratch / "spawned.log"
    for name in ("python3", "node"):
        real = shutil.which(name)
        target = f'exec "{real}" "$@"' if real else "exit 127"
        (shims / name).write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n{target}\n')
        (shims / name).chmod(0o755)
    env = {**os.environ, "PATH": f"{shims}{os.pathsep}{os.environ.get('PATH', '')}"}
    project = scratch / "project"
    init = run([str(tools), "init", str(project), "--template", "release-readiness", "--core", str(core)], env)
    assert init.returncode == 0, init.stderr
    for command in (["run", "--project", str(project), "--input", str(project / "inputs/ready.json")],
                    ["test", "--project", str(project)], ["inspect", "--project", str(project)]):
        result = run([str(tools), *command], env)
        assert result.returncode == 0, (command, result.stderr)
    spawned = log.read_text() if log.exists() else ""
    assert spawned == "", f"project commands started optional runtimes:\n{spawned}"
    doctor = run([str(tools), "doctor", "--core", str(core)], env)
    assert doctor.returncode == 0, doctor.stderr
    assert "python3 --version" in log.read_text() and "node --version" in log.read_text()


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: test_cli_core_checks.py TOOLS CORE")
    tools, core = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    with tempfile.TemporaryDirectory(prefix="sley-cli-checks-") as temporary:
        scratch = Path(temporary)
        informational_flags(tools)
        argument_and_output_edges(tools, core)
        doctor_names_unpinned_release(tools, scratch)
        project_commands_skip_runtime_probes(tools, core, scratch)
    print("cli core checks: informational flags, argument and closed-stdout edges, doctor manifest report, and runtime-probe-free project commands passed")


if __name__ == "__main__":
    main()
