"""Process-scoped Sley tools client; program decisions are executed by Sley."""

from __future__ import annotations

import json
import os
import re
import select
import signal
import subprocess
import threading
import time
from pathlib import Path
from collections.abc import Mapping

# hashlib, shutil, and tempfile are imported where used, and typing only for
# checkers: together with dataclasses they were about half of the SDK's
# import time, and most programs only call an already-loaded program.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import Any


class SleyToolsError(Exception):
    def __init__(self, code: str, category: str, message: str, *, core_symbol: str | None = None, core_numeric: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.category = category
        self.core_symbol = core_symbol
        self.core_numeric = core_numeric


class CallResult:
    """Immutable decoded value and execution metadata of one native call."""

    __slots__ = ("value", "metadata")
    __match_args__ = ("value", "metadata")

    value: bool | int | dict[str, Any]
    metadata: dict[str, Any]

    def __init__(self, value: bool | int | dict[str, Any], metadata: dict[str, Any]) -> None:
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "metadata", metadata)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"cannot assign to field {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"cannot delete field {name!r}")

    def __repr__(self) -> str:
        return f"CallResult(value={self.value!r}, metadata={self.metadata!r})"

    def __eq__(self, other: object) -> bool:
        if other.__class__ is not CallResult:
            return NotImplemented
        return (self.value, self.metadata) == (other.value, other.metadata)

    def __hash__(self) -> int:
        return hash((self.value, self.metadata))

    def __reduce__(self) -> tuple[type[CallResult], tuple[object, object]]:
        # copy, deepcopy, and pickle rebuild through __init__, since
        # __setattr__ refuses the default slot-state restore.
        return (CallResult, (self.value, self.metadata))


def _input_error(message: str) -> SleyToolsError:
    return SleyToolsError("ST_INPUT_INVALID", "input_shape", message)


def encode_integer(value: int, type_alias: str) -> dict[str, str]:
    """Encode exact u32/i64 wire values; bool is deliberately not an integer."""
    if type(value) is not int:
        raise _input_error("integer input must be a Python int, not bool or another type")
    limits = {"u32": (0, 2**32 - 1), "i64": (-(2**63), 2**63 - 1)}
    if type_alias not in limits:
        raise _input_error(f"unsupported integer type alias: {type_alias}")
    minimum, maximum = limits[type_alias]
    if not minimum <= value <= maximum:
        raise _input_error(f"{type_alias} input is outside its declared domain")
    return {"kind": "integer", "type": type_alias, "value": str(value)}


def _encode_facts(inputs: Mapping[str, object]) -> dict[str, Any]:
    expected = {"test_run_completed", "tests_failed", "artifact_verified", "blocking_findings"}
    if not isinstance(inputs, Mapping) or set(inputs) != expected:
        raise _input_error("Release Readiness requires exactly four named inputs")
    completed, verified = inputs["test_run_completed"], inputs["artifact_verified"]
    if type(completed) is not bool or type(verified) is not bool:
        raise _input_error("test_run_completed and artifact_verified must be Boolean")
    return {
        "kind": "record",
        "fields": {
            "test_run_completed": {"kind": "boolean", "value": completed},
            "tests_failed": encode_integer(inputs["tests_failed"], "u32"),
            "artifact_verified": {"kind": "boolean", "value": verified},
            "blocking_findings": encode_integer(inputs["blocking_findings"], "u32"),
        },
    }


def _encode_resource(inputs: Mapping[str, object]) -> dict[str, Any]:
    names = ("requested_workers", "available_workers", "requested_memory_mib", "available_memory_mib")
    if not isinstance(inputs, Mapping) or set(inputs) != set(names):
        raise _input_error("Resource Admission requires exactly four named inputs")
    fields = {}
    for name in names:
        value = inputs[name]
        tagged = encode_integer(value, "i64")
        if value < 0:
            raise _input_error(f"{name} is outside the declared nonnegative domain")
        fields[name] = tagged
    return {"kind": "record", "fields": fields}


