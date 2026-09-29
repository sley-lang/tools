//! Read-only reconciliation of an ordinary candidate against verified core history.

use std::collections::{BTreeSet, VecDeque};
use std::path::Path;

use serde_json::{Value, json};
use sley_id::{CandidateId, TransactionId};
use sley_txn::TransactionRepository;

use super::CliFailure;

const MAX_HISTORY: usize = 256;

fn id_bytes(value: &str, label: &str) -> Result<[u8; 32], CliFailure> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(CliFailure::invalid(format!(
            "{label} must be 32 bytes of lowercase hex"
        )));
    }
    let mut bytes = [0_u8; 32];
    for (index, slot) in bytes.iter_mut().enumerate() {
        *slot = u8::from_str_radix(&value[index * 2..index * 2 + 2], 16)
            .map_err(|_| CliFailure::invalid(format!("{label} is not hex")))?;
    }
    Ok(bytes)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

pub(super) fn candidate(
    project: &Path,
    candidate: &str,
    expected_parent: &str,
) -> Result<Value, CliFailure> {
    let selected = CandidateId::from_bytes(id_bytes(candidate, "candidate_id")?);
    let parent = TransactionId::from_bytes(id_bytes(expected_parent, "expected_parent")?);
    let root = project
        .canonicalize()
        .map_err(|error| CliFailure::invalid(format!("project path is unavailable: {error}")))?;
    for relative in [".sley-tools", "workspace"] {
        let location = root.join(relative);
        let metadata = std::fs::symlink_metadata(&location).map_err(|error| {
            CliFailure::invalid(format!("project {relative} is unavailable: {error}"))
        })?;
        if !metadata.file_type().is_dir() {
            return Err(CliFailure::invalid(format!(
                "project {relative} is not a real directory"
            )));
        }
    }
    let repository = TransactionRepository::new(root.join("workspace"));
    let head = repository.accepted_head().map_err(|error| {
        CliFailure::outcome_unknown(format!(
            "cannot verify accepted core head for reconciliation: {error}"
        ))
    })?;
    let observed_head = head.transaction_id();
    let observed_root = hex(head.state_root().root.as_bytes());
    let workspace_id = hex(head.receipt().transaction.record.workspace_id.as_bytes());
    let mut queue = VecDeque::from([observed_head]);
    let mut visited = BTreeSet::new();
    while let Some(transaction) = queue.pop_front() {
        if !visited.insert(transaction) {
            continue;
        }
        if visited.len() > MAX_HISTORY {
            break;
        }
        let revision = repository.verified_revision(transaction).map_err(|error| {
            CliFailure::outcome_unknown(format!(
                "cannot verify core history during reconciliation: {error}"
            ))
        })?;
        let receipt = revision.receipt();
        let record = &receipt.transaction.record;
        if record.candidate_id == Some(selected) {
            if record.parent_transaction_ids.as_slice() != [parent] {
                return Ok(json!({
                    "contract": "sley-tools.candidate-reconciliation.v0", "state": "outcome_unknown",
                    "candidate_id": candidate, "expected_parent": expected_parent,
                    "workspace_id": workspace_id,
                    "observed_head": hex(observed_head.as_bytes()), "observed_root": observed_root,
                    "reason": "candidate appeared in verified ancestry with a different parent; inspect exact receipt",
                    "verified_revisions": visited.len(),
                }));
            }
            return Ok(json!({
                "contract": "sley-tools.candidate-reconciliation.v0", "state": "completed",
                "candidate_id": candidate, "expected_parent": expected_parent,
                "workspace_id": workspace_id,
                "transaction_id": hex(transaction.as_bytes()),
                "receipt_id": hex(receipt.receipt_id.as_bytes()),
                "committed_root": hex(revision.state_root().root.as_bytes()),
                "observed_head": hex(observed_head.as_bytes()), "observed_root": observed_root,
                "verified_revisions": visited.len(),
            }));
        }
        queue.extend(record.parent_transaction_ids.iter().copied());
    }
    Ok(json!({
        "contract": "sley-tools.candidate-reconciliation.v0", "state": "outcome_unknown",
        "candidate_id": candidate, "expected_parent": expected_parent,
        "workspace_id": workspace_id,
        "observed_head": hex(observed_head.as_bytes()), "observed_root": observed_root,
        "reason": if visited.len() > MAX_HISTORY {
            "verified history limit reached; do not retry the write"
        } else {
            "candidate is absent from the observed accepted ancestry; an in-flight commit may still complete, so do not retry the write"
        },
        "verified_revisions": visited.len().min(MAX_HISTORY),
    }))
}
