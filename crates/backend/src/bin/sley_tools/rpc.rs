//! Process-scoped JSON-RPC facade over one explicitly selected local project.

use std::fmt;
use std::fs::File;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Arc, Mutex, mpsc};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use serde::de::{Error as _, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use super::{
    CliFailure, TESTED_CORE_COMMIT, operation, rpc_transfer, starter, with_project_write_lock,
};

const MAX_REQUEST_BYTES: usize = 65_536;
const MAX_RESPONSE_BYTES: usize = 262_144;
const MAX_WORKER_RESPONSE_BYTES: usize = 65_536;
const MAX_DEADLINE_MS: u64 = 60_000;
const MAX_JSON_DEPTH: usize = 16;

struct StrictValue(Value);

pub(super) fn parse_strict_json(bytes: &[u8]) -> Result<Value, serde_json::Error> {
    serde_json::from_slice::<StrictValue>(bytes).map(|value| value.0)
}

impl<'de> Deserialize<'de> for StrictValue {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct StrictVisitor;
        impl<'de> Visitor<'de> for StrictVisitor {
            type Value = StrictValue;

            fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
                formatter.write_str("JSON without duplicate object fields")
            }

            fn visit_bool<E: serde::de::Error>(self, value: bool) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Bool(value)))
            }

            fn visit_i64<E: serde::de::Error>(self, value: i64) -> Result<Self::Value, E> {
                Ok(StrictValue(json!(value)))
            }

            fn visit_u64<E: serde::de::Error>(self, value: u64) -> Result<Self::Value, E> {
                Ok(StrictValue(json!(value)))
            }

            fn visit_f64<E: serde::de::Error>(self, value: f64) -> Result<Self::Value, E> {
                serde_json::Number::from_f64(value)
                    .map(Value::Number)
                    .map(StrictValue)
                    .ok_or_else(|| E::custom("invalid JSON number"))
            }

            fn visit_str<E: serde::de::Error>(self, value: &str) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::String(value.to_owned())))
            }

            fn visit_string<E: serde::de::Error>(self, value: String) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::String(value)))
            }

            fn visit_unit<E: serde::de::Error>(self) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Null))
            }

            fn visit_none<E: serde::de::Error>(self) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Null))
            }

            fn visit_seq<A: SeqAccess<'de>>(self, mut values: A) -> Result<Self::Value, A::Error> {
                let mut result = Vec::new();
                while let Some(value) = values.next_element::<StrictValue>()? {
                    result.push(value.0);
                }
                Ok(StrictValue(Value::Array(result)))
            }

            fn visit_map<A: MapAccess<'de>>(self, mut fields: A) -> Result<Self::Value, A::Error> {
                let mut result = serde_json::Map::new();
                while let Some((name, value)) = fields.next_entry::<String, StrictValue>()? {
                    if result.insert(name.clone(), value.0).is_some() {
                        return Err(A::Error::custom(format!("duplicate JSON field: {name}")));
                    }
                }
                Ok(StrictValue(Value::Object(result)))
            }
        }
        deserializer.deserialize_any(StrictVisitor)
    }
}

fn too_deep(value: &Value, depth: usize) -> bool {
    if depth > MAX_JSON_DEPTH {
        return true;
    }
    match value {
        Value::Array(items) => items.iter().any(|item| too_deep(item, depth + 1)),
        Value::Object(fields) => fields.values().any(|item| too_deep(item, depth + 1)),
        _ => false,
    }
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn random_handle() -> Result<String, CliFailure> {
    let mut bytes = [0_u8; 32];
    File::open("/dev/urandom")
        .and_then(|mut source| source.read_exact(&mut bytes))
        .map_err(|error| CliFailure::failed(format!("cannot create opaque handle: {error}")))?;
    Ok(hex(&bytes))
}

pub(super) fn new_random_handle() -> Result<String, CliFailure> {
    random_handle()
}

fn exact_object<'a>(
    value: &'a Value,
    required: &[&str],
    optional: &[&str],
) -> Result<&'a serde_json::Map<String, Value>, CliFailure> {
    let object = value
        .as_object()
        .ok_or_else(|| CliFailure::invalid("parameters must be a JSON object"))?;
    if required.iter().any(|name| !object.contains_key(*name))
        || object
            .keys()
            .any(|name| !required.contains(&name.as_str()) && !optional.contains(&name.as_str()))
    {
        return Err(CliFailure::invalid(
            "parameters have missing or unknown fields",
        ));
    }
    Ok(object)
}

fn boolean(value: &Value) -> Result<bool, CliFailure> {
    exact_object(value, &["kind", "value"], &[])?;
    if value["kind"] != "boolean" {
        return Err(CliFailure::invalid("expected tagged Boolean value"));
    }
    value["value"]
        .as_bool()
        .ok_or_else(|| CliFailure::invalid("Boolean value must be true or false"))
}

fn u32_value(value: &Value) -> Result<u32, CliFailure> {
    exact_object(value, &["kind", "type", "value"], &[])?;
    if value["kind"] != "integer" || value["type"] != "u32" {
        return Err(CliFailure::invalid("expected tagged u32 integer"));
    }
    let decimal = value["value"]
        .as_str()
        .ok_or_else(|| CliFailure::invalid("u32 value must be decimal text"))?;
    let parsed = decimal
        .parse::<u32>()
        .map_err(|_| CliFailure::invalid("u32 value is outside its domain"))?;
    if parsed.to_string() != decimal {
        return Err(CliFailure::invalid(
            "u32 value is not canonical decimal text",
        ));
    }
    Ok(parsed)
}

fn candidate_id(value: &Value) -> Result<&str, CliFailure> {
    let selected = value
        .as_str()
        .ok_or_else(|| CliFailure::invalid("candidate_id must be text"))?;
    if selected.len() != 64
        || !selected
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(CliFailure::invalid(
            "candidate_id must be 32 bytes of lowercase hex",
        ));
    }
    Ok(selected)
}

