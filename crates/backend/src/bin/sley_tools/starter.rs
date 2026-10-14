//! Fresh local project construction through the selected public core.

use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sley_id::{EntityId, PrincipalId, WorkspaceId};
use sley_mutate::value::{ConstantBody, EntityBodyValue, EntityIdSet, NamespaceBody};
use sley_mutate::{
    EntityObjectRecord, MutationClass, MutationPayload, PreconditionPayload, PreimageRequirement,
    build_candidate, build_entity_object, decode_candidate_record,
};
use sley_policy::{
    CandidateDecision, PolicyResourceCeilings, PolicyRootBuilder, PrincipalGrantBuilder,
    conformance_registry as policy_registry, import_candidate_result,
};
use sley_protocol::ProtocolFailure;
use sley_scb1::{ScbValueCursor, encode_list, encode_record, encode_uvar};
use sley_ssmc::{
    ConstData, ConstValue, IntegerWidth, Opcode, OperationResultRef, TypeExpr, ValueRef,
};
use sley_state_root::{StateRootBuilder, conformance_epoch_id, conformance_registry};
use sley_txn::TransactionRepository;

use super::rpc::{NativeInput, NativeWorker};
use super::{CliFailure, core_check, live_core_check};

#[path = "starter/graph_project.rs"]
mod graph_project;

struct Genesis {
    workspace_id: String,
    principal_id: String,
    root: String,
    policy: String,
    epoch: String,
    body_hex: String,
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn unhex(value: &str) -> Result<Vec<u8>, CliFailure> {
    if !value.len().is_multiple_of(2) || value.len() > 134_217_728 {
        return Err(CliFailure::failed(
            "core returned an invalid hex body length",
        ));
    }
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let text = std::str::from_utf8(pair)
                .map_err(|_| CliFailure::failed("core returned non-ASCII hex"))?;
            u8::from_str_radix(text, 16)
                .map_err(|_| CliFailure::failed("core returned invalid hex"))
        })
        .collect()
}

fn require_valid_candidate(reply: &str, expected_id: &str) -> Result<(), CliFailure> {
    let result = import_candidate_result(&unhex(reply)?).map_err(|error| {
        CliFailure::failed(format!("core candidate result is invalid: {error}"))
    })?;
    if result
        .record
        .candidate_id
        .as_ref()
        .map(|candidate| hex(candidate.as_bytes()).eq(expected_id))
        != Some(true)
    {
        return Err(CliFailure::failed(
            "core validated a different candidate identity",
        ));
    }
    let decision = result.record.decision;
    if decision != CandidateDecision::Valid {
        let diagnostic = result
            .record
            .diagnostics
            .first()
            .map(|item| item.source_symbol.as_str())
            .unwrap_or("NO_DIAGNOSTIC");
        let code = match decision {
            CandidateDecision::CapabilityDenied => "ST_PERMISSION_DENIED",
            CandidateDecision::StaleRoot | CandidateDecision::StaleEntity => "ST_STALE_BASE",
            CandidateDecision::ResourceLimit => "ST_RESOURCE_LIMIT",
            _ => "ST_VALIDATION_FAILED",
        };
        return Err(CliFailure {
            code,
            message: format!("candidate.validate: {} ({diagnostic})", decision.symbol()),
            exit_status: 4,
            core_symbol: Some(decision.symbol().to_owned()),
            core_numeric: decision.numeric_code(),
        });
    }
    Ok(())
}

fn random32() -> Result<[u8; 32], CliFailure> {
    let mut bytes = [0_u8; 32];
    File::open("/dev/urandom")
        .and_then(|mut source| source.read_exact(&mut bytes))
        .map_err(|error| CliFailure::failed(format!("cannot obtain workspace nonce: {error}")))?;
    Ok(bytes)
}

fn genesis(limited_grant: bool) -> Result<Genesis, CliFailure> {
    let workspace = WorkspaceId::from_bytes(random32()?);
    let principal = PrincipalId::from_bytes(random32()?);
    let namespace = EntityId::from_bytes(random32()?);
    let epoch = conformance_epoch_id()
        .map_err(|error| CliFailure::failed(format!("core epoch: {error}")))?;
    let object = build_entity_object(
        epoch,
        &EntityObjectRecord {
            entity_id: namespace,
            body: EntityBodyValue::Namespace(NamespaceBody {
                parent: None,
                members: EntityIdSet::from_unsorted(vec![])
                    .map_err(|error| CliFailure::failed(format!("namespace: {error}")))?,
            }),
            label: Some("sley-tools-project".to_owned()),
            semantic_fingerprint: None,
        },
    )
    .map_err(|error| CliFailure::failed(format!("namespace object: {error}")))?;
    let mut grant_builder = PrincipalGrantBuilder::new(PolicyResourceCeilings::new(
        1_000_000, 16_777_216, 1_048_576, 0, 100, 0,
    ))
    .mutation_class(MutationClass::CreateEntity)
    .mutation_class(MutationClass::AddEntryPoint)
    .mutation_class(MutationClass::AddTest);
    if !limited_grant {
        grant_builder = grant_builder.mutation_class(MutationClass::ReplaceEntityVersion);
    }
    let grant = grant_builder
        .build()
        .map_err(|error| CliFailure::failed(format!("workspace grant: {error}")))?;
    let policy = PolicyRootBuilder::new(workspace)
        .principal_grant(principal, grant)
        .build(
            &policy_registry()
                .map_err(|error| CliFailure::failed(format!("policy registry: {error}")))?,
        )
        .map_err(|error| CliFailure::failed(format!("workspace policy: {error}")))?;
    let state = StateRootBuilder::new(
        workspace,
        object.object_id(),
        object.object_id(),
        policy.root(),
    )
    .entity_binding(namespace, object.object_id())
    .build(
        &conformance_registry()
            .map_err(|error| CliFailure::failed(format!("state registry: {error}")))?,
    )
    .map_err(|error| CliFailure::failed(format!("workspace root: {error}")))?;
    let body = encode_record(&[
        (1, state.stored_bytes.clone()),
        (2, policy.stored_bytes().to_vec()),
        (
            3,
            encode_list(&[object.stored_bytes().to_vec()])
                .map_err(|error| CliFailure::failed(format!("workspace object list: {error}")))?,
        ),
        (
            4,
            encode_list(&[])
                .map_err(|error| CliFailure::failed(format!("workspace grant list: {error}")))?,
        ),
    ])
    .map_err(|error| CliFailure::failed(format!("workspace create request: {error}")))?;
    Ok(Genesis {
        workspace_id: hex(workspace.as_bytes()),
        principal_id: hex(principal.as_bytes()),
        root: hex(state.root.as_bytes()),
        policy: hex(policy.root().as_bytes()),
        epoch: hex(epoch.as_bytes()),
        body_hex: hex(&body),
    })
}

fn record_fields(data: &[u8], expected: usize) -> Result<Vec<Vec<u8>>, CliFailure> {
    let mut cursor = ScbValueCursor::new(data)
        .map_err(|error| CliFailure::failed(format!("core record: {error}")))?;
    let count = cursor
        .read_record_field_count()
        .map_err(|error| CliFailure::failed(format!("core record count: {error}")))?;
    if count != expected as u64 {
        return Err(CliFailure::failed(
            "core returned an unexpected record field count",
        ));
    }
    let mut fields = Vec::with_capacity(expected);
    for expected_tag in 1..=expected {
        let tag = cursor
            .read_uvar(32)
            .map_err(|error| CliFailure::failed(format!("core record tag: {error}")))?;
        if tag != expected_tag as u64 {
            return Err(CliFailure::failed(
                "core returned an unexpected record field tag",
            ));
        }
        fields.push(
            cursor
                .read_sized_payload()
                .map_err(|error| CliFailure::failed(format!("core record field: {error}")))?
                .to_vec(),
        );
    }
    cursor
        .check_finished()
        .map_err(|error| CliFailure::failed(format!("core record trailing bytes: {error}")))?;
    Ok(fields)
}

fn command_output(binary: &Path, args: &[&str], input: &[u8]) -> Result<Vec<u8>, CliFailure> {
    let mut child = Command::new(binary)
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| CliFailure::failed(format!("cannot start core command: {error}")))?;
    child
        .stdin
        .take()
        .ok_or_else(|| CliFailure::failed("core command has no input pipe"))?
        .write_all(input)
        .map_err(|error| CliFailure::failed(format!("cannot write core input: {error}")))?;
    let output = child
        .wait_with_output()
        .map_err(|error| CliFailure::failed(format!("cannot wait for core: {error}")))?;
    if !output.status.success() {
        return Err(CliFailure::failed(format!(
            "core command exited {}; stderr: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr)
                .chars()
                .take(300)
                .collect::<String>()
        )));
    }
    Ok(output.stdout)
}

fn offered_hello(core: &Path) -> Result<Value, CliFailure> {
    let bytes = command_output(core, &["hello"], &[])?;
    let decoded = command_output(core, &["frame", "decode"], &bytes)?;
    serde_json::from_slice(&decoded)
        .map_err(|error| CliFailure::failed(format!("cannot decode core hello: {error}")))
}

fn handshake(core: &Path, root: &Path, hello: &Value) -> Result<String, CliFailure> {
    let repository = root.join("handshake");
    let report_path = root.join("handshake-report.json");
    let mut line = serde_json::to_vec(hello)
        .map_err(|error| CliFailure::failed(format!("cannot encode core hello: {error}")))?;
    line.push(b'\n');
    command_output(
        core,
        &[
            "serve",
            "--repository",
            repository
                .to_str()
                .ok_or_else(|| CliFailure::invalid("project path is not UTF-8"))?,
            "--json",
            "--report",
            report_path
                .to_str()
                .ok_or_else(|| CliFailure::invalid("project path is not UTF-8"))?,
        ],
        &line,
    )?;
    let report: Value =
        serde_json::from_slice(&fs::read(&report_path).map_err(|error| {
            CliFailure::failed(format!("cannot read handshake report: {error}"))
        })?)
        .map_err(|error| CliFailure::failed(format!("invalid handshake report: {error}")))?;
    report["handshake_id"]
        .as_str()
        .map(ToOwned::to_owned)
        .ok_or_else(|| CliFailure::failed("handshake report omitted identity"))
}

struct Smp {
    child: Child,
    stdin: Option<ChildStdin>,
    stdout: BufReader<ChildStdout>,
}

