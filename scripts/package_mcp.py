#!/usr/bin/env python3
"""Build a standalone local MCP npm tarball from the pinned monorepo sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1]
IMPORT_SOURCE = "from '../../typescript/src/index.js';"
IMPORT_PACKED = "from './sdk/index.js';"
DEPENDENCIES = ("@modelcontextprotocol/server", "zod")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(*args: object, cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    command = [str(arg) for arg in args]
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                            timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"{command[:3]} failed: {(result.stderr or result.stdout)[-700:]}")
    return result


def package(output_dir: Path) -> dict:
    source = SOURCE / "packages/mcp"
    sdk = SOURCE / "packages/typescript/src/index.js"
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sley-mcp-pack-") as temporary:
        staged = Path(temporary) / "package"
        staged.mkdir()
        shutil.copytree(source / "src", staged / "src")
        for name in ("LICENSE", "NOTICE"):
            shutil.copy2(source / name, staged / name)
        shutil.copy2(source / "package-lock.json", staged / "package-lock.json")
        manifest = json.loads((source / "package.json").read_text())
        if (manifest.get("name") != "@sley-lang/tools-mcp"
                or any(manifest.get("dependencies", {}).get(name) is None
                       for name in DEPENDENCIES)):
            raise RuntimeError("MCP package manifest differs from the pinned local profile")
        manifest["bundleDependencies"] = list(DEPENDENCIES)
        manifest["bin"] = {"sley-tools-mcp": "src/server.mjs"}
        (staged / "package.json").write_text(json.dumps(manifest, indent=2) + "\n")
        server = staged / "src/server.mjs"
        text = server.read_text()
        if text.count(IMPORT_SOURCE) != 1:
            raise RuntimeError("MCP server SDK import differs from the versioned source")
        server.write_text(text.replace(IMPORT_SOURCE, IMPORT_PACKED))
        (staged / "src/sdk").mkdir()
        shutil.copy2(sdk, staged / "src/sdk/index.js")
        invoke("npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund",
               "--prefer-offline", cwd=staged, timeout=180)
        packed = json.loads(invoke("npm", "pack", "--json", "--ignore-scripts",
                                   "--pack-destination", output_dir, cwd=staged).stdout)
        if len(packed) != 1 or packed[0].get("name") != manifest["name"]:
            raise RuntimeError("npm pack did not return one MCP package")
        metadata = packed[0]
        archive = output_dir / metadata["filename"]
        bundled = set(metadata.get("bundled", []))
        if not set(DEPENDENCIES).issubset(bundled):
            raise RuntimeError("standalone MCP package omitted a pinned runtime dependency")
        with tarfile.open(archive, "r:gz") as contents:
            members = set()
            for item in contents:
                if not item.isfile():
                    continue
                members.add(item.name)
                relative = item.name.removeprefix("package/")
                staged_file = staged / relative
                if (not item.name.startswith("package/") or not staged_file.is_file()
                        or (item.name.startswith("package/node_modules/") and item.size == 0)
                        or item.size != staged_file.stat().st_size):
                    raise RuntimeError(f"standalone MCP archive has incomplete member: {item.name}")
                stream = contents.extractfile(item)
                if stream is None or hashlib.sha256(stream.read()).digest() != hashlib.sha256(
                        staged_file.read_bytes()).digest():
                    raise RuntimeError(f"standalone MCP archive differs from staged source: {item.name}")
        required = {"package/package.json", "package/LICENSE", "package/NOTICE",
                    "package/src/server.mjs", "package/src/sdk/index.js"}
        if not required.issubset(members):
            raise RuntimeError("standalone MCP package omitted server or SDK source")
        return {
            "contract": "sley-tools.mcp-standalone-package.v1", "result": "PASS",
            "filename": archive.name, "package_name": manifest["name"],
            "package_version": manifest["version"],
            "archive_sha256": digest(archive), "archive_bytes": archive.stat().st_size,
            "source_server_sha256": digest(source / "src/server.mjs"),
            "standalone_server_sha256": digest(server),
            "sdk_source_sha256": digest(sdk),
            "source_manifest_sha256": digest(source / "package.json"),
            "package_lock_sha256": digest(source / "package-lock.json"),
            "bundled_dependencies": sorted(bundled),
            "member_count": len(members),
            "npm_lifecycle_scripts_disabled": True,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = package(args.output_dir)
    if args.report:
        target = args.report.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"MCP package build failed: {error}", file=sys.stderr)
        raise SystemExit(1)
