//! Durable tools identity for one validated ordinary candidate commit.

use std::fs::{self, DirBuilder, File, OpenOptions};
use std::io::{ErrorKind, Write};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::{Path, PathBuf};

use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use super::{CliFailure, reconcile, rpc};

const MAX_RECORD: u64 = 4096;

fn exact_id(value: &str, name: &str) -> Result<(), CliFailure> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(CliFailure::invalid(format!(
            "{name} must be 32 bytes of lowercase hex"
        )));
    }
    Ok(())
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn derived_id(workspace: &str, candidate: &str, parent: &str) -> Result<String, CliFailure> {
    for (name, value) in [
        ("workspace_id", workspace),
        ("candidate_id", candidate),
        ("expected_parent", parent),
    ] {
        exact_id(value, name)?;
    }
    let mut preimage = b"sley-tools.commit-operation.v1\0".to_vec();
    for value in [workspace, candidate, parent] {
        preimage.extend_from_slice(value.as_bytes());
    }
    Ok(hex(&Sha256::digest(&preimage)))
}

fn record_path(project: &Path, id: &str, create: bool) -> Result<PathBuf, CliFailure> {
    exact_id(id, "operation_id")?;
    let root = project
        .canonicalize()
        .map_err(|error| CliFailure::invalid(format!("project path is unavailable: {error}")))?;
    let state = root.join(".sley-tools");
    if !fs::symlink_metadata(&state).is_ok_and(|meta| meta.file_type().is_dir()) {
        return Err(CliFailure::invalid(
            "project state directory is not a real directory",
        ));
    }
    let folder = state.join("operations");
    if create && !folder.exists() {
        DirBuilder::new()
            .mode(0o700)
            .create(&folder)
            .map_err(|error| {
                CliFailure::failed(format!("cannot create operation directory: {error}"))
            })?;
    }
    if !fs::symlink_metadata(&folder).is_ok_and(|meta| meta.file_type().is_dir()) {
        return Err(CliFailure::invalid(
            "operation directory is not a real directory",
        ));
    }
    Ok(folder.join(format!("{id}.json")))
}

fn read_json(path: &Path, maximum: u64) -> Result<Value, CliFailure> {
    let metadata = fs::symlink_metadata(path).map_err(|error| {
        CliFailure::invalid(format!("operation record is unavailable: {error}"))
    })?;
    if !metadata.file_type().is_file() || metadata.len() > maximum {
        return Err(CliFailure::invalid(
            "operation record is not a bounded regular file",
        ));
    }
    let bytes = fs::read(path)
        .map_err(|error| CliFailure::failed(format!("cannot read operation record: {error}")))?;
    rpc::parse_strict_json(&bytes)
        .map_err(|error| CliFailure::invalid(format!("operation record has invalid JSON: {error}")))
}

fn write_record(path: &Path, record: &Value) -> Result<(), CliFailure> {
    let mut bytes = serde_json::to_vec_pretty(record)
        .map_err(|error| CliFailure::failed(format!("cannot encode operation record: {error}")))?;
    bytes.push(b'\n');
    if bytes.len() as u64 > MAX_RECORD {
        return Err(CliFailure::resource("operation record exceeds 4 KiB"));
    }
    let suffix = super::rpc::new_random_handle()?;
    let temporary = path.with_extension(format!("{suffix}.tmp"));
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&temporary)
        .map_err(|error| {
            CliFailure::failed(format!("cannot create operation staging record: {error}"))
        })?;
    if let Err(error) = output.write_all(&bytes).and_then(|()| output.sync_all()) {
        let _ = fs::remove_file(&temporary);
        return Err(CliFailure::failed(format!(
            "cannot sync operation record: {error}"
        )));
    }
    fs::rename(&temporary, path)
        .map_err(|error| CliFailure::failed(format!("cannot publish operation record: {error}")))?;
    File::open(path.parent().expect("operation parent"))
        .and_then(|folder| folder.sync_all())
        .map_err(|error| CliFailure::failed(format!("cannot sync operation directory: {error}")))?;
    Ok(())
}