impl Smp {
    fn start(core: &Path, repository: &Path, hello: &Value) -> Result<Self, CliFailure> {
        let mut child = Command::new(core)
            .args(["serve", "--repository"])
            .arg(repository)
            .arg("--json")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|error| CliFailure::failed(format!("cannot start core endpoint: {error}")))?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| CliFailure::failed("core endpoint has no input pipe"))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| CliFailure::failed("core endpoint has no output pipe"))?;
        let mut result = Self {
            child,
            stdin: Some(stdin),
            stdout: BufReader::new(stdout),
        };
        if result.send(hello)?["kind"] != "hello" {
            return Err(CliFailure::failed("core endpoint refused hello"));
        }
        Ok(result)
    }

    fn send(&mut self, frame: &Value) -> Result<Value, CliFailure> {
        let stdin = self
            .stdin
            .as_mut()
            .ok_or_else(|| CliFailure::failed("core endpoint is closed"))?;
        serde_json::to_writer(&mut *stdin, frame)
            .map_err(|error| CliFailure::failed(format!("cannot encode core frame: {error}")))?;
        stdin
            .write_all(b"\n")
            .and_then(|()| stdin.flush())
            .map_err(|error| CliFailure::failed(format!("cannot write core frame: {error}")))?;
        loop {
            let mut line = String::new();
            let read = self
                .stdout
                .read_line(&mut line)
                .map_err(|error| CliFailure::failed(format!("cannot read core frame: {error}")))?;
            if read == 0 {
                return Err(CliFailure::failed("core endpoint closed before response"));
            }
            if read > 67_108_864 {
                return Err(CliFailure::failed("core response exceeds 64 MiB"));
            }
            let response: Value = serde_json::from_str(&line).map_err(|error| {
                CliFailure::failed(format!("core returned malformed frame: {error}"))
            })?;
            if response["kind"] == "hello" || response["kind"] == "response" {
                return Ok(response);
            }
        }
    }

    fn request(
        &mut self,
        method: &str,
        body: &str,
        request_id: u64,
        session: Option<&str>,
    ) -> Result<String, CliFailure> {
        let frame = json!({
            "kind": "request",
            "method": method,
            "body": body,
            "protocol_version": 1,
            "request_id": request_id,
            "session": session,
            "bounds": {
                "applied_limits": {"max_depth": 0, "max_edges": 0, "max_entities": 0, "max_frame_bytes": 0, "max_inflight": 0, "max_response_bytes": 0, "max_sessions": 0, "max_work": 0},
                "continuation": false, "omitted": 0, "reached_depth": 0, "returned_bytes": 0,
                "returned_edges": 0, "returned_entities": 0, "truncated": false
            },
            "flags": {"cancel": false, "failed": false, "stream": false}
        });
        let response = self.send(&frame)?;
        if response["kind"] != "response" {
            return Err(CliFailure::failed(format!(
                "{method}: core did not return a response"
            )));
        }
        let body = response["body"]
            .as_str()
            .ok_or_else(|| CliFailure::failed(format!("{method}: response has no body")))?;
        if response["flags"]["failed"] == true {
            let failure = ProtocolFailure::decode(&unhex(body)?).map_err(|error| {
                CliFailure::failed(format!("{method}: invalid core failure: {error}"))
            })?;
            return Err(CliFailure::core(method, &failure));
        }
        Ok(body.to_owned())
    }

    fn finish(mut self) -> Result<(), CliFailure> {
        self.stdin.take();
        let status = self.child.wait().map_err(|error| {
            CliFailure::failed(format!("cannot wait for core endpoint: {error}"))
        })?;
        if !status.success() {
            return Err(CliFailure::failed(format!("core endpoint exited {status}")));
        }
        Ok(())
    }
}

