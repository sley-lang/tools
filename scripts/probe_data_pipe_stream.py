#!/usr/bin/env python3
"""Verify the bounded JSON Lines host command against three native Sley projects."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


I64_MAX = (1 << 63) - 1
UNITS = {"wall": "us", "cpu": "us", "archive": "bytes"}
FIELDS = ("wall_us", "cpu_us", "archive_bytes")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def row(case_id: str, baseline: tuple[int, int, int], candidate: tuple[int, int, int]) -> dict:
    return {"case_id": case_id, "units": UNITS,
            "baseline": dict(zip(FIELDS, map(str, baseline), strict=True)),
            "candidate": dict(zip(FIELDS, map(str, candidate), strict=True))}


def encode(rows: list[dict]) -> bytes:
    return b"".join((json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n").encode()
                    for item in rows)


def oracle(rows: list[dict], *, include_all: bool = False) -> bytes:
    result = []
    totals = [0, 0, 0]
    for item in rows:
        baseline = [int(item["baseline"][field]) for field in FIELDS]
        candidate = [int(item["candidate"][field]) for field in FIELDS]
        totals = [left + right for left, right in zip(totals, candidate, strict=True)]
        differences = [right - left for left, right in zip(baseline, candidate, strict=True)]
        flags = [right > left for left, right in zip(baseline, candidate, strict=True)]
        if any(flags) or include_all:
            result.append({"kind": "selected", "case_id": item["case_id"],
                           "candidate": item["candidate"],
                           "comparison": {
                               "wall_delta_us": {"ok": str(differences[0])},
                               "cpu_delta_us": {"ok": str(differences[1])},
                               "archive_delta_bytes": {"ok": str(differences[2])},
                               "wall_regressed": flags[0], "cpu_regressed": flags[1],
                               "archive_regressed": flags[2], "any_regression": any(flags)}})
    result.append({"kind": "summary", "count": str(len(rows)),
                   "wall_total_us": str(totals[0]), "cpu_total_us": str(totals[1]),
                   "archive_total_bytes": str(totals[2])})
    return encode(result)


def init_project(tools: Path, core: Path, root: Path, name: str, examples: Path) -> tuple[Path, str]:
    project = root / name
    result = subprocess.run([str(tools), "init", str(project), "--graph",
                             str(examples / f"{name}.json"), "--tests",
                             str(examples / f"{name}-cases.json"), "--core", str(core)],
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f"native {name} init failed: {result.stderr[:300]}")
    created = json.loads(result.stdout)
    return project, created["program"]["entrypoint"]["state_root"]


def invoke(base: list[str], env: dict[str, str], source: Path, *, batch: int = 2,
           output: Path | None = None, extra: list[str] | None = None) -> tuple[bytes, dict, int]:
    command = [*base, "--input", str(source), "--batch-size", str(batch)]
    if output is not None:
        command.extend(["--output", str(output)])
    command.extend(extra or [])
    process = subprocess.run(command, capture_output=True, env=env, timeout=180)
    try:
        report = json.loads(process.stderr.decode().splitlines()[-1])
    except (UnicodeDecodeError, json.JSONDecodeError, IndexError) as error:
        raise RuntimeError(f"pipe omitted machine report: {process.stderr[-500:]!r}") from error
    if report.get("contract") != "sley-tools.data-pipe-report.v0":
        raise RuntimeError("pipe machine report has wrong contract")
    return process.stdout, report, process.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "core", "comparator-spec", "comparator-cases",
                 "selector-spec", "selector-cases", "selector-after-cases",
                 "reducer-spec", "reducer-cases",
                 "sample", "expected", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    source_root = Path(__file__).resolve().parents[1]
    source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                            cwd=source_root, text=True).strip()
    source_dirty = bool(subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=source_root, text=True).strip())
    paths = {name: getattr(args, name).resolve() for name in
             ("tools", "core", "comparator_spec", "comparator_cases", "selector_spec",
              "selector_cases", "selector_after_cases", "reducer_spec", "reducer_cases",
              "sample", "expected", "output")}
    examples = paths["comparator_spec"].parent
    sample_rows = [json.loads(line) for line in paths["sample"].read_text().splitlines()]
    if len(sample_rows) != 4 or oracle(sample_rows) != paths["expected"].read_bytes():
        raise RuntimeError("published sample differs from independent comparison/reduction oracle")
    package_root = Path(__file__).resolve().parents[1] / "packages/python"
    installed = subprocess.run(["uv", "run", "--no-project", "--with", str(package_root),
                                "sley-data-pipe", "--help"], capture_output=True,
                               text=True, timeout=120)
    if installed.returncode or "--comparator-project" not in installed.stdout:
        raise RuntimeError("installed Data Pipe command is unavailable")
    with tempfile.TemporaryDirectory(prefix="sley-data-pipe-stream-") as temporary:
        root = Path(temporary)
        projects = {}
        roots = {}
        for role, name in (("comparator", "benchmark-comparator"),
                           ("selector", "benchmark-selector"),
                           ("reducer", "benchmark-summary-reducer")):
            projects[role], roots[role] = init_project(paths["tools"], paths["core"], root,
                                                        name, examples)
        base = [sys.executable, "-m", "sley_tools.data_pipe", "--tools", str(paths["tools"]),
                "--core", str(paths["core"])]
        for role, project in projects.items():
            base.extend([f"--{role}-project", str(project)])
        env = {**os.environ, "PYTHONPATH": str(package_root)}
        for batch in (1, 2, 4):
            observed, report, status = invoke(base, env, paths["sample"], batch=batch)
            if (status != 0 or observed != paths["expected"].read_bytes()
                    or report.get("result") != "PASS"
                    or report.get("processed_records") != 4
                    or report.get("emitted_records") != 2):
                raise RuntimeError(f"native pipeline changed output for batch size {batch}")
        adapter = root / "sley-data-pipe"
        adapter.write_text(
            f"#!{sys.executable}\nfrom sley_tools.data_pipe import main\n"
            "raise SystemExit(main())\n"
        )
        adapter.chmod(0o700)
        launcher = [str(paths["tools"]), "pipe", "--adapter", str(adapter),
                    "--core", str(paths["core"])]
        for role, project in projects.items():
            launcher.extend([f"--{role}-project", str(project)])
        observed, report, status = invoke(launcher, env, paths["sample"])
        if (status or observed != paths["expected"].read_bytes()
                or report.get("processed_records") != 4
                or report.get("emitted_records") != 2):
            raise RuntimeError("sley-tools pipe did not preserve native Data Pipe output")
        missing = subprocess.run([*launcher[:3], str(root / "missing-adapter"),
                                  *launcher[4:], "--input", str(paths["sample"])],
                                 capture_output=True, env=env, timeout=30)
        if (missing.returncode != 4 or missing.stdout
                or json.loads(missing.stderr)["code"] != "ST_FEATURE_UNSUPPORTED"):
            raise RuntimeError("sley-tools pipe did not diagnose a missing optional adapter")
        duplicate = subprocess.run([*launcher, "--input", str(paths["sample"]),
                                    "--input", str(paths["sample"])],
                                   capture_output=True, env=env, timeout=30)
        if (duplicate.returncode != 2 or duplicate.stdout
                or json.loads(duplicate.stderr)["code"] != "ST_INPUT_INVALID"):
            raise RuntimeError("sley-tools pipe accepted a duplicate option")
        empty = root / "empty.jsonl"
        empty.write_bytes(b"")
        observed, report, status = invoke(base, env, empty)
        if status or observed != oracle([]) or report.get("processed_records") != 0:
            raise RuntimeError("empty input did not return native reducer identity")
        boundary = root / "boundary.jsonl"
        boundary.write_bytes(encode([row("max", (0, 0, 0), (I64_MAX, 0, 0))]))
        observed, report, status = invoke(base, env, boundary,
                                          extra=["--max-measurement", str(I64_MAX)])
        if status or observed != oracle([row("max", (0, 0, 0), (I64_MAX, 0, 0))]):
            raise RuntimeError("signed boundary or zero baseline changed in native pipeline")
        overflow = root / "overflow.jsonl"
        overflow.write_bytes(encode([row("max-a", (0, 0, 0), (I64_MAX, 0, 0)),
                                     row("max-b", (0, 0, 0), (I64_MAX, 0, 0))]))
        observed, report, status = invoke(base, env, overflow, batch=2,
                                          extra=["--max-measurement", str(I64_MAX)])
        if (status == 0 or report.get("code") != "DP_ARITHMETIC_OVERFLOW"
                or report.get("emitted_records") != 2
                or len(observed.splitlines()) != 2):
            raise RuntimeError("stdout overflow hid partial native output")
        invalid = {
            "duplicate": encode([sample_rows[0], sample_rows[1], sample_rows[1]]),
            "missing": encode([{**sample_rows[0], "candidate": {"wall_us": "1", "cpu_us": "1"}}]),
            "unit": encode([{**sample_rows[0], "units": {"wall": "ms", "cpu": "us", "archive": "bytes"}}]),
            "malformed": b"not-json\n",
            "negative": encode([row("negative", (-1, 0, 0), (0, 0, 0))]),
            "noncanonical": encode([{**sample_rows[0], "candidate":
                                     {**sample_rows[0]["candidate"], "wall_us": "01"}}]),
        }
        expected_codes = {"duplicate": "DP_DUPLICATE_CASE", "unit": "DP_UNIT_MISMATCH"}
        for name, content in invalid.items():
            path = root / f"{name}.jsonl"
            path.write_bytes(content)
            observed, report, status = invoke(base, env, path)
            if status == 0 or report.get("code") != expected_codes.get(name, "DP_INPUT_INVALID"):
                raise RuntimeError(f"{name} input was not diagnosed")
            if name == "duplicate" and (report.get("emitted_records") != 1 or len(observed.splitlines()) != 1):
                raise RuntimeError("duplicate input did not report the exact visible stdout prefix")
        previous = root / "published.jsonl"
        previous.write_bytes(b"EXISTING VALID OUTPUT\n")
        observed, report, status = invoke(base, env, root / "duplicate.jsonl", output=previous)
        if (status == 0 or observed or previous.read_bytes() != b"EXISTING VALID OUTPUT\n"
                or report.get("emitted_records") != 0
                or list(root.glob(".published.jsonl.tmp-*"))):
            raise RuntimeError("failed file output replaced or leaked the existing artifact")
        observed, report, status = invoke(base, env, paths["sample"], output=previous)
        if status or observed or previous.read_bytes() != paths["expected"].read_bytes():
            raise RuntimeError("successful file output did not publish exact native result")
        alias = root / "output-symlink.jsonl"
        alias.symlink_to(previous)
        observed, report, status = invoke(base, env, paths["sample"], output=alias)
        if (status == 0 or report.get("code") != "DP_INPUT_INVALID"
                or previous.read_bytes() != paths["expected"].read_bytes()):
            raise RuntimeError("symbolic-link output bypassed file publication scope")
        observed, report, status = invoke(base, env, paths["sample"], extra=["--max-records", "2"])
        if status == 0 or report.get("code") != "DP_RESOURCE_LIMIT" or report.get("processed_records") != 2:
            raise RuntimeError("record limit was not enforced while streaming")
        for option, value in (("--max-line-bytes", "32"), ("--max-input-bytes", "32")):
            observed, report, status = invoke(base, env, paths["sample"], extra=[option, value])
            if status == 0 or report.get("code") != "DP_RESOURCE_LIMIT" or report.get("processed_records") != 0:
                raise RuntimeError(f"{option} was not enforced before native execution")
        selector_value = root / "include-nonregressions.json"
        selector_value.write_text("true\n")
        proposal = subprocess.run([str(paths["tools"]), "propose", "--project",
                                   str(projects["selector"]), "--constant", "include_nonregressions",
                                   "--value-file", str(selector_value), "--tests",
                                   str(paths["selector_after_cases"])], capture_output=True,
                                  text=True, timeout=120)
        if proposal.returncode:
            raise RuntimeError(f"native selector edit proposal failed: {proposal.stderr[:300]}")
        validation = subprocess.run([str(paths["tools"]), "validate", "--project",
                                     str(projects["selector"])], capture_output=True,
                                    text=True, timeout=120)
        if validation.returncode:
            raise RuntimeError(f"native selector edit validation failed: {validation.stderr[:300]}")
        commit = subprocess.run([str(paths["tools"]), "commit", "--project",
                                 str(projects["selector"])], capture_output=True,
                                text=True, timeout=120)
        if commit.returncode:
            raise RuntimeError(f"native selector edit commit failed: {commit.stderr[:300]}")
        edited = json.loads(commit.stdout)
        if (edited["old_root"] != roots["selector"] or edited["new_root"] == roots["selector"]
                or edited["changed_case_count"] != 2):
            raise RuntimeError("native selector predicate edit lacked an accepted changed root")
        observed, report, status = invoke(base, env, paths["sample"])
        if (status or observed != oracle(sample_rows, include_all=True)
                or report.get("emitted_records") != 4):
            raise RuntimeError("unchanged host command did not use edited native predicate")
    report = {
        "contract": "sley-tools.data-pipe-stream-probe.v2", "result": "PASS",
        "tools_binary_sha256": digest(paths["tools"]),
        "tools_source_commit": source_commit, "tools_source_dirty": source_dirty,
        "core_binary_sha256": digest(paths["core"]),
        "module_sha256": digest(Path(__file__).resolve().parents[1] / "packages/python/sley_tools/data_pipe.py"),
        "python_sdk_sha256": digest(Path(__file__).resolve().parents[1] / "packages/python/sley_tools/runtime.py"),
        "package_sha256": digest(Path(__file__).resolve().parents[1] / "packages/python/pyproject.toml"),
        "probe_entry_sha256": digest(Path(__file__)),
        **{name + "_sha256": digest(paths[name]) for name in
           ("comparator_spec", "comparator_cases", "selector_spec", "selector_cases",
            "selector_after_cases",
            "reducer_spec", "reducer_cases", "sample", "expected")},
        "native_roots": roots, "edited_selector_root": edited["new_root"],
        "sample_records": 4, "sample_selected": 2,
        "predicate_edit_host_reuse": True,
        "installed_entrypoint_verified": True,
        "unified_cli_launcher_verified": True,
        "missing_adapter_and_duplicate_option_rejected": True,
        "batch_sizes": [1, 2, 4], "empty_native_identity": True,
        "signed_boundary": True, "reducer_overflow": True,
        "negative_paths": sorted(invalid), "atomic_file_output": True,
        "symlink_output_rejected": True,
        "stdout_partial_count": 1, "record_limit_enforced": True,
        "line_and_byte_limits_enforced": True,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }
    paths["output"].parent.mkdir(parents=True, exist_ok=True)
    paths["output"].write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"wrote {paths['output']}: native JSON Lines streaming sample and failure paths passed")


if __name__ == "__main__":
    main()