fn details(project: &Path, candidate: &str) -> Result<Value, CliFailure> {
    exact_id(candidate, "candidate_id")?;
    let proposal = read_json(&project.join(".sley-tools/proposal.json"), 1_048_576)?;
    let state = read_json(&project.join(".sley-tools/state.json"), 1_048_576)?;
    let validated = read_json(
        &project.join(".sley-tools/validated-proposal.json"),
        1_048_576,
    )?;
    if proposal["contract"] != "sley-tools.proposal.v0"
        || validated["contract"] != "sley-tools.validation.v0"
        || proposal["candidate_id"] != candidate
        || validated["candidate_id"] != candidate
        || validated["result"] != "PASS"
        || validated["base_root"] != proposal["base_root"]
        || validated["expires_unix_millis"] != proposal["expires_unix_millis"]
    {
        return Err(CliFailure::stale(
            "operation candidate differs from separately validated proposal",
        ));
    }
    let workspace = state["workspace_id"]
        .as_str()
        .ok_or_else(|| CliFailure::invalid("project state has no workspace ID"))?;
    let parent = proposal["expected_parent"]
        .as_str()
        .ok_or_else(|| CliFailure::invalid("proposal has no expected parent"))?;
    let base = proposal["base_root"]
        .as_str()
        .ok_or_else(|| CliFailure::invalid("proposal has no base root"))?;
    if state["state_root"] != base || state["transaction_id"] != parent {
        return Err(CliFailure::stale(
            "operation proposal differs from local accepted state",
        ));
    }
    for (name, value) in [
        ("workspace_id", workspace),
        ("expected_parent", parent),
        ("base_root", base),
    ] {
        exact_id(value, name)?;
    }
    let id = derived_id(workspace, candidate, parent)?;
    Ok(
        json!({"contract":"sley-tools.operation.v1", "operation_id":id,
        "phase":"validated", "workspace_id":workspace, "candidate_id":candidate,
        "expected_parent":parent, "base_root":base}),
    )
}

pub(super) fn prepare(project: &Path, candidate: &str) -> Result<String, CliFailure> {
    let record = details(project, candidate)?;
    let id = record["operation_id"]
        .as_str()
        .expect("derived operation ID");
    let path = record_path(project, id, true)?;
    match fs::symlink_metadata(&path) {
        Ok(_) => {
            let prior = read_json(&path, MAX_RECORD)?;
            if prior["contract"] != record["contract"]
                || prior["operation_id"] != record["operation_id"]
                || prior["workspace_id"] != record["workspace_id"]
                || prior["candidate_id"] != record["candidate_id"]
                || prior["expected_parent"] != record["expected_parent"]
                || prior["base_root"] != record["base_root"]
            {
                return Err(CliFailure::stale(
                    "existing operation record has different bindings",
                ));
            }
        }
        Err(error) if error.kind() == ErrorKind::NotFound => write_record(&path, &record)?,
        Err(error) => {
            return Err(CliFailure::failed(format!(
                "cannot inspect operation record: {error}"
            )));
        }
    }
    Ok(id.to_owned())
}

pub(super) fn proposal_candidate(project: &Path) -> Result<String, CliFailure> {
    let proposal = read_json(&project.join(".sley-tools/proposal.json"), 1_048_576)?;
    let candidate = proposal["candidate_id"]
        .as_str()
        .ok_or_else(|| CliFailure::invalid("proposal has no candidate ID"))?;
    exact_id(candidate, "candidate_id")?;
    Ok(candidate.to_owned())
}

pub(super) fn begin_commit(project: &Path, candidate: &str) -> Result<String, CliFailure> {
    let id = prepare(project, candidate)?;
    let path = record_path(project, &id, false)?;
    let mut record = read_json(&path, MAX_RECORD)?;
    if record["phase"] != "validated" {
        return Err(CliFailure::outcome_unknown(format!(
            "operation {id} has already requested a commit; reconcile before any retry"
        )));
    }
    record["phase"] = json!("commit_requested");
    write_record(&path, &record)?;
    Ok(id)
}