impl Drop for Smp {
    fn drop(&mut self) {
        if self.child.try_wait().ok().flatten().is_none() {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}

fn builder(binary: &Path, command: &str, args: &[&str]) -> Result<Value, CliFailure> {
    let output = Command::new(binary)
        .arg(command)
        .args(args)
        .output()
        .map_err(|error| CliFailure::failed(format!("cannot start native builder: {error}")))?;
    if !output.status.success() {
        let message = format!(
            "native builder {command} exited {}; stderr: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr)
                .chars()
                .take(300)
                .collect::<String>()
        );
        return Err(match command {
            "portable-import" | "portable-import-graph" => CliFailure::import_unsupported(message),
            "construct-graph" | "construct-graph-native-test" | "edit-graph-constant" => {
                CliFailure::invalid(message)
            }
            "run-graph" => CliFailure::execution(message),
            _ => CliFailure::failed(message),
        });
    }
    serde_json::from_slice(&output.stdout).map_err(|error| {
        CliFailure::failed(format!(
            "native builder {command} returned invalid JSON: {error}"
        ))
    })
}

fn write_json(path: &Path, value: &Value) -> Result<(), CliFailure> {
    let mut bytes = serde_json::to_vec_pretty(value)
        .map_err(|error| CliFailure::failed(format!("cannot encode project file: {error}")))?;
    bytes.push(b'\n');
    fs::write(path, bytes)
        .map_err(|error| CliFailure::failed(format!("cannot write {}: {error}", path.display())))
}

struct NewWorkspace {
    genesis: Genesis,
    transaction: String,
    session: String,
    endpoint: Smp,
}

struct CommittedWorkspace {
    genesis: Genesis,
    accepted_transaction: String,
    accepted_root: String,
    commit_fields: Vec<Vec<u8>>,
}

impl NewWorkspace {
    fn open(core: &Path, state_dir: &Path, workspace_dir: &Path) -> Result<Self, CliFailure> {
        Self::open_with_grant(core, state_dir, workspace_dir, false)
    }

    fn open_with_grant(
        core: &Path,
        state_dir: &Path,
        workspace_dir: &Path,
        limited_grant: bool,
    ) -> Result<Self, CliFailure> {
        let hello = offered_hello(core)?;
        let handshake_id = handshake(core, state_dir, &hello)?;
        let genesis = genesis(limited_grant)?;
        let mut endpoint = Smp::start(core, workspace_dir, &hello)?;
        let transaction = endpoint.request("workspace.create", &genesis.body_hex, 0, None)?;
        let session = endpoint.request("session.open", &handshake_id, 0, None)?;
        let summary = endpoint.request("workspace.open", "", 1, Some(&session))?;
        let summary_fields = record_fields(&unhex(&summary)?, 8)?;
        for (index, expected) in [
            &transaction,
            &genesis.root,
            &genesis.policy,
            &genesis.workspace_id,
            &genesis.epoch,
        ]
        .iter()
        .enumerate()
        {
            if hex(&summary_fields[index]) != **expected {
                return Err(CliFailure::failed(
                    "workspace.open identity differs from constructed genesis",
                ));
            }
        }
        Ok(Self {
            genesis,
            transaction,
            session,
            endpoint,
        })
    }

    fn context(&self) -> [&str; 6] {
        [
            &self.genesis.workspace_id,
            &self.genesis.principal_id,
            &self.genesis.root,
            &self.genesis.policy,
            &self.transaction,
            &self.genesis.epoch,
        ]
    }

    fn commit(mut self, constructed: &Value) -> Result<CommittedWorkspace, CliFailure> {
        let candidate_record = constructed["candidate_record_hex"]
            .as_str()
            .ok_or_else(|| CliFailure::failed("builder omitted candidate record"))?;
        let stored =
            self.endpoint
                .request("candidate.create", candidate_record, 2, Some(&self.session))?;
        if stored != constructed["candidate_stored_hex"] {
            return Err(CliFailure::failed(
                "candidate.create differs from native builder",
            ));
        }
        let body = encode_record(&[
            (1, unhex(&self.transaction)?),
            (2, unhex(&self.genesis.principal_id)?),
            (
                3,
                encode_uvar(
                    constructed["now_unix_millis"]
                        .as_u64()
                        .ok_or_else(|| CliFailure::failed("builder omitted validation time"))?,
                ),
            ),
            (4, unhex(&stored)?),
        ])
        .map_err(|error| CliFailure::failed(format!("validation request: {error}")))?;
        let validation_body = hex(&body);
        let validation = self.endpoint.request(
            "candidate.validate",
            &validation_body,
            3,
            Some(&self.session),
        )?;
        require_valid_candidate(
            &validation,
            constructed["candidate_id"]
                .as_str()
                .ok_or_else(|| CliFailure::failed("builder omitted candidate identity"))?,
        )?;
        let committed =
            self.endpoint
                .request("commit", &validation_body, 4, Some(&self.session))?;
        let commit_fields = record_fields(&unhex(&committed)?, 4)?;
        if commit_fields[0].len() != 32
            || commit_fields[1].len() != 32
            || commit_fields[2].len() != 32
        {
            return Err(CliFailure::failed(
                "commit returned invalid identity lengths",
            ));
        }
        let accepted_transaction = hex(&commit_fields[0]);
        let accepted_root = hex(&commit_fields[2]);
        self.endpoint
            .request("session.close", "", 5, Some(&self.session))?;
        self.endpoint.finish()?;
        Ok(CommittedWorkspace {
            genesis: self.genesis,
            accepted_transaction,
            accepted_root,
            commit_fields,
        })
    }
}

fn initialize(
    project: &Path,
    core: &Path,
    builder_path: &Path,
    core_sha: &str,
    source_artifact: Option<&Path>,
    template: &str,
    limited_grant: bool,
) -> Result<Value, CliFailure> {
    let state_dir = project.join(".sley-tools");
    let workspace_dir = project.join("workspace");
    let input_dir = project.join("inputs");
    for directory in [&state_dir, &workspace_dir, &input_dir] {
        fs::create_dir(directory).map_err(|error| {
            CliFailure::failed(format!("cannot create {}: {error}", directory.display()))
        })?;
    }
    fs::write(
        project.join(".gitignore"),
        "/workspace/\n/.sley-tools/\n/config.local.json\n",
    )
    .map_err(|error| CliFailure::failed(format!("cannot write project .gitignore: {error}")))?;
    write_json(
        &project.join("config.local.json"),
        &json!({"core": core, "core_binary_sha256": core_sha}),
    )?;
    write_json(
        &project.join("inputs/ready.json"),
        &if template == "resource-admission" {
            json!({"requested_workers": 1, "available_workers": 1, "requested_memory_mib": 4096, "available_memory_mib": 4096})
        } else {
            json!({"test_run_completed": true, "tests_failed": 0, "artifact_verified": true, "blocking_findings": 0})
        },
    )?;
    let imported_artifact = if let Some(source) = source_artifact {
        let metadata = fs::symlink_metadata(source)
            .map_err(|error| CliFailure::invalid(format!("cannot stat artifact: {error}")))?;
        if !metadata.file_type().is_file() || metadata.len() > 1_048_576 {
            return Err(CliFailure::invalid(
                "artifact must be a regular file of at most 1 MiB",
            ));
        }
        let mut bytes = Vec::new();
        File::open(source)
            .and_then(|file| file.take(1_048_577).read_to_end(&mut bytes))
            .map_err(|error| CliFailure::invalid(format!("cannot read artifact: {error}")))?;
        if bytes.len() > 1_048_576 {
            return Err(CliFailure::invalid("artifact exceeds 1 MiB"));
        }
        let folder = project.join("artifact");
        fs::create_dir(&folder).map_err(|error| {
            CliFailure::failed(format!("cannot create artifact folder: {error}"))
        })?;
        let copy = folder.join("native-program.json");
        fs::write(&copy, &bytes)
            .map_err(|error| CliFailure::failed(format!("cannot copy artifact: {error}")))?;
        Some((copy, format!("{:x}", Sha256::digest(&bytes))))
    } else {
        None
    };

    let opened = NewWorkspace::open_with_grant(core, &state_dir, &workspace_dir, limited_grant)?;
    let context = opened.context();
    let constructed = if let Some((artifact, _)) = &imported_artifact {
        let source = artifact
            .to_str()
            .ok_or_else(|| CliFailure::invalid("artifact path is not UTF-8"))?;
        let mut arguments = vec![source];
        arguments.extend(context);
        builder(builder_path, "portable-import", &arguments)?
    } else {
        builder(
            builder_path,
            if template == "resource-admission" {
                "construct-resource"
            } else {
                "construct"
            },
            &context,
        )?
    };
    let CommittedWorkspace {
        genesis,
        accepted_transaction,
        accepted_root,
        commit_fields,
    } = opened.commit(&constructed)?;

    let function = constructed["function_id"]
        .as_str()
        .ok_or_else(|| CliFailure::failed("builder omitted function identity"))?;
    if template == "resource-admission" {
        return finish_resource(
            project,
            &workspace_dir,
            builder_path,
            core_sha,
            &genesis,
            &constructed,
            &accepted_transaction,
            &accepted_root,
            &commit_fields,
            imported_artifact.as_ref(),
        );
    }
    let ready = builder(
        builder_path,
        "run",
        &[
            workspace_dir
                .to_str()
                .ok_or_else(|| CliFailure::invalid("project path is not UTF-8"))?,
            &accepted_root,
            function,
            "true",
            "0",
            "true",
            "0",
        ],
    )?;
    if ready["result"]["allow"] != true || ready["reason_codes"] != json!([]) {
        return Err(CliFailure::failed(
            "newly committed native program did not approve the ready case",
        ));
    }
    let threshold = if imported_artifact.is_some() {
        constructed["threshold"]
            .as_u64()
            .and_then(|value| u32::try_from(value).ok())
            .ok_or_else(|| CliFailure::failed("import builder omitted a valid native threshold"))?
    } else {
        0
    };
    let mut manifest = json!({
        "contract": "sley-tools.program.v0",
        "program_id": "release-readiness",
        "title": "Release Readiness",
        "purpose": "Decide whether supplied test, artifact, and blocking-finding facts permit release",
        "program_version": if imported_artifact.is_some() {"0.1.0-imported"} else {"0.1.0-local"},
        "status": "local_workspace_only",
        "tools_contract": "sley-tools.cli.v0",
        "core": {"commit": super::TESTED_CORE_COMMIT, "profile": "extended_v1", "binary_sha256": core_sha},
        "entrypoint": {"name": "evaluate", "function_id": function, "state_root": accepted_root},
        "result_type_id": constructed["result_type_id"],
        "result_fields": constructed["result_fields"],
        "inputs": {
            "test_run_completed": "Bool",
            "tests_failed": "UInt32 0..4294967295",
            "artifact_verified": "Bool",
            "blocking_findings": "UInt32 0..4294967295"
        },
        "output": {
            "type": "Record",
            "fields": {
                "allow": "Bool",
                "test_run_incomplete": "Bool",
                "tests_failed": "Bool",
                "artifact_unverified": "Bool",
                "blocking_findings_exceeded": "Bool"
            }
        },
        "artifact": null,
        "native_logic": "four reason flags and allow decision",
        "host_responsibility": "typed input validation, local workspace lifecycle, and presentation",
        "test_cases": {
            "external_assertions": "sley-tools test --project PROJECT",
            "independent_oracle": "crates/backend/src/bin/sley_tools/starter.rs::expected_flags",
            "native_core_test_admission": "PENDING_CORE_EXECUTOR"
        },
        "worked_change": {
            "operation": "replace max_blocking_findings constant by exact old-object precondition",
            "initial_threshold": 0,
            "example_threshold": 2
        },
        "source_provenance": {
            "builder": "crates/backend/src/main.rs",
            "core_commit": super::TESTED_CORE_COMMIT
        },
        "license_status": "Apache-2.0"
    });
    if let Some((_, digest)) = &imported_artifact {
        manifest["imported_source_artifact"] = json!({
            "path": "artifact/native-program.json",
            "sha256": digest,
            "contract": "sley-tools.native-program-artifact.v0"
        });
    }
    write_json(&project.join("program.json"), &manifest)?;
    write_json(
        &state_dir.join("state.json"),
        &json!({
            "workspace_id": genesis.workspace_id,
            "principal_id": genesis.principal_id,
            "policy_root": genesis.policy,
            "schema_epoch": genesis.epoch,
            "transaction_id": accepted_transaction,
            "state_root": accepted_root,
            "candidate_id": constructed["candidate_id"],
            "commit_receipt_id": hex(&commit_fields[1]),
            "commit_history": [{
                "kind": if imported_artifact.is_some() {"artifact_import"} else {"construct"},
                "candidate_id": constructed["candidate_id"],
                "transaction_id": accepted_transaction,
                "state_root": accepted_root,
                "receipt_id": hex(&commit_fields[1]),
            }],
            "function_id": function,
            "threshold_id": constructed["threshold_id"],
            "threshold_object_id": constructed["threshold_object_id"],
            "threshold": threshold,
        }),
    )?;
    let status = json!({
        "state": "READY_EXTERNAL_ASSERTIONS_ONLY",
        "native_commit_count": 1,
        "native_test_admission": "PENDING_CORE_EXECUTOR",
        "state_root": accepted_root,
    });
    write_json(&state_dir.join("status.json"), &status)?;
    Ok(
        json!({"contract": if imported_artifact.is_some() {"sley-tools.artifact-import.v0"} else {"sley-tools.init.v0"}, "project": project, "status": status, "program": manifest, "ready_observation_id": ready["observation_id"], "source_state_root": constructed["source_state_root"]}),
    )
}

fn finish_resource(
    project: &Path,
    workspace_dir: &Path,
    builder_path: &Path,
    core_sha: &str,
    genesis: &Genesis,
    constructed: &Value,
    accepted_transaction: &str,
    accepted_root: &str,
    commit_fields: &[Vec<u8>],
    imported_artifact: Option<&(PathBuf, String)>,
) -> Result<Value, CliFailure> {
    let function = constructed["function_id"]
        .as_str()
        .ok_or_else(|| CliFailure::failed("resource builder omitted function identity"))?;
    let require_spare = constructed["require_memory_spare"]
        .as_bool()
        .ok_or_else(|| CliFailure::failed("resource builder omitted memory policy"))?;
    let ready = builder(
        builder_path,
        "run-resource",
        &[
            workspace_dir
                .to_str()
                .ok_or_else(|| CliFailure::invalid("project path is not UTF-8"))?,
            accepted_root,
            function,
            "1",
            "1",
            "4096",
            "4096",
        ],
    )?;
    if ready["result"]["allow"] != !require_spare
        || ready["result"]["requested_memory_mib"] != "4096"
    {
        return Err(CliFailure::failed(
            "newly committed Resource Admission graph failed exact-fit execution",
        ));
    }
    let mut manifest = json!({
        "contract": "sley-tools.program.v0",
        "program_id": "resource-admission",
        "title": "Resource Admission",
        "purpose": "Decide whether a requested bounded job fits supplied worker and memory capacity",
        "program_version": if imported_artifact.is_some() { "0.1.0-imported" } else { "0.1.0-local" },
        "status": "local_workspace_only",
        "tools_contract": "sley-tools.cli.v0",
        "core": {"commit": super::TESTED_CORE_COMMIT, "profile": "extended_v1", "binary_sha256": core_sha},
        "entrypoint": {"name": "evaluate", "function_id": function, "state_root": accepted_root},
        "result_type_id": constructed["result_type_id"],
        "result_fields": constructed["result_fields"],
        "inputs": {
            "requested_workers": "SInt64 0..9223372036854775807",
            "available_workers": "SInt64 0..9223372036854775807",
            "requested_memory_mib": "SInt64 0..9223372036854775807",
            "available_memory_mib": "SInt64 0..9223372036854775807"
        },
        "output": {"type": "Record", "fields": {
            "allow": "Bool", "invalid_request": "Bool", "invalid_capacity": "Bool",
            "insufficient_workers": "Bool", "insufficient_memory": "Bool",
            "requested_memory_mib": "SInt64", "available_memory_mib": "SInt64"
        }},
        "artifact": null,
        "native_logic": if require_spare {
            "signed comparisons for invalid values and insufficient capacity; exact memory fit requires spare capacity"
        } else {
            "signed comparisons for invalid values and insufficient capacity; exact memory fit is allowed"
        },
        "host_responsibility": "typed input validation, local workspace lifecycle, and presentation",
        "test_cases": {"external_assertions": "sley-tools test --project PROJECT", "independent_oracle": "crates/backend/src/bin/sley_tools/starter.rs::test_resource", "native_core_test_admission": "PENDING_CORE_EXECUTOR"},
        "worked_change": {
            "operation": "replace exact memory capacity comparison under old-object precondition",
            "initial_require_memory_spare": false,
            "example_require_memory_spare": true
        },
        "source_provenance": {"builder": "crates/backend/src/resource.rs", "core_commit": super::TESTED_CORE_COMMIT},
        "license_status": "Apache-2.0"
    });
    if let Some((_, digest)) = imported_artifact {
        manifest["imported_source_artifact"] = json!({
            "path": "artifact/native-program.json", "sha256": digest,
            "contract": "sley-tools.native-program-artifact.v0"
        });
    }
    write_json(&project.join("program.json"), &manifest)?;
    write_json(
        &project.join(".sley-tools/state.json"),
        &json!({
            "workspace_id": genesis.workspace_id, "principal_id": genesis.principal_id,
            "policy_root": genesis.policy, "schema_epoch": genesis.epoch,
            "transaction_id": accepted_transaction, "state_root": accepted_root,
            "candidate_id": constructed["candidate_id"], "commit_receipt_id": hex(&commit_fields[1]),
            "commit_history": [{"kind": if imported_artifact.is_some() { "artifact_import" } else { "construct" }, "candidate_id": constructed["candidate_id"],
                "transaction_id": accepted_transaction, "state_root": accepted_root, "receipt_id": hex(&commit_fields[1])}],
            "function_id": function,
            "memory_comparison_id": constructed["memory_comparison_id"],
            "memory_comparison_object_id": constructed["memory_comparison_object_id"],
            "require_memory_spare": require_spare,
        }),
    )?;
    let status = json!({
        "state": "READY_EXTERNAL_ASSERTIONS_ONLY", "native_commit_count": 1,
        "native_test_admission": "PENDING_CORE_EXECUTOR", "state_root": accepted_root,
    });
    write_json(&project.join(".sley-tools/status.json"), &status)?;
    Ok(
        json!({"contract": if imported_artifact.is_some() { "sley-tools.artifact-import.v0" } else { "sley-tools.init.v0" }, "project": project, "status": status,
        "program": manifest, "ready_observation_id": ready["observation_id"], "source_state_root": constructed["source_state_root"]}),
    )
}

fn create_project(
    project: &Path,
    core: &Path,
    source_artifact: Option<&Path>,
    template: &str,
    limited_grant: bool,
) -> Result<Value, CliFailure> {
    if !["release-readiness", "resource-admission"].contains(&template) {
        return Err(CliFailure::invalid(
            "unsupported template or artifact import profile",
        ));
    }
    let core_report = live_core_check(core.to_path_buf())?;
    if core_report["status"] != "compatible" {
        return Err(CliFailure::incompatible(
            "init requires the pinned Sley 2.0.1 core",
        ));
    }
    let core = PathBuf::from(
        core_report["core"]["path"]
            .as_str()
            .ok_or_else(|| CliFailure::failed("doctor omitted core path"))?,
    );
    let core_sha = core_report["core"]["binary_sha256"]
        .as_str()
        .ok_or_else(|| CliFailure::failed("doctor omitted binary digest"))?;
    let current = std::env::current_exe()
        .map_err(|error| CliFailure::failed(format!("cannot locate tools binary: {error}")))?;
    let builder_path = current.with_file_name("sley-tools-native-builder");
    if !builder_path.is_file() {
        return Err(CliFailure::failed(
            "sley-tools-native-builder is missing beside sley-tools",
        ));
    }
    fs::create_dir(project).map_err(|error| {
        CliFailure::invalid(format!("cannot create new project directory: {error}"))
    })?;
    let project = project.canonicalize().map_err(|error| {
        CliFailure::failed(format!("cannot resolve project directory: {error}"))
    })?;
    let status_path = project.join(".sley-tools/status.json");
    let outcome = initialize(
        &project,
        &core,
        &builder_path,
        core_sha,
        source_artifact,
        template,
        limited_grant,
    )
    .and_then(|mut report| {
        if source_artifact.is_some() {
            report["verification"] = test(&project)?;
        }
        Ok(report)
    });
    if let Err(error) = &outcome {
        if let Some(parent) = status_path.parent() {
            let _ = fs::create_dir_all(parent);
            let _ = write_json(
                &status_path,
                &json!({"state": "INCOMPLETE", "code": error.code, "reason": error.message}),
            );
        }
    }
    outcome
}

pub(super) fn init(
    project: &Path,
    core: &Path,
    template: &str,
    limited_grant: bool,
) -> Result<Value, CliFailure> {
    create_project(project, core, None, template, limited_grant)
}

pub(super) fn init_graph(
    project: &Path,
    core: &Path,
    spec: &Path,
    cases: &Path,
) -> Result<Value, CliFailure> {
    graph_project::init(project, core, spec, cases)
}

pub(super) fn artifact_import(
    artifact: &Path,
    project: &Path,
    core: &Path,
) -> Result<Value, CliFailure> {
    let metadata = fs::symlink_metadata(artifact)
        .map_err(|error| CliFailure::invalid(format!("cannot stat artifact: {error}")))?;
    if !metadata.file_type().is_file() || metadata.len() > 8_388_608 {
        return Err(CliFailure::invalid(
            "artifact must be a regular file of at most 8 MiB",
        ));
    }
    let bytes = fs::read(artifact)
        .map_err(|error| CliFailure::invalid(format!("cannot read artifact: {error}")))?;
    let header: Value = serde_json::from_slice(&bytes)
        .map_err(|error| CliFailure::invalid(format!("invalid artifact JSON: {error}")))?;
    if header["contract"] == "sley-tools.native-graph-artifact.v1" {
        return graph_project::import_artifact(artifact, &bytes, project, core);
    }
    if bytes.len() > 1_048_576 {
        return Err(CliFailure::invalid("fixed-profile artifact exceeds 1 MiB"));
    }
    let program_id = header["program_id"]
        .as_str()
        .ok_or_else(|| CliFailure::import_unsupported("artifact has no program profile"))?;
    create_project(project, core, Some(artifact), program_id, false)
}

struct Project {
    root: PathBuf,
    core: PathBuf,
    manifest: Value,
    state: Value,
    status: Value,
    builder: PathBuf,
}

fn read_json(path: &Path) -> Result<Value, CliFailure> {
    let size = fs::metadata(path)
        .map_err(|error| CliFailure::invalid(format!("cannot stat {}: {error}", path.display())))?
        .len();
    if size > 1_048_576 {
        return Err(CliFailure::invalid(format!(
            "{} exceeds 1 MiB",
            path.display()
        )));
    }
    serde_json::from_slice(
        &fs::read(path).map_err(|error| {
            CliFailure::invalid(format!("cannot read {}: {error}", path.display()))
        })?,
    )
    .map_err(|error| CliFailure::invalid(format!("invalid JSON in {}: {error}", path.display())))
}

fn load_project(path: &Path) -> Result<Project, CliFailure> {
    let root = path
        .canonicalize()
        .map_err(|error| CliFailure::invalid(format!("project path is unavailable: {error}")))?;
    if !root.is_dir() {
        return Err(CliFailure::invalid("project path is not a directory"));
    }
    let manifest = read_json(&root.join("program.json"))?;
    let state = read_json(&root.join(".sley-tools/state.json"))?;
    let status = read_json(&root.join(".sley-tools/status.json"))?;
    let configuration = read_json(&root.join("config.local.json"))?;
    let graph_profile = manifest["authoring_profile"] == "graph_v0";
    if manifest["contract"] != "sley-tools.program.v0"
        || (!graph_profile
            && !["release-readiness", "resource-admission"]
                .contains(&manifest["program_id"].as_str().unwrap_or("")))
        || status["state"] != "READY_EXTERNAL_ASSERTIONS_ONLY"
    {
        return Err(CliFailure::invalid(
            "project manifest or initialization status is unsupported",
        ));
    }
    if manifest["entrypoint"]["state_root"] != state["state_root"]
        || manifest["entrypoint"]["function_id"] != state["function_id"]
        || status["state_root"] != state["state_root"]
    {
        return Err(CliFailure::stale(
            "project manifest and local state disagree",
        ));
    }
    if state["commit_history"].as_array().map(Vec::len)
        != status["native_commit_count"]
            .as_u64()
            .map(|count| count as usize)
    {
        return Err(CliFailure::stale(
            "project commit history disagrees with initialization status",
        ));
    }
    if graph_profile {
        graph_project::verify_recipe(&root, &manifest, &state)?;
    }
    let core = configuration["core"]
        .as_str()
        .ok_or_else(|| CliFailure::invalid("trusted local configuration has no core path"))?;
    let core_report = core_check(PathBuf::from(core))?;
    if core_report["status"] != "compatible"
        || core_report["core"]["binary_sha256"] != configuration["core_binary_sha256"]
    {
        return Err(CliFailure::incompatible(
            "trusted local core selection no longer matches the pinned release",
        ));
    }
    let repository = TransactionRepository::new(&root.join("workspace"));
    let head = repository.accepted_head().map_err(|error| {
        CliFailure::stale(format!("cannot verify accepted project head: {error}"))
    })?;
    if hex(head.state_root().root.as_bytes()) != state["state_root"] {
        return Err(CliFailure::stale(
            "accepted core head differs from project state root",
        ));
    }
    let current = std::env::current_exe()
        .map_err(|error| CliFailure::failed(format!("cannot locate tools binary: {error}")))?;
    let builder = current.with_file_name("sley-tools-native-builder");
    if !builder.is_file() {
        return Err(CliFailure::failed(
            "sley-tools-native-builder is missing beside sley-tools",
        ));
    }
    Ok(Project {
        root,
        core: PathBuf::from(core),
        manifest,
        state,
        status,
        builder,
    })
}

pub(super) fn selected_core(path: &Path) -> Result<PathBuf, CliFailure> {
    Ok(load_project(path)?.core)
}

fn input_facts(input: &Value) -> Result<(bool, u32, bool, u32), CliFailure> {
    let object = input
        .as_object()
        .ok_or_else(|| CliFailure::invalid("input must be one JSON object"))?;
    let fields = [
        "test_run_completed",
        "tests_failed",
        "artifact_verified",
        "blocking_findings",
    ];
    if object.len() != fields.len() || fields.iter().any(|name| !object.contains_key(*name)) {
        return Err(CliFailure::invalid(
            "input must contain exactly the four Release Readiness fields",
        ));
    }
    let completed = object["test_run_completed"]
        .as_bool()
        .ok_or_else(|| CliFailure::invalid("test_run_completed must be Boolean"))?;
    let failed = object["tests_failed"]
        .as_u64()
        .and_then(|value| u32::try_from(value).ok())
        .ok_or_else(|| CliFailure::invalid("tests_failed must be an unsigned 32-bit integer"))?;
    let verified = object["artifact_verified"]
        .as_bool()
        .ok_or_else(|| CliFailure::invalid("artifact_verified must be Boolean"))?;
    let blocking = object["blocking_findings"]
        .as_u64()
        .and_then(|value| u32::try_from(value).ok())
        .ok_or_else(|| {
            CliFailure::invalid("blocking_findings must be an unsigned 32-bit integer")
        })?;
    Ok((completed, failed, verified, blocking))
}

fn execute_case(project: &Project, facts: (bool, u32, bool, u32)) -> Result<Value, CliFailure> {
    let state_root = project.state["state_root"]
        .as_str()
        .ok_or_else(|| CliFailure::invalid("project state omits root"))?;
    let function = project.state["function_id"]
        .as_str()
        .ok_or_else(|| CliFailure::invalid("project state omits function"))?;
    let completed = facts.0.to_string();
    let failed = facts.1.to_string();
    let verified = facts.2.to_string();
    let blocking = facts.3.to_string();
    let output = builder(
        &project.builder,
        "run",
        &[
            project
                .root
                .join("workspace")
                .to_str()
                .ok_or_else(|| CliFailure::invalid("project path is not UTF-8"))?,
            state_root,
            function,
            &completed,
            &failed,
            &verified,
            &blocking,
        ],
    )?;
    checked_release(project, output)
}

/// Bounded wall time for one case inside a batch worker.
const CASE_DEADLINE: Duration = Duration::from_secs(60);

/// One long-lived native worker for a batch of cases. Each request is
/// evaluated exactly like the one-shot `run` modes, without a new builder
/// process (and repository open) per case.
fn case_worker(project: &Project) -> Result<NativeWorker, CliFailure> {
    NativeWorker::start(
        &project.root,
        state_text(&project.state, "state_root")?,
        state_text(&project.state, "function_id")?,
        project.manifest["program_id"].as_str().unwrap_or(""),
        project.manifest["authoring_profile"].as_str().unwrap_or(""),
        project.manifest["graph_recipe"]["sha256"]
            .as_str()
            .unwrap_or(""),
    )
}

fn checked_release(project: &Project, output: Value) -> Result<Value, CliFailure> {
    if output["root"] != project.state["state_root"]
        || output["function_id"] != project.state["function_id"]
    {
        return Err(CliFailure::execution(
            "native result is not bound to requested root and function",
        ));
    }
    if !output["flags"]
        .as_array()
        .is_some_and(|flags| flags.len() == 5 && flags.iter().all(Value::is_boolean))
    {
        return Err(CliFailure::execution(
            "native result has an unexpected shape",
        ));
    }
    Ok(output)
}

fn reason_description(code: &str) -> Option<&'static str> {
    match code {
        "test_run_incomplete" => Some("The test run is incomplete."),
        "tests_failed" => Some("One or more tests failed."),
        "artifact_unverified" => Some("The artifact has not been verified."),
        "blocking_findings_exceeded" => Some("Blocking findings exceed the allowed threshold."),
        "invalid_request" => Some("Requested workers or memory are negative."),
        "invalid_capacity" => Some("Available workers or memory are negative."),
        "insufficient_workers" => Some("Requested workers exceed available workers."),
        "insufficient_memory" => Some("Requested memory exceeds available memory."),
        _ => None,
    }
}

fn resource_facts(input: &Value) -> Result<(i64, i64, i64, i64), CliFailure> {
    let object = input
        .as_object()
        .ok_or_else(|| CliFailure::invalid("input must be one JSON object"))?;
    let names = [
        "requested_workers",
        "available_workers",
        "requested_memory_mib",
        "available_memory_mib",
    ];
    if object.len() != names.len() || names.iter().any(|name| !object.contains_key(*name)) {
        return Err(CliFailure::invalid(
            "Resource Admission requires exactly four named integer inputs",
        ));
    }
    let values = names
        .iter()
        .map(|name| {
            object[*name]
                .as_i64()
                .filter(|value| *value >= 0)
                .ok_or_else(|| {
                    CliFailure::invalid(format!(
                        "{name} must be an integer from 0 through i64::MAX"
                    ))
                })
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok((values[0], values[1], values[2], values[3]))
}

fn execute_resource(project: &Project, facts: (i64, i64, i64, i64)) -> Result<Value, CliFailure> {
    let root = state_text(&project.state, "state_root")?;
    let function = state_text(&project.state, "function_id")?;
    let values = [
        facts.0.to_string(),
        facts.1.to_string(),
        facts.2.to_string(),
        facts.3.to_string(),
    ];
    let output = builder(
        &project.builder,
        "run-resource",
        &[
            project
                .root
                .join("workspace")
                .to_str()
                .ok_or_else(|| CliFailure::invalid("project path is not UTF-8"))?,
            root,
            function,
            &values[0],
            &values[1],
            &values[2],
            &values[3],
        ],
    )?;
    checked_resource(project, output)
}

fn checked_resource(project: &Project, output: Value) -> Result<Value, CliFailure> {
    if output["root"] != project.state["state_root"]
        || output["function_id"] != project.state["function_id"]
    {
        return Err(CliFailure::execution(
            "Resource Admission native result lost root or function binding",
        ));
    }
    Ok(output)
}

fn run_resource(project: &Project, input: &Path) -> Result<Value, CliFailure> {
    let facts = resource_facts(&read_json(input)?)?;
    let native = execute_resource(project, facts)?;
    let codes = native["reason_codes"]
        .as_array()
        .ok_or_else(|| CliFailure::execution("native result omitted reason codes"))?;
    let require_spare = project.state["require_memory_spare"]
        .as_bool()
        .ok_or_else(|| CliFailure::invalid("project state omits memory policy"))?;
    let descriptions = codes
        .iter()
        .map(|value| {
            let code = value
                .as_str()
                .ok_or_else(|| CliFailure::execution("native reason code is not text"))?;
            if require_spare && code == "insufficient_memory" {
                return Ok("Requested memory does not leave spare capacity.");
            }
            reason_description(code).ok_or_else(|| {
                CliFailure::execution("native result contains an unknown reason code")
            })
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok(json!({
        "contract": "sley-tools.run.v0", "program_id": "resource-admission", "state_root": native["root"],
        "result": native["result"], "reason_codes": native["reason_codes"], "reason_descriptions": descriptions,
        "observation_id": native["observation_id"], "execution_profile": native["profile"],
        "test_evidence_kind": "direct native VM execution"
    }))
}

pub(super) fn run(project: &Path, input: &Path) -> Result<Value, CliFailure> {
    let project = load_project(project)?;
    if project.manifest["authoring_profile"] == "graph_v0" {
        return graph_project::run(&project, input);
    }
    if project.manifest["program_id"] == "resource-admission" {
        return run_resource(&project, input);
    }
    let facts = input_facts(&read_json(input)?)?;
    let native = execute_case(&project, facts)?;
    let codes = native["reason_codes"]
        .as_array()
        .ok_or_else(|| CliFailure::execution("native result omitted reason codes"))?;
    let descriptions: Vec<_> = codes
        .iter()
        .map(|value| {
            let code = value
                .as_str()
                .ok_or_else(|| CliFailure::execution("native reason code is not text"))?;
            reason_description(code).ok_or_else(|| {
                CliFailure::execution("native result contains an unknown reason code")
            })
        })
        .collect::<Result<_, _>>()?;
    Ok(json!({
        "contract": "sley-tools.run.v0",
        "program_id": project.manifest["program_id"],
        "state_root": native["root"],
        "result": native["result"],
        "reason_codes": native["reason_codes"],
        "reason_descriptions": descriptions,
        "observation_id": native["observation_id"],
        "execution_profile": native["profile"],
        "test_evidence_kind": "direct native VM execution"
    }))
}

fn expected_flags(facts: (bool, u32, bool, u32), threshold: u32) -> [bool; 5] {
    let reasons = [!facts.0, facts.1 > 0, !facts.2, facts.3 > threshold];
    [
        !reasons.iter().any(|reason| *reason),
        reasons[0],
        reasons[1],
        reasons[2],
        reasons[3],
    ]
}

fn test_resource(project: &Project) -> Result<Value, CliFailure> {
    let require_spare = project.state["require_memory_spare"]
        .as_bool()
        .ok_or_else(|| CliFailure::invalid("project state omits memory policy"))?;
    let wide = 9_007_199_254_740_993_i64;
    let cases = [
        (0, 0, 0, 0),
        (1, 1, 4096, 4096),
        (i64::MAX, i64::MAX, i64::MAX, i64::MAX),
        (2, 1, 0, 0),
        (0, 0, 2, 1),
        (2, 1, 2, 1),
        (wide, wide - 1, 1, 1),
        (1, 1, wide, wide - 1),
        (i64::MAX, i64::MAX - 1, i64::MAX, i64::MAX - 1),
        (-1, 0, 0, 0),
        (0, -1, 0, 0),
        (0, 0, -1, -2),
    ];
    let mut worker = case_worker(project)?;
    for facts in cases {
        let observed = checked_resource(
            project,
            worker.call(
                NativeInput::Resource([facts.0, facts.1, facts.2, facts.3]),
                CASE_DEADLINE,
            )?,
        )?;
        let invalid_request = facts.0 < 0 || facts.2 < 0;
        let invalid_capacity = facts.1 < 0 || facts.3 < 0;
        let insufficient_workers = facts.0 > facts.1;
        let insufficient_memory = facts.2 > facts.3 || (require_spare && facts.2 == facts.3);
        let expected = json!({
            "allow": !(invalid_request || invalid_capacity || insufficient_workers || insufficient_memory),
            "invalid_request": invalid_request, "invalid_capacity": invalid_capacity,
            "insufficient_workers": insufficient_workers, "insufficient_memory": insufficient_memory,
            "requested_memory_mib": facts.2.to_string(), "available_memory_mib": facts.3.to_string(),
        });
        if observed["result"] != expected {
            return Err(CliFailure::execution(format!(
                "Resource Admission native result differs from independent oracle at {facts:?}"
            )));
        }
    }
    Ok(json!({
        "contract": "sley-tools.test.v0", "result": "PASS", "program_id": "resource-admission",
        "state_root": project.state["state_root"],
        "external_assertions_against_native_execution": cases.len(),
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
        "wide_signed_input_and_result_checked": true,
        "require_memory_spare": require_spare,
    }))
}

pub(super) fn test(path: &Path) -> Result<Value, CliFailure> {
    let project = load_project(path)?;
    if project.manifest["authoring_profile"] == "graph_v0" {
        return graph_project::test(&project);
    }
    if project.manifest["program_id"] == "resource-admission" {
        return test_resource(&project);
    }
    let threshold = project.state["threshold"]
        .as_u64()
        .and_then(|value| u32::try_from(value).ok())
        .ok_or_else(|| CliFailure::invalid("project state has invalid threshold"))?;
    let mut blocking_values = vec![0, 1, threshold, threshold.saturating_add(1), u32::MAX];
    blocking_values.sort_unstable();
    blocking_values.dedup();
    let mut count = 0_u32;
    let mut worker = case_worker(&project)?;
    for completed in [false, true] {
        for failed in [0, 1, u32::MAX] {
            for verified in [false, true] {
                let mut prior_allow = true;
                for blocking in &blocking_values {
                    let facts = (completed, failed, verified, *blocking);
                    let native = checked_release(
                        &project,
                        worker.call(NativeInput::Release(facts), CASE_DEADLINE)?,
                    )?;
                    let expected = expected_flags(facts, threshold);
                    let observed: Vec<_> = native["flags"]
                        .as_array()
                        .ok_or_else(|| CliFailure::execution("native flags are absent"))?
                        .iter()
                        .map(|value| value.as_bool().unwrap_or(false))
                        .collect();
                    if observed != expected || (!prior_allow && observed[0]) {
                        return Err(CliFailure::execution(format!(
                            "native result differs from external oracle at input {facts:?}"
                        )));
                    }
                    prior_allow = observed[0];
                    count += 1;
                }
            }
        }
    }
    Ok(json!({
        "contract": "sley-tools.test.v0",
        "result": "PASS",
        "program_id": project.manifest["program_id"],
        "state_root": project.state["state_root"],
        "external_assertions_against_native_execution": count,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
        "threshold": threshold,
    }))
}

pub(super) fn inspect(path: &Path) -> Result<Value, CliFailure> {
    let project = load_project(path)?;
    let principal_bytes: [u8; 32] = unhex(state_text(&project.state, "principal_id")?)?
        .try_into()
        .map_err(|_| CliFailure::failed("project principal identity is not 32 bytes"))?;
    let principal = PrincipalId::from_bytes(principal_bytes);
    let repository = TransactionRepository::new(&project.root.join("workspace"));
    let head = repository
        .accepted_head()
        .map_err(|error| CliFailure::failed(format!("cannot inspect accepted policy: {error}")))?;
    let policy = head.policy_root();
    let grant = policy.principal_grant(principal).map_err(|error| {
        CliFailure::failed(format!("accepted principal grant is absent: {error}"))
    })?;
    let mutation_classes = grant
        .allowed_mutation_class_tags()
        .iter()
        .map(|tag| {
            let name = u16::try_from(*tag)
                .ok()
                .and_then(MutationClass::from_tag)
                .map(|class| format!("{class:?}"));
            json!({"tag": tag, "name": name})
        })
        .collect::<Vec<_>>();
    let mut report = json!({
        "contract": "sley-tools.inspect.v0",
        "program": project.manifest,
        "accepted_transaction_id": project.state["transaction_id"],
        "accepted_state_root": project.state["state_root"],
        "threshold": project.state.get("threshold"),
        "initialization": project.status,
        "root_verified_against_local_core_repository": true,
        "authority": {
            "source": "accepted_workspace_policy",
            "policy_root": hex(policy.root().as_bytes()),
            "principal_id": hex(principal.as_bytes()),
            "allowed_mutation_classes": mutation_classes,
            "replace_entity_version_allowed": grant.allowed_mutation_class_tags()
                .binary_search(&u32::from(MutationClass::ReplaceEntityVersion.tag())).is_ok(),
        },
    });
    if project.manifest["authoring_profile"] == "graph_v0" {
        report["authoring"] = graph_project::inspect_authoring(&project)?;
    }
    Ok(report)
}

/// Export only the verified program objects. Repository exchange also carries
/// receipts and policy grants, so it is unsuitable as a public program file.
pub(super) fn artifact_export(
    path: &Path,
    output: &Path,
    bind_manifest: bool,
) -> Result<Value, CliFailure> {
    let project = load_project(path)?;
    let graph_profile = project.manifest["authoring_profile"] == "graph_v0";
    let bound_name = if bind_manifest {
        let name = output
            .file_name()
            .and_then(|value| value.to_str())
            .ok_or_else(|| CliFailure::invalid("bound artifact output has no UTF-8 file name"))?;
        if name == "."
            || name == ".."
            || name.is_empty()
            || !name
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
        {
            return Err(CliFailure::invalid(
                "bound artifact file name must use letters, digits, dot, underscore, or dash",
            ));
        }
        let folder = output
            .parent()
            .ok_or_else(|| CliFailure::invalid("bound artifact output has no parent"))?;
        let parent = folder
            .parent()
            .ok_or_else(|| {
                CliFailure::invalid("bound artifact output must be in PROJECT/artifact")
            })?
            .canonicalize()
            .map_err(|error| {
                CliFailure::invalid(format!("bound artifact parent is unavailable: {error}"))
            })?;
        if folder.file_name().and_then(|value| value.to_str()) != Some("artifact")
            || parent != project.root
        {
            return Err(CliFailure::invalid(
                "bound artifact output must be in PROJECT/artifact",
            ));
        }
        match fs::symlink_metadata(folder) {
            Ok(metadata) if !metadata.file_type().is_dir() => {
                return Err(CliFailure::invalid(
                    "project artifact directory is not a real directory",
                ));
            }
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => {
                return Err(CliFailure::invalid(format!(
                    "cannot inspect artifact directory: {error}"
                )));
            }
        }
        Some(format!("artifact/{name}"))
    } else {
        None
    };
    let resource = !graph_profile && project.manifest["program_id"] == "resource-admission";
    let assertions = test(path)?;
    let repository = TransactionRepository::new(&project.root.join("workspace"));
    let head = repository.accepted_head().map_err(|error| {
        CliFailure::stale(format!("cannot verify accepted head for export: {error}"))
    })?;
    if hex(head.state_root().root.as_bytes()) != state_text(&project.state, "state_root")? {
        return Err(CliFailure::stale("accepted head changed before export"));
    }
    let epoch = state_text(&project.state, "schema_epoch")?;
    if hex(head.state_root().record.schema_epoch_id.as_bytes()) != epoch {
        return Err(CliFailure::stale(
            "accepted schema epoch changed before export",
        ));
    }
    let mut namespace_count = 0_usize;
    let mut counts = [0_usize; 6];
    let mut objects = Vec::new();
    let mut function_present = false;
    let mut threshold_present = false;
    for object in head.objects() {
        let kind = object.record().body.kind_tag();
        if kind == 3 {
            namespace_count += 1;
            continue;
        }
        if !(4..=9).contains(&kind) {
            return Err(CliFailure::invalid(format!(
                "accepted project contains unsupported artifact entity kind {kind}"
            )));
        }
        counts[usize::from(kind - 4)] += 1;
        let entity = hex(object.record().entity_id.as_bytes());
        if kind == 5 && entity == state_text(&project.state, "function_id")? {
            function_present = true;
        }
        if !resource
            && !graph_profile
            && kind == 9
            && entity == state_text(&project.state, "threshold_id")?
        {
            if hex(object.object_id().as_bytes())
                != state_text(&project.state, "threshold_object_id")?
            {
                return Err(CliFailure::stale(
                    "threshold object differs from local state",
                ));
            }
            threshold_present = true;
        }
        if resource && kind == 9 {
            let EntityBodyValue::Constant(constant) = &object.record().body else {
                return Err(CliFailure::invalid(
                    "Resource Admission constant has wrong entity body",
                ));
            };
            if constant.value.value_type
                != sley_ssmc::TypeExpr::SInt(sley_ssmc::IntegerWidth::from_bits(64))
                || constant.value.data != sley_ssmc::ConstData::SInt(0)
            {
                return Err(CliFailure::invalid(
                    "Resource Admission native zero constant differs from profile",
                ));
            }
        }
        objects.push(json!({
            "entity_id": entity,
            "object_id": hex(object.object_id().as_bytes()),
            "kind": kind,
            "stored_hex": hex(object.stored_bytes()),
        }));
    }
    let graph_counts_valid = counts[0] <= 1
        && counts[1] == 1
        && counts[2] <= 16
        && (1..=16).contains(&counts[3])
        && counts[4] <= 128
        && counts[5] <= 64
        && counts.iter().sum::<usize>() <= 226;
    if namespace_count != 1
        || (graph_profile && !graph_counts_valid)
        || (!graph_profile
            && counts
                != if resource {
                    [1, 1, 4, 1, 14, 1]
                } else {
                    [1, 1, 4, 1, 11, 2]
                })
        || !function_present
        || (!resource && !graph_profile && !threshold_present)
    {
        return Err(CliFailure::invalid(
            "accepted graph is outside its native artifact profile",
        ));
    }
    let mut artifact = json!({
        "contract": if graph_profile { "sley-tools.native-graph-artifact.v1" } else { "sley-tools.native-program-artifact.v0" },
        "program_id": project.manifest["program_id"],
        "core_commit": super::TESTED_CORE_COMMIT,
        "schema_epoch_id": epoch,
        "source_state_root": project.state["state_root"],
        "source_function_id": project.state["function_id"],
        "result_fields": project.manifest["result_fields"],
        "objects": objects,
        "test_evidence": {
            "kind": "external_assertions_against_native_execution",
            "count": assertions["external_assertions_against_native_execution"],
            "native_core_test_admission": "NOT_RUN: production native executor unavailable"
        }
    });
    if graph_profile {
        let recipe_bytes =
            graph_project::read_regular(&project.root.join("build/graph.json"), 65_536)?;
        let cases_bytes =
            graph_project::read_regular(&project.root.join("tests/cases.json"), 1_048_576)?;
        artifact["source_result_type_id"] = project.manifest["result_type_id"].clone();
        artifact["source_constant_ids"] = project.state["constant_ids"].clone();
        artifact["recipe_sha256"] = json!(format!("{:x}", Sha256::digest(&recipe_bytes)));
        artifact["cases_sha256"] = json!(format!("{:x}", Sha256::digest(&cases_bytes)));
        artifact["recipe_json"] = json!(
            String::from_utf8(recipe_bytes)
                .map_err(|_| CliFailure::invalid("bound graph recipe is not UTF-8"))?
        );
        artifact["cases_json"] = json!(
            String::from_utf8(cases_bytes)
                .map_err(|_| CliFailure::invalid("graph cases are not UTF-8"))?
        );
    } else if !resource {
        artifact["source_threshold_id"] = project.state["threshold_id"].clone();
        artifact["source_threshold_object_id"] = project.state["threshold_object_id"].clone();
        artifact["threshold"] = project.state["threshold"].clone();
    }
    let mut bytes = serde_json::to_vec_pretty(&artifact)
        .map_err(|error| CliFailure::failed(format!("cannot encode native artifact: {error}")))?;
    bytes.push(b'\n');
    if bytes.len() > if graph_profile { 8_388_608 } else { 1_048_576 } {
        return Err(CliFailure::invalid(
            "native artifact exceeds its profile limit",
        ));
    }
    let digest = format!("{:x}", Sha256::digest(&bytes));
    let output_path = bound_name
        .as_ref()
        .map(|relative| project.root.join(relative))
        .unwrap_or_else(|| output.to_path_buf());
    let manifest_update = if let Some(relative) = &bound_name {
        let mut manifest = project.manifest.clone();
        manifest["artifact"] = json!({
            "contract": if graph_profile { "sley-tools.native-graph-artifact.v1" } else { "sley-tools.native-program-artifact.v0" },
            "path": relative,
            "sha256": digest,
        });
        manifest["status"] = json!("portable_native_artifact");
        let mut manifest_bytes = serde_json::to_vec_pretty(&manifest).map_err(|error| {
            CliFailure::failed(format!("cannot encode bound manifest: {error}"))
        })?;
        manifest_bytes.push(b'\n');
        if manifest_bytes.len() > 1_048_576 {
            return Err(CliFailure::invalid("bound manifest exceeds 1 MiB"));
        }
        let manifest_digest = format!("{:x}", Sha256::digest(&manifest_bytes));
        let temporary = project.root.join(format!(
            ".sley-tools/bound-manifest-{}.json",
            hex(&random32()?[..8])
        ));
        Some((manifest_bytes, temporary, manifest_digest))
    } else {
        None
    };
    if bind_manifest {
        fs::create_dir(project.root.join("artifact"))
            .or_else(|error| {
                if error.kind() == std::io::ErrorKind::AlreadyExists {
                    Ok(())
                } else {
                    Err(error)
                }
            })
            .map_err(|error| {
                CliFailure::failed(format!("cannot create artifact directory: {error}"))
            })?;
        if !fs::symlink_metadata(project.root.join("artifact"))
            .map_err(|error| {
                CliFailure::failed(format!("cannot inspect artifact directory: {error}"))
            })?
            .file_type()
            .is_dir()
        {
            return Err(CliFailure::invalid(
                "project artifact directory is not a real directory",
            ));
        }
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&output_path)
        .map_err(|error| {
            CliFailure::invalid(format!("cannot create new artifact file: {error}"))
        })?;
    if let Err(error) = file.write_all(&bytes).and_then(|()| file.sync_all()) {
        let _ = fs::remove_file(&output_path);
        return Err(CliFailure::failed(format!(
            "cannot finish native artifact: {error}"
        )));
    }
    let manifest_sha256 =
        if let Some((manifest_bytes, temporary, manifest_digest)) = manifest_update {
            let update = (|| -> Result<(), CliFailure> {
                let mut next = OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&temporary)
                    .map_err(|error| {
                        CliFailure::failed(format!("cannot create bound manifest: {error}"))
                    })?;
                next.write_all(&manifest_bytes)
                    .and_then(|()| next.sync_all())
                    .map_err(|error| {
                        CliFailure::failed(format!("cannot write bound manifest: {error}"))
                    })?;
                fs::rename(&temporary, project.root.join("program.json")).map_err(|error| {
                    CliFailure::failed(format!("cannot activate bound manifest: {error}"))
                })
            })();
            if let Err(error) = update {
                let _ = fs::remove_file(&temporary);
                let _ = fs::remove_file(&output_path);
                return Err(error);
            }
            Some(manifest_digest)
        } else {
            None
        };
    Ok(json!({
        "contract": "sley-tools.artifact-export.v0",
        "artifact": output_path,
        "sha256": digest,
        "object_count": counts.iter().sum::<usize>(),
        "source_state_root": project.state["state_root"],
        "external_assertions_against_native_execution": assertions["external_assertions_against_native_execution"],
        "authority_included": false,
        "manifest_bound": bind_manifest,
        "manifest_sha256": manifest_sha256,
    }))
}

fn state_text<'a>(state: &'a Value, field: &str) -> Result<&'a str, CliFailure> {
    state[field]
        .as_str()
        .ok_or_else(|| CliFailure::invalid(format!("project state omits {field}")))
}

fn current_millis() -> Result<u64, CliFailure> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| CliFailure::failed(format!("cannot read validation clock: {error}")))?;
    u64::try_from(duration.as_millis())
        .map_err(|_| CliFailure::failed("validation clock exceeds u64 milliseconds"))
}

