//! User-facing toolkit CLI. Start with an identity-checked core doctor.

use std::collections::BTreeSet;
use std::fs::{File, OpenOptions};
use std::io::{BufReader, Read, Write};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::Command;

use sha2::{Digest, Sha256};

#[path = "sley_tools/operation.rs"]
mod operation;
#[path = "sley_tools/reconcile.rs"]
mod reconcile;
#[path = "sley_tools/review.rs"]
mod review;
#[path = "sley_tools/rpc.rs"]
mod rpc;
#[path = "sley_tools/rpc_transfer.rs"]
mod rpc_transfer;
#[path = "sley_tools/starter.rs"]
mod starter;

#[path = "../core_pin.rs"]
mod core_pin;
use core_pin::{TESTED_CORE_BINARY_SHA256, TESTED_CORE_COMMIT, TESTED_CORE_RELEASE};

const USAGE: &str = "usage: sley-tools rpc --project PATH [--workspace PATH] | operation status|cancel --project PATH --id HEX | reconcile --project PATH --candidate-id HEX --expected-parent HEX | mcp --project PATH --core PATH --adapter PATH [--allow-write] [--workspace PATH] [--review-before PATH --review-after PATH --review-cases PATH] | pipe --adapter PATH --core CORE --comparator-project PATH --selector-project PATH --reducer-project PATH --input FILE|- [--output FILE] [--batch-size N] | playground --adapter PATH --core CORE [--port N] | lab list --adapter PATH | lab start EXERCISE|check|reset --adapter PATH --workspace PATH --core PATH | doctor --core PATH | init PROJECT_DIR --template release-readiness|resource-admission --core PATH [--grant-profile create-only] | init PROJECT_DIR --graph SPEC --tests CASES --core PATH | import schema SCHEMA PROJECT_DIR --core PATH | import python SOURCE DOMAIN PROJECT_DIR --core PATH | run --project PATH --input FILE [--check] | test --project PATH | inspect --project PATH | edit|propose --project PATH --max-blocking-findings N|--require-memory-spare true|false | propose --project PATH --constant NAME --value-file FILE --tests CASES | propose --project PATH --operation BLOCK.NAME --opcode OPCODE --tests CASES | propose --project PATH --operation BLOCK.NAME --operand left|right|operand --reference param.NAME|op.NAME --tests CASES | propose --project PATH --branch BLOCK --targets-file FILE --tests CASES | validate|commit --project PATH | propose discard --project PATH | artifact export --project PATH --output FILE [--bind-manifest] | artifact import ARTIFACT PROJECT_DIR --core PATH | review [verify REPORT] --before MANIFEST --after MANIFEST --core CORE --cases FILE [--output FILE]";

struct CliFailure {
    code: &'static str,
    message: String,
    exit_status: i32,
    core_symbol: Option<String>,
    core_numeric: Option<u32>,
}

impl CliFailure {
    fn invalid(message: impl Into<String>) -> Self {
        Self {
            code: "ST_INPUT_INVALID",
            message: message.into(),
            exit_status: 2,
            core_symbol: None,
            core_numeric: None,
        }
    }

    fn incompatible(message: impl Into<String>) -> Self {
        Self {
            code: "ST_CORE_INCOMPATIBLE",
            message: message.into(),
            exit_status: 3,
            core_symbol: None,
            core_numeric: None,
        }
    }

    fn failed(message: impl Into<String>) -> Self {
        Self {
            code: "ST_TRANSPORT_FAILED",
            message: message.into(),
            exit_status: 4,
            core_symbol: None,
            core_numeric: None,
        }
    }

    fn stale(message: impl Into<String>) -> Self {
        Self {
            code: "ST_STALE_BASE",
            message: message.into(),
            exit_status: 4,
            core_symbol: None,
            core_numeric: None,
        }
    }

    fn execution(message: impl Into<String>) -> Self {
        Self {
            code: "ST_EXECUTION_FAILED",
            message: message.into(),
            exit_status: 4,
            core_symbol: None,
            core_numeric: None,
        }
    }

    fn core(method: &str, failure: &sley_protocol::ProtocolFailure) -> Self {
        let code = if failure.symbol.contains("STALE") || failure.symbol.contains("ROOT_ADVANCED") {
            "ST_STALE_BASE"
        } else if failure.symbol.contains("PERMISSION") || failure.symbol.contains("CAPABILITY") {
            "ST_PERMISSION_DENIED"
        } else {
            "ST_VALIDATION_FAILED"
        };
        Self {
            code,
            message: format!("{method}: {} ({})", failure.symbol, failure.code),
            exit_status: 4,
            core_symbol: Some(failure.symbol.clone()),
            core_numeric: Some(failure.code),
        }
    }

    fn outcome_unknown(message: impl Into<String>) -> Self {
        Self {
            code: "ST_COMMIT_OUTCOME_UNKNOWN",
            message: message.into(),
            exit_status: 4,
            core_symbol: None,
            core_numeric: None,
        }
    }

    fn import_unsupported(message: impl Into<String>) -> Self {
        Self {
            code: "ST_IMPORT_UNSUPPORTED",
            message: message.into(),
            exit_status: 4,
            core_symbol: None,
            core_numeric: None,
        }
    }

    fn feature(message: impl Into<String>) -> Self {
        Self {
            code: "ST_FEATURE_UNSUPPORTED",
            message: message.into(),
            exit_status: 4,
            core_symbol: None,
            core_numeric: None,
        }
    }

    fn resource(message: impl Into<String>) -> Self {
        Self {
            code: "ST_RESOURCE_LIMIT",
            message: message.into(),
            exit_status: 4,
            core_symbol: None,
            core_numeric: None,
        }
    }

    fn category(&self) -> &'static str {
        match self.code {
            "ST_INPUT_INVALID" => "input_shape",
            "ST_FEATURE_UNSUPPORTED" | "ST_IMPORT_UNSUPPORTED" | "ST_SCHEMA_UNSUPPORTED" => {
                "unsupported_feature"
            }
            "ST_CORE_INCOMPATIBLE" => "compatibility",
            "ST_PERMISSION_DENIED" => "permission",
            "ST_STALE_BASE" => "stale_state",
            "ST_VALIDATION_FAILED" => "semantic_validation",
            "ST_EXECUTION_FAILED" => "execution",
            "ST_RESOURCE_LIMIT" => "resource_limit",
            "ST_COMMIT_OUTCOME_UNKNOWN" | "ST_TRANSPORT_FAILED" => "transport",
            _ => "internal",
        }
    }
}

