#!/usr/bin/env python3
"""Run the immutable action checkout against data from a caller workspace."""

from __future__ import annotations

import hashlib
import html
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


CORE_ARCHIVE_URL = (
    "https://github.com/sley-lang/sley/releases/download/v2.0.1/"
    "sley-2.0.1-linux-x86_64.tar.gz"
)
CORE_ARCHIVE_SHA256 = "34b3b56957a9ddf9a4edc35c2a961c89928d8e78dc74c76b23f1f5378e975e44"
CORE_BINARY_SHA256 = "90f48df4d8b983d4bb643bd7c6663f990c33c5927f8ff6217db86d8275b7fd0e"


class CheckFailure(Exception):
    pass


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            value.update(chunk)
    return value.hexdigest()


def workspace_file(workspace: Path, raw: str) -> Path:
    relative = Path(raw)
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise CheckFailure("action inputs must be workspace-relative file paths")
    selected = workspace
    for component in relative.parts:
        selected = selected / component
        if selected.is_symlink():
            raise CheckFailure(f"symlinked action input path: {relative}")
    if not selected.is_file():
        raise CheckFailure(f"action input is not a regular file: {relative}")
    return selected


def bound_package(manifest: Path) -> None:
    if manifest.stat().st_size > 1_048_576:
        raise CheckFailure("program manifest exceeds the Review input limit")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CheckFailure("program manifest must be a JSON object")
    for key, limit in (("artifact", 8_388_608), ("imported_source_artifact", 8_388_608),
                       ("graph_recipe", 65_536)):
        binding = payload.get(key)
        if not isinstance(binding, dict):
            continue
        raw = binding.get("path")
        if not isinstance(raw, str):
            continue
        selected = workspace_file(manifest.parent, raw)
        if selected.stat().st_size > limit:
            raise CheckFailure(f"{key} exceeds the Review input limit")


def invoke(command: list[str], *, cwd: Path | None = None, timeout: int = 600,
           env: dict[str, str] | None = None) -> str:
    try:
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                                text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        raise CheckFailure(f"{Path(command[0]).name} exceeded {timeout} seconds") from error
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-1600:]
        raise CheckFailure(f"{Path(command[0]).name} exited {result.returncode}: {detail}")
    return result.stdout


def install_validator(action_root: Path, stage: Path) -> Path:
    venv = stage / "venv"
    invoke([sys.executable, "-m", "venv", str(venv)], timeout=60)
    pip = venv / "bin/pip"
    environment = {key: os.environ[key] for key in ("PATH", "HOME", "SSL_CERT_FILE", "HTTPS_PROXY",
                                                    "HTTP_PROXY", "NO_PROXY") if key in os.environ}
    environment.update(PIP_INDEX_URL="https://pypi.org/simple", PIP_NO_INPUT="1",
                       PIP_CONFIG_FILE=os.devnull)
    invoke([str(pip), "install", "--disable-pip-version-check", "--require-hashes",
            "-r", str(action_root.parent.parent / "requirements-dev.lock")], timeout=180,
           env=environment)
    return venv / "bin/python"