fn observed_boundary(
    builder_path: &Path,
    repository: &Path,
    root: &str,
    function: &str,
    blocking: u32,
) -> Result<Value, CliFailure> {
    builder(
        builder_path,
        "run",
        &[
            repository
                .to_str()
                .ok_or_else(|| CliFailure::invalid("project path is not UTF-8"))?,
            root,
            function,
            "true",
            "0",
            "true",
            &blocking.to_string(),
        ],
    )
}

fn perform_edit(
    project: &mut Project,
    threshold: u32,
    previous: u32,
    accepted_commit: &mut bool,
    prepared: Option<&Value>,
) -> Result<Value, CliFailure> {
    let state_dir = project.root.join(".sley-tools");
    let repository = project.root.join("workspace");
    let old_root = state_text(&project.state, "state_root")?.to_owned();
    let old_transaction = state_text(&project.state, "transaction_id")?.to_owned();
    let function = state_text(&project.state, "function_id")?.to_owned();
    let comparison = if threshold > previous {
        previous + 1
    } else {
        threshold + 1
    };
    let before = observed_boundary(
        &project.builder,
        &repository,
        &old_root,
        &function,
        comparison,
    )?;

    let context = [
        state_text(&project.state, "workspace_id")?,
        state_text(&project.state, "principal_id")?,
        &old_root,
        state_text(&project.state, "policy_root")?,
        &old_transaction,
        state_text(&project.state, "schema_epoch")?,
        state_text(&project.state, "threshold_id")?,
        state_text(&project.state, "threshold_object_id")?,
        &threshold.to_string(),
    ];
    let edit = match prepared {
        Some(value) => value.clone(),
        None => builder(&project.builder, "edit", &context)?,
    };
    let candidate_id = edit["candidate_id"]
        .as_str()
        .ok_or_else(|| CliFailure::failed("builder omitted edit candidate identity"))?;
    let pending_path = state_dir.join("pending-edit.json");
    write_json(
        &pending_path,
        &json!({
            "phase": "PROPOSED",
            "candidate_id": candidate_id,
            "base_root": old_root,
            "expected_parent": old_transaction,
            "old_threshold": previous,
            "new_threshold": threshold,
        }),
    )?;
    let handshake_dir = state_dir.join(format!("handshake-edit-{}", &hex(&random32()?)[..16]));
    fs::create_dir(&handshake_dir)
        .map_err(|error| CliFailure::failed(format!("cannot create edit handshake: {error}")))?;
    let hello = offered_hello(&project.core)?;
    let handshake_id = handshake(&project.core, &handshake_dir, &hello)?;
    let mut endpoint = Smp::start(&project.core, &repository, &hello)?;
    let session = endpoint.request("session.open", &handshake_id, 0, None)?;
    let opened = endpoint.request("workspace.open", "", 1, Some(&session))?;
    let open_fields = record_fields(&unhex(&opened)?, 8)?;
    if hex(&open_fields[0]) != old_transaction || hex(&open_fields[1]) != old_root {
        return Err(CliFailure::stale(
            "accepted head advanced before threshold proposal",
        ));
    }
    let candidate_record = edit["candidate_record_hex"]
        .as_str()
        .ok_or_else(|| CliFailure::failed("builder omitted edit candidate bytes"))?;
    let stored = endpoint.request("candidate.create", candidate_record, 2, Some(&session))?;
    if stored != edit["candidate_stored_hex"] {
        return Err(CliFailure::failed(
            "edit candidate differs from public core storage",
        ));
    }
    let validation_body = hex(&encode_record(&[
        (1, unhex(&old_transaction)?),
        (2, unhex(state_text(&project.state, "principal_id")?)?),
        (3, encode_uvar(current_millis()?)),
        (4, unhex(&stored)?),
    ])
    .map_err(|error| CliFailure::failed(format!("edit validation request: {error}")))?);
    let validation = endpoint.request("candidate.validate", &validation_body, 3, Some(&session))?;
    require_valid_candidate(&validation, &candidate_id)?;
    write_json(
        &pending_path,
        &json!({
            "phase": "COMMIT_REQUESTED",
            "candidate_id": candidate_id,
            "base_root": old_root,
            "expected_parent": old_transaction,
            "old_threshold": previous,
            "new_threshold": threshold,
        }),
    )?;
    let committed = match endpoint.request("commit", &validation_body, 4, Some(&session)) {
        Ok(result) => result,
        Err(error) if error.core_symbol.is_none() => {
            return Err(CliFailure::outcome_unknown(format!(
                "commit outcome is unknown for candidate {candidate_id}; inspect accepted head before retrying: {}",
                error.message
            )));
        }
        Err(error) => return Err(error),
    };
    *accepted_commit = true;
    let commit_fields = record_fields(&unhex(&committed)?, 4)?;
    if commit_fields[0].len() != 32 || commit_fields[1].len() != 32 || commit_fields[2].len() != 32
    {
        return Err(CliFailure::failed(
            "edit commit returned invalid identities",
        ));
    }
    let transaction = hex(&commit_fields[0]);
    let root = hex(&commit_fields[2]);
    if root == old_root {
        return Err(CliFailure::failed(
            "edit commit did not change accepted root",
        ));
    }
    let _ = endpoint.request("session.close", "", 5, Some(&session));
    let _ = endpoint.finish();
    let after = observed_boundary(&project.builder, &repository, &root, &function, comparison)?;
    if before["result"]["allow"] == after["result"]["allow"] {
        return Err(CliFailure::execution(
            "native threshold edit did not change boundary behavior",
        ));
    }
    let new_object = edit["threshold_object_id"]
        .as_str()
        .ok_or_else(|| CliFailure::failed("builder omitted new threshold object"))?;
    let receipt = hex(&commit_fields[1]);
    project.state["transaction_id"] = json!(transaction);
    project.state["state_root"] = json!(root);
    project.state["candidate_id"] = json!(candidate_id);
    project.state["commit_receipt_id"] = json!(receipt);
    project.state["threshold_object_id"] = json!(new_object);
    project.state["threshold"] = json!(threshold);
    project.state["commit_history"]
        .as_array_mut()
        .ok_or_else(|| CliFailure::stale("project commit history is absent"))?
        .push(json!({
            "kind": "threshold_edit",
            "candidate_id": candidate_id,
            "transaction_id": transaction,
            "state_root": root,
            "receipt_id": receipt,
            "old_threshold": previous,
            "new_threshold": threshold,
        }));
    project.manifest["entrypoint"]["state_root"] = json!(root);
    if project.manifest["artifact"].is_object() {
        project.manifest["imported_source_artifact"] = project.manifest["artifact"].clone();
        project.manifest["artifact"] = Value::Null;
    }
    project.manifest["program_version"] = json!(format!(
        "0.1.{}-local",
        project.state["commit_history"]
            .as_array()
            .map_or(0, Vec::len)
            - 1
    ));
    write_json(&state_dir.join("state.json"), &project.state)?;
    write_json(&project.root.join("program.json"), &project.manifest)?;
    project.status["state"] = json!("READY_EXTERNAL_ASSERTIONS_ONLY");
    project.status["state_root"] = json!(root);
    project.status["native_commit_count"] = json!(
        project.state["commit_history"]
            .as_array()
            .map_or(0, Vec::len)
    );
    write_json(&state_dir.join("status.json"), &project.status)?;
    // The accepted root and local READY state are durable at this point. A stale
    // diagnostic marker must not turn a successful native commit into a CLI error.
    let _ = fs::remove_file(&pending_path);
    Ok(json!({
        "contract": "sley-tools.edit.v0",
        "program_id": "release-readiness",
        "candidate_id": candidate_id,
        "receipt_id": receipt,
        "old_root": old_root,
        "new_root": root,
        "old_threshold": previous,
        "new_threshold": threshold,
        "comparison_blocking_findings": comparison,
        "before": before["result"],
        "after": after["result"],
        "native_commit_count": project.status["native_commit_count"],
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }))
}