fn facts(value: &Value) -> Result<(bool, u32, bool, u32), CliFailure> {
    exact_object(value, &["kind", "fields"], &[])?;
    if value["kind"] != "record" {
        return Err(CliFailure::invalid("input must be a tagged record"));
    }
    let fields = &value["fields"];
    exact_object(
        fields,
        &[
            "test_run_completed",
            "tests_failed",
            "artifact_verified",
            "blocking_findings",
        ],
        &[],
    )?;
    Ok((
        boolean(&fields["test_run_completed"])?,
        u32_value(&fields["tests_failed"])?,
        boolean(&fields["artifact_verified"])?,
        u32_value(&fields["blocking_findings"])?,
    ))
}

fn i64_value(value: &Value) -> Result<i64, CliFailure> {
    exact_object(value, &["kind", "type", "value"], &[])?;
    if value["kind"] != "integer" || value["type"] != "i64" {
        return Err(CliFailure::invalid("expected tagged i64 integer"));
    }
    let decimal = value["value"]
        .as_str()
        .ok_or_else(|| CliFailure::invalid("i64 value must be decimal text"))?;
    let parsed = decimal
        .parse::<i64>()
        .map_err(|_| CliFailure::invalid("i64 value is outside its domain"))?;
    if parsed.to_string() != decimal || parsed < 0 {
        return Err(CliFailure::invalid(
            "Resource Admission requires canonical nonnegative i64 values",
        ));
    }
    Ok(parsed)
}

fn resource_facts(value: &Value) -> Result<[i64; 4], CliFailure> {
    exact_object(value, &["kind", "fields"], &[])?;
    if value["kind"] != "record" {
        return Err(CliFailure::invalid("input must be a tagged record"));
    }
    let fields = &value["fields"];
    exact_object(
        fields,
        &[
            "requested_workers",
            "available_workers",
            "requested_memory_mib",
            "available_memory_mib",
        ],
        &[],
    )?;
    Ok([
        i64_value(&fields["requested_workers"])?,
        i64_value(&fields["available_workers"])?,
        i64_value(&fields["requested_memory_mib"])?,
        i64_value(&fields["available_memory_mib"])?,
    ])
}

fn graph_value(value: &Value, kind: &str) -> Result<Value, CliFailure> {
    match kind {
        "Bool" => Ok(json!(boolean(value)?)),
        "UInt32" => Ok(json!(u32_value(value)?.to_string())),
        "SInt64" => {
            exact_object(value, &["kind", "type", "value"], &[])?;
            if value["kind"] != "integer" || value["type"] != "i64" {
                return Err(CliFailure::invalid("expected tagged i64 integer"));
            }
            let decimal = value["value"]
                .as_str()
                .ok_or_else(|| CliFailure::invalid("i64 value must be decimal text"))?;
            let parsed = decimal
                .parse::<i64>()
                .map_err(|_| CliFailure::invalid("i64 value is outside its domain"))?;
            if parsed.to_string() != decimal {
                return Err(CliFailure::invalid(
                    "i64 value is not canonical decimal text",
                ));
            }
            Ok(json!(decimal))
        }
        _ => Err(CliFailure::feature("graph parameter type is unavailable")),
    }
}

fn graph_arguments(input: &Value, inspection: &Value) -> Result<Vec<Value>, CliFailure> {
    exact_object(input, &["kind", "values"], &[])?;
    if input["kind"] != "arguments" {
        return Err(CliFailure::invalid("graph input must be tagged arguments"));
    }
    let values = input["values"]
        .as_array()
        .ok_or_else(|| CliFailure::invalid("graph argument values must be an array"))?;
    let parameters = inspection["authoring"]["parameters"]
        .as_array()
        .ok_or_else(|| CliFailure::failed("graph inspection has no parameters"))?;
    if values.len() != parameters.len() {
        return Err(CliFailure::invalid(
            "graph argument count differs from entrypoint",
        ));
    }
    values
        .iter()
        .zip(parameters)
        .map(|(value, parameter)| {
            let kind = parameter["value_type"]
                .as_str()
                .ok_or_else(|| CliFailure::failed("graph parameter has no type"))?;
            graph_value(value, kind)
        })
        .collect()
}

fn tag_graph_result(value: &Value, kind: &str) -> Result<Value, CliFailure> {
    match kind {
        "CheckedSInt64" => {
            let result = value
                .as_object()
                .ok_or_else(|| CliFailure::execution("native checked result is not an object"))?;
            if result.len() != 1 {
                return Err(CliFailure::execution(
                    "native checked result has wrong arms",
                ));
            }
            if let Some(ok) = result.get("ok") {
                return Ok(json!({"kind": "result", "arm": "ok",
                    "value": tag_graph_result(ok, "SInt64")?}));
            }
            if result
                .get("error")
                .and_then(Value::as_object)
                .is_some_and(|failure| {
                    failure.len() == 1 && failure.get("code").and_then(Value::as_u64) == Some(1)
                })
            {
                return Ok(json!({"kind": "result", "arm": "error",
                    "value": {"kind": "builtin_failure", "failure": "arithmetic", "code": 1}}));
            }
            Err(CliFailure::execution(
                "native checked result has an unavailable arithmetic failure",
            ))
        }
        "Bool" => value
            .as_bool()
            .map(|bit| json!({"kind": "boolean", "value": bit}))
            .ok_or_else(|| CliFailure::execution("native graph result is not Boolean")),
        "UInt32" | "SInt64" => {
            let decimal = value
                .as_str()
                .ok_or_else(|| CliFailure::execution("native graph integer is not decimal text"))?;
            let canonical = if kind == "UInt32" {
                decimal
                    .parse::<u32>()
                    .ok()
                    .is_some_and(|number| number.to_string() == decimal)
            } else {
                decimal
                    .parse::<i64>()
                    .ok()
                    .is_some_and(|number| number.to_string() == decimal)
            };
            if !canonical {
                return Err(CliFailure::execution(
                    "native graph integer is not canonical",
                ));
            }
            Ok(
                json!({"kind": "integer", "type": if kind == "UInt32" { "u32" } else { "i64" }, "value": decimal}),
            )
        }
        _ => Err(CliFailure::feature("graph result type is unavailable")),
    }
}