pub(super) fn finish_commit(project: &Path, id: &str) -> Result<(), CliFailure> {
    let path = record_path(project, id, false)?;
    let mut record = read_json(&path, MAX_RECORD)?;
    if record["phase"] != "commit_requested" {
        return Err(CliFailure::outcome_unknown(
            "operation phase changed during commit",
        ));
    }
    let candidate = record["candidate_id"]
        .as_str()
        .expect("checked operation candidate");
    let parent = record["expected_parent"]
        .as_str()
        .expect("checked operation parent");
    let resolved = reconcile::candidate(project, candidate, parent)?;
    if resolved["workspace_id"] != record["workspace_id"] {
        return Err(CliFailure::outcome_unknown(
            "operation workspace differs from verified core history",
        ));
    }
    if resolved["state"] != "completed" {
        return Err(CliFailure::outcome_unknown(format!(
            "operation {id} returned from commit but exact core receipt is not reconciled"
        )));
    }
    record["phase"] = json!("completed");
    record["transaction_id"] = resolved["transaction_id"].clone();
    record["receipt_id"] = resolved["receipt_id"].clone();
    record["committed_root"] = resolved["committed_root"].clone();
    write_record(&path, &record)
}

pub(super) fn status(project: &Path, id: &str) -> Result<Value, CliFailure> {
    let record = read_json(&record_path(project, id, false)?, MAX_RECORD)?;
    if record["contract"] != "sley-tools.operation.v1" || record["operation_id"] != id {
        return Err(CliFailure::invalid(
            "operation record has wrong contract or identity",
        ));
    }
    let candidate = record["candidate_id"]
        .as_str()
        .ok_or_else(|| CliFailure::invalid("operation record has no candidate ID"))?;
    let parent = record["expected_parent"]
        .as_str()
        .ok_or_else(|| CliFailure::invalid("operation record has no expected parent"))?;
    let workspace = record["workspace_id"]
        .as_str()
        .ok_or_else(|| CliFailure::invalid("operation record has no workspace ID"))?;
    if derived_id(workspace, candidate, parent)? != id {
        return Err(CliFailure::invalid(
            "operation record bindings do not derive its identity",
        ));
    }
    let resolved = reconcile::candidate(project, candidate, parent)?;
    if resolved["workspace_id"] != workspace {
        return Err(CliFailure::outcome_unknown(
            "operation workspace differs from verified core history",
        ));
    }
    let phase = record["phase"]
        .as_str()
        .ok_or_else(|| CliFailure::invalid("operation record has no phase"))?;
    if ![
        "validated",
        "commit_requested",
        "cancel_requested",
        "cancelled_before_commit",
        "completed",
    ]
    .contains(&phase)
    {
        return Err(CliFailure::invalid("operation record has an unknown phase"));
    }
    if phase == "completed"
        && resolved["state"] == "completed"
        && (record["transaction_id"] != resolved["transaction_id"]
            || record["receipt_id"] != resolved["receipt_id"]
            || record["committed_root"] != resolved["committed_root"])
    {
        return Err(CliFailure::outcome_unknown(
            "operation record and verified core receipt disagree",
        ));
    }
    let state = if resolved["state"] == "completed" {
        "completed"
    } else if phase == "cancelled_before_commit" {
        "cancelled_before_commit"
    } else if phase == "cancel_requested" {
        "cancel_requested"
    } else if phase == "validated" {
        "validated"
    } else {
        "outcome_unknown"
    };
    Ok(
        json!({"contract":"sley-tools.operation-status.v1", "operation_id":id,
        "candidate_id":candidate, "expected_parent":parent, "recorded_phase":phase,
        "state":state, "core_reconciliation":resolved}),
    )
}

pub(super) fn cancel(project: &Path, id: &str) -> Result<Value, CliFailure> {
    let current = status(project, id)?;
    if current["state"] == "completed" || current["state"] == "cancelled_before_commit" {
        return Ok(current);
    }
    let path = record_path(project, id, false)?;
    let mut record = read_json(&path, MAX_RECORD)?;
    match record["phase"].as_str() {
        Some("validated") => record["phase"] = json!("cancelled_before_commit"),
        Some("commit_requested" | "cancel_requested") => {
            record["phase"] = json!("cancel_requested")
        }
        _ => return Err(CliFailure::invalid("operation phase cannot be cancelled")),
    }
    write_record(&path, &record)?;
    status(project, id)
}