fn edit_with_prepared(
    path: &Path,
    threshold: u32,
    prepared: Option<&Value>,
) -> Result<Value, CliFailure> {
    let mut project = load_project(path)?;
    let previous = project.state["threshold"]
        .as_u64()
        .and_then(|value| u32::try_from(value).ok())
        .ok_or_else(|| CliFailure::invalid("project state has invalid threshold"))?;
    if threshold == previous {
        return Err(CliFailure::invalid(
            "new threshold is identical to the accepted threshold",
        ));
    }
    let status_path = project.root.join(".sley-tools/status.json");
    let prior_status = project.status.clone();
    project.status["state"] = json!("EDIT_IN_PROGRESS");
    write_json(&status_path, &project.status)?;
    let mut committed = false;
    let result = perform_edit(&mut project, threshold, previous, &mut committed, prepared);
    if let Err(error) = &result {
        if committed {
            return Err(CliFailure::outcome_unknown(format!(
                "native edit committed, but local project update is incomplete; reconcile candidate and accepted head: {}",
                error.message
            )));
        }
        if !committed && error.code != "ST_COMMIT_OUTCOME_UNKNOWN" {
            let _ = write_json(&status_path, &prior_status);
            let pending = project.root.join(".sley-tools/pending-edit.json");
            if pending.is_file() {
                let _ = write_json(
                    &project.root.join(".sley-tools/last-edit-rejection.json"),
                    &json!({
                        "code": error.code,
                        "core_symbol": error.core_symbol,
                        "message": error.message,
                        "accepted_root_unchanged": true,
                    }),
                );
                let _ = fs::remove_file(pending);
            }
        }
    }
    result
}