def build_tools(action_root: Path, stage: Path) -> Path:
    source = action_root.parent.parent
    target = stage / "target"
    environment = {key: os.environ[key] for key in ("PATH", "HOME", "RUSTUP_HOME", "SSL_CERT_FILE",
                                                    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")
                   if key in os.environ}
    environment["CARGO_TARGET_DIR"] = str(target)
    invoke(["rustup", "toolchain", "install", "1.93.0", "--profile", "minimal"],
           timeout=300, env=environment)
    # rustup installs toolchain proxies in its existing Cargo home on hosted runners.
    # Use a private Cargo home only for the subsequent dependency build.
    environment["CARGO_HOME"] = str(stage / "cargo-home")
    invoke(["cargo", "+1.93.0", "build", "--locked", "--manifest-path", str(source / "Cargo.toml"),
            "-p", "sley-tools-backend", "--bin", "sley-tools",
            "--bin", "sley-tools-native-builder"], cwd=source, timeout=900,
           env=environment)
    return target / "debug/sley-tools"


def acquire_core(workspace: Path, raw: str, stage: Path) -> Path:
    archive = stage / "sley-core.tar.gz"
    if raw:
        source = workspace_file(workspace, raw)
        if source.stat().st_size > 16_777_216:
            raise CheckFailure("supplied Sley core archive exceeds the pinned release limit")
        shutil.copyfile(source, archive)
    else:
        invoke(["curl", "--disable", "--fail", "--location", "--retry", "3", "--max-time", "120",
                "--proto", "=https", "--proto-redir", "=https",
                "--max-filesize", "16777216", "--output", str(archive), CORE_ARCHIVE_URL],
               timeout=150)
    if digest(archive) != CORE_ARCHIVE_SHA256:
        raise CheckFailure("Sley core archive differs from the pinned 2.0.1 release")
    extracted = stage / "core"
    extracted.mkdir()
    invoke(["tar", "-xzf", str(archive), "-C", str(extracted)], timeout=30)
    core = extracted / "sley-2.0.1-linux-x86_64/bin/sley"
    if not core.is_file() or digest(core) != CORE_BINARY_SHA256:
        raise CheckFailure("unpacked Sley core binary failed the pinned digest")
    return core


def render_summary(status: dict, report: dict | None) -> str:
    def safe(value: object) -> str:
        return html.escape(" ".join(str(value).split())).replace("[", "&#91;").replace("]", "&#93;")

    result = status["result"]
    lines = ["## Sley native artifact review", "",
             f"Result: **{safe(result)}**", ""]
    if report:
        evidence = report["behavioral_evidence"]
        change = report["structural_change"]
        lines += [
            safe(report["summary"]), "",
            f"- Before artifact SHA-256: `{report['identity']['before']['artifact_sha256']}`",
            f"- After artifact SHA-256: `{report['identity']['after']['artifact_sha256']}`",
            f"- Cases executed: {evidence['case_count']}",
            f"- Cases changed: {evidence['changed_case_count']}",
            f"- Exact entity versions changed: {len(change['changed'])}",
            "- Manifest JSON Schema: PASS (action validator)",
            f"- Native core tests: {safe(report['validation']['native_core_test_admission'])}",
            "",
            "The full JSON report and verification result are attached to this run.",
        ]
    else:
        lines += [safe(str(status.get("reason", "review did not produce a report"))[:500]),
                  "", "Diagnostics are attached to this run."]
    return "\n".join(lines) + "\n"


def check(action_root: Path, report_dir: Path) -> dict:
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise CheckFailure("the selected Sley 2.0.1 Action profile requires Linux x86_64")
    workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    before = workspace_file(workspace, os.environ.get("SLEY_BEFORE", ""))
    after = workspace_file(workspace, os.environ.get("SLEY_AFTER", ""))
    cases = workspace_file(workspace, os.environ.get("SLEY_CASES", ""))
    if cases.stat().st_size > 1_048_576:
        raise CheckFailure("case file exceeds the Review input limit")
    bound_package(before)
    bound_package(after)
    with tempfile.TemporaryDirectory(prefix="sley-action-") as temporary:
        stage = Path(temporary)
        validator = install_validator(action_root, stage)
        source = action_root.parent.parent
        checks = []
        for manifest in (before, after):
            output = invoke([str(validator), str(source / "scripts/validate_program_manifest.py"),
                             "--manifest", str(manifest), "--schema",
                             str(source / "schemas/program-v0.schema.json")], timeout=30)
            checks.append(json.loads(output))
        (report_dir / "manifest-validation.json").write_text(
            json.dumps(checks, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        core = acquire_core(workspace, os.environ.get("SLEY_CORE_ARCHIVE", ""), stage)
        tools = build_tools(action_root, stage)
        report_path = report_dir / "report.json"
        output = invoke([str(tools), "review", "--before", str(before), "--after", str(after),
                         "--core", str(core), "--cases", str(cases), "--output",
                         str(report_path)], timeout=180)
        result = json.loads(output)
        verification = json.loads(invoke([str(tools), "review", "verify", str(report_path),
                                          "--before", str(before), "--after", str(after),
                                          "--core", str(core), "--cases", str(cases)],
                                         timeout=180))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if (verification.get("result") != "PASS"
                or verification.get("report_sha256") != digest(report_path)
                or result.get("report_sha256") != digest(report_path)
                or report.get("result") != "PASS_BOUNDED_LOCAL"):
            raise CheckFailure("native review report failed exact recomputation")
        (report_dir / "verification.json").write_text(
            json.dumps(verification, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {"contract": "sley-tools.ci-review-check.v1", "result": "PASS_BOUNDED_REVIEW",
                "tools_binary_sha256": digest(tools), "core_binary_sha256": digest(core),
                "core_archive_sha256": CORE_ARCHIVE_SHA256,
                "schema_sha256": digest(source / "schemas/program-v0.schema.json"),
                "report_sha256": digest(report_path), "native_core_test_admission":
                report["validation"]["native_core_test_admission"]}


def main() -> int:
    report_dir = Path(os.environ["SLEY_REPORT_DIR"]).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    action_root = Path(os.environ["SLEY_ACTION_ROOT"]).resolve(strict=True)
    try:
        status = check(action_root, report_dir)
        exit_code = 0
    except (CheckFailure, OSError, ValueError, KeyError, TypeError, UnicodeError) as error:
        status = {"contract": "sley-tools.ci-review-check.v1", "result": "FAIL",
                  "reason": str(error)[:1600]}
        exit_code = 1
    report_path = report_dir / "report.json"
    if exit_code == 0:
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            summary = render_summary(status, report)
        except (OSError, ValueError, KeyError, TypeError) as error:
            status = {"contract": "sley-tools.ci-review-check.v1", "result": "FAIL",
                      "reason": f"validated report could not be summarized: {error}"[:1600]}
            exit_code = 1
            summary = render_summary(status, None)
    else:
        summary = render_summary(status, None)
    (report_dir / "status.json").write_text(
        json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (report_dir / "summary.md").write_text(summary, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as destination:
            destination.write(summary)
    print(summary)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
