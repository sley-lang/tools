#!/usr/bin/env python3
"""Build and inspect a path-clean Linux x86_64 tools release candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from probe_fresh_install import CORE_ARCHIVE_SHA256, copy_bundle, deterministic_tar, digest


SOURCE = Path(__file__).resolve().parents[1]
CORE_BINARY_SHA256 = "b2d8ce53051043cd86f0c071b16584053d9a08ec7af5d2d8a91fa3ab68a79e52"


def call(*command: object, cwd: Path | None = None, timeout: int = 180) -> str:
    result = subprocess.run([str(item) for item in command], cwd=cwd,
                            capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"{command[0]} exited {result.returncode}: "
                           f"{(result.stderr or result.stdout)[-700:]}")
    return result.stdout.strip()


def checked_json(*command: object, cwd: Path | None = None) -> dict:
    return json.loads(call(*command, cwd=cwd))


def public_docs(payload: Path) -> None:
    docs = payload / "docs"
    public_status = docs / "F0_NATIVE_TEST_STATUS_PUBLIC.md"
    (docs / "F0_NATIVE_TEST_STATUS.md").write_bytes(public_status.read_bytes())
    public_status.unlink()
    (payload / "README.md").write_text(
        "# Sley Tools 0.0.1 release candidate\n\n"
        "Linux x86_64 package for local use with the separately verified Sley 2.1.1 core. "
        "This is a release candidate; native core test admission remains unavailable.\n\n"
        "Run `bin/sley-tools doctor --core /path/to/sley-2.1.1-linux-x86_64/bin/sley`, "
        "then `bin/sley-tools init my-program --template release-readiness --core "
        "/path/to/sley-2.1.1-linux-x86_64/bin/sley`. "
        "Use `bin/sley-tools test --project my-program` for external assertions against native execution.\n\n"
        "The `programs/` directory contains the native examples; `docs/` describes supported "
        "authoring and adapter profiles. `MANIFEST.json` binds the included files. "
        "Full source and evidence: https://github.com/sley-lang/tools.\n",
        encoding="utf-8",
    )


def audit_payload(payload: Path, source: Path) -> int:
    required = ("LICENSE", "NOTICE", "README.md", "MANIFEST.json",
                "bin/sley-tools", "bin/sley-tools-native-builder",
                "programs/catalog.json", "docs/F0_NATIVE_TEST_STATUS.md",
                "scripts/probe_authorized_native_test.py",
                "scripts/probe_genesis.py", "scripts/probe_native_test_route.py",
                "packages/mcp/LICENSE", "packages/mcp/NOTICE",
                "packages/typescript/LICENSE", "packages/typescript/NOTICE")
    if any(not (payload / name).is_file() for name in required):
        raise RuntimeError("release candidate omits a required binary, license, manifest, or program")
    forbidden = (b"/home/", b"/Users/", b"/root/", str(Path.home()).encode(),
                 str(source).encode(), b"/home/",
                 b"BEGIN OPENSSH PRIVATE KEY", b"BEGIN RSA PRIVATE KEY",
                 b"github_pat_", b"gho_")
    count = 0
    for selected in sorted(payload.rglob("*")):
        if selected.is_symlink() and not selected.resolve().is_relative_to(payload.resolve()):
            raise RuntimeError(f"package contains an escaping symlink: {selected.relative_to(payload)}")
        if not selected.is_file():
            continue
        relative = selected.relative_to(payload)
        if any(part.startswith(".env") or part in (".ssh", ".git") for part in relative.parts):
            raise RuntimeError(f"package contains a private file path: {relative}")
        data = selected.read_bytes()
        if any(marker in data for marker in forbidden):
            raise RuntimeError(f"package contains a private marker: {relative}")
        count += 1
    return count


def verify_archive(archive_path: Path) -> int:
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        if any(not item.isfile() or not item.name.startswith("sley-tools-local/")
               or ".." in Path(item.name).parts for item in members):
            raise RuntimeError("release archive has an unsafe or unexpected member")
        files = {}
        for item in members:
            relative = item.name.removeprefix("sley-tools-local/")
            stream = archive.extractfile(item)
            if stream is None or relative in files:
                raise RuntimeError("release archive has a missing or duplicate file")
            files[relative] = stream.read()
    manifest = json.loads(files["MANIFEST.json"])
    expected = {item["path"]: item for item in manifest["files"]}
    if set(files) != set(expected) | {"MANIFEST.json"}:
        raise RuntimeError("release archive and checksum manifest list different files")
    for name, item in expected.items():
        data = files[name]
        if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise RuntimeError(f"release archive content differs from checksum manifest: {name}")
    return len(files)


def write_manifest(payload: Path, commit: str, tools: Path, builder: Path) -> None:
    files = []
    for selected in sorted(payload.rglob("*")):
        if selected.is_file():
            files.append({"path": selected.relative_to(payload).as_posix(),
                          "bytes": selected.stat().st_size, "sha256": digest(selected)})
    manifest = {
        "contract": "sley-tools.release-candidate-manifest.v1",
        "version": "0.0.1", "status": "candidate", "platform": "linux-x86_64",
        "source_commit": commit, "tools_binary_sha256": digest(tools),
        "builder_binary_sha256": digest(builder),
        "compatible_core_release": "v2.1.1",
        "compatible_core_archive_sha256": CORE_ARCHIVE_SHA256,
        "compatible_core_binary_sha256": CORE_BINARY_SHA256,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
        "files": files,
    }
    (payload / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                            encoding="utf-8")


def smoke(payload: Path, core_archive: Path, stage: Path) -> dict:
    (stage / "core").mkdir()
    with tarfile.open(core_archive, "r:gz") as archive:
        archive.extractall(stage / "core", filter="data")
    core = stage / "core/sley-2.1.1-linux-x86_64/bin/sley"
    if digest(core) != CORE_BINARY_SHA256:
        raise RuntimeError("release candidate selected a different core binary")
    tools = payload / "bin/sley-tools"
    doctor = checked_json(tools, "doctor", "--core", core)
    if doctor.get("status") != "compatible":
        raise RuntimeError("release candidate doctor did not accept the selected core")
    project = stage / "work/release-readiness"
    project.parent.mkdir()
    created = checked_json(tools, "init", project, "--template", "release-readiness",
                           "--core", core)
    tests = checked_json(tools, "test", "--project", project)
    observed = checked_json(tools, "run", "--project", project,
                            "--input", project / "inputs/ready.json")
    if (tests.get("result") != "PASS" or observed.get("result", {}).get("allow") is not True
            or not created.get("program")):
        raise RuntimeError("release candidate first-run native behavior differs")
    return {"doctor": "PASS", "native_project_created": True,
            "external_assertions": "PASS", "ready_decision": True,
            "native_core_test_admission": "NOT_RUN: production native executor unavailable"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--builder", required=True, type=Path)
    parser.add_argument("--core-archive", required=True, type=Path)
    parser.add_argument("--archive-output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()
    tools, builder, core_archive = (getattr(args, name).resolve() for name in
                                    ("tools", "builder", "core_archive"))
    if digest(core_archive) != CORE_ARCHIVE_SHA256:
        raise RuntimeError("selected core archive differs from the pinned release")
    commit = call("git", "rev-parse", "HEAD", cwd=SOURCE)
    dirty = bool(call("git", "status", "--porcelain", "--untracked-files=all", cwd=SOURCE))
    if dirty and not args.allow_dirty:
        raise RuntimeError("release candidate source checkout is dirty")
    if args.archive_output.exists() or args.report.exists():
        raise RuntimeError("release candidate output path already exists")
    with tempfile.TemporaryDirectory(prefix="sley-tools-release-") as temporary:
        stage = Path(temporary)
        payload = stage / "payload"
        copy_bundle(SOURCE, payload, tools, builder)
        call("npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--prefer-offline",
             cwd=payload / "packages/mcp", timeout=180)
        public_docs(payload)
        write_manifest(payload, commit, tools, builder)
        file_count = audit_payload(payload, SOURCE)
        checked = smoke(payload, core_archive, stage)
        args.archive_output.parent.mkdir(parents=True, exist_ok=True)
        deterministic_tar(payload, args.archive_output)
        archived_file_count = verify_archive(args.archive_output)
        if archived_file_count != file_count:
            raise RuntimeError("release archive omitted a scanned payload file")
        report = {
            "contract": "sley-tools.release-candidate-probe.v1", "result": "PASS",
            "archive_name": args.archive_output.name,
            "archive_sha256": digest(args.archive_output), "archive_bytes": args.archive_output.stat().st_size,
            "source_commit": commit, "source_dirty": dirty,
            "tools_binary_sha256": digest(tools), "builder_binary_sha256": digest(builder),
            "core_archive_sha256": CORE_ARCHIVE_SHA256,
            "manifest_sha256": digest(payload / "MANIFEST.json"),
            "package_file_count": file_count,
            "archive_manifest_verified": True,
            "private_path_and_secret_marker_scan": "PASS",
            "smoke": checked,
            "public_release_status": "NOT_PUBLISHED",
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
        print(f"wrote {args.report}: {file_count} package files audited and first run passed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"release package probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