pub(super) fn edit(path: &Path, threshold: u32) -> Result<Value, CliFailure> {
    edit_with_prepared(path, threshold, None)
}

fn perform_resource_edit(
    project: &mut Project,
    require_spare: bool,
    committed: &mut bool,
    prepared: Option<&Value>,
) -> Result<Value, CliFailure> {
    let state_dir = project.root.join(".sley-tools");
    let repository = project.root.join("workspace");
    let old_root = state_text(&project.state, "state_root")?.to_owned();
    let old_transaction = state_text(&project.state, "transaction_id")?.to_owned();
    let function = state_text(&project.state, "function_id")?.to_owned();
    let previous = project.state["require_memory_spare"]
        .as_bool()
        .ok_or_else(|| CliFailure::invalid("project state omits memory policy"))?;
    let before = execute_resource(project, (1, 1, 4096, 4096))?;
    let desired = require_spare.to_string();
    let edit = match prepared {
        Some(value) => value.clone(),
        None => builder(
            &project.builder,
            "edit-resource",
            &[
                state_text(&project.state, "workspace_id")?,
                state_text(&project.state, "principal_id")?,
                &old_root,
                state_text(&project.state, "policy_root")?,
                &old_transaction,
                state_text(&project.state, "schema_epoch")?,
                repository
                    .to_str()
                    .ok_or_else(|| CliFailure::invalid("project path is not UTF-8"))?,
                state_text(&project.state, "memory_comparison_id")?,
                state_text(&project.state, "memory_comparison_object_id")?,
                &desired,
            ],
        )?,
    };
    let candidate_id = edit["candidate_id"].as_str().ok_or_else(|| {
        CliFailure::failed("builder omitted Resource Admission candidate identity")
    })?;
    let pending_path = state_dir.join("pending-edit.json");
    write_json(
        &pending_path,
        &json!({
            "phase": "PROPOSED", "candidate_id": candidate_id, "base_root": old_root,
            "expected_parent": old_transaction, "old_require_memory_spare": previous,
            "new_require_memory_spare": require_spare,
        }),
    )?;
    let handshake_dir = state_dir.join(format!("handshake-edit-{}", &hex(&random32()?)[..16]));
    fs::create_dir(&handshake_dir)
        .map_err(|error| CliFailure::failed(format!("cannot create edit handshake: {error}")))?;
    let hello = offered_hello(&project.core)?;
    let handshake_id = handshake(&project.core, &handshake_dir, &hello)?;
    let mut endpoint = Smp::start(&project.core, &repository, &hello)?;
    let session = endpoint.request("session.open", &handshake_id, 0, None)?;
    let opened = endpoint.request("workspace.open", "", 1, Some(&session))?;
    let open_fields = record_fields(&unhex(&opened)?, 8)?;
    if hex(&open_fields[0]) != old_transaction || hex(&open_fields[1]) != old_root {
        return Err(CliFailure::stale(
            "accepted head advanced before Resource Admission proposal",
        ));
    }
    let candidate_record = edit["candidate_record_hex"]
        .as_str()
        .ok_or_else(|| CliFailure::failed("builder omitted Resource Admission candidate bytes"))?;
    let stored = endpoint.request("candidate.create", candidate_record, 2, Some(&session))?;
    if stored != edit["candidate_stored_hex"] {
        return Err(CliFailure::failed(
            "Resource Admission candidate differs from public core storage",
        ));
    }
    let validation_body = hex(&encode_record(&[
        (1, unhex(&old_transaction)?),
        (2, unhex(state_text(&project.state, "principal_id")?)?),
        (3, encode_uvar(current_millis()?)),
        (4, unhex(&stored)?),
    ])
    .map_err(|error| CliFailure::failed(format!("edit validation request: {error}")))?);
    let validation = endpoint.request("candidate.validate", &validation_body, 3, Some(&session))?;
    require_valid_candidate(&validation, &candidate_id)?;
    write_json(
        &pending_path,
        &json!({
            "phase": "COMMIT_REQUESTED", "candidate_id": candidate_id, "base_root": old_root,
            "expected_parent": old_transaction, "old_require_memory_spare": previous,
            "new_require_memory_spare": require_spare,
        }),
    )?;
    let response = match endpoint.request("commit", &validation_body, 4, Some(&session)) {
        Ok(value) => value,
        Err(error) if error.core_symbol.is_none() => {
            return Err(CliFailure::outcome_unknown(format!(
                "commit outcome is unknown for candidate {candidate_id}; inspect accepted head before retrying: {}",
                error.message,
            )));
        }
        Err(error) => return Err(error),
    };
    *committed = true;
    let fields = record_fields(&unhex(&response)?, 4)?;
    if fields[0].len() != 32 || fields[1].len() != 32 || fields[2].len() != 32 {
        return Err(CliFailure::failed(
            "Resource Admission commit returned invalid identities",
        ));
    }
    let transaction = hex(&fields[0]);
    let root = hex(&fields[2]);
    let receipt = hex(&fields[1]);
    if root == old_root {
        return Err(CliFailure::failed(
            "Resource Admission edit did not change accepted root",
        ));
    }
    let _ = endpoint.request("session.close", "", 5, Some(&session));
    let _ = endpoint.finish();
    let after = builder(
        &project.builder,
        "run-resource",
        &[
            repository
                .to_str()
                .ok_or_else(|| CliFailure::invalid("project path is not UTF-8"))?,
            &root,
            &function,
            "1",
            "1",
            "4096",
            "4096",
        ],
    )?;
    if before["result"]["allow"] == after["result"]["allow"]
        || after["result"]["allow"] != !require_spare
        || before["result"]["requested_memory_mib"] != after["result"]["requested_memory_mib"]
    {
        return Err(CliFailure::execution(
            "native Resource Admission edit did not change only the intended boundary",
        ));
    }
    let new_object = edit["memory_comparison_object_id"]
        .as_str()
        .ok_or_else(|| CliFailure::failed("builder omitted new memory comparison object"))?;
    project.state["transaction_id"] = json!(transaction);
    project.state["state_root"] = json!(root);
    project.state["candidate_id"] = json!(candidate_id);
    project.state["commit_receipt_id"] = json!(receipt);
    project.state["memory_comparison_object_id"] = json!(new_object);
    project.state["require_memory_spare"] = json!(require_spare);
    project.state["commit_history"]
        .as_array_mut()
        .ok_or_else(|| CliFailure::stale("project commit history is absent"))?
        .push(json!({
            "kind": "memory_spare_edit", "candidate_id": candidate_id,
            "transaction_id": transaction, "state_root": root, "receipt_id": receipt,
            "old_require_memory_spare": previous, "new_require_memory_spare": require_spare,
        }));
    let verification = test_resource(project)?;
    project.manifest["entrypoint"]["state_root"] = json!(root);
    if project.manifest["artifact"].is_object() {
        project.manifest["imported_source_artifact"] = project.manifest["artifact"].clone();
        project.manifest["artifact"] = Value::Null;
    }
    project.manifest["native_logic"] = json!(if require_spare {
        "signed comparisons; exact memory fit requires spare capacity"
    } else {
        "signed comparisons; exact memory fit is allowed"
    });
    project.manifest["worked_change"] = json!({
        "operation": "replace exact memory capacity comparison under old-object precondition",
        "initial_require_memory_spare": false, "example_require_memory_spare": true,
    });
    project.manifest["program_version"] = json!(format!(
        "0.1.{}-local",
        project.state["commit_history"]
            .as_array()
            .map_or(0, Vec::len)
            - 1
    ));
    write_json(&state_dir.join("state.json"), &project.state)?;
    write_json(&project.root.join("program.json"), &project.manifest)?;
    project.status["state"] = json!("READY_EXTERNAL_ASSERTIONS_ONLY");
    project.status["state_root"] = json!(root);
    project.status["native_commit_count"] = json!(
        project.state["commit_history"]
            .as_array()
            .map_or(0, Vec::len)
    );
    write_json(&state_dir.join("status.json"), &project.status)?;
    let _ = fs::remove_file(&pending_path);
    Ok(json!({
        "contract": "sley-tools.edit.v0", "program_id": "resource-admission",
        "candidate_id": candidate_id, "receipt_id": receipt,
        "old_root": old_root, "new_root": root,
        "old_require_memory_spare": previous, "new_require_memory_spare": require_spare,
        "before": before["result"], "after": after["result"],
        "native_commit_count": project.status["native_commit_count"],
        "verification": verification,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }))
}

