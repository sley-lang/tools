#!/usr/bin/env python3
"""Exercise the exact draft assets in a checkout-hidden, network-off install."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from probe_fresh_install import CORE_ARCHIVE_SHA256, digest, restricted_environment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "release_archive", "core_archive", "wheel", "typescript", "mcp",
                 "mcp_package_report", "release_report", "adapter_report", "output"):
        parser.add_argument("--" + name.replace("_", "-"), required=True, type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"],
                               cwd=source):
        raise RuntimeError("release asset source checkout is dirty")
    release_report = json.loads(args.release_report.read_text())
    adapter_report = json.loads(args.adapter_report.read_text())
    if (commit != release_report["source_commit"]
            or commit != adapter_report["source_commit"]):
        raise RuntimeError("release and adapter assets came from different source commits")
    assets = {"release_archive": args.release_archive, "core_archive": args.core_archive,
              "wheel": args.wheel, "typescript": args.typescript, "mcp": args.mcp}
    expected = {
        "release_archive": release_report["archive_sha256"],
        "core_archive": CORE_ARCHIVE_SHA256,
        "wheel": adapter_report["packages"]["python"]["archive_sha256"],
        "typescript": adapter_report["packages"]["typescript"]["archive_sha256"],
        "mcp": adapter_report["packages"]["mcp"]["archive_sha256"],
    }
    for name, path in assets.items():
        if digest(path) != expected[name]:
            raise RuntimeError(f"draft asset differs from its audited identity: {name}")
    mcp_report = json.loads(args.mcp_package_report.read_text())
    if mcp_report["archive_sha256"] != expected["mcp"]:
        raise RuntimeError("standalone MCP package report names another archive")
    with tempfile.TemporaryDirectory(prefix="sley-release-assets-") as temporary:
        stage = Path(temporary)
        for name in ("dist", "tmp", "cache", "config", "work"):
            (stage / name).mkdir()
        destinations = {"release_archive": "sley-tools-local.tar.gz",
                        "core_archive": "sley-core.tar.gz", "wheel": args.wheel.name,
                        "typescript": args.typescript.name, "mcp": args.mcp.name}
        for name, path in assets.items():
            shutil.copy2(path, stage / "dist" / destinations[name])
        shutil.copy2(args.mcp_package_report, stage / "dist/mcp-package.json")
        shutil.copy2(source / "scripts/probe_fresh_install.py", stage / "runner.py")
        sandbox_base = ["bwrap", "--ro-bind", "/", "/", "--dev-bind", "/dev", "/dev",
                        "--proc", "/proc", "--tmpfs", "/tmp", "--bind", str(stage), str(stage),
                        "--tmpfs", str(source), "--unshare-net", "--chdir", str(stage), "--"]
        sandbox = sandbox_base + [sys.executable, str(stage / "runner.py"), "--inside",
                                  "--stage", str(stage), "--source-checkout", str(source)]
        sandboxed = subprocess.run(sandbox, cwd=stage, env=restricted_environment(stage),
                                   capture_output=True, text=True, timeout=900, check=False)
        if sandboxed.returncode:
            detail = sandboxed.stderr or sandboxed.stdout
            raise RuntimeError(f"isolated install exited {sandboxed.returncode}: "
                               f"{detail[:1800]} ... {detail[-900:]}")
        observed = json.loads((stage / "result.json").read_text())
        if observed.get("result") != "PASS" or observed.get("source_checkout_hidden") is not True:
            raise RuntimeError(f"exact draft assets failed isolated install: {sandboxed.stdout[-500:]}")
        for name in ("probe_rpc.py", "probe_rpc_portable.py"):
            shutil.copy2(source / "scripts" / name, stage / name)
        rpc_output = stage / "rpc-portable.json"
        installed = stage / "install"
        rpc_command = sandbox_base + [
            sys.executable, str(stage / "probe_rpc_portable.py"),
            "--tools", str(installed / "sley-tools-local/bin/sley-tools"),
            "--core", str(installed / "sley-2.0.1-linux-x86_64/bin/sley"),
            "--examples", str(installed / "sley-tools-local/examples/graph"),
            "--output", str(rpc_output),
        ]
        rpc_run = subprocess.run(rpc_command, cwd=stage, env=restricted_environment(stage),
                                 capture_output=True, text=True, timeout=240, check=False)
        if rpc_run.returncode:
            detail = rpc_run.stderr or rpc_run.stdout
            raise RuntimeError(f"installed scoped RPC probe exited {rpc_run.returncode}: "
                               f"{detail[:1200]} ... {detail[-600:]}")
        rpc_observed = json.loads(rpc_output.read_text())
        if (rpc_observed.get("result") != "PASS"
                or rpc_observed.get("tools_binary_sha256") != release_report["tools_binary_sha256"]
                or rpc_observed.get("core_binary_sha256") != observed["installed_core_binary_sha256"]):
            raise RuntimeError("isolated scoped RPC result differs from installed asset identities")
    observed.update({"contract": "sley-tools.release-assets-install.v1",
                     "source_commit": commit, "source_dirty": False,
                     "exact_asset_sha256": expected,
                     "sandbox_stdout": sandboxed.stdout.strip(),
                     "scoped_rpc_portable": rpc_observed,
                     "draft_assets_exact": True})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(observed, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.output}: exact draft assets passed isolated install")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"release asset install probe failed: {error}", file=sys.stderr)
        raise SystemExit(1)