fn tagged_graph_output(observed: &Value, inspection: &Value) -> Result<Value, CliFailure> {
    let result = &inspection["authoring"]["result"];
    if result["kind"] == "primitive" {
        let kind = result["value_type"]
            .as_str()
            .ok_or_else(|| CliFailure::failed("graph primitive result has no type"))?;
        return tag_graph_result(&observed["result"], kind);
    }
    if result["kind"] != "record" {
        return Err(CliFailure::failed("graph result shape is unavailable"));
    }
    let definitions = result["fields"]
        .as_array()
        .ok_or_else(|| CliFailure::failed("graph result has no fields"))?;
    let values = observed["result"]
        .as_object()
        .ok_or_else(|| CliFailure::execution("native graph result is not a record"))?;
    if values.len() != definitions.len() {
        return Err(CliFailure::execution(
            "native graph result field count differs",
        ));
    }
    let mut fields = serde_json::Map::new();
    for definition in definitions {
        let name = definition["name"]
            .as_str()
            .ok_or_else(|| CliFailure::failed("graph result field has no name"))?;
        let kind = definition["value_type"]
            .as_str()
            .ok_or_else(|| CliFailure::failed("graph result field has no type"))?;
        let value = values
            .get(name)
            .ok_or_else(|| CliFailure::execution("native graph result field is absent"))?;
        fields.insert(name.to_owned(), tag_graph_result(value, kind)?);
    }
    Ok(
        json!({"kind": "record", "type_id": inspection["program"]["result_type_id"], "fields": fields}),
    )
}

pub(super) enum NativeInput {
    Release((bool, u32, bool, u32)),
    Resource([i64; 4]),
    Graph(Vec<Value>),
}

fn spawn_stdout_reader(
    stdout: std::process::ChildStdout,
) -> (mpsc::Receiver<Result<Value, String>>, JoinHandle<()>) {
    let (sender, receiver) = mpsc::channel();
    let handle = thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        loop {
            let mut line = Vec::new();
            let count = match (&mut reader)
                .take((MAX_WORKER_RESPONSE_BYTES + 1) as u64)
                .read_until(b'\n', &mut line)
            {
                Ok(count) => count,
                Err(error) => {
                    let _ = sender.send(Err(format!("cannot read native worker: {error}")));
                    return;
                }
            };
            if count == 0 {
                let _ = sender.send(Err("native worker closed its response stream".to_owned()));
                return;
            }
            if count > MAX_WORKER_RESPONSE_BYTES || !line.ends_with(b"\n") {
                let _ = sender.send(Err("native worker response exceeds 64 KiB".to_owned()));
                return;
            }
            match serde_json::from_slice(&line) {
                Ok(response) => {
                    if sender.send(Ok(response)).is_err() {
                        return;
                    }
                }
                Err(error) => {
                    let _ =
                        sender.send(Err(format!("native worker returned invalid JSON: {error}")));
                    return;
                }
            }
        }
    });
    (receiver, handle)
}

fn spawn_stderr_reader(stderr: std::process::ChildStderr) -> (Arc<Mutex<Vec<u8>>>, JoinHandle<()>) {
    let captured = Arc::new(Mutex::new(Vec::new()));
    let shared = Arc::clone(&captured);
    let handle = thread::spawn(move || {
        let mut reader = BufReader::new(stderr);
        let mut buffer = [0_u8; 1_024];
        loop {
            let Ok(count) = reader.read(&mut buffer) else {
                return;
            };
            if count == 0 {
                return;
            }
            if let Ok(mut bytes) = shared.lock() {
                let room = 8_192_usize.saturating_sub(bytes.len());
                bytes.extend_from_slice(&buffer[..count.min(room)]);
            }
        }
    });
    (captured, handle)
}

pub(super) struct NativeWorker {
    child: Child,
    stdin: ChildStdin,
    receiver: mpsc::Receiver<Result<Value, String>>,
    stdout_reader: Option<JoinHandle<()>>,
    stderr_reader: Option<JoinHandle<()>>,
    stderr: Arc<Mutex<Vec<u8>>>,
    next_id: u64,
    usable: bool,
}