fn edit_resource_with_prepared(
    path: &Path,
    require_spare: bool,
    prepared: Option<&Value>,
) -> Result<Value, CliFailure> {
    let mut project = load_project(path)?;
    if project.manifest["program_id"] != "resource-admission" {
        return Err(CliFailure::invalid(
            "memory spare edit requires a Resource Admission project",
        ));
    }
    let previous = project.state["require_memory_spare"]
        .as_bool()
        .ok_or_else(|| CliFailure::invalid("project state omits memory policy"))?;
    if previous == require_spare {
        return Err(CliFailure::invalid(
            "new memory spare policy matches accepted policy",
        ));
    }
    let status_path = project.root.join(".sley-tools/status.json");
    let prior_status = project.status.clone();
    project.status["state"] = json!("EDIT_IN_PROGRESS");
    write_json(&status_path, &project.status)?;
    let mut committed = false;
    let result = perform_resource_edit(&mut project, require_spare, &mut committed, prepared);
    if let Err(error) = &result {
        if committed {
            return Err(CliFailure::outcome_unknown(format!(
                "native Resource Admission edit committed, but local project update is incomplete; reconcile candidate and accepted head: {}",
                error.message
            )));
        }
        if !committed && error.code != "ST_COMMIT_OUTCOME_UNKNOWN" {
            let _ = write_json(&status_path, &prior_status);
            let pending = project.root.join(".sley-tools/pending-edit.json");
            if pending.is_file() {
                let _ = write_json(
                    &project.root.join(".sley-tools/last-edit-rejection.json"),
                    &json!({
                        "code": error.code, "core_symbol": error.core_symbol,
                        "message": error.message, "accepted_root_unchanged": true,
                    }),
                );
                let _ = fs::remove_file(pending);
            }
        }
    }
    result
}

pub(super) fn edit_resource(path: &Path, require_spare: bool) -> Result<Value, CliFailure> {
    edit_resource_with_prepared(path, require_spare, None)
}

#[path = "starter_authoring.rs"]
mod authoring;
#[path = "starter_native_test.rs"]
mod native_test;
pub(super) use authoring::{
    commit_proposal_observed, discard_proposal, preflight_commit_proposal, propose_graph_branch,
    propose_graph_branch_values, propose_graph_constant, propose_graph_constant_values,
    propose_graph_opcode, propose_graph_opcode_values, propose_graph_operand,
    propose_graph_operand_values, propose_release, propose_resource, validate_proposal,
};
pub(super) use native_test::native_test_candidate;
