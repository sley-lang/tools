"""Bounded JSON Lines benchmark pipeline backed by three native Sley graphs."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import ExitStack, closing
from pathlib import Path
from typing import BinaryIO, Mapping

from .runtime import Program, SleyToolsError, open_runtime


I64_MAX = (1 << 63) - 1
MEASUREMENTS = ("wall_us", "cpu_us", "archive_bytes")
UNITS = {"wall": "us", "cpu": "us", "archive": "bytes"}
INPUT_NAMES = tuple(f"{side}_{metric}" for metric in MEASUREMENTS for side in ("baseline", "candidate"))
SELECTOR_RESULT = {"selected": "Bool", "candidate_wall_us": "SInt64",
                   "candidate_cpu_us": "SInt64", "candidate_archive_bytes": "SInt64",
                   "invalid_input": "Bool"}
REDUCER_RESULT = {"count": "CheckedSInt64", "wall_total_us": "CheckedSInt64",
                  "cpu_total_us": "CheckedSInt64", "archive_total_bytes": "CheckedSInt64",
                  "invalid_input": "Bool"}
COMPARATOR_RESULT = {"wall_delta_us": "CheckedSInt64", "cpu_delta_us": "CheckedSInt64",
                     "archive_delta_bytes": "CheckedSInt64", "wall_regressed": "Bool",
                     "cpu_regressed": "Bool", "archive_regressed": "Bool",
                     "any_regression": "Bool", "invalid_input": "Bool"}
SUMMARY_METRICS = ("count", "wall_us", "cpu_us", "archive_bytes")
SUMMARY_FIELDS = ("count", "wall_total_us", "cpu_total_us", "archive_total_bytes")
CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class PipeFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def exact_object(value: object, fields: set[str], description: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise PipeFailure("DP_INPUT_INVALID", f"{description} needs exactly {sorted(fields)}")
    return value


def unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def reject_nonfinite(value: str) -> None:
    raise ValueError(f"nonfinite JSON number {value}")


def measurement(value: object, *, maximum: int, name: str) -> int:
    if type(value) is not str or not value.isascii():
        raise PipeFailure("DP_INPUT_INVALID", f"{name} must be canonical decimal text")
    try:
        integer = int(value)
    except ValueError as error:
        raise PipeFailure("DP_INPUT_INVALID", f"{name} must be canonical decimal text") from error
    if str(integer) != value or not 0 <= integer <= maximum:
        raise PipeFailure("DP_INPUT_INVALID", f"{name} is outside the nonnegative measurement domain")
    return integer


def parse_row(raw: bytes, maximum: int) -> tuple[str, dict[str, int]]:
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs,
                             parse_constant=reject_nonfinite)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise PipeFailure("DP_INPUT_INVALID", f"invalid JSON Lines record: {error}") from error
    row = exact_object(payload, {"case_id", "units", "baseline", "candidate"}, "comparison row")
    case_id = row["case_id"]
    if type(case_id) is not str or CASE_ID.fullmatch(case_id) is None:
        raise PipeFailure("DP_INPUT_INVALID", "case_id must be 1..64 ASCII identifier characters")
    if exact_object(row["units"], set(UNITS), "units") != UNITS:
        raise PipeFailure("DP_UNIT_MISMATCH", "units must be wall=us, cpu=us, archive=bytes")
    values = {}
    for side in ("baseline", "candidate"):
        source = exact_object(row[side], set(MEASUREMENTS), side)
        for metric in MEASUREMENTS:
            name = f"{side}_{metric}"
            values[name] = measurement(source[metric], maximum=maximum, name=name)
    return case_id, values


def check_program(program: Program, name: str, parameters: tuple[str, ...], result: dict[str, str]) -> None:
    actual_parameters = program.graph_parameters
    if (program.name != name or not program.graph_profile
            or program.entrypoint != "evaluate"
            or [(item["name"], item["value_type"]) for item in actual_parameters]
            != [(parameter, "SInt64") for parameter in parameters]
            or program.graph_result.get("kind") != "record"
            or {field["name"]: field["value_type"] for field in program.graph_result.get("fields", [])}
            != result):
        raise PipeFailure("DP_PROGRAM_INCOMPATIBLE", f"{name} project has an incompatible native interface")


def checked(value: object, field: str) -> int:
    if not isinstance(value, dict) or len(value) != 1:
        raise PipeFailure("DP_NATIVE_RESULT_INVALID", f"native {field} lost its checked result arm")
    if "error" in value:
        if value != {"error": {"code": 1}}:
            raise PipeFailure("DP_NATIVE_RESULT_INVALID", f"native {field} returned an unknown error")
        raise PipeFailure("DP_ARITHMETIC_OVERFLOW", f"native {field} overflowed signed 64 bit arithmetic")
    if "ok" not in value or type(value["ok"]) is not int or not -(1 << 63) <= value["ok"] <= I64_MAX:
        raise PipeFailure("DP_NATIVE_RESULT_INVALID", f"native {field} is not exact signed 64 bit")
    return value["ok"]


def compare_native(program: Program, values: Mapping[str, int]) -> dict:
    result = program.call("evaluate", inputs=values).value
    if result["invalid_input"]:
        raise PipeFailure("DP_INPUT_INVALID", "native comparator rejected a negative measurement")
    for field in ("wall_delta_us", "cpu_delta_us", "archive_delta_bytes"):
        checked(result[field], field)
    return result


def select_native(program: Program, values: Mapping[str, int]) -> dict:
    result = program.call("evaluate", inputs=values).value
    if result["invalid_input"]:
        raise PipeFailure("DP_INPUT_INVALID", "native selector rejected a negative measurement")
    for metric in MEASUREMENTS:
        name = f"candidate_{metric}"
        if result[name] != values[name]:
            raise PipeFailure("DP_NATIVE_RESULT_INVALID", f"native projection changed {name}")
    return result


def reduce_native(program: Program, left: tuple[int, int, int, int],
                  right: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    inputs = {f"{side}_{metric}": value
              for side, summary in (("left", left), ("right", right))
              for metric, value in zip(SUMMARY_METRICS, summary, strict=True)}
    result = program.call("evaluate", inputs=inputs).value
    if result["invalid_input"]:
        raise PipeFailure("DP_INPUT_INVALID", "native reducer rejected a negative summary")
    return tuple(checked(result[field], field) for field in SUMMARY_FIELDS)


def output_line(stream: BinaryIO, value: dict) -> None:
    stream.write((json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8"))
    if stream is sys.stdout.buffer:
        stream.flush()


def selected_record(case_id: str, comparison: dict, projection: dict) -> dict:
    return {
        "kind": "selected", "case_id": case_id,
        "candidate": {metric: str(projection[f"candidate_{metric}"]) for metric in MEASUREMENTS},
        "comparison": {
            **{field: {"ok": str(checked(comparison[field], field))}
               for field in ("wall_delta_us", "cpu_delta_us", "archive_delta_bytes")},
            **{field: comparison[field] for field in
               ("wall_regressed", "cpu_regressed", "archive_regressed", "any_regression")},
        },
    }


def run(args: argparse.Namespace, stream: BinaryIO, stats: dict[str, int]) -> tuple[int, int]:
    identity = (0, 0, 0, 0)
    total = identity
    partial = identity
    in_batch = 0
    with ExitStack() as stack:
        if args.input == "-":
            source = sys.stdin.buffer
        else:
            source = stack.enter_context(Path(args.input).open("rb"))
        directory = stack.enter_context(tempfile.TemporaryDirectory(prefix="sley-data-pipe-seen-"))
        database = stack.enter_context(closing(sqlite3.connect(Path(directory) / "ids.sqlite3")))
        database.execute("PRAGMA cache_size=-2048")
        database.execute("PRAGMA temp_store=FILE")
        database.execute("CREATE TABLE seen (case_id TEXT PRIMARY KEY) WITHOUT ROWID")
        runtimes = [stack.enter_context(open_runtime(tools=args.tools, core=args.core)) for _ in range(3)]
        comparator = runtimes[0].open_project(args.comparator_project)
        selector = runtimes[1].open_project(args.selector_project)
        reducer = runtimes[2].open_project(args.reducer_project)
        check_program(comparator, "benchmark-comparator", INPUT_NAMES, COMPARATOR_RESULT)
        check_program(selector, "benchmark-selector", INPUT_NAMES, SELECTOR_RESULT)
        reducer_parameters = tuple(f"{side}_{metric}" for metric in SUMMARY_METRICS
                                   for side in ("left", "right"))
        check_program(reducer, "benchmark-summary-reducer", reducer_parameters, REDUCER_RESULT)
        byte_count = 0
        while raw := source.readline(args.max_line_bytes + 2):
            byte_count += len(raw)
            if byte_count > args.max_input_bytes:
                raise PipeFailure("DP_RESOURCE_LIMIT", "input exceeds max_input_bytes")
            if len(raw) > args.max_line_bytes or (not raw.endswith(b"\n") and len(raw) >= args.max_line_bytes):
                raise PipeFailure("DP_RESOURCE_LIMIT", "JSON Lines record exceeds max_line_bytes")
            if stats["processed"] >= args.max_records:
                raise PipeFailure("DP_RESOURCE_LIMIT", "input exceeds max_records")
            case_id, values = parse_row(raw, args.max_measurement)
            try:
                database.execute("INSERT INTO seen(case_id) VALUES (?)", (case_id,))
            except sqlite3.IntegrityError as error:
                raise PipeFailure("DP_DUPLICATE_CASE", f"duplicate case_id {case_id!r}") from error
            comparison = compare_native(comparator, values)
            projection = select_native(selector, values)
            if projection["selected"]:
                output_line(stream, selected_record(case_id, comparison, projection))
                stats["emitted"] += 1
            row_summary = (1, *(projection[f"candidate_{metric}"] for metric in MEASUREMENTS))
            partial = reduce_native(reducer, partial, row_summary)
            in_batch += 1
            stats["processed"] += 1
            if in_batch == args.batch_size:
                total = reduce_native(reducer, total, partial)
                partial = identity
                in_batch = 0
        if in_batch:
            total = reduce_native(reducer, total, partial)
        if not stats["processed"]:
            total = reduce_native(reducer, identity, identity)
        summary = {"kind": "summary", **{field: str(value)
                                          for field, value in zip(SUMMARY_FIELDS, total, strict=True)}}
        output_line(stream, summary)
    return stats["processed"], stats["emitted"]


def positive(value: str) -> int:
    integer = int(value)
    if integer <= 0:
        raise argparse.ArgumentTypeError("limit must be positive")
    return integer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--comparator-project", required=True, type=Path)
    parser.add_argument("--selector-project", required=True, type=Path)
    parser.add_argument("--reducer-project", required=True, type=Path)
    parser.add_argument("--input", required=True, help="JSON Lines path or - for stdin")
    parser.add_argument("--output", type=Path, help="atomically published JSON Lines file; default stdout")
    parser.add_argument("--max-records", type=positive, default=100_000)
    parser.add_argument("--max-line-bytes", type=positive, default=8192)
    parser.add_argument("--max-input-bytes", type=positive, default=1_073_741_824)
    parser.add_argument("--max-measurement", type=positive, default=1_000_000_000_000)
    parser.add_argument("--batch-size", type=positive, default=64)
    args = parser.parse_args(argv)
    if args.max_measurement > I64_MAX or args.batch_size > 4096 or args.max_records > 1_000_000:
        parser.error("max_measurement, batch_size, or max_records exceeds the supported profile")
    if args.output and args.input != "-" and args.output.resolve() == Path(args.input).resolve():
        parser.error("output must differ from input")
    stats = {"processed": 0, "emitted": 0}
    temporary_path = None
    try:
        if args.output:
            output = args.output.expanduser().absolute()
            if output.is_symlink():
                raise PipeFailure("DP_INPUT_INVALID", "output path must not be a symbolic link")
            with tempfile.NamedTemporaryFile(mode="wb", dir=output.parent,
                                             prefix=f".{output.name}.tmp-", delete=False) as temporary:
                temporary_path = Path(temporary.name)
                processed, emitted = run(args, temporary, stats)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, output)
            temporary_path = None
        else:
            processed, emitted = run(args, sys.stdout.buffer, stats)
        print(json.dumps({"contract": "sley-tools.data-pipe-report.v0", "result": "PASS",
                          "processed_records": processed, "emitted_records": emitted}), file=sys.stderr)
        return 0
    except (PipeFailure, SleyToolsError, OSError, sqlite3.Error, KeyError, TypeError) as error:
        code = error.code if isinstance(error, PipeFailure) else (
            error.code if isinstance(error, SleyToolsError) else "DP_HOST_FAILURE")
        print(json.dumps({"contract": "sley-tools.data-pipe-report.v0", "result": "FAIL",
                          "code": code, "message": str(error),
                          "processed_records": stats["processed"],
                          "emitted_records": stats["emitted"] if not args.output else 0}),
              file=sys.stderr)
        return 1
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