impl NativeWorker {
    pub(super) fn start(
        project: &Path,
        root: &str,
        function: &str,
        program: &str,
        profile: &str,
        recipe_sha: &str,
    ) -> Result<Self, CliFailure> {
        let current = std::env::current_exe()
            .map_err(|error| CliFailure::failed(format!("cannot locate tools binary: {error}")))?;
        let builder = current.with_file_name("sley-tools-native-builder");
        let mode = if profile == "graph_v0" {
            "serve-graph"
        } else if program == "resource-admission" {
            "serve-resource"
        } else {
            "serve"
        };
        let mut command = Command::new(builder);
        command
            .arg(mode)
            .arg(project.join("workspace"))
            .arg(root)
            .arg(function);
        if profile == "graph_v0" {
            command
                .arg(project.join("build/graph.json"))
                .arg(recipe_sha);
        }
        let mut child = command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|error| CliFailure::failed(format!("cannot start native worker: {error}")))?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| CliFailure::failed("native worker has no input pipe"))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| CliFailure::failed("native worker has no output pipe"))?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| CliFailure::failed("native worker has no error pipe"))?;
        let (receiver, stdout_reader) = spawn_stdout_reader(stdout);
        let (captured, stderr_reader) = spawn_stderr_reader(stderr);
        Ok(Self {
            child,
            stdin,
            receiver,
            stdout_reader: Some(stdout_reader),
            stderr_reader: Some(stderr_reader),
            stderr: captured,
            next_id: 1,
            usable: true,
        })
    }

    fn pid(&self) -> u32 {
        self.child.id()
    }

    fn alive(&mut self) -> bool {
        self.usable && matches!(self.child.try_wait(), Ok(None))
    }

    fn stop(&mut self) {
        self.usable = false;
        let _ = self.child.kill();
        let _ = self.child.wait();
    }

    fn stderr_excerpt(&self) -> String {
        self.stderr
            .lock()
            .map(|bytes| String::from_utf8_lossy(&bytes).chars().take(300).collect())
            .unwrap_or_default()
    }

    pub(super) fn call(
        &mut self,
        input: NativeInput,
        deadline: Duration,
    ) -> Result<Value, CliFailure> {
        if !self.usable {
            return Err(CliFailure::failed(
                "native worker is unavailable; reopen the runtime",
            ));
        }
        let id = self.next_id;
        self.next_id = self
            .next_id
            .checked_add(1)
            .ok_or_else(|| CliFailure::resource("native worker request ID exhausted"))?;
        let request = match input {
            NativeInput::Release(input) => json!({
                "id": id, "completed": input.0, "failed": input.1.to_string(),
                "verified": input.2, "blocking": input.3.to_string(),
            }),
            NativeInput::Resource(input) => json!({
                "id": id,
                "requested_workers": input[0].to_string(), "available_workers": input[1].to_string(),
                "requested_memory_mib": input[2].to_string(), "available_memory_mib": input[3].to_string(),
            }),
            NativeInput::Graph(input) => json!({"id": id, "input": input}),
        };
        let mut encoded = serde_json::to_vec(&request).map_err(|error| {
            CliFailure::failed(format!("cannot encode native request: {error}"))
        })?;
        encoded.push(b'\n');
        if encoded.len() > 4_096 {
            return Err(CliFailure::resource("native worker request exceeds 4 KiB"));
        }
        if let Err(error) = self
            .stdin
            .write_all(&encoded)
            .and_then(|()| self.stdin.flush())
        {
            self.stop();
            return Err(CliFailure::failed(format!(
                "cannot write native request: {error}"
            )));
        }
        let response = match self.receiver.recv_timeout(deadline) {
            Ok(Ok(response)) => response,
            Ok(Err(message)) => {
                self.stop();
                return Err(CliFailure::failed(format!(
                    "{message}; stderr: {}",
                    self.stderr_excerpt()
                )));
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                self.stop();
                return Err(CliFailure::resource(
                    "native execution exceeded its deadline; worker stopped",
                ));
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                self.stop();
                return Err(CliFailure::failed(format!(
                    "native worker disconnected; stderr: {}",
                    self.stderr_excerpt()
                )));
            }
        };
        if response["id"] != id {
            self.stop();
            return Err(CliFailure::failed(
                "native worker response ID differs from request",
            ));
        }
        if response["ok"] != true {
            if response["code"] == "NATIVE_STALE_ROOT" {
                return Err(CliFailure::stale(
                    "accepted root changed during native execution",
                ));
            }
            return Err(CliFailure::execution(format!(
                "native execution failed: {}",
                response["message"]
                    .as_str()
                    .unwrap_or("unknown native error")
            )));
        }
        Ok(response["result"].clone())
    }
}

impl Drop for NativeWorker {
    fn drop(&mut self) {
        self.stop();
        if let Some(handle) = self.stdout_reader.take() {
            let _ = handle.join();
        }
        if let Some(handle) = self.stderr_reader.take() {
            let _ = handle.join();
        }
    }
}

struct RpcState {
    project: PathBuf,
    inspection: Value,
    handle: String,
    worker: Option<NativeWorker>,
    graph_snapshot: Option<GraphSnapshot>,
    transfer: Option<rpc_transfer::Scope>,
}

#[derive(PartialEq)]
struct GraphSnapshot([String; 5]);

impl GraphSnapshot {
    fn capture(project: &Path) -> Result<Self, CliFailure> {
        let paths = [
            "program.json",
            ".sley-tools/state.json",
            ".sley-tools/status.json",
            "config.local.json",
            "build/graph.json",
        ];
        let mut hashes = Vec::with_capacity(paths.len());
        for relative in paths {
            let path = project.join(relative);
            let metadata = std::fs::symlink_metadata(&path).map_err(|error| {
                CliFailure::stale(format!(
                    "cannot inspect graph project binding {}: {error}",
                    path.display()
                ))
            })?;
            let limit = if relative == "build/graph.json" {
                65_536
            } else {
                1_048_576
            };
            if !metadata.file_type().is_file() || metadata.len() > limit {
                return Err(CliFailure::stale(format!(
                    "graph project binding {} is not a bounded regular file",
                    path.display()
                )));
            }
            let bytes = std::fs::read(&path).map_err(|error| {
                CliFailure::stale(format!(
                    "cannot read graph project binding {}: {error}",
                    path.display()
                ))
            })?;
            hashes.push(format!("{:x}", Sha256::digest(bytes)));
        }
        Ok(Self(hashes.try_into().expect("five graph binding files")))
    }
}

impl RpcState {
    fn new(project: &Path, workspace: Option<&Path>) -> Result<Self, CliFailure> {
        let project = project.canonicalize().map_err(|error| {
            CliFailure::invalid(format!("project path is unavailable: {error}"))
        })?;
        let inspection = match starter::inspect(&project) {
            Ok(value) => value,
            Err(_) if project.join(".sley-tools/operations").is_dir() => Value::Null,
            Err(error) => return Err(error),
        };
        let graph_snapshot = if inspection["program"]["authoring_profile"] == "graph_v0" {
            Some(GraphSnapshot::capture(&project)?)
        } else {
            None
        };
        let transfer = workspace
            .map(|path| rpc_transfer::Scope::new(&project, path, &inspection))
            .transpose()?;
        Ok(Self {
            project,
            inspection,
            handle: random_handle()?,
            worker: None,
            graph_snapshot,
            transfer,
        })
    }

    fn root(&self) -> Result<&str, CliFailure> {
        self.inspection["accepted_state_root"]
            .as_str()
            .ok_or_else(|| CliFailure::failed("inspection has no accepted root"))
    }

