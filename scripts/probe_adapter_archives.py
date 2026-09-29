#!/usr/bin/env python3
"""Audit the installable Python, TypeScript, and MCP draft assets."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(name: str) -> None:
    parts = name.split("/")
    if (name.startswith("/") or any(part in ("", ".", "..", ".git", ".ssh")
                                    or part.startswith(".env") for part in parts)):
        raise RuntimeError(f"unsafe archive path: {name}")


def members_clean(files: dict[str, bytes], source: Path) -> None:
    forbidden = (b"/home/", b"/Users/", b"/root/", str(Path.home()).encode(),
                 str(source).encode(), b"/home/", b"BEGIN OPENSSH PRIVATE KEY",
                 b"BEGIN RSA PRIVATE KEY", b"github_pat_", b"gho_")
    for name, data in files.items():
        safe_name(name)
        if any(marker in data for marker in forbidden):
            raise RuntimeError(f"private marker in archive member: {name}")


def wheel_files(path: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(path) as archive:
        for item in archive.infolist():
            safe_name(item.filename.rstrip("/"))
            if item.is_dir():
                continue
            if stat.S_ISLNK(item.external_attr >> 16) or item.filename in files:
                raise RuntimeError(f"wheel has a link or duplicate member: {item.filename}")
            files[item.filename] = archive.read(item)
    return files


def tar_files(path: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    with tarfile.open(path, "r:gz") as archive:
        for item in archive:
            safe_name(item.name.rstrip("/"))
            if item.isdir():
                continue
            if not item.isfile() or item.name in files or not item.name.startswith("package/"):
                raise RuntimeError(f"npm archive has a link or unexpected member: {item.name}")
            stream = archive.extractfile(item)
            if stream is None:
                raise RuntimeError(f"npm archive member is unreadable: {item.name}")
            files[item.name] = stream.read()
    return files


def verify_wheel(files: dict[str, bytes], source: Path) -> None:
    prefix = "sley_tools-0.0.1.dist-info/"
    record_name = prefix + "RECORD"
    required = {prefix + "METADATA", prefix + "licenses/LICENSE",
                prefix + "licenses/NOTICE", record_name}
    if not required.issubset(files):
        raise RuntimeError("wheel omits package metadata, licenses, or RECORD")
    metadata = files[prefix + "METADATA"].decode()
    if "Name: sley_tools\n" not in metadata or "Version: 0.0.1\n" not in metadata:
        raise RuntimeError("wheel identity differs from the selected 0.0.1 package")
    source_modules = {"sley_tools/" + selected.name: selected.read_bytes()
                      for selected in (source / "packages/python/sley_tools").glob("*.py")}
    wheel_modules = {name: data for name, data in files.items()
                     if name.startswith("sley_tools/") and name.endswith(".py")}
    if wheel_modules != source_modules:
        raise RuntimeError("wheel Python modules differ from the clean checkout")
    listed = set()
    for name, encoded_hash, encoded_size in csv.reader(io.StringIO(files[record_name].decode())):
        if name in listed or name not in files:
            raise RuntimeError(f"wheel RECORD has an absent or duplicate member: {name}")
        listed.add(name)
        if name == record_name:
            if encoded_hash or encoded_size:
                raise RuntimeError("wheel RECORD self-entry must be unsigned")
            continue
        expected = base64.urlsafe_b64encode(hashlib.sha256(files[name]).digest()).rstrip(b"=").decode()
        if encoded_hash != "sha256=" + expected or encoded_size != str(len(files[name])):
            raise RuntimeError(f"wheel RECORD differs from member bytes: {name}")
    if listed != set(files):
        raise RuntimeError("wheel RECORD does not enumerate every member")


def verify_typescript(files: dict[str, bytes], source: Path) -> None:
    manifest = json.loads(files["package/package.json"])
    if manifest.get("name") != "@sley-lang/tools" or manifest.get("version") != "0.0.1":
        raise RuntimeError("TypeScript archive has the wrong package identity")
    names = ("LICENSE", "NOTICE", "package.json", "src/index.js", "src/index.d.ts",
             "examples/release-readiness.mjs", "examples/resource-admission.mjs")
    if set(files) != {"package/" + name for name in names}:
        raise RuntimeError("TypeScript archive contains unexpected files")
    for name in names:
        if files.get("package/" + name) != (source / "packages/typescript" / name).read_bytes():
            raise RuntimeError(f"TypeScript archive differs from the clean checkout: {name}")


def verify_mcp(files: dict[str, bytes], source: Path) -> None:
    manifest = json.loads(files["package/package.json"])
    if (manifest.get("name") != "@sley-lang/tools-mcp"
            or manifest.get("version") != "0.0.1"
            or set(manifest.get("bundleDependencies", [])) !=
            {"@modelcontextprotocol/server", "zod"}):
        raise RuntimeError("MCP archive has the wrong package identity or bundle")
    own_files = {name for name in files if not name.startswith("package/node_modules/")}
    if own_files != {"package/LICENSE", "package/NOTICE", "package/package.json",
                     "package/src/server.mjs", "package/src/sdk/index.js"}:
        raise RuntimeError("MCP archive contains unexpected first-party files")
    for name, data in files.items():
        if name.startswith("package/node_modules/") and not data:
            raise RuntimeError(f"MCP archive has an empty bundled dependency file: {name}")
        if name.startswith("package/node_modules/") and name.endswith("/package.json"):
            json.loads(data)
    for name in ("LICENSE", "NOTICE"):
        if files.get("package/" + name) != (source / "packages/mcp" / name).read_bytes():
            raise RuntimeError(f"MCP archive differs from the clean checkout: {name}")
    sdk = (source / "packages/typescript/src/index.js").read_bytes()
    if files.get("package/src/sdk/index.js") != sdk:
        raise RuntimeError("MCP archive contains a different TypeScript adapter")
    server = (source / "packages/mcp/src/server.mjs").read_text()
    source_import = "from '../../typescript/src/index.js';"
    if server.count(source_import) != 1:
        raise RuntimeError("MCP source import is no longer the pinned form")
    expected = server.replace(source_import, "from './sdk/index.js';").encode()
    if files.get("package/src/server.mjs") != expected:
        raise RuntimeError("MCP archive server differs from the audited source transformation")
    versions = {"@modelcontextprotocol/server": "2.1.0",
                "@modelcontextprotocol/core": "2.1.0", "zod": "4.6.5"}
    for name, version in versions.items():
        path = f"package/node_modules/{name}/package.json"
        if json.loads(files[path]).get("version") != version:
            raise RuntimeError(f"MCP bundled dependency differs: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--typescript", type=Path, required=True)
    parser.add_argument("--mcp", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"],
                                         cwd=source))
    if dirty:
        raise RuntimeError("adapter source checkout is dirty")
    result = {"contract": "sley-tools.adapter-archive-probe.v1", "result": "PASS",
              "source_commit": commit, "source_dirty": False, "packages": {}}
    paths = {"python": args.wheel, "typescript": args.typescript, "mcp": args.mcp}
    for kind, reader, verifier in (("python", wheel_files, verify_wheel),
                                   ("typescript", tar_files, verify_typescript),
                                   ("mcp", tar_files, verify_mcp)):
        path = paths[kind].resolve()
        files = reader(path)
        members_clean(files, source)
        verifier(files, source)
        result["packages"][kind] = {"archive_name": path.name,
                                    "archive_sha256": sha256(path.read_bytes()),
                                    "archive_bytes": path.stat().st_size,
                                    "file_count": len(files), "path_and_secret_scan": "PASS",
                                    "source_and_identity_verified": True}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.report}: three adapter archives verified")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile,
            tarfile.TarError, subprocess.CalledProcessError) as error:
        print(f"adapter archive probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