def _encode_graph(inputs: Mapping[str, object], parameters: list[dict[str, str]]) -> dict[str, Any]:
    names = [parameter["name"] for parameter in parameters]
    if not isinstance(inputs, Mapping) or set(inputs) != set(names) or len(inputs) != len(names):
        raise _input_error("graph inputs must match the named parameters exactly")
    values = []
    for parameter in parameters:
        name, kind = parameter["name"], parameter["value_type"]
        value = inputs[name]
        if kind == "Bool":
            if type(value) is not bool:
                raise _input_error(f"graph parameter {name} must be Boolean")
            values.append({"kind": "boolean", "value": value})
        elif kind in ("UInt32", "SInt64"):
            values.append(encode_integer(value, "u32" if kind == "UInt32" else "i64"))
        else:
            raise _input_error(f"unsupported graph parameter type: {kind}")
    return {"kind": "arguments", "values": values}


def _graph_raw(value: object, kind: str) -> bool | str | dict[str, object]:
    if kind == "CheckedSInt64":
        if not isinstance(value, Mapping) or len(value) != 1:
            raise _input_error("checked graph value needs one arm")
        if "ok" in value:
            return {"ok": _graph_raw(value["ok"], "SInt64")}
        failure = value.get("error")
        if isinstance(failure, Mapping) and set(failure) == {"code"} and type(failure["code"]) is int and failure["code"] == 1:
            return {"error": {"code": 1}}
        raise _input_error("checked graph value has an unavailable arithmetic failure")
    if kind == "Bool":
        if type(value) is not bool:
            raise _input_error("graph Boolean value must be true or false")
        return value
    if kind in ("UInt32", "SInt64"):
        return encode_integer(value, "u32" if kind == "UInt32" else "i64")["value"]
    raise _input_error(f"unsupported graph value type: {kind}")