    fn function(&self) -> Result<&str, CliFailure> {
        self.inspection["program"]["entrypoint"]["function_id"]
            .as_str()
            .ok_or_else(|| CliFailure::failed("inspection has no function identity"))
    }

    fn program_id(&self) -> Result<&str, CliFailure> {
        self.inspection["program"]["program_id"]
            .as_str()
            .ok_or_else(|| CliFailure::failed("inspection has no program identity"))
    }

    fn require_handle(&self, params: &Value) -> Result<(), CliFailure> {
        if params["handle"] != self.handle || self.worker.is_none() {
            return Err(CliFailure::invalid("workspace handle is absent or expired"));
        }
        Ok(())
    }

    fn require_fresh_handle(&self, params: &Value) -> Result<(), CliFailure> {
        self.require_handle(params)?;
        let latest = starter::inspect(&self.project)?;
        if latest["accepted_state_root"] != self.inspection["accepted_state_root"] {
            return Err(CliFailure::stale(
                "accepted root changed during RPC session",
            ));
        }
        Ok(())
    }

    fn dispatch(&mut self, method: &str, params: &Value) -> Result<Value, CliFailure> {
        if method == "sleytools.operation.status" {
            exact_object(params, &["operation_id"], &[])?;
            let id = candidate_id(&params["operation_id"])?;
            return operation::status(&self.project, id);
        }
        if method == "sleytools.operation.cancel" {
            exact_object(params, &["operation_id"], &[])?;
            let id = candidate_id(&params["operation_id"])?;
            return with_project_write_lock(&self.project, || operation::cancel(&self.project, id));
        }
        if self.inspection.is_null() {
            return Err(CliFailure::stale(
                "local project metadata differs from the accepted core head; only operation.status is available",
            ));
        }
        match method {
            "sleytools.hello" => {
                exact_object(params, &[], &[])?;
                let mut methods = vec![
                    "sleytools.hello",
                    "sleytools.workspace.open",
                    "sleytools.program.inspect",
                    "sleytools.program.propose",
                    "sleytools.candidate.validate",
                    "sleytools.candidate.commit",
                    "sleytools.candidate.discard",
                    "sleytools.program.execute",
                    "sleytools.tests.run",
                    "sleytools.operation.status",
                    "sleytools.operation.cancel",
                ];
                if self.transfer.is_some() {
                    methods.extend([
                        "sleytools.artifact.export",
                        "sleytools.artifact.import",
                        "sleytools.review.compare",
                    ]);
                }
                Ok(json!({
                    "contract": "sley-tools.rpc.v0",
                    "tools_contract": "sley-tools.cli.v0",
                    "core": self.inspection["program"]["core"],
                    "methods": methods,
                    "limits": {"max_request_bytes": MAX_REQUEST_BYTES, "max_response_bytes": MAX_RESPONSE_BYTES, "max_inflight": 1, "max_deadline_ms": MAX_DEADLINE_MS},
                    "native_core_test_admission": "PENDING_CORE_EXECUTOR",
                    "selected_core_commit": TESTED_CORE_COMMIT,
                }))
            }
            "sleytools.workspace.open" => {
                exact_object(params, &[], &[])?;
                let latest = starter::inspect(&self.project)?;
                if latest["accepted_state_root"] != self.inspection["accepted_state_root"] {
                    return Err(CliFailure::stale(
                        "accepted root changed during RPC session",
                    ));
                }
                if self.worker.as_mut().is_some_and(|worker| !worker.alive()) {
                    self.worker.take();
                    self.handle = random_handle()?;
                }
                if self.worker.is_none() {
                    self.worker = Some(NativeWorker::start(
                        &self.project,
                        self.root()?,
                        self.function()?,
                        self.program_id()?,
                        self.inspection["program"]["authoring_profile"]
                            .as_str()
                            .unwrap_or(""),
                        self.inspection["authoring"]["recipe_sha256"]
                            .as_str()
                            .unwrap_or(""),
                    )?);
                }
                Ok(json!({
                    "handle": self.handle,
                    "program_id": self.inspection["program"]["program_id"],
                    "entrypoint": "evaluate",
                    "state_root": self.root()?,
                    "worker_pid": self.worker.as_ref().map(NativeWorker::pid),
                }))
            }
            "sleytools.program.inspect" => {
                exact_object(params, &["handle"], &[])?;
                self.require_handle(params)?;
                let latest = starter::inspect(&self.project)?;
                if latest["accepted_state_root"] != self.inspection["accepted_state_root"] {
                    return Err(CliFailure::stale(
                        "accepted root changed during RPC session",
                    ));
                }
                Ok(latest)
            }
            "sleytools.program.propose" => {
                exact_object(params, &["handle", "change"], &[])?;
                let change = &params["change"];
                if self.inspection["program"]["authoring_profile"] == "graph_v0" {
                    exact_object(change, &["field", "value", "cases"], &[])?;
                    let field = change["field"]
                        .as_str()
                        .ok_or_else(|| CliFailure::invalid("graph edit field must be text"))?;
                    if let Some(name) = field
                        .strip_prefix("constant.")
                        .filter(|name| !name.is_empty())
                    {
                        let constant = self.inspection["authoring"]["constants"]
                            .as_array()
                            .and_then(|constants| {
                                constants.iter().find(|item| item["name"] == name)
                            })
                            .ok_or_else(|| CliFailure::invalid("graph constant is absent"))?;
                        let kind = constant["value_type"]
                            .as_str()
                            .ok_or_else(|| CliFailure::failed("graph constant has no type"))?;
                        let value = graph_value(&change["value"], kind)?;
                        return with_project_write_lock(&self.project, || {
                            self.require_fresh_handle(params)?;
                            starter::propose_graph_constant_values(
                                &self.project,
                                name,
                                &value,
                                &change["cases"],
                            )
                        });
                    }
                    if let Some(selector) = field
                        .strip_prefix("operation.")
                        .and_then(|suffix| suffix.strip_suffix(".opcode"))
                    {
                        let (block, name) = selector
                            .split_once('.')
                            .filter(|(block, name)| {
                                !block.is_empty() && !name.is_empty() && !name.contains('.')
                            })
                            .ok_or_else(|| {
                                CliFailure::invalid(
                                    "graph opcode selector needs block and operation",
                                )
                            })?;
                        let opcode = change["value"]
                            .as_str()
                            .ok_or_else(|| CliFailure::invalid("graph opcode must be text"))?;
                        return with_project_write_lock(&self.project, || {
                            self.require_fresh_handle(params)?;
                            starter::propose_graph_opcode_values(
                                &self.project,
                                block,
                                name,
                                opcode,
                                &change["cases"],
                            )
                        });
                    }
                    if let Some(selector) = field.strip_prefix("operation.") {
                        let (target, slot) = selector.rsplit_once('.').ok_or_else(|| {
                            CliFailure::invalid("graph operand selector needs a slot")
                        })?;
                        let (block, name) = target
                            .split_once('.')
                            .filter(|(block, name)| {
                                !block.is_empty() && !name.is_empty() && !name.contains('.')
                            })
                            .ok_or_else(|| {
                                CliFailure::invalid(
                                    "graph operand selector needs block and operation",
                                )
                            })?;
                        let reference = change["value"].as_str().ok_or_else(|| {
                            CliFailure::invalid("graph operand reference must be text")
                        })?;
                        return with_project_write_lock(&self.project, || {
                            self.require_fresh_handle(params)?;
                            starter::propose_graph_operand_values(
                                &self.project,
                                block,
                                name,
                                slot,
                                reference,
                                &change["cases"],
                            )
                        });
                    }
                    if let Some(block) = field
                        .strip_prefix("branch.")
                        .and_then(|suffix| suffix.strip_suffix(".targets"))
                    {
                        if block.is_empty() || block.contains('.') {
                            return Err(CliFailure::invalid(
                                "graph branch selector needs one block",
                            ));
                        }
                        return with_project_write_lock(&self.project, || {
                            self.require_fresh_handle(params)?;
                            starter::propose_graph_branch_values(
                                &self.project,
                                block,
                                &change["value"],
                                &change["cases"],
                            )
                        });
                    }
                    return Err(CliFailure::invalid(
                        "graph edit must select constant.NAME, operation.BLOCK.NAME.opcode, operation.BLOCK.NAME.SLOT, or branch.BLOCK.targets",
                    ));
                }
                exact_object(change, &["field", "value"], &[])?;
                match (self.program_id()?, change["field"].as_str()) {
                    ("release-readiness", Some("max_blocking_findings")) => {
                        let threshold = u32_value(&change["value"])?;
                        with_project_write_lock(&self.project, || {
                            self.require_fresh_handle(params)?;
                            starter::propose_release(&self.project, threshold)
                        })
                    }
                    ("resource-admission", Some("require_memory_spare")) => {
                        let require_spare = boolean(&change["value"])?;
                        with_project_write_lock(&self.project, || {
                            self.require_fresh_handle(params)?;
                            starter::propose_resource(&self.project, require_spare)
                        })
                    }
                    _ => Err(CliFailure::invalid(
                        "change field is unavailable for this program profile",
                    )),
                }
            }
            "sleytools.candidate.validate" => {
                exact_object(params, &["handle", "candidate_id"], &[])?;
                let selected = candidate_id(&params["candidate_id"])?;
                with_project_write_lock(&self.project, || {
                    self.require_fresh_handle(params)?;
                    let mut result = starter::validate_proposal(&self.project, Some(selected))?;
                    result["operation_id"] = json!(operation::prepare(&self.project, selected)?);
                    Ok(result)
                })
            }
            "sleytools.candidate.commit" => {
                exact_object(params, &["handle", "candidate_id"], &[])?;
                let selected = candidate_id(&params["candidate_id"])?;
                let next_handle = random_handle()?;
                let result = with_project_write_lock(&self.project, || {
                    self.require_fresh_handle(params)?;
                    starter::preflight_commit_proposal(&self.project, Some(selected))?;
                    let id = operation::begin_commit(&self.project, selected)?;
                    let mut committed =
                        starter::commit_proposal_observed(&self.project, Some(selected))?;
                    operation::finish_commit(&self.project, &id)?;
                    committed["operation_id"] = json!(id);
                    Ok(committed)
                });
                let mut committed = match result {
                    Ok(committed) => committed,
                    Err(error) => {
                        if ![
                            "ST_INPUT_INVALID",
                            "ST_STALE_BASE",
                            "ST_VALIDATION_FAILED",
                            "ST_PERMISSION_DENIED",
                            "ST_RESOURCE_LIMIT",
                            "ST_CORE_INCOMPATIBLE",
                        ]
                        .contains(&error.code)
                        {
                            self.worker.take();
                            self.handle = next_handle;
                        }
                        return Err(error);
                    }
                };
                self.worker.take();
                self.handle = next_handle;
                self.inspection = starter::inspect(&self.project).map_err(|error| {
                    CliFailure::outcome_unknown(format!(
                        "candidate {} committed but RPC could not refresh the project: {}",
                        selected, error.message
                    ))
                })?;
                self.graph_snapshot =
                    if self.inspection["program"]["authoring_profile"] == "graph_v0" {
                        Some(GraphSnapshot::capture(&self.project).map_err(|error| {
                        CliFailure::outcome_unknown(format!(
                            "candidate {} committed but RPC could not refresh graph bindings: {}",
                            selected, error.message
                        ))
                    })?)
                    } else {
                        None
                    };
                let assertions = if committed["verification_error"].is_string() {
                    "FAIL"
                } else {
                    "PASS"
                };
                committed["postcommit_assertions_status"] = json!(assertions);
                Ok(
                    json!({"contract": "sley-tools.rpc-commit.v0", "commit": committed,
                    "new_state_root": self.inspection["accepted_state_root"], "reopen_required": true,
                    "external_assertions": assertions}),
                )
            }
            "sleytools.candidate.discard" => {
                exact_object(params, &["handle", "candidate_id"], &[])?;
                let selected = candidate_id(&params["candidate_id"])?;
                with_project_write_lock(&self.project, || {
                    self.require_fresh_handle(params)?;
                    starter::discard_proposal(&self.project, Some(selected))
                })
            }
            "sleytools.program.execute" => {
                exact_object(params, &["handle", "entrypoint", "input"], &["deadline_ms"])?;
                self.require_handle(params)?;
                if params["entrypoint"] != "evaluate" {
                    return Err(CliFailure::invalid("entrypoint is not evaluate"));
                }
                let graph_profile = self.inspection["program"]["authoring_profile"] == "graph_v0";
                let values = if graph_profile {
                    if self.graph_snapshot.as_ref() != Some(&GraphSnapshot::capture(&self.project)?)
                    {
                        return Err(CliFailure::stale(
                            "graph project changed during RPC session",
                        ));
                    }
                    NativeInput::Graph(graph_arguments(&params["input"], &self.inspection)?)
                } else {
                    match self.program_id()? {
                        "release-readiness" => NativeInput::Release(facts(&params["input"])?),
                        "resource-admission" => {
                            NativeInput::Resource(resource_facts(&params["input"])?)
                        }
                        _ => return Err(CliFailure::feature("RPC program profile is unavailable")),
                    }
                };
                let deadline_ms = if params.get("deadline_ms").is_some() {
                    params["deadline_ms"]
                        .as_u64()
                        .filter(|value| (1..=MAX_DEADLINE_MS).contains(value))
                        .ok_or_else(|| CliFailure::invalid("deadline_ms must be 1..60000"))?
                } else {
                    10_000
                };
                let started = Instant::now();
                let worker = self
                    .worker
                    .as_mut()
                    .ok_or_else(|| CliFailure::failed("native worker is absent"))?;
                let worker_pid = worker.pid();
                let observed = worker.call(values, Duration::from_millis(deadline_ms))?;
                if observed["root"] != self.root()? || observed["function_id"] != self.function()? {
                    return Err(CliFailure::execution(
                        "native result is not bound to the selected root and function",
                    ));
                }
                let tagged_value = if graph_profile {
                    tagged_graph_output(&observed, &self.inspection)?
                } else {
                    let definitions = self.inspection["program"]["result_fields"]
                        .as_array()
                        .ok_or_else(|| CliFailure::execution("manifest has no result fields"))?;
                    if observed["result"].as_object().map(serde_json::Map::len)
                        != Some(definitions.len())
                    {
                        return Err(CliFailure::execution(
                            "native result field count differs from manifest",
                        ));
                    }
                    let mut fields = serde_json::Map::new();
                    for definition in definitions {
                        let name = definition["name"].as_str().ok_or_else(|| {
                            CliFailure::execution("manifest result field has no name")
                        })?;
                        let value = &observed["result"][name];
                        let tagged = if definition["type"] == "SInt64" {
                            let decimal = value.as_str().ok_or_else(|| {
                                CliFailure::execution("native signed result is not decimal text")
                            })?;
                            if decimal
                                .parse::<i64>()
                                .map(|number| number.to_string() != decimal)
                                .unwrap_or(true)
                            {
                                return Err(CliFailure::execution(
                                    "native signed result is outside canonical i64",
                                ));
                            }
                            json!({"kind": "integer", "type": "i64", "value": decimal})
                        } else {
                            let flag = value.as_bool().ok_or_else(|| {
                                CliFailure::execution("native result has an invalid Boolean field")
                            })?;
                            json!({"kind": "boolean", "value": flag})
                        };
                        fields.insert(name.to_owned(), tagged);
                    }
                    json!({"kind": "record", "type_id": self.inspection["program"]["result_type_id"], "fields": fields})
                };
                Ok(json!({
                    "value": tagged_value,
                    "metadata": {
                        "state_root": observed["root"],
                        "function_id": observed["function_id"],
                        "observation_id": observed["observation_id"],
                        "value_hash": observed["value_hash"],
                        "instruction_count": observed["instruction_count"],
                        "profile": observed["profile"],
                        "worker_pid": worker_pid,
                        "end_to_end_ms": started.elapsed().as_millis(),
                        "measurement_scope": "RPC dispatch through native worker result; no separate VM subdivision recorded"
                    }
                }))
            }
            "sleytools.tests.run" => {
                exact_object(params, &["handle"], &[])?;
                self.require_handle(params)?;
                starter::test(&self.project)
            }
            "sleytools.artifact.export" => {
                exact_object(params, &["handle", "package"], &[])?;
                let package = params["package"]
                    .as_str()
                    .ok_or_else(|| CliFailure::invalid("package must be a direct-child name"))?;
                let scope = self.transfer.as_ref().ok_or_else(|| {
                    CliFailure::feature("RPC package operations require --workspace at startup")
                })?;
                with_project_write_lock(&self.project, || {
                    self.require_fresh_handle(params)?;
                    scope.export(&self.project, &self.inspection, package)
                })
            }
            "sleytools.artifact.import" => {
                exact_object(params, &["handle", "package", "destination"], &[])?;
                self.require_fresh_handle(params)?;
                let scope = self.transfer.as_ref().ok_or_else(|| {
                    CliFailure::feature("RPC package operations require --workspace at startup")
                })?;
                let package = params["package"]
                    .as_str()
                    .ok_or_else(|| CliFailure::invalid("package must be a direct-child name"))?;
                let destination = params["destination"].as_str().ok_or_else(|| {
                    CliFailure::invalid("destination must be a direct-child name")
                })?;
                scope.import(package, destination)
            }
            "sleytools.review.compare" => {
                exact_object(params, &["handle", "before", "after", "cases"], &[])?;
                self.require_fresh_handle(params)?;
                let scope = self.transfer.as_ref().ok_or_else(|| {
                    CliFailure::feature("RPC package operations require --workspace at startup")
                })?;
                let before = params["before"].as_str().ok_or_else(|| {
                    CliFailure::invalid("before must be a direct-child package name")
                })?;
                let after = params["after"].as_str().ok_or_else(|| {
                    CliFailure::invalid("after must be a direct-child package name")
                })?;
                scope.review(before, after, &params["cases"])
            }
            _ => Err(CliFailure::feature(format!(
                "unknown or unavailable RPC method: {method}"
            ))),
        }
    }
}