fn sha256(path: &Path) -> Result<String, CliFailure> {
    let file = File::open(path)
        .map_err(|error| CliFailure::invalid(format!("cannot read core binary: {error}")))?;
    let mut reader = BufReader::new(file);
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let length = reader
            .read(&mut buffer)
            .map_err(|error| CliFailure::failed(format!("cannot hash core binary: {error}")))?;
        if length == 0 {
            break;
        }
        hasher.update(&buffer[..length]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

struct McpOptions {
    project: PathBuf,
    core: PathBuf,
    adapter: PathBuf,
    allow_write: bool,
    workspace: Option<PathBuf>,
    review_before: Option<PathBuf>,
    review_after: Option<PathBuf>,
    review_cases: Option<PathBuf>,
}

struct PipeOptions {
    adapter: PathBuf,
    core: PathBuf,
    arguments: Vec<String>,
}

struct PlaygroundOptions {
    adapter: PathBuf,
    core: PathBuf,
    port: u16,
}

struct LabOptions {
    adapter: PathBuf,
    command: &'static str,
    exercise: Option<String>,
    workspace: Option<PathBuf>,
    core: Option<PathBuf>,
}

fn parse_lab(mut args: impl Iterator<Item = String>) -> Result<LabOptions, CliFailure> {
    let (command, needs_workspace) = match args.next().as_deref() {
        Some("list") => ("list", false),
        Some("start") => ("start", true),
        Some("check") => ("judge", true),
        Some("reset") => ("reset", true),
        _ => {
            return Err(CliFailure::invalid(
                "lab requires list, start, check, or reset",
            ));
        }
    };
    let exercise = if command == "start" {
        Some(
            args.next()
                .filter(|value| !value.starts_with("--"))
                .ok_or_else(|| CliFailure::invalid("lab start requires an exercise ID"))?,
        )
    } else {
        None
    };
    let mut adapter = None;
    let mut workspace = None;
    let mut core = None;
    while let Some(flag) = args.next() {
        let selected = match flag.as_str() {
            "--adapter" => &mut adapter,
            "--workspace" if needs_workspace => &mut workspace,
            "--core" if needs_workspace => &mut core,
            _ => return Err(CliFailure::invalid(format!("unknown lab option: {flag}"))),
        };
        if selected.is_some() {
            return Err(CliFailure::invalid(format!("duplicate lab option: {flag}")));
        }
        let value = args
            .next()
            .filter(|value| !value.starts_with("--"))
            .ok_or_else(|| CliFailure::invalid(format!("lab option {flag} requires a path")))?;
        *selected = Some(PathBuf::from(value));
    }
    Ok(LabOptions {
        adapter: adapter.ok_or_else(|| {
            CliFailure::feature(
                "Repair Lab is optional; pass the installed lab.py with --adapter PATH",
            )
        })?,
        command,
        exercise,
        workspace: if needs_workspace {
            Some(workspace.ok_or_else(|| CliFailure::invalid("lab requires --workspace PATH"))?)
        } else {
            None
        },
        core: if needs_workspace {
            Some(core.ok_or_else(|| CliFailure::invalid("lab requires --core PATH"))?)
        } else {
            None
        },
    })
}

fn launch_lab(options: LabOptions) -> Result<(serde_json::Value, i32, bool), CliFailure> {
    let adapter = options.adapter.canonicalize().map_err(|error| {
        CliFailure::feature(format!(
            "Repair Lab adapter is unavailable at {}: {error}; pass an installed lab.py with --adapter PATH",
            options.adapter.display()
        ))
    })?;
    if !adapter.is_file() {
        return Err(CliFailure::feature(
            "Repair Lab adapter must be a regular file",
        ));
    }
    let tools = std::env::current_exe()
        .map_err(|error| CliFailure::failed(format!("cannot locate tools executable: {error}")))?;
    let mut command = Command::new("python3");
    command.arg(adapter).arg(options.command);
    if let Some(exercise) = options.exercise {
        command.arg(exercise);
    }
    if let (Some(workspace), Some(core)) = (options.workspace, options.core) {
        command
            .arg("--workspace")
            .arg(workspace)
            .arg("--core")
            .arg(core)
            .arg("--tools")
            .arg(tools);
    }
    let error = command.exec();
    if error.kind() == std::io::ErrorKind::NotFound {
        Err(CliFailure::feature(
            "Python 3 is unavailable for the optional Repair Lab adapter",
        ))
    } else {
        Err(CliFailure::failed(format!(
            "cannot start the Repair Lab adapter: {error}"
        )))
    }
}

fn parse_playground(
    mut args: impl Iterator<Item = String>,
) -> Result<PlaygroundOptions, CliFailure> {
    let mut adapter = None;
    let mut core = None;
    let mut port = None;
    while let Some(flag) = args.next() {
        let value = args
            .next()
            .filter(|value| !value.starts_with("--"))
            .ok_or_else(|| {
                CliFailure::invalid(format!("playground option {flag} requires a value"))
            })?;
        match flag.as_str() {
            "--adapter" if adapter.is_none() => adapter = Some(PathBuf::from(value)),
            "--core" if core.is_none() => core = Some(PathBuf::from(value)),
            "--port" if port.is_none() => {
                port =
                    Some(value.parse::<u16>().map_err(|_| {
                        CliFailure::invalid("playground port must be in 0..=65535")
                    })?);
            }
            _ => {
                return Err(CliFailure::invalid(format!(
                    "unknown or duplicate playground option: {flag}"
                )));
            }
        }
    }
    Ok(PlaygroundOptions {
        adapter: adapter.ok_or_else(|| {
            CliFailure::feature(
                "Playground is optional; install the local browser adapter and pass --adapter PATH",
            )
        })?,
        core: core.ok_or_else(|| CliFailure::invalid("playground requires --core PATH"))?,
        port: port.unwrap_or(4177),
    })
}

fn launch_playground(
    options: PlaygroundOptions,
) -> Result<(serde_json::Value, i32, bool), CliFailure> {
    let adapter = options.adapter.canonicalize().map_err(|error| {
        CliFailure::feature(format!(
            "Playground adapter is unavailable at {}: {error}; pass an installed local adapter with --adapter PATH",
            options.adapter.display()
        ))
    })?;
    if !adapter.is_file() {
        return Err(CliFailure::feature(
            "Playground adapter must be a regular file",
        ));
    }
    let tools = std::env::current_exe()
        .map_err(|error| CliFailure::failed(format!("cannot locate tools executable: {error}")))?;
    let error = Command::new("node")
        .arg(adapter)
        .arg("--tools")
        .arg(tools)
        .arg("--core")
        .arg(options.core)
        .arg("--port")
        .arg(options.port.to_string())
        .exec();
    if error.kind() == std::io::ErrorKind::NotFound {
        Err(CliFailure::feature(
            "Node.js is unavailable for the optional Playground adapter",
        ))
    } else {
        Err(CliFailure::failed(format!(
            "cannot start the local Playground adapter: {error}"
        )))
    }
}

fn parse_pipe(mut args: impl Iterator<Item = String>) -> Result<PipeOptions, CliFailure> {
    const FLAGS: &[&str] = &[
        "--adapter",
        "--core",
        "--comparator-project",
        "--selector-project",
        "--reducer-project",
        "--input",
        "--output",
        "--max-records",
        "--max-line-bytes",
        "--max-input-bytes",
        "--max-measurement",
        "--batch-size",
    ];
    let mut seen = BTreeSet::new();
    let mut adapter = None;
    let mut core = None;
    let mut arguments = Vec::new();
    while let Some(flag) = args.next() {
        if !FLAGS.contains(&flag.as_str()) || !seen.insert(flag.clone()) {
            return Err(CliFailure::invalid(format!(
                "unknown or duplicate pipe option: {flag}"
            )));
        }
        let value = args
            .next()
            .filter(|value| !value.starts_with("--"))
            .ok_or_else(|| CliFailure::invalid(format!("pipe option {flag} requires a value")))?;
        match flag.as_str() {
            "--adapter" => adapter = Some(PathBuf::from(value)),
            "--core" => core = Some(PathBuf::from(value)),
            _ => {
                arguments.push(flag);
                arguments.push(value);
            }
        }
    }
    for required in [
        "--comparator-project",
        "--selector-project",
        "--reducer-project",
        "--input",
    ] {
        if !seen.contains(required) {
            return Err(CliFailure::invalid(format!("pipe requires {required}")));
        }
    }
    Ok(PipeOptions {
        adapter: adapter.ok_or_else(|| {
            CliFailure::feature(
                "Data Pipe is optional; install sley-data-pipe and pass --adapter PATH",
            )
        })?,
        core: core.ok_or_else(|| CliFailure::invalid("pipe requires --core PATH"))?,
        arguments,
    })
}

fn launch_pipe(options: PipeOptions) -> Result<(serde_json::Value, i32, bool), CliFailure> {
    let adapter = options.adapter.canonicalize().map_err(|error| {
        CliFailure::feature(format!(
            "Data Pipe adapter is unavailable at {}: {error}; install the optional Python package and pass --adapter PATH",
            options.adapter.display()
        ))
    })?;
    if !adapter.is_file() {
        return Err(CliFailure::feature(
            "Data Pipe adapter must be a regular file",
        ));
    }
    let tools = std::env::current_exe()
        .map_err(|error| CliFailure::failed(format!("cannot locate tools executable: {error}")))?;
    let error = Command::new(adapter)
        .arg("--tools")
        .arg(tools)
        .arg("--core")
        .arg(options.core)
        .args(options.arguments)
        .exec();
    Err(CliFailure::failed(format!(
        "cannot start Data Pipe adapter: {error}"
    )))
}

fn launch_mcp(options: McpOptions) -> Result<(serde_json::Value, i32, bool), CliFailure> {
    let adapter = options.adapter.canonicalize().map_err(|error| {
        CliFailure::feature(format!(
            "MCP adapter is unavailable at {}: {error}; install the optional adapter and pass --adapter PATH",
            options.adapter.display()
        ))
    })?;
    if !adapter.is_file() {
        return Err(CliFailure::feature("MCP adapter must be a regular file"));
    }
    let tools = std::env::current_exe()
        .map_err(|error| CliFailure::failed(format!("cannot locate tools executable: {error}")))?;
    let mut command = Command::new("node");
    command
        .arg(adapter)
        .arg("--project")
        .arg(options.project)
        .arg("--tools")
        .arg(tools)
        .arg("--core")
        .arg(options.core);
    if options.allow_write {
        command.arg("--allow-write");
    }
    if let Some(workspace) = options.workspace {
        command.arg("--workspace").arg(workspace);
    }
    if let (Some(before), Some(after), Some(cases)) = (
        options.review_before,
        options.review_after,
        options.review_cases,
    ) {
        command
            .arg("--review-before")
            .arg(before)
            .arg("--review-after")
            .arg(after)
            .arg("--review-cases")
            .arg(cases);
    }
    let error = command.exec();
    if error.kind() == std::io::ErrorKind::NotFound {
        Err(CliFailure::feature(
            "Node.js is unavailable; install Node 20 or later for the optional MCP adapter",
        ))
    } else {
        Err(CliFailure::failed(format!(
            "cannot start the local MCP adapter: {error}"
        )))
    }
}

fn import_schema(
    schema: &Path,
    project: &Path,
    core: &Path,
) -> Result<serde_json::Value, CliFailure> {
    let tools = std::env::current_exe()
        .map_err(|error| CliFailure::failed(format!("cannot locate tools executable: {error}")))?;
    let output = Command::new("python3")
        .args(["-m", "sley_tools.schema_importer", "import"])
        .arg(schema)
        .arg(project)
        .arg("--tools")
        .arg(tools)
        .arg("--core")
        .arg(core)
        .output()
        .map_err(|error| {
            CliFailure::feature(format!(
                "Schema Importer needs the installed sley_tools Python package and python3: {error}"
            ))
        })?;
    if output.status.success() {
        let report: serde_json::Value =
            serde_json::from_slice(&output.stdout).map_err(|error| {
                CliFailure::failed(format!("Schema Importer returned invalid JSON: {error}"))
            })?;
        if report["contract"] != "sley-tools.schema-import.v1" {
            return Err(CliFailure::failed(
                "Schema Importer returned an unexpected contract",
            ));
        }
        return Ok(report);
    }
    let child: serde_json::Value = serde_json::from_slice(&output.stderr).map_err(|_| {
        CliFailure::feature("Schema Importer is unavailable; install the sley_tools Python package for this interpreter")
    })?;
    let pointer = child["schema_pointer"].as_str().unwrap_or("");
    let detail = child["message"]
        .as_str()
        .unwrap_or("Schema Importer failed");
    let message = if pointer.is_empty() {
        detail.to_owned()
    } else {
        format!("{pointer}: {detail}")
    };
    let (code, exit_status) = match child["code"].as_str() {
        Some("ST_RESOURCE_LIMIT") => ("ST_RESOURCE_LIMIT", 4),
        Some("ST_SCHEMA_UNSUPPORTED") => ("ST_SCHEMA_UNSUPPORTED", 2),
        Some("ST_INPUT_INVALID") => ("ST_INPUT_INVALID", 2),
        Some("ST_CORE_INCOMPATIBLE") => ("ST_CORE_INCOMPATIBLE", 3),
        Some("ST_VALIDATION_FAILED") => ("ST_VALIDATION_FAILED", 4),
        Some("ST_STALE_BASE") => ("ST_STALE_BASE", 4),
        Some("ST_TRANSPORT_FAILED") => ("ST_TRANSPORT_FAILED", 4),
        _ => ("ST_IMPORT_UNSUPPORTED", 4),
    };
    Err(CliFailure {
        code,
        message,
        exit_status,
        core_symbol: None,
        core_numeric: None,
    })
}

fn import_python(
    source: &Path,
    domain: &Path,
    project: &Path,
    core: &Path,
) -> Result<serde_json::Value, CliFailure> {
    let tools = std::env::current_exe()
        .map_err(|error| CliFailure::failed(format!("cannot locate tools executable: {error}")))?;
    let output = Command::new("python3")
        .args(["-m", "sley_tools.python_importer", "import"])
        .arg(source)
        .arg(domain)
        .arg(project)
        .arg("--tools")
        .arg(tools)
        .arg("--core")
        .arg(core)
        .output()
        .map_err(|error| {
            CliFailure::feature(format!(
                "Python Importer needs python3 and the sley_tools package: {error}"
            ))
        })?;
    if output.status.success() {
        let report: serde_json::Value =
            serde_json::from_slice(&output.stdout).map_err(|error| {
                CliFailure::failed(format!("Python Importer returned invalid JSON: {error}"))
            })?;
        if report["contract"] != "sley-tools.python-import.v1" {
            return Err(CliFailure::failed(
                "Python Importer returned an unexpected contract",
            ));
        }
        return Ok(report);
    }
    let child: serde_json::Value = serde_json::from_slice(&output.stderr).map_err(|_| {
        CliFailure::feature("Python Importer is unavailable; install the sley_tools Python package for this interpreter")
    })?;
    let detail = child["message"]
        .as_str()
        .unwrap_or("Python Importer failed");
    let location = match (child["line"].as_u64(), child["column"].as_u64()) {
        (Some(line), Some(column)) => format!("line {line}, column {column}: "),
        _ => String::new(),
    };
    let code = child["code"].as_str().unwrap_or("ST_IMPORT_UNSUPPORTED");
    let exit_status = match code {
        "ST_INPUT_INVALID" | "ST_IMPORT_UNSUPPORTED" => 2,
        "ST_CORE_INCOMPATIBLE" | "ST_FEATURE_UNSUPPORTED" => 3,
        _ => 4,
    };
    Err(CliFailure {
        code: match code {
            "ST_INPUT_INVALID" => "ST_INPUT_INVALID",
            "ST_RESOURCE_LIMIT" => "ST_RESOURCE_LIMIT",
            "ST_CORE_INCOMPATIBLE" => "ST_CORE_INCOMPATIBLE",
            "ST_FEATURE_UNSUPPORTED" => "ST_FEATURE_UNSUPPORTED",
            "ST_VALIDATION_FAILED" => "ST_VALIDATION_FAILED",
            "ST_TRANSPORT_FAILED" => "ST_TRANSPORT_FAILED",
            _ => "ST_IMPORT_UNSUPPORTED",
        },
        message: format!("{location}{detail}"),
        exit_status,
        core_symbol: None,
        core_numeric: None,
    })
}

fn invoke_json(
    binary: &Path,
    command: &str,
    profile: Option<&str>,
) -> Result<serde_json::Value, CliFailure> {
    let mut invocation = Command::new(binary);
    invocation.arg(command);
    if command == "hello" {
        invocation.arg("--json");
    }
    if let Some(profile) = profile {
        invocation.args(["--protocol-profile", profile]);
    }
    let output = invocation
        .output()
        .map_err(|error| CliFailure::failed(format!("core {command} failed to start: {error}")))?;
    if !output.status.success() {
        return Err(CliFailure::failed(format!(
            "core {command} exited {}",
            output.status
        )));
    }
    serde_json::from_slice(&output.stdout).map_err(|error| {
        CliFailure::failed(format!("core {command} produced invalid JSON: {error}"))
    })
}

fn read_adjacent_manifest(binary: &Path) -> Result<Option<serde_json::Value>, CliFailure> {
    let Some(root) = binary.parent().and_then(Path::parent) else {
        return Ok(None);
    };
    let manifest_path = root.join("MANIFEST.json");
    if !manifest_path.is_file() {
        return Ok(None);
    }
    let size = std::fs::metadata(&manifest_path)
        .map_err(|error| {
            CliFailure::failed(format!("cannot stat adjacent core manifest: {error}"))
        })?
        .len();
    if size > 1_048_576 {
        return Err(CliFailure::incompatible(
            "adjacent core manifest exceeds 1 MiB",
        ));
    }
    let bytes = std::fs::read(&manifest_path).map_err(|error| {
        CliFailure::failed(format!("cannot read adjacent core manifest: {error}"))
    })?;
    serde_json::from_slice(&bytes).map(Some).map_err(|error| {
        CliFailure::incompatible(format!("adjacent core manifest is invalid: {error}"))
    })
}

fn manifest_binary_sha256(manifest: &serde_json::Value) -> Option<&str> {
    manifest["files"]
        .as_array()
        .and_then(|files| files.iter().find(|file| file["path"] == "bin/sley"))
        .and_then(|file| file["sha256"].as_str())
}

fn manifest_identity(
    binary: &Path,
    binary_sha256: &str,
) -> Result<Option<serde_json::Value>, CliFailure> {
    let Some(manifest) = read_adjacent_manifest(binary)? else {
        return Ok(None);
    };
    let expected = manifest_binary_sha256(&manifest)
        .ok_or_else(|| CliFailure::incompatible("adjacent core manifest omits bin/sley"))?;
    if expected != binary_sha256 {
        return Err(CliFailure::incompatible(
            "core binary differs from adjacent manifest",
        ));
    }
    Ok(Some(manifest))
}

fn optional_runtime(binary: &str) -> Option<String> {
    let output = Command::new(binary).arg("--version").output().ok()?;
    if !output.status.success() {
        return None;
    }
    let text = if output.stdout.is_empty() {
        &output.stderr
    } else {
        &output.stdout
    };
    Some(String::from_utf8_lossy(text).trim().to_owned())
}

fn core_summary(
    binary: &Path,
    digest: &str,
    manifest: Option<&serde_json::Value>,
) -> serde_json::Value {
    serde_json::json!({
        "path": binary,
        "binary_sha256": digest,
        "manifest_binary_matches": manifest.is_some_and(|value| manifest_binary_sha256(value) == Some(digest)),
        "manifest_commit": manifest.and_then(|value| value["commit"].as_str()),
        "tested_commit": TESTED_CORE_COMMIT,
    })
}

fn incompatible_report(
    binary: &Path,
    digest: &str,
    manifest: Option<&serde_json::Value>,
    reason: &str,
) -> serde_json::Value {
    serde_json::json!({
        "contract": "sley-tools.doctor.v1",
        "status": "incompatible",
        "code": "ST_CORE_INCOMPATIBLE",
        "core": core_summary(binary, digest, manifest),
        "profiles": serde_json::Value::Null,
        "reason": reason,
    })
}

/// Identity-only core check: canonical path, pinned SHA-256, and adjacent
/// manifest binding. It never starts a process, so every project command and
/// RPC request can afford it. `doctor` adds the live protocol profiles.
fn core_check(binary: PathBuf) -> Result<serde_json::Value, CliFailure> {
    let binary = binary
        .canonicalize()
        .map_err(|error| CliFailure::invalid(format!("core path is unavailable: {error}")))?;
    if !binary.is_file() {
        return Err(CliFailure::invalid("core path is not a file"));
    }
    let digest = sha256(&binary)?;
    if digest != TESTED_CORE_BINARY_SHA256 {
        // Best effort only: name the release that was selected instead.
        let manifest = read_adjacent_manifest(&binary).ok().flatten();
        return Ok(incompatible_report(
            &binary,
            &digest,
            manifest.as_ref(),
            &format!("selected core binary is not the pinned {TESTED_CORE_RELEASE} artifact"),
        ));
    }
    let manifest = manifest_identity(&binary, &digest)?;
    if manifest
        .as_ref()
        .is_some_and(|value| value["commit"] != TESTED_CORE_COMMIT)
    {
        return Ok(incompatible_report(
            &binary,
            &digest,
            manifest.as_ref(),
            "adjacent manifest commit differs from the pinned release",
        ));
    }
    Ok(serde_json::json!({
        "contract": "sley-tools.doctor.v1",
        "status": "compatible",
        "core": core_summary(&binary, &digest, manifest.as_ref()),
    }))
}

/// Identity check plus the live protocol profiles of each selector. Used before
/// a new project is created so an unrunnable core fails before any files exist.
fn live_core_check(binary: PathBuf) -> Result<serde_json::Value, CliFailure> {
    let mut result = core_check(binary)?;
    if result["status"] != "compatible" {
        return Ok(result);
    }
    let binary = PathBuf::from(
        result["core"]["path"]
            .as_str()
            .ok_or_else(|| CliFailure::failed("core check omitted path"))?,
    );
    let mut profiles = serde_json::Map::new();
    for (name, selector) in [
        ("legacy", None),
        ("v2-capable", Some("v2-capable")),
        ("v3-capable", Some("v3-capable")),
    ] {
        let version = invoke_json(&binary, "version", selector)?;
        let hello = invoke_json(&binary, "hello", selector)?;
        profiles.insert(name.to_owned(), serde_json::json!({
            "version": version,
            "protocol_versions": hello["protocol_versions"],
            "schema_epochs": hello["schema_epochs"],
            "features": hello["features"],
            "offered_methods": hello["methods"],
            "extended_execute_offered": hello["features"]["extended_execute"] == true,
            "native_test_methods_offered": hello["methods"].as_array().is_some_and(|methods| methods.iter().any(|method| method == "tests.selected")),
        }));
    }
    result["profiles"] = serde_json::Value::Object(profiles);
    result["tested_release_observations"] = serde_json::json!({
        "evidence_scope": "prior F0 probe against the identical binary; method names alone do not prove execution",
        "smp1_extended_execute": "not offered by released CLI",
        "native_test_execution": "unconfigured CLI returned NATIVE_EXECUTOR_UNAVAILABLE in F0 probe",
        "typed_output_via_smp1_execute": "execution report carries output hash; toolkit uses pinned public VM library for typed output",
    });
    Ok(result)
}

fn doctor(binary: PathBuf) -> Result<serde_json::Value, CliFailure> {
    let mut result = live_core_check(binary)?;
    if result["status"] == "compatible" {
        result["optional_runtimes"] = serde_json::json!({
            "python3": optional_runtime("python3"),
            "node": optional_runtime("node"),
        });
    }
    Ok(result)
}

enum CliCommand {
    Rpc {
        project: PathBuf,
        workspace: Option<PathBuf>,
    },
    Mcp(McpOptions),
    Pipe(PipeOptions),
    Playground(PlaygroundOptions),
    Lab(LabOptions),
    Doctor(PathBuf),
    Init {
        project: PathBuf,
        core: PathBuf,
        template: String,
        limited_grant: bool,
    },
    InitGraph {
        project: PathBuf,
        core: PathBuf,
        spec: PathBuf,
        cases: PathBuf,
    },
    ImportSchema {
        schema: PathBuf,
        project: PathBuf,
        core: PathBuf,
    },
    ImportPython {
        source: PathBuf,
        domain: PathBuf,
        project: PathBuf,
        core: PathBuf,
    },
    Run {
        project: PathBuf,
        input: PathBuf,
        check: bool,
    },
    Test(PathBuf),
    NativeTestCandidate {
        project: PathBuf,
        case_id: String,
    },
    Inspect(PathBuf),
    ReconcileCandidate {
        project: PathBuf,
        candidate_id: String,
        expected_parent: String,
    },
    OperationStatus {
        project: PathBuf,
        operation_id: String,
    },
    OperationCancel {
        project: PathBuf,
        operation_id: String,
    },
    Edit {
        project: PathBuf,
        threshold: u32,
    },
    EditResource {
        project: PathBuf,
        require_spare: bool,
    },
    ProposeRelease {
        project: PathBuf,
        threshold: u32,
    },
    ProposeResource {
        project: PathBuf,
        require_spare: bool,
    },
    ProposeGraphConstant {
        project: PathBuf,
        name: String,
        value_file: PathBuf,
        cases_file: PathBuf,
    },
    ProposeGraphOpcode {
        project: PathBuf,
        block: String,
        name: String,
        opcode: String,
        cases_file: PathBuf,
    },
    ProposeGraphOperand {
        project: PathBuf,
        block: String,
        name: String,
        slot: String,
        reference: String,
        cases_file: PathBuf,
    },
    ProposeGraphBranch {
        project: PathBuf,
        block: String,
        targets_file: PathBuf,
        cases_file: PathBuf,
    },
    Validate(PathBuf),
    Commit(PathBuf),
    DiscardProposal(PathBuf),
    ArtifactExport {
        project: PathBuf,
        output: PathBuf,
        bind_manifest: bool,
    },
    ArtifactImport {
        artifact: PathBuf,
        project: PathBuf,
        core: PathBuf,
    },
    Review {
        before: PathBuf,
        after: PathBuf,
        core: PathBuf,
        cases: PathBuf,
        output: PathBuf,
    },
    ReviewVerify {
        report: PathBuf,
        before: PathBuf,
        after: PathBuf,
        core: PathBuf,
        cases: PathBuf,
    },
}

/// Command-line arguments after the program name. Paths are reported back in
/// JSON, so an argument that is not valid UTF-8 is refused as input instead of
/// panicking inside `std::env::args`.
fn cli_arguments() -> Result<Vec<String>, CliFailure> {
    std::env::args_os()
        .skip(1)
        .map(|argument| {
            argument.into_string().map_err(|argument| {
                CliFailure::invalid(format!(
                    "command-line argument is not valid UTF-8: {}",
                    argument.to_string_lossy()
                ))
            })
        })
        .collect()
}

fn parse(arguments: Vec<String>) -> Result<CliCommand, CliFailure> {
    let mut args = arguments.into_iter();
    match args.next().as_deref() {
        Some("rpc") => {
            if args.next().as_deref() != Some("--project") {
                return Err(CliFailure::invalid("rpc requires --project PATH"));
            }
            let project = args
                .next()
                .ok_or_else(|| CliFailure::invalid("rpc requires --project PATH"))?;
            let workspace = match args.next().as_deref() {
                Some("--workspace") => {
                    Some(PathBuf::from(args.next().ok_or_else(|| {
                        CliFailure::invalid("rpc --workspace requires PATH")
                    })?))
                }
                None => None,
                _ => {
                    return Err(CliFailure::invalid(
                        "rpc accepts --project PATH [--workspace PATH]",
                    ));
                }
            };
            if args.next().is_some() {
                return Err(CliFailure::invalid(
                    "rpc accepts --project PATH [--workspace PATH]",
                ));
            }
            Ok(CliCommand::Rpc {
                project: project.into(),
                workspace,
            })
        }
        Some("mcp") => {
            let mut project = None;
            let mut core = None;
            let mut adapter = None;
            let mut workspace = None;
            let mut review_before = None;
            let mut review_after = None;
            let mut review_cases = None;
            let mut allow_write = false;
            while let Some(option) = args.next() {
                let selected = match option.as_str() {
                    "--project" => &mut project,
                    "--core" => &mut core,
                    "--adapter" => &mut adapter,
                    "--workspace" => &mut workspace,
                    "--review-before" => &mut review_before,
                    "--review-after" => &mut review_after,
                    "--review-cases" => &mut review_cases,
                    "--allow-write" if !allow_write => {
                        allow_write = true;
                        continue;
                    }
                    _ => return Err(CliFailure::invalid("unknown or duplicate MCP option")),
                };
                if selected.is_some() {
                    return Err(CliFailure::invalid("duplicate MCP option"));
                }
                *selected =
                    Some(PathBuf::from(args.next().ok_or_else(|| {
                        CliFailure::invalid("MCP option requires a path")
                    })?));
            }
            let review_count = [
                review_before.is_some(),
                review_after.is_some(),
                review_cases.is_some(),
            ]
            .into_iter()
            .filter(|present| *present)
            .count();
            if review_count != 0 && review_count != 3 {
                return Err(CliFailure::invalid(
                    "MCP review requires --review-before, --review-after, and --review-cases together",
                ));
            }
            Ok(CliCommand::Mcp(McpOptions {
                project: project
                    .ok_or_else(|| CliFailure::invalid("MCP requires --project PATH"))?,
                core: core.ok_or_else(|| CliFailure::invalid("MCP requires --core PATH"))?,
                adapter: adapter.ok_or_else(|| {
                    CliFailure::feature(
                        "MCP is optional; install the local adapter and pass --adapter PATH",
                    )
                })?,
                allow_write,
                workspace,
                review_before,
                review_after,
                review_cases,
            }))
        }
        Some("pipe") => Ok(CliCommand::Pipe(parse_pipe(args)?)),
        Some("playground") => Ok(CliCommand::Playground(parse_playground(args)?)),
        Some("lab") => Ok(CliCommand::Lab(parse_lab(args)?)),
        Some("doctor") => {
            if args.next().as_deref() != Some("--core") {
                return Err(CliFailure::invalid("doctor requires --core PATH"));
            }
            let core = args
                .next()
                .ok_or_else(|| CliFailure::invalid("doctor requires --core PATH"))?;
            if args.next().is_some() {
                return Err(CliFailure::invalid("doctor accepts only --core PATH"));
            }
            Ok(CliCommand::Doctor(PathBuf::from(core)))
        }
        Some("import") => {
            let importer = args.next().ok_or_else(|| {
                CliFailure::invalid("import requires schema or python subcommand")
            })?;
            if importer == "python" {
                let source = args
                    .next()
                    .ok_or_else(|| CliFailure::invalid("import python requires SOURCE"))?;
                let domain = args
                    .next()
                    .ok_or_else(|| CliFailure::invalid("import python requires DOMAIN"))?;
                let project = args
                    .next()
                    .ok_or_else(|| CliFailure::invalid("import python requires PROJECT_DIR"))?;
                if args.next().as_deref() != Some("--core") {
                    return Err(CliFailure::invalid("import python requires --core PATH"));
                }
                let core = args
                    .next()
                    .ok_or_else(|| CliFailure::invalid("import python requires --core PATH"))?;
                if args.next().is_some() {
                    return Err(CliFailure::invalid(
                        "import python accepts only SOURCE DOMAIN PROJECT_DIR --core PATH",
                    ));
                }
                return Ok(CliCommand::ImportPython {
                    source: source.into(),
                    domain: domain.into(),
                    project: project.into(),
                    core: core.into(),
                });
            }
            if importer != "schema" {
                return Err(CliFailure::invalid(
                    "import requires schema or python subcommand",
                ));
            }
            let schema = args
                .next()
                .ok_or_else(|| CliFailure::invalid("import schema requires SCHEMA"))?;
            let project = args
                .next()
                .ok_or_else(|| CliFailure::invalid("import schema requires PROJECT_DIR"))?;
            if args.next().as_deref() != Some("--core") {
                return Err(CliFailure::invalid("import schema requires --core PATH"));
            }
            let core = args
                .next()
                .ok_or_else(|| CliFailure::invalid("import schema requires --core PATH"))?;
            if args.next().is_some() {
                return Err(CliFailure::invalid(
                    "import schema accepts only SCHEMA PROJECT_DIR --core PATH",
                ));
            }
            Ok(CliCommand::ImportSchema {
                schema: schema.into(),
                project: project.into(),
                core: core.into(),
            })
        }
        Some("init") => {
            let project = args
                .next()
                .ok_or_else(|| CliFailure::invalid("init requires PROJECT_DIR"))?;
            let mode = args.next();
            if mode.as_deref() == Some("--graph") {
                let spec = args
                    .next()
                    .ok_or_else(|| CliFailure::invalid("init --graph requires SPEC"))?;
                if args.next().as_deref() != Some("--tests") {
                    return Err(CliFailure::invalid("init --graph requires --tests CASES"));
                }
                let cases = args
                    .next()
                    .ok_or_else(|| CliFailure::invalid("init --graph requires --tests CASES"))?;
                if args.next().as_deref() != Some("--core") {
                    return Err(CliFailure::invalid("init --graph requires --core PATH"));
                }
                let core = args
                    .next()
                    .ok_or_else(|| CliFailure::invalid("init --graph requires --core PATH"))?;
                if args.next().is_some() {
                    return Err(CliFailure::invalid(
                        "init --graph accepts only PROJECT_DIR --graph SPEC --tests CASES --core PATH",
                    ));
                }
                return Ok(CliCommand::InitGraph {
                    project: project.into(),
                    core: core.into(),
                    spec: spec.into(),
                    cases: cases.into(),
                });
            }
            if mode.as_deref() != Some("--template") {
                return Err(CliFailure::invalid(
                    "usage: sley-tools init PROJECT_DIR --template NAME --core PATH | init PROJECT_DIR --graph SPEC --tests CASES --core PATH",
                ));
            }
            let template = args
                .next()
                .ok_or_else(|| CliFailure::invalid("init requires --template NAME"))?;
            if !["release-readiness", "resource-admission"].contains(&template.as_str())
                || args.next().as_deref() != Some("--core")
            {
                return Err(CliFailure::invalid(
                    "init requires a supported template followed by --core PATH",
                ));
            }
            let core = args
                .next()
                .ok_or_else(|| CliFailure::invalid("init requires --core PATH"))?;
            let limited_grant = match args.next() {
                None => false,
                Some(option) if option == "--grant-profile" => {
                    if args.next().as_deref() != Some("create-only") || args.next().is_some() {
                        return Err(CliFailure::invalid(
                            "--grant-profile accepts only create-only after --core PATH",
                        ));
                    }
                    true
                }
                Some(_) => {
                    return Err(CliFailure::invalid(
                        "init accepts only PROJECT_DIR --template NAME --core PATH [--grant-profile create-only]",
                    ));
                }
            };
            if limited_grant && template != "release-readiness" {
                return Err(CliFailure::invalid(
                    "create-only grant profile currently requires the release-readiness template",
                ));
            }
            Ok(CliCommand::Init {
                project: PathBuf::from(project),
                core: PathBuf::from(core),
                template,
                limited_grant,
            })
        }
        Some("run") => {
            if args.next().as_deref() != Some("--project") {
                return Err(CliFailure::invalid(
                    "run requires --project PATH --input FILE",
                ));
            }
            let project = args
                .next()
                .ok_or_else(|| CliFailure::invalid("run requires --project PATH"))?;
            if args.next().as_deref() != Some("--input") {
                return Err(CliFailure::invalid("run requires --input FILE"));
            }
            let input = args
                .next()
                .ok_or_else(|| CliFailure::invalid("run requires --input FILE"))?;
            let check = match args.next().as_deref() {
                None => false,
                Some("--check") => true,
                _ => {
                    return Err(CliFailure::invalid(
                        "run accepts only --project PATH --input FILE [--check]",
                    ));
                }
            };
            if args.next().is_some() {
                return Err(CliFailure::invalid(
                    "run accepts only --project PATH --input FILE [--check]",
                ));
            }
            Ok(CliCommand::Run {
                project: PathBuf::from(project),
                input: PathBuf::from(input),
                check,
            })
        }
        Some(command @ ("test" | "inspect")) => {
            let first = args.next();
            if command == "test" && first.as_deref() == Some("native-candidate") {
                if args.next().as_deref() != Some("--project") {
                    return Err(CliFailure::invalid(
                        "test native-candidate requires --project PATH --case ID",
                    ));
                }
                let project = args.next().ok_or_else(|| {
                    CliFailure::invalid("test native-candidate requires --project PATH")
                })?;
                if args.next().as_deref() != Some("--case") {
                    return Err(CliFailure::invalid(
                        "test native-candidate requires --case ID",
                    ));
                }
                let case_id = args.next().ok_or_else(|| {
                    CliFailure::invalid("test native-candidate requires --case ID")
                })?;
                if args.next().is_some() {
                    return Err(CliFailure::invalid(
                        "test native-candidate accepts only --project PATH --case ID",
                    ));
                }
                return Ok(CliCommand::NativeTestCandidate {
                    project: project.into(),
                    case_id,
                });
            }
            if first.as_deref() != Some("--project") {
                return Err(CliFailure::invalid(
                    "test and inspect require --project PATH",
                ));
            }
            let project = args
                .next()
                .ok_or_else(|| CliFailure::invalid("missing project path"))?;
            if args.next().is_some() {
                return Err(CliFailure::invalid(
                    "test and inspect accept only --project PATH",
                ));
            }
            if command == "test" {
                Ok(CliCommand::Test(PathBuf::from(project)))
            } else {
                Ok(CliCommand::Inspect(PathBuf::from(project)))
            }
        }
        Some("reconcile") => {
            if args.next().as_deref() != Some("--project") {
                return Err(CliFailure::invalid(
                    "reconcile requires --project PATH --candidate-id HEX --expected-parent HEX",
                ));
            }
            let project = args
                .next()
                .ok_or_else(|| CliFailure::invalid("missing project path"))?;
            if args.next().as_deref() != Some("--candidate-id") {
                return Err(CliFailure::invalid("reconcile requires --candidate-id HEX"));
            }
            let candidate_id = args
                .next()
                .ok_or_else(|| CliFailure::invalid("missing candidate ID"))?;
            if args.next().as_deref() != Some("--expected-parent") {
                return Err(CliFailure::invalid(
                    "reconcile requires --expected-parent HEX",
                ));
            }
            let expected_parent = args
                .next()
                .ok_or_else(|| CliFailure::invalid("missing expected parent"))?;
            if args.next().is_some() {
                return Err(CliFailure::invalid("reconcile accepts no further options"));
            }
            Ok(CliCommand::ReconcileCandidate {
                project: project.into(),
                candidate_id,
                expected_parent,
            })
        }
        Some("operation") => {
            let action = args
                .next()
                .ok_or_else(|| CliFailure::invalid("operation requires status or cancel"))?;
            if !["status", "cancel"].contains(&action.as_str())
                || args.next().as_deref() != Some("--project")
            {
                return Err(CliFailure::invalid(
                    "operation status|cancel requires --project PATH --id HEX",
                ));
            }
            let project = args
                .next()
                .ok_or_else(|| CliFailure::invalid("missing project path"))?;
            if args.next().as_deref() != Some("--id") {
                return Err(CliFailure::invalid("operation status requires --id HEX"));
            }
            let operation_id = args
                .next()
                .ok_or_else(|| CliFailure::invalid("missing operation ID"))?;
            if args.next().is_some() {
                return Err(CliFailure::invalid(
                    "operation status accepts no further options",
                ));
            }
            if action == "status" {
                Ok(CliCommand::OperationStatus {
                    project: project.into(),
                    operation_id,
                })
            } else {
                Ok(CliCommand::OperationCancel {
                    project: project.into(),
                    operation_id,
                })
            }
        }
        Some(command @ ("edit" | "propose")) => {
            let first = args.next();
            if command == "propose" && first.as_deref() == Some("discard") {
                if args.next().as_deref() != Some("--project") {
                    return Err(CliFailure::invalid(
                        "propose discard requires --project PATH",
                    ));
                }
                let project = args
                    .next()
                    .ok_or_else(|| CliFailure::invalid("missing project path"))?;
                if args.next().is_some() {
                    return Err(CliFailure::invalid(
                        "propose discard accepts only --project PATH",
                    ));
                }
                return Ok(CliCommand::DiscardProposal(PathBuf::from(project)));
            }
            if first.as_deref() != Some("--project") {
                return Err(CliFailure::invalid(
                    "edit/propose requires --project PATH and one typed change",
                ));
            }
            let project = args
                .next()
                .ok_or_else(|| CliFailure::invalid("edit requires --project PATH"))?;
            let option = args
                .next()
                .ok_or_else(|| CliFailure::invalid("edit requires a supported change option"))?;
            if option == "--constant" && command == "propose" {
                let name = args
                    .next()
                    .ok_or_else(|| CliFailure::invalid("constant proposal requires a name"))?;
                if args.next().as_deref() != Some("--value-file") {
                    return Err(CliFailure::invalid(
                        "constant proposal requires --value-file FILE",
                    ));
                }
                let value_file = args.next().ok_or_else(|| {
                    CliFailure::invalid("constant proposal requires a value file")
                })?;
                if args.next().as_deref() != Some("--tests") {
                    return Err(CliFailure::invalid(
                        "constant proposal requires --tests CASES",
                    ));
                }
                let cases_file = args.next().ok_or_else(|| {
                    CliFailure::invalid("constant proposal requires a cases file")
                })?;
                if args.next().is_some() {
                    return Err(CliFailure::invalid(
                        "constant proposal accepts only --constant NAME --value-file FILE --tests CASES",
                    ));
                }
                return Ok(CliCommand::ProposeGraphConstant {
                    project: project.into(),
                    name,
                    value_file: value_file.into(),
                    cases_file: cases_file.into(),
                });
            }
            if option == "--operation" && command == "propose" {
                let selector = args.next().ok_or_else(|| {
                    CliFailure::invalid("opcode proposal requires BLOCK.OPERATION")
                })?;
                let (block, name) = selector.split_once('.').ok_or_else(|| {
                    CliFailure::invalid("opcode proposal requires BLOCK.OPERATION")
                })?;
                if block.is_empty() || name.is_empty() || name.contains('.') {
                    return Err(CliFailure::invalid(
                        "opcode proposal requires BLOCK.OPERATION",
                    ));
                }
                let kind = args.next().ok_or_else(|| {
                    CliFailure::invalid("operation proposal requires --opcode or --operand")
                })?;
                let value = args.next().ok_or_else(|| {
                    CliFailure::invalid("operation proposal requires a new value")
                })?;
                let (slot, reference) = if kind == "--operand" {
                    if args.next().as_deref() != Some("--reference") {
                        return Err(CliFailure::invalid(
                            "operand proposal requires --reference param.NAME|op.NAME",
                        ));
                    }
                    let reference = args.next().ok_or_else(|| {
                        CliFailure::invalid("operand proposal requires a new reference")
                    })?;
                    (Some(value.clone()), Some(reference))
                } else {
                    (None, None)
                };
                if args.next().as_deref() != Some("--tests") {
                    return Err(CliFailure::invalid(
                        "operation proposal requires --tests CASES",
                    ));
                }
                let cases_file = args.next().ok_or_else(|| {
                    CliFailure::invalid("operation proposal requires a cases file")
                })?;
                if args.next().is_some() {
                    return Err(CliFailure::invalid(
                        "operation proposal has unexpected arguments",
                    ));
                }
                return match kind.as_str() {
                    "--opcode" => Ok(CliCommand::ProposeGraphOpcode {
                        project: project.into(),
                        block: block.to_owned(),
                        name: name.to_owned(),
                        opcode: value,
                        cases_file: cases_file.into(),
                    }),
                    "--operand" => Ok(CliCommand::ProposeGraphOperand {
                        project: project.into(),
                        block: block.to_owned(),
                        name: name.to_owned(),
                        slot: slot.expect("operand slot parsed"),
                        reference: reference.expect("operand reference parsed"),
                        cases_file: cases_file.into(),
                    }),
                    _ => Err(CliFailure::invalid(
                        "operation proposal requires --opcode or --operand",
                    )),
                };
            }
            if option == "--branch" && command == "propose" {
                let block = args
                    .next()
                    .ok_or_else(|| CliFailure::invalid("branch proposal requires BLOCK"))?;
                if block.is_empty() || block.contains('.') {
                    return Err(CliFailure::invalid(
                        "branch proposal requires one block name",
                    ));
                }
                if args.next().as_deref() != Some("--targets-file") {
                    return Err(CliFailure::invalid(
                        "branch proposal requires --targets-file FILE",
                    ));
                }
                let targets_file = args
                    .next()
                    .ok_or_else(|| CliFailure::invalid("branch proposal requires target file"))?;
                if args.next().as_deref() != Some("--tests") {
                    return Err(CliFailure::invalid(
                        "branch proposal requires --tests CASES",
                    ));
                }
                let cases_file = args
                    .next()
                    .ok_or_else(|| CliFailure::invalid("branch proposal requires cases file"))?;
                if args.next().is_some() {
                    return Err(CliFailure::invalid(
                        "branch proposal accepts only --branch BLOCK --targets-file FILE --tests CASES",
                    ));
                }
                return Ok(CliCommand::ProposeGraphBranch {
                    project: project.into(),
                    block,
                    targets_file: targets_file.into(),
                    cases_file: cases_file.into(),
                });
            }
            let value = args
                .next()
                .ok_or_else(|| CliFailure::invalid("edit requires a change value"))?;
            if args.next().is_some() {
                return Err(CliFailure::invalid(
                    "edit accepts only one change after --project PATH",
                ));
            }
            match option.as_str() {
                "--max-blocking-findings" => {
                    let threshold = value.parse::<u32>().map_err(|_| {
                        CliFailure::invalid("threshold must be an unsigned 32-bit integer")
                    })?;
                    if command == "propose" {
                        Ok(CliCommand::ProposeRelease {
                            project: PathBuf::from(project),
                            threshold,
                        })
                    } else {
                        Ok(CliCommand::Edit {
                            project: PathBuf::from(project),
                            threshold,
                        })
                    }
                }
                "--require-memory-spare" => {
                    let require_spare = match value.as_str() {
                        "true" => true,
                        "false" => false,
                        _ => {
                            return Err(CliFailure::invalid(
                                "--require-memory-spare requires true or false",
                            ));
                        }
                    };
                    if command == "propose" {
                        Ok(CliCommand::ProposeResource {
                            project: PathBuf::from(project),
                            require_spare,
                        })
                    } else {
                        Ok(CliCommand::EditResource {
                            project: PathBuf::from(project),
                            require_spare,
                        })
                    }
                }
                _ => Err(CliFailure::invalid("unsupported edit option")),
            }
        }
        Some(command @ ("validate" | "commit")) => {
            if args.next().as_deref() != Some("--project") {
                return Err(CliFailure::invalid(
                    "validate/commit requires --project PATH",
                ));
            }
            let project = args
                .next()
                .ok_or_else(|| CliFailure::invalid("missing project path"))?;
            if args.next().is_some() {
                return Err(CliFailure::invalid(
                    "validate/commit accepts only --project PATH",
                ));
            }
            if command == "validate" {
                Ok(CliCommand::Validate(PathBuf::from(project)))
            } else {
                Ok(CliCommand::Commit(PathBuf::from(project)))
            }
        }
        Some("artifact") => match args.next().as_deref() {
            Some("export") => {
                if args.next().as_deref() != Some("--project") {
                    return Err(CliFailure::invalid(
                        "artifact export requires --project PATH --output FILE",
                    ));
                }
                let project = args.next().ok_or_else(|| {
                    CliFailure::invalid("artifact export requires --project PATH")
                })?;
                if args.next().as_deref() != Some("--output") {
                    return Err(CliFailure::invalid(
                        "artifact export requires --output FILE",
                    ));
                }
                let output = args
                    .next()
                    .ok_or_else(|| CliFailure::invalid("artifact export requires --output FILE"))?;
                let bind_manifest = match args.next() {
                    None => false,
                    Some(option) if option == "--bind-manifest" && args.next().is_none() => true,
                    _ => {
                        return Err(CliFailure::invalid(
                            "artifact export accepts only --project PATH --output FILE [--bind-manifest]",
                        ));
                    }
                };
                Ok(CliCommand::ArtifactExport {
                    project: PathBuf::from(project),
                    output: PathBuf::from(output),
                    bind_manifest,
                })
            }
            Some("import") => {
                let artifact = args
                    .next()
                    .ok_or_else(|| CliFailure::invalid("artifact import requires ARTIFACT"))?;
                let project = args
                    .next()
                    .ok_or_else(|| CliFailure::invalid("artifact import requires PROJECT_DIR"))?;
                if args.next().as_deref() != Some("--core") {
                    return Err(CliFailure::invalid(
                        "artifact import requires ARTIFACT PROJECT_DIR --core PATH",
                    ));
                }
                let core = args
                    .next()
                    .ok_or_else(|| CliFailure::invalid("artifact import requires --core PATH"))?;
                if args.next().is_some() {
                    return Err(CliFailure::invalid(
                        "artifact import accepts only ARTIFACT PROJECT_DIR --core PATH",
                    ));
                }
                Ok(CliCommand::ArtifactImport {
                    artifact: PathBuf::from(artifact),
                    project: PathBuf::from(project),
                    core: PathBuf::from(core),
                })
            }
            _ => Err(CliFailure::invalid(
                "artifact requires export or import subcommand",
            )),
        },
        Some("review") => {
            let verify_report = match args.next().as_deref() {
                Some("verify") => Some(
                    args.next()
                        .ok_or_else(|| CliFailure::invalid("review verify requires REPORT"))?,
                ),
                Some("--before") => None,
                _ => {
                    return Err(CliFailure::invalid(
                        "review requires --before MANIFEST or verify REPORT",
                    ));
                }
            };
            if verify_report.is_some() && args.next().as_deref() != Some("--before") {
                return Err(CliFailure::invalid("review requires --before MANIFEST"));
            }
            let before = args
                .next()
                .ok_or_else(|| CliFailure::invalid("review requires before manifest"))?;
            if args.next().as_deref() != Some("--after") {
                return Err(CliFailure::invalid("review requires --after MANIFEST"));
            }
            let after = args
                .next()
                .ok_or_else(|| CliFailure::invalid("review requires after manifest"))?;
            if args.next().as_deref() != Some("--core") {
                return Err(CliFailure::invalid("review requires --core PATH"));
            }
            let core = args
                .next()
                .ok_or_else(|| CliFailure::invalid("review requires core path"))?;
            if args.next().as_deref() != Some("--cases") {
                return Err(CliFailure::invalid("review requires --cases FILE"));
            }
            let cases = args
                .next()
                .ok_or_else(|| CliFailure::invalid("review requires cases file"))?;
            if let Some(report) = verify_report {
                if args.next().is_some() {
                    return Err(CliFailure::invalid(
                        "review verify accepts no output option",
                    ));
                }
                Ok(CliCommand::ReviewVerify {
                    report: PathBuf::from(report),
                    before: PathBuf::from(before),
                    after: PathBuf::from(after),
                    core: PathBuf::from(core),
                    cases: PathBuf::from(cases),
                })
            } else {
                if args.next().as_deref() != Some("--output") {
                    return Err(CliFailure::invalid("review requires --output FILE"));
                }
                let output = args
                    .next()
                    .ok_or_else(|| CliFailure::invalid("review requires report output"))?;
                if args.next().is_some() {
                    return Err(CliFailure::invalid("review accepts no further options"));
                }
                Ok(CliCommand::Review {
                    before: PathBuf::from(before),
                    after: PathBuf::from(after),
                    core: PathBuf::from(core),
                    cases: PathBuf::from(cases),
                    output: PathBuf::from(output),
                })
            }
        }
        _ => Err(CliFailure::invalid(USAGE)),
    }
}

fn with_project_write_lock<F>(project: &Path, action: F) -> Result<serde_json::Value, CliFailure>
where
    F: FnOnce() -> Result<serde_json::Value, CliFailure>,
{
    let root = project.canonicalize().map_err(|error| {
        CliFailure::invalid(format!(
            "project path is unavailable for write lock: {error}"
        ))
    })?;
    let state_dir = root.join(".sley-tools");
    let metadata = std::fs::symlink_metadata(&state_dir).map_err(|error| {
        CliFailure::invalid(format!("project state directory is unavailable: {error}"))
    })?;
    if !metadata.file_type().is_dir() {
        return Err(CliFailure::invalid(
            "project state directory is not a real directory",
        ));
    }
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .open(state_dir.join("write.lock"))
        .map_err(|error| CliFailure::failed(format!("cannot open project write lock: {error}")))?;
    file.try_lock().map_err(|error| {
        CliFailure::resource(format!(
            "project write is busy or lock unavailable: {error}"
        ))
    })?;
    action()
}

/// `--version` and `--help` print plain text to stdout and exit 0; every other
/// command keeps the JSON report/failure contract.
fn informational_flag(arguments: &[String]) -> Option<String> {
    let [flag] = arguments else {
        return None;
    };
    match flag.as_str() {
        "--version" | "-V" => Some(format!(
            "sley-tools {} (tested core commit {TESTED_CORE_COMMIT})",
            env!("CARGO_PKG_VERSION")
        )),
        "--help" | "-h" | "help" => Some(USAGE.trim_start_matches("usage: ").split(" | ").fold(
            String::from("usage:"),
            |mut text, form| {
                text.push_str("\n  ");
                text.push_str(form);
                if !form.starts_with("sley-tools ") {
                    text.insert_str(text.len() - form.len(), "sley-tools ");
                }
                text
            },
        )),
        _ => None,
    }
}

/// Print one stdout document. A reader that closed the pipe early (for example
/// `| head`) is not worth a panic: the exit status still reports the command's
/// outcome. Any other write failure is reported as a JSON failure.
fn print_stdout(text: &str) {
    let mut stdout = std::io::stdout().lock();
    if let Err(error) = writeln!(stdout, "{text}").and_then(|()| stdout.flush())
        && error.kind() != std::io::ErrorKind::BrokenPipe
    {
        exit_with_failure(&CliFailure::failed(format!(
            "cannot write report to stdout: {error}"
        )));
    }
}

fn exit_with_failure(error: &CliFailure) -> ! {
    eprintln!(
        "{}",
        serde_json::json!({"code": error.code, "category": error.category(), "message": error.message, "core_symbol": error.core_symbol, "core_numeric": error.core_numeric})
    );
    std::process::exit(error.exit_status);
}

fn main() {
    let arguments = match cli_arguments() {
        Ok(arguments) => arguments,
        Err(error) => exit_with_failure(&error),
    };
    if let Some(text) = informational_flag(&arguments) {
        print_stdout(&text);
        return;
    }
    let result = parse(arguments).and_then(|command| match command {
        CliCommand::Rpc { project, workspace } => {
            rpc::serve(&project, workspace.as_deref()).map(|()| (serde_json::Value::Null, 0, false))
        }
        CliCommand::Mcp(options) => launch_mcp(options),
        CliCommand::Pipe(options) => launch_pipe(options),
        CliCommand::Playground(options) => launch_playground(options),
        CliCommand::Lab(options) => launch_lab(options),
        CliCommand::Doctor(binary) => doctor(binary).map(|report| (report, 0, true)),
        CliCommand::Init {
            project,
            core,
            template,
            limited_grant,
        } => starter::init(&project, &core, &template, limited_grant)
            .map(|report| (report, 0, false)),
        CliCommand::InitGraph {
            project,
            core,
            spec,
            cases,
        } => starter::init_graph(&project, &core, &spec, &cases).map(|report| (report, 0, false)),
        CliCommand::ImportSchema {
            schema,
            project,
            core,
        } => import_schema(&schema, &project, &core).map(|report| (report, 0, false)),
        CliCommand::ImportPython {
            source,
            domain,
            project,
            core,
        } => import_python(&source, &domain, &project, &core).map(|report| (report, 0, false)),
        CliCommand::Run {
            project,
            input,
            check,
        } => starter::run(&project, &input).and_then(|report| {
            if check && report["check_supported"] == false {
                return Err(CliFailure::feature(
                    "--check is unavailable for this graph result",
                ));
            }
            let exit = if check && report["result"]["allow"] == false {
                1
            } else {
                0
            };
            Ok((report, exit, false))
        }),
        CliCommand::Test(project) => starter::test(&project).map(|report| (report, 0, false)),
        CliCommand::NativeTestCandidate { project, case_id } => {
            with_project_write_lock(&project, || {
                starter::native_test_candidate(&project, &case_id)
            })
            .map(|report| (report, 0, false))
        }
        CliCommand::Inspect(project) => starter::inspect(&project).map(|report| (report, 0, false)),
        CliCommand::ReconcileCandidate {
            project,
            candidate_id,
            expected_parent,
        } => reconcile::candidate(&project, &candidate_id, &expected_parent)
            .map(|report| (report, 0, false)),
        CliCommand::OperationStatus {
            project,
            operation_id,
        } => operation::status(&project, &operation_id).map(|report| (report, 0, false)),
        CliCommand::OperationCancel {
            project,
            operation_id,
        } => with_project_write_lock(&project, || operation::cancel(&project, &operation_id))
            .map(|report| (report, 0, false)),
        CliCommand::Edit { project, threshold } => {
            with_project_write_lock(&project, || starter::edit(&project, threshold))
                .map(|report| (report, 0, false))
        }
        CliCommand::EditResource {
            project,
            require_spare,
        } => with_project_write_lock(&project, || starter::edit_resource(&project, require_spare))
            .map(|report| (report, 0, false)),
        CliCommand::ProposeRelease { project, threshold } => {
            with_project_write_lock(&project, || starter::propose_release(&project, threshold))
                .map(|report| (report, 0, false))
        }
        CliCommand::ProposeResource {
            project,
            require_spare,
        } => with_project_write_lock(&project, || {
            starter::propose_resource(&project, require_spare)
        })
        .map(|report| (report, 0, false)),
        CliCommand::ProposeGraphConstant {
            project,
            name,
            value_file,
            cases_file,
        } => with_project_write_lock(&project, || {
            starter::propose_graph_constant(&project, &name, &value_file, &cases_file)
        })
        .map(|report| (report, 0, false)),
        CliCommand::ProposeGraphOpcode {
            project,
            block,
            name,
            opcode,
            cases_file,
        } => with_project_write_lock(&project, || {
            starter::propose_graph_opcode(&project, &block, &name, &opcode, &cases_file)
        })
        .map(|report| (report, 0, false)),
        CliCommand::ProposeGraphOperand {
            project,
            block,
            name,
            slot,
            reference,
            cases_file,
        } => with_project_write_lock(&project, || {
            starter::propose_graph_operand(&project, &block, &name, &slot, &reference, &cases_file)
        })
        .map(|report| (report, 0, false)),
        CliCommand::ProposeGraphBranch {
            project,
            block,
            targets_file,
            cases_file,
        } => with_project_write_lock(&project, || {
            starter::propose_graph_branch(&project, &block, &targets_file, &cases_file)
        })
        .map(|report| (report, 0, false)),
        CliCommand::Validate(project) => {
            with_project_write_lock(&project, || starter::validate_proposal(&project, None))
                .map(|report| (report, 0, false))
        }
        CliCommand::Commit(project) => with_project_write_lock(&project, || {
            starter::inspect(&project)?;
            let candidate = operation::proposal_candidate(&project)?;
            starter::preflight_commit_proposal(&project, Some(&candidate))?;
            let id = operation::begin_commit(&project, &candidate)?;
            let mut result = starter::commit_proposal_observed(&project, Some(&candidate))?;
            operation::finish_commit(&project, &id)?;
            result["operation_id"] = serde_json::json!(id);
            if let Some(error) = result["verification_error"].as_str() {
                return Err(CliFailure::execution(format!(
                    "native edit committed at {}, but external assertions failed: {error}",
                    result["new_root"].as_str().unwrap_or("unknown root")
                )));
            }
            Ok(result)
        })
        .map(|report| (report, 0, false)),
        CliCommand::DiscardProposal(project) => {
            with_project_write_lock(&project, || starter::discard_proposal(&project, None))
                .map(|report| (report, 0, false))
        }
        CliCommand::ArtifactExport {
            project,
            output,
            bind_manifest,
        } => with_project_write_lock(&project, || {
            starter::artifact_export(&project, &output, bind_manifest)
        })
        .map(|report| (report, 0, false)),
        CliCommand::ArtifactImport {
            artifact,
            project,
            core,
        } => starter::artifact_import(&artifact, &project, &core).map(|report| (report, 0, false)),
        CliCommand::Review {
            before,
            after,
            core,
            cases,
            output,
        } => review::compute(&before, &after, &core, &cases)
            .and_then(|report| review::write_report(&report, &output))
            .map(|result| (result, 0, false)),
        CliCommand::ReviewVerify {
            report,
            before,
            after,
            core,
            cases,
        } => review::verify_report(&report, &before, &after, &core, &cases)
            .map(|result| (result, 0, false)),
    });
    match result {
        Ok((report, exit, was_doctor)) => {
            if !report.is_null() {
                print_stdout(&report.to_string());
            }
            if was_doctor && report["status"] != "compatible" {
                std::process::exit(3);
            }
            if exit != 0 {
                std::process::exit(exit);
            }
        }
        Err(error) => exit_with_failure(&error),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn arguments(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| (*value).to_owned()).collect()
    }

    #[test]
    fn informational_flags_need_a_single_argument() {
        let version = informational_flag(&arguments(&["--version"])).expect("version text");
        assert!(version.starts_with("sley-tools 0.0.1 (tested core commit "));
        let help = informational_flag(&arguments(&["-h"])).expect("help text");
        assert!(help.contains("\n  sley-tools doctor --core PATH\n"));
        assert!(informational_flag(&arguments(&["--version", "extra"])).is_none());
        assert!(informational_flag(&[]).is_none());
    }

    #[test]
    fn parse_reads_the_given_arguments() {
        let Ok(CliCommand::Run {
            project,
            input,
            check,
        }) = parse(arguments(&[
            "run",
            "--project",
            "p",
            "--input",
            "i.json",
            "--check",
        ]))
        else {
            panic!("run command");
        };
        assert_eq!((project, input, check), ("p".into(), "i.json".into(), true));
        let Err(error) = parse(Vec::new()) else {
            panic!("empty arguments are a usage error");
        };
        assert_eq!((error.code, error.exit_status), ("ST_INPUT_INVALID", 2));
    }
}