def _graph_cases(cases: object, parameters: list[dict[str, str]], result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(cases, list) or not 1 <= len(cases) <= 256:
        raise _input_error("graph proposal needs 1 through 256 cases")
    if any(not isinstance(parameter, Mapping) or not isinstance(parameter.get("name"), str)
           or not isinstance(parameter.get("value_type"), str) for parameter in parameters):
        raise _input_error("graph parameters need name and value_type")
    if result.get("kind") == "primitive":
        if not isinstance(result.get("value_type"), str):
            raise _input_error("graph primitive result needs value_type")
    elif result.get("kind") == "record":
        if not isinstance(result.get("fields"), list) or any(
            not isinstance(field, Mapping) or not isinstance(field.get("name"), str)
            or not isinstance(field.get("value_type"), str) for field in result["fields"]
        ):
            raise _input_error("graph record result needs typed fields")
    else:
        raise _input_error("unsupported graph result shape")
    names = [parameter["name"] for parameter in parameters]
    rows = []
    for case in cases:
        if not isinstance(case, Mapping) or set(case) != {"id", "inputs", "expected"}:
            raise _input_error("graph case needs id, inputs, and expected")
        case_id, inputs, expected = case["id"], case["inputs"], case["expected"]
        if type(case_id) is not str or not 1 <= len(case_id) <= 64:
            raise _input_error("graph case id must be 1 through 64 characters")
        if not isinstance(inputs, Mapping) or set(inputs) != set(names) or len(inputs) != len(names):
            raise _input_error("graph case inputs must match named parameters")
        encoded_input = [_graph_raw(inputs[parameter["name"]], parameter["value_type"]) for parameter in parameters]
        if result.get("kind") == "primitive":
            encoded_expected = _graph_raw(expected, result["value_type"])
        elif result.get("kind") == "record":
            fields = result["fields"]
            if not isinstance(expected, Mapping) or set(expected) != {field["name"] for field in fields}:
                raise _input_error("graph case expected record has the wrong fields")
            encoded_expected = {field["name"]: _graph_raw(expected[field["name"]], field["value_type"]) for field in fields}
        else:
            raise _input_error("unsupported graph result shape")
        rows.append({"id": case_id, "input": encoded_input, "expected": encoded_expected})
    return {"contract": "sley-tools.graph-cases.v0", "cases": rows}


def _decode_graph_value(value: object, kind: str) -> bool | int | dict[str, object]:
    if kind == "CheckedSInt64":
        if not isinstance(value, dict) or set(value) != {"kind", "arm", "value"} or value.get("kind") != "result":
            raise SleyToolsError("ST_EXECUTION_FAILED", "execution", "native checked result has the wrong tag")
        if value["arm"] == "ok":
            return {"ok": _decode_graph_value(value["value"], "SInt64")}
        failure = value["value"]
        if value["arm"] == "error" and isinstance(failure, dict) and set(failure) == {"kind", "failure", "code"} and failure == {"kind": "builtin_failure", "failure": "arithmetic", "code": 1}:
            return {"error": {"code": 1}}
        raise SleyToolsError("ST_EXECUTION_FAILED", "execution", "native checked result has an unavailable arithmetic failure")
    if kind == "Bool":
        if not isinstance(value, dict) or set(value) != {"kind", "value"} or value.get("kind") != "boolean" or type(value.get("value")) is not bool:
            raise SleyToolsError("ST_EXECUTION_FAILED", "execution", "native graph result is not tagged Boolean")
        return value["value"]
    alias = {"UInt32": "u32", "SInt64": "i64"}.get(kind)
    if alias is None or not isinstance(value, dict) or set(value) != {"kind", "type", "value"} or value.get("kind") != "integer" or value.get("type") != alias or type(value.get("value")) is not str:
        raise SleyToolsError("ST_EXECUTION_FAILED", "execution", "native graph result has the wrong integer tag")
    decimal = value["value"]
    try:
        number = int(decimal)
        if encode_integer(number, alias)["value"] != decimal:
            raise ValueError("noncanonical decimal")
    except (ValueError, SleyToolsError) as error:
        raise SleyToolsError("ST_EXECUTION_FAILED", "execution", "native graph integer is not canonical") from error
    return number


class Runtime:
    def __init__(self, *, tools: str | Path, core: str | Path) -> None:
        self.tools = Path(tools).resolve()
        self.core = Path(core).resolve()
        self._temporary: Path | None = None
        self._project_path: Path | None = None
        self._preserve_temporary = False
        self._process: subprocess.Popen[str] | None = None
        self._stderr_chunks: list[str] = []
        self._stderr_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._handle: str | None = None
        self._program: Program | None = None
        self.hello: dict[str, Any] | None = None

    def __enter__(self) -> Runtime:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _drain_stderr(self, stream: Any) -> None:
        length = 0
        while chunk := stream.read(1024):
            if length < 8192:
                excerpt = chunk[: 8192 - length]
                self._stderr_chunks.append(excerpt)
                length += len(excerpt)

    def _raise_cli(self, result: subprocess.CompletedProcess[str]) -> None:
        try:
            payload = json.loads(result.stderr)
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict):
            raise SleyToolsError("ST_TRANSPORT_FAILED", "transport", result.stderr[:300] or "sley-tools command failed")
        raise SleyToolsError(
            payload.get("code", "ST_TRANSPORT_FAILED"),
            payload.get("category", "transport"),
            payload.get("message", "sley-tools command failed"),
            core_symbol=payload.get("core_symbol"),
            core_numeric=payload.get("core_numeric"),
        )

    def load(self, *, artifact: str | Path) -> Program:
        if self._process is not None:
            raise _input_error("runtime already has a loaded program")
        import tempfile

        self._temporary = Path(tempfile.mkdtemp(prefix="sley-python-sdk-"))
        project = self._temporary / "project"
        try:
            result = subprocess.run(
                [str(self.tools), "artifact", "import", str(Path(artifact).resolve()), str(project), "--core", str(self.core)],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if result.returncode:
                self._raise_cli(result)
            return self.open_project(project)
        except subprocess.TimeoutExpired as error:
            self.close()
            raise SleyToolsError("ST_TRANSPORT_FAILED", "transport", "artifact import timed out") from error
        except Exception:
            self.close()
            raise

    def create_graph(self, *, project: str | Path, recipe: Mapping[str, object],
                     cases: list[Mapping[str, object]]) -> Program:
        """Construct and open one native graph through the shared Rust builder."""
        if self._process is not None:
            raise _input_error("runtime already has a loaded program")
        if not isinstance(project, (str, Path)) or not str(project):
            raise _input_error("graph project path is required")
        if not isinstance(recipe, Mapping) or recipe.get("contract") != "sley-tools.graph-authoring.v0":
            raise _input_error("graph recipe has the wrong contract")
        parameters, constants, result = recipe.get("parameters"), recipe.get("constants"), recipe.get("result")
        if not isinstance(parameters, list) or not isinstance(constants, list) or not isinstance(result, Mapping):
            raise _input_error("graph recipe needs parameters, constants, and result")
        normalized = dict(recipe)
        normalized_constants = []
        for constant in constants:
            if not isinstance(constant, Mapping) or set(constant) != {"name", "value_type", "value"}:
                raise _input_error("graph constant needs name, value_type, and value")
            normalized_constants.append({**constant, "value": _graph_raw(constant["value"], constant["value_type"])})
        normalized["constants"] = normalized_constants
        fixture = _graph_cases(cases, parameters, dict(result))
        try:
            spec_bytes = (json.dumps(normalized, allow_nan=False, separators=(",", ":")) + "\n").encode()
            cases_bytes = (json.dumps(fixture, allow_nan=False, separators=(",", ":")) + "\n").encode()
        except (TypeError, ValueError) as error:
            raise _input_error(f"graph authoring data is not JSON: {error}") from error
        if len(spec_bytes) > 65_536 or len(cases_bytes) > 1_048_576:
            raise SleyToolsError("ST_RESOURCE_LIMIT", "resource_limit", "graph recipe or cases exceed the profile limit")
        destination = Path(project).resolve()
        import tempfile

        with tempfile.TemporaryDirectory(prefix="sley-python-graph-") as temporary:
            spec_path, cases_path = Path(temporary) / "graph.json", Path(temporary) / "cases.json"
            spec_path.write_bytes(spec_bytes)
            cases_path.write_bytes(cases_bytes)
            try:
                result = subprocess.run(
                    [str(self.tools), "init", str(destination), "--graph", str(spec_path),
                     "--tests", str(cases_path), "--core", str(self.core)],
                    capture_output=True, text=True, timeout=120, check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise SleyToolsError("ST_TRANSPORT_FAILED", "transport",
                                     f"graph initialization timed out at {destination}; inspect its status before retrying") from error
            if result.returncode:
                self._raise_cli(result)
        return self.open_project(destination)

    def open_project(self, project: str | Path) -> Program:
        if self._process is not None:
            raise _input_error("runtime already has a loaded program")
        self._project_path = Path(project).resolve()
        try:
            self._process = subprocess.Popen(
                [str(self.tools), "rpc", "--project", str(self._project_path)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, start_new_session=True,
            )
        except OSError as error:
            self.close()
            raise SleyToolsError("ST_TRANSPORT_FAILED", "transport", f"cannot start RPC process: {error}") from error
        assert self._process.stderr is not None
        self._stderr_thread = threading.Thread(target=self._drain_stderr, args=(self._process.stderr,), daemon=True)
        self._stderr_thread.start()
        try:
            self.hello = self._request("sleytools.hello", {}, timeout=5)
            if self.hello.get("contract") != "sley-tools.rpc.v0":
                raise SleyToolsError("ST_CORE_INCOMPATIBLE", "compatibility", "RPC contract differs from Python SDK")
            import hashlib

            digest = hashlib.sha256()
            with self.core.open("rb") as binary:
                for chunk in iter(lambda: binary.read(64 * 1024), b""):
                    digest.update(chunk)
            if self.hello.get("core", {}).get("binary_sha256") != digest.hexdigest():
                raise SleyToolsError("ST_CORE_INCOMPATIBLE", "compatibility", "RPC project uses a different selected core binary")
            opened = self._request("sleytools.workspace.open", {}, timeout=5)
            self._handle = opened["handle"]
            inspection = self._request("sleytools.program.inspect", {"handle": self._handle}, timeout=5)
            self._program = Program(self, inspection)
            return self._program
        except Exception:
            self.close()
            raise

    def _operation_command(self, action: str, operation_id: str) -> dict[str, Any]:
        if self._project_path is None or not isinstance(operation_id, str) or re.fullmatch(r"[0-9a-f]{64}", operation_id) is None:
            raise _input_error("operation command needs a loaded project and exact operation ID")
        try:
            completed = subprocess.run(
                [str(self.tools), "operation", action, "--project", str(self._project_path), "--id", operation_id],
                capture_output=True, text=True, timeout=30, check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise SleyToolsError("ST_TRANSPORT_FAILED", "transport", f"operation {action} timed out") from error
        if completed.returncode:
            self._raise_cli(completed)
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise SleyToolsError("ST_TRANSPORT_FAILED", "transport", f"operation {action} returned invalid JSON") from error
        if result.get("state") in ("outcome_unknown", "cancel_requested"):
            self._preserve_temporary = True
        return result

    def operation_status(self, operation_id: str) -> dict[str, Any]:
        """Read durable tools/core status even if this runtime's RPC process died."""
        return self._operation_command("status", operation_id)

    def cancel_operation(self, operation_id: str) -> dict[str, Any]:
        """Cancel before commit when possible; otherwise retain reconciliation evidence."""
        return self._operation_command("cancel", operation_id)

    @staticmethod
    def _stop_process_group(process: subprocess.Popen[str]) -> None:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=5)

    def _request(self, method: str, params: dict[str, Any], *, timeout: float,
                 cancel_event: threading.Event | None = None) -> dict[str, Any]:
        with self._lock:
            process = self._process
            if process is None or process.stdin is None or process.stdout is None:
                raise SleyToolsError("ST_TRANSPORT_FAILED", "transport", "RPC process is closed")
            if cancel_event is not None and cancel_event.is_set():
                raise SleyToolsError("ST_CANCELLED", "execution", "read-only call cancelled before dispatch")
            request_id = self._next_id
            self._next_id += 1
            frame = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            try:
                process.stdin.write(json.dumps(frame, separators=(",", ":")) + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as error:
                self._stop_process_group(process)
                raise SleyToolsError("ST_TRANSPORT_FAILED", "transport", f"cannot send RPC request: {error}") from error
            expires = time.monotonic() + timeout
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    self._stop_process_group(process)
                    raise SleyToolsError("ST_CANCELLED", "execution", "read-only call cancelled; runtime stopped")
                remaining = expires - time.monotonic()
                if remaining <= 0:
                    self._stop_process_group(process)
                    raise SleyToolsError("ST_TRANSPORT_FAILED", "transport", "RPC response timed out; runtime stopped")
                ready, _, _ = select.select([process.stdout], [], [],
                                            min(remaining, 0.05) if cancel_event is not None else remaining)
                if ready:
                    break
            line = process.stdout.readline(262_146)
            if not line:
                self._stop_process_group(process)
                raise SleyToolsError("ST_TRANSPORT_FAILED", "transport", "RPC process closed: " + "".join(self._stderr_chunks)[:300])
            if len(line) > 262_145 or not line.endswith("\n"):
                self._stop_process_group(process)
                raise SleyToolsError("ST_RESOURCE_LIMIT", "resource_limit", "RPC response exceeded 256 KiB or lacked newline")
            try:
                response = json.loads(line)
            except json.JSONDecodeError as error:
                self._stop_process_group(process)
                raise SleyToolsError("ST_TRANSPORT_FAILED", "transport", f"RPC response is invalid JSON: {error}") from error
            if not isinstance(response, dict) or response.get("jsonrpc") != "2.0" or response.get("id") != request_id:
                self._stop_process_group(process)
                raise SleyToolsError("ST_TRANSPORT_FAILED", "transport", "RPC response ID or version differs from request")
            if "error" in response:
                rpc_error = response["error"] if isinstance(response["error"], dict) else {}
                data = rpc_error.get("data") if isinstance(rpc_error.get("data"), dict) else {}
                raise SleyToolsError(
                    data.get("tools_code", "ST_TRANSPORT_FAILED"),
                    data.get("category", "transport"),
                    rpc_error.get("message", "RPC operation failed"),
                    core_symbol=data.get("core_symbol"),
                    core_numeric=data.get("core_numeric"),
                )
            result = response.get("result")
            if not isinstance(result, dict):
                raise SleyToolsError("ST_TRANSPORT_FAILED", "transport", "RPC result is not an object")
            return result

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is not None:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._stop_process_group(process)
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=1)
            self._stderr_thread = None
        if self._temporary is not None:
            if not self._preserve_temporary:
                import shutil

                shutil.rmtree(self._temporary)
            self._temporary = None


class Program:
    def __init__(self, runtime: Runtime, inspection: dict[str, Any]) -> None:
        self._runtime = runtime
        self._operations: dict[str, str] = {}
        self._inspection = inspection
        program = inspection["program"]
        self.name = program["program_id"]
        self.entrypoint = program["entrypoint"]["name"]
        self.function_id = program["entrypoint"]["function_id"]
        self.state_root = inspection["accepted_state_root"]
        self.result_type_id = program["result_type_id"]
        self.result_fields = {field["name"]: field.get("type", "Bool") for field in program["result_fields"]}
        self.graph_profile = program.get("authoring_profile") == "graph_v0"
        authoring = inspection.get("authoring", {}) if self.graph_profile else {}
        self.graph_parameters = authoring.get("parameters", [])
        self.graph_constants = authoring.get("constants", [])
        self.graph_operations = authoring.get("operations", [])
        self.graph_branches = authoring.get("branches", [])
        self.graph_result = authoring.get("result", {})

    def call(self, entrypoint: str, *, inputs: Mapping[str, object], deadline_ms: int = 10_000,
             cancel_event: threading.Event | None = None) -> CallResult:
        if entrypoint != self.entrypoint:
            raise _input_error(f"unknown entrypoint: {entrypoint}")
        if type(deadline_ms) is not int or not 1 <= deadline_ms <= 60_000:
            raise _input_error("deadline_ms must be an integer from 1 through 60000")
        if cancel_event is not None and not isinstance(cancel_event, type(threading.Event())):
            raise _input_error("cancel_event must be a threading.Event")
        if self.graph_profile:
            wire = _encode_graph(inputs, self.graph_parameters)
        elif self.name == "release-readiness":
            wire = _encode_facts(inputs)
        elif self.name == "resource-admission":
            wire = _encode_resource(inputs)
        else:
            raise _input_error(f"unsupported program profile: {self.name}")
        response = self._runtime._request(
            "sleytools.program.execute",
            {"handle": self._runtime._handle, "entrypoint": entrypoint, "input": wire, "deadline_ms": deadline_ms},
            timeout=deadline_ms / 1000 + 2, cancel_event=cancel_event,
        )
        value, metadata = response.get("value"), response.get("metadata")
        if not isinstance(value, dict) or not isinstance(metadata, dict):
            raise SleyToolsError("ST_TRANSPORT_FAILED", "transport", "RPC execution response is incomplete")
        if metadata.get("state_root") != self.state_root or metadata.get("function_id") != self.function_id:
            raise SleyToolsError("ST_EXECUTION_FAILED", "execution", "native result differs from pinned entrypoint identity")
        if self.graph_profile:
            if self.graph_result.get("kind") == "primitive":
                return CallResult(_decode_graph_value(value, self.graph_result["value_type"]), metadata)
            if self.graph_result.get("kind") != "record" or value.get("kind") != "record" or value.get("type_id") != self.result_type_id:
                raise SleyToolsError("ST_EXECUTION_FAILED", "execution", "native graph record identity differs from entrypoint")
            fields = value.get("fields")
            definitions = self.graph_result.get("fields", [])
            if not isinstance(fields, dict) or set(fields) != {field["name"] for field in definitions}:
                raise SleyToolsError("ST_EXECUTION_FAILED", "execution", "native graph record has unexpected fields")
            decoded = {field["name"]: _decode_graph_value(fields[field["name"]], field["value_type"]) for field in definitions}
            return CallResult(decoded, metadata)
        if value.get("kind") != "record" or value.get("type_id") != self.result_type_id:
            raise SleyToolsError("ST_EXECUTION_FAILED", "execution", "native result differs from pinned entrypoint identity")
        fields = value.get("fields")
        if not isinstance(fields, dict) or set(fields) != set(self.result_fields):
            raise SleyToolsError("ST_EXECUTION_FAILED", "execution", "native result record has unexpected fields")
        decoded = {}
        for name, field_type in self.result_fields.items():
            field = fields[name]
            if field_type == "SInt64":
                if not isinstance(field, dict) or set(field) != {"kind", "type", "value"} or field.get("kind") != "integer" or field.get("type") != "i64" or type(field.get("value")) is not str:
                    raise SleyToolsError("ST_EXECUTION_FAILED", "execution", f"native field {name} is not tagged i64")
                decimal = field["value"]
                try:
                    number = int(decimal)
                    if encode_integer(number, "i64")["value"] != decimal:
                        raise ValueError("noncanonical decimal")
                except (ValueError, SleyToolsError) as error:
                    raise SleyToolsError("ST_EXECUTION_FAILED", "execution", f"native field {name} is not canonical i64") from error
                decoded[name] = number
            else:
                if not isinstance(field, dict) or set(field) != {"kind", "value"} or field["kind"] != "boolean" or type(field["value"]) is not bool:
                    raise SleyToolsError("ST_EXECUTION_FAILED", "execution", f"native field {name} is not Boolean")
                decoded[name] = field["value"]
        return CallResult(decoded, metadata)

    def inspect(self) -> dict[str, Any]:
        return self._runtime._request("sleytools.program.inspect", {"handle": self._runtime._handle}, timeout=5)

    def test(self) -> dict[str, Any]:
        return self._runtime._request("sleytools.tests.run", {"handle": self._runtime._handle}, timeout=30)

    def propose(self, change: Mapping[str, object]) -> dict[str, Any]:
        if not isinstance(change, Mapping) or len(change) != 1:
            raise _input_error("authoring change must contain exactly one supported field")
        if self.name == "release-readiness" and set(change) == {"max_blocking_findings"}:
            field = "max_blocking_findings"
            value = encode_integer(change[field], "u32")
        elif self.name == "resource-admission" and set(change) == {"require_memory_spare"}:
            field = "require_memory_spare"
            if type(change[field]) is not bool:
                raise _input_error("require_memory_spare must be Boolean")
            value = {"kind": "boolean", "value": change[field]}
        else:
            raise _input_error("authoring field is unavailable for this program profile")
        return self._runtime._request("sleytools.program.propose", {
            "handle": self._runtime._handle, "change": {"field": field, "value": value},
        }, timeout=20)

    def propose_graph_constant(self, name: str, value: object, cases: list[Mapping[str, object]]) -> dict[str, Any]:
        if not self.graph_profile:
            raise _input_error("program is not a graph_v0 project")
        selected = next((constant for constant in self.graph_constants if constant["name"] == name), None)
        if selected is None:
            raise _input_error(f"unknown graph constant: {name}")
        kind = selected["value_type"]
        raw = _graph_raw(value, kind)
        tagged = {"kind": "boolean", "value": raw} if kind == "Bool" else encode_integer(value, "u32" if kind == "UInt32" else "i64")
        return self._runtime._request("sleytools.program.propose", {
            "handle": self._runtime._handle,
            "change": {"field": f"constant.{name}", "value": tagged,
                       "cases": _graph_cases(cases, self.graph_parameters, self.graph_result)},
        }, timeout=20)

    def propose_graph_opcode(self, block: str, name: str, opcode: str,
                             cases: list[Mapping[str, object]]) -> dict[str, Any]:
        if not self.graph_profile:
            raise _input_error("program is not a graph_v0 project")
        label = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
        if any(type(value) is not str or label.fullmatch(value) is None for value in (block, name)):
            raise _input_error("graph opcode selector needs block and operation labels")
        if type(opcode) is not str or opcode not in {
            "bool_and", "bool_or", "equal", "not_equal", "less_than", "less_equal",
            "greater_than", "greater_equal", "int_add_checked", "int_sub_checked",
            "int_mul_checked", "int_neg_checked", "result_ok",
        }:
            raise _input_error("unsupported graph opcode edit")
        return self._runtime._request("sleytools.program.propose", {
            "handle": self._runtime._handle,
            "change": {"field": f"operation.{block}.{name}.opcode", "value": opcode,
                       "cases": _graph_cases(cases, self.graph_parameters, self.graph_result)},
        }, timeout=20)

    def propose_graph_operand(self, block: str, name: str, slot: str, reference: str,
                              cases: list[Mapping[str, object]]) -> dict[str, Any]:
        if not self.graph_profile:
            raise _input_error("program is not a graph_v0 project")
        label = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
        if any(type(value) is not str or label.fullmatch(value) is None for value in (block, name)):
            raise _input_error("graph operand selector needs block and operation labels")
        selected = next((item for item in self.graph_operations
                         if item["block"] == block and item["name"] == name), None)
        if selected is None or slot not in selected.get("operands", {}):
            raise _input_error("graph operation has no editable operand at that slot")
        if (type(reference) is not str or not re.fullmatch(
                r"(?:param|op)\.[A-Za-z][A-Za-z0-9_]{0,63}", reference)):
            raise _input_error("graph operand reference must be param.NAME or op.NAME")
        return self._runtime._request("sleytools.program.propose", {
            "handle": self._runtime._handle,
            "change": {"field": f"operation.{block}.{name}.{slot}", "value": reference,
                       "cases": _graph_cases(cases, self.graph_parameters, self.graph_result)},
        }, timeout=20)

    def propose_graph_branch(self, block: str, targets: Mapping[str, str],
                             cases: list[Mapping[str, object]]) -> dict[str, Any]:
        if not self.graph_profile:
            raise _input_error("program is not a graph_v0 project")
        label = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
        if type(block) is not str or label.fullmatch(block) is None:
            raise _input_error("graph branch selector needs one block label")
        selected = next((item for item in self.graph_branches if item["block"] == block), None)
        if selected is None:
            raise _input_error(f"unknown graph branch: {block}")
        if not isinstance(targets, Mapping) or set(targets) != set(selected["targets"]):
            raise _input_error("graph branch targets have the wrong fields")
        if any(type(value) is not str or label.fullmatch(value) is None for value in targets.values()):
            raise _input_error("graph branch target must be a block label")
        return self._runtime._request("sleytools.program.propose", {
            "handle": self._runtime._handle,
            "change": {"field": f"branch.{block}.targets", "value": dict(targets),
                       "cases": _graph_cases(cases, self.graph_parameters, self.graph_result)},
        }, timeout=20)

    def validate(self, candidate_id: str) -> dict[str, Any]:
        response = self._runtime._request("sleytools.candidate.validate", {
            "handle": self._runtime._handle, "candidate_id": candidate_id,
        }, timeout=30)
        operation_id = response.get("operation_id")
        if not isinstance(operation_id, str) or re.fullmatch(r"[0-9a-f]{64}", operation_id) is None:
            raise SleyToolsError("ST_CORE_INCOMPATIBLE", "compatibility", "validation omitted durable operation ID")
        self._operations[candidate_id] = operation_id
        return response

    def commit(self, candidate_id: str) -> dict[str, Any]:
        try:
            response = self._runtime._request("sleytools.candidate.commit", {
                "handle": self._runtime._handle, "candidate_id": candidate_id,
            }, timeout=90)
        except SleyToolsError as error:
            if error.code in ("ST_TRANSPORT_FAILED", "ST_COMMIT_OUTCOME_UNKNOWN"):
                self._runtime._handle = None
                self._runtime._preserve_temporary = True
                operation_id = self._operations.get(candidate_id)
                project = self._runtime._project_path
                status = None
                if operation_id is not None:
                    try:
                        status = self._runtime.operation_status(operation_id)
                    except SleyToolsError:
                        pass
                if status is not None and status.get("state") == "completed":
                    core = status["core_reconciliation"]
                    raise SleyToolsError("ST_STALE_BASE", "stale_state",
                                         f"candidate {candidate_id} committed with receipt {core['receipt_id']}; reopen or repair project {project}; operation {operation_id}") from error
                raise SleyToolsError("ST_COMMIT_OUTCOME_UNKNOWN", "transport",
                                     f"candidate {candidate_id} outcome is unknown; query operation {operation_id} in preserved project {project} before retrying") from error
            raise
        self._runtime._handle = None
        new_root = response.get("new_state_root")
        committed = response.get("commit")
        if (response.get("contract") != "sley-tools.rpc-commit.v0"
                or not isinstance(committed, dict) or committed.get("candidate_id") != candidate_id
                or committed.get("operation_id") != self._operations.get(candidate_id)
                or not isinstance(new_root, str) or len(new_root) != 64
                or any(character not in "0123456789abcdef" for character in new_root)):
            self._runtime._preserve_temporary = True
            raise SleyToolsError("ST_COMMIT_OUTCOME_UNKNOWN", "transport",
                                 f"commit response for candidate {candidate_id} was inconsistent; inspect operation {self._operations.get(candidate_id)} in project {self._runtime._project_path}")
        self.state_root = new_root
        try:
            opened = self._runtime._request("sleytools.workspace.open", {}, timeout=5)
            self._runtime._handle = opened["handle"]
            inspection = self._runtime._request("sleytools.program.inspect", {"handle": opened["handle"]}, timeout=5)
            if inspection["accepted_state_root"] != new_root:
                raise SleyToolsError("ST_STALE_BASE", "stale_state", "accepted root advanced again after commit")
            self.__init__(self._runtime, inspection)
        except (KeyError, SleyToolsError) as error:
            self._runtime._preserve_temporary = True
            raise SleyToolsError("ST_COMMIT_OUTCOME_UNKNOWN", "transport",
                                 f"candidate {candidate_id} committed at {new_root}, but the SDK could not reopen project {self._runtime._project_path}; operation {self._operations.get(candidate_id)}") from error
        return committed

    def discard(self, candidate_id: str) -> dict[str, Any]:
        return self._runtime._request("sleytools.candidate.discard", {
            "handle": self._runtime._handle, "candidate_id": candidate_id,
        }, timeout=10)


def open_runtime(*, tools: str | Path, core: str | Path) -> Runtime:
    return Runtime(tools=tools, core=core)