fn rpc_error(id: Value, numeric: i32, error: &CliFailure) -> Value {
    json!({
        "jsonrpc": "2.0",
        "id": id,
        "error": {
            "code": numeric,
            "message": error.message,
            "data": {"tools_code": error.code, "category": error.category(), "core_symbol": error.core_symbol, "core_numeric": error.core_numeric}
        }
    })
}

fn request(state: &mut RpcState, line: &[u8]) -> Option<Value> {
    let parsed = match serde_json::from_slice::<StrictValue>(line) {
        Ok(value) => value.0,
        Err(error) => {
            return Some(rpc_error(
                Value::Null,
                -32700,
                &CliFailure::invalid(format!("invalid JSON: {error}")),
            ));
        }
    };
    if too_deep(&parsed, 0) {
        return Some(rpc_error(
            Value::Null,
            -32000,
            &CliFailure::resource("RPC request exceeds JSON depth 16"),
        ));
    }
    let Some(object) = parsed.as_object() else {
        return Some(rpc_error(
            Value::Null,
            -32600,
            &CliFailure::invalid("JSON-RPC request must be an object"),
        ));
    };
    let id = object.get("id").cloned().unwrap_or(Value::Null);
    if parsed["jsonrpc"] != "2.0"
        || !parsed["method"].is_string()
        || object
            .keys()
            .any(|name| !["jsonrpc", "id", "method", "params"].contains(&name.as_str()))
        || (!id.is_null() && !id.is_string() && !id.as_i64().is_some() && !id.as_u64().is_some())
    {
        return Some(rpc_error(
            Value::Null,
            -32600,
            &CliFailure::invalid("invalid JSON-RPC 2.0 request envelope"),
        ));
    }
    let method = parsed["method"].as_str().unwrap_or_default();
    let params = object.get("params").cloned().unwrap_or_else(|| json!({}));
    let result = state.dispatch(method, &params);
    if !object.contains_key("id") {
        return None;
    }
    Some(match result {
        Ok(result) => json!({"jsonrpc": "2.0", "id": id, "result": result}),
        Err(error) => rpc_error(
            id,
            if error.code == "ST_FEATURE_UNSUPPORTED" {
                -32601
            } else if error.code == "ST_INPUT_INVALID" {
                -32602
            } else {
                -32000
            },
            &error,
        ),
    })
}

/// Encode one response frame. An oversized result is replaced by a bounded
/// error that keeps the request ID, so clients can match it to their call.
fn encode_response(response: &Value) -> Result<Vec<u8>, CliFailure> {
    let encoded = serde_json::to_vec(response)
        .map_err(|error| CliFailure::failed(format!("cannot encode RPC response: {error}")))?;
    if encoded.len() <= MAX_RESPONSE_BYTES {
        return Ok(encoded);
    }
    serde_json::to_vec(&rpc_error(
        response["id"].clone(),
        -32000,
        &CliFailure::resource("RPC response exceeds 256 KiB"),
    ))
    .map_err(|error| CliFailure::failed(format!("cannot encode RPC limit: {error}")))
}

pub(super) fn serve(project: &Path, workspace: Option<&Path>) -> Result<(), CliFailure> {
    let mut state = RpcState::new(project, workspace)?;
    let input = std::io::stdin();
    let mut reader = BufReader::new(input.lock());
    let output = std::io::stdout();
    let mut writer = output.lock();
    loop {
        let mut line = Vec::new();
        let count = (&mut reader)
            .take((MAX_REQUEST_BYTES + 1) as u64)
            .read_until(b'\n', &mut line)
            .map_err(|error| CliFailure::failed(format!("cannot read RPC request: {error}")))?;
        if count == 0 {
            return Ok(());
        }
        if count > MAX_REQUEST_BYTES || !line.ends_with(b"\n") {
            let failure = CliFailure::resource("RPC request exceeds 64 KiB or lacks newline");
            let encoded =
                serde_json::to_vec(&rpc_error(Value::Null, -32000, &failure)).map_err(|error| {
                    CliFailure::failed(format!("cannot encode RPC failure: {error}"))
                })?;
            writer
                .write_all(&encoded)
                .and_then(|()| writer.write_all(b"\n"))
                .map_err(|error| {
                    CliFailure::failed(format!("cannot write RPC failure: {error}"))
                })?;
            writer.flush().map_err(|error| {
                CliFailure::failed(format!("cannot flush RPC failure: {error}"))
            })?;
            return Ok(());
        }
        let Some(response) = request(&mut state, &line) else {
            continue;
        };
        let encoded = encode_response(&response)?;
        writer
            .write_all(&encoded)
            .and_then(|()| writer.write_all(b"\n"))
            .and_then(|()| writer.flush())
            .map_err(|error| CliFailure::failed(format!("cannot write RPC response: {error}")))?;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn oversized_response_keeps_request_id() {
        let large = "x".repeat(MAX_RESPONSE_BYTES);
        let response = json!({"jsonrpc": "2.0", "id": 7, "result": {"blob": large}});
        let Ok(encoded) = encode_response(&response) else {
            panic!("bounded failure frame");
        };
        assert!(encoded.len() < 1_024);
        let frame: Value = serde_json::from_slice(&encoded).expect("valid JSON");
        assert_eq!(frame["id"], 7);
        assert_eq!(frame["error"]["data"]["tools_code"], "ST_RESOURCE_LIMIT");
    }

    #[test]
    fn bounded_response_is_unchanged() {
        let response = json!({"jsonrpc": "2.0", "id": "a", "result": {"ok": true}});
        let Ok(encoded) = encode_response(&response) else {
            panic!("encoded");
        };
        assert_eq!(encoded, serde_json::to_vec(&response).expect("reference"));
    }
}
