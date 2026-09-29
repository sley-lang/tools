//! Bounded explicit proposal lifecycle for the two native Rules edits.

use super::*;
use std::os::unix::fs::OpenOptionsExt;

use sley_mutate::{CandidateRecord, full_validation_profile_id};

const PROPOSAL_FILE: &str = "proposal.json";
const VALIDATION_FILE: &str = "validated-proposal.json";

enum Change {
    Release {
        old: u32,
        new: u32,
    },
    Resource {
        old: bool,
        new: bool,
    },
    GraphConstant {
        name: String,
        old: Value,
        new: Value,
        cases: Value,
    },
    GraphOpcode {
        block: String,
        name: String,
        old: String,
        new: String,
        cases: Value,
    },
    GraphOperand {
        block: String,
        name: String,
        slot: String,
        old: String,
        new: String,
        cases: Value,
    },
    GraphBranch {
        block: String,
        old: Value,
        new: Value,
        cases: Value,
    },
}

struct Prepared {
    document: Value,
    edit: Value,
    candidate: CandidateRecord,
    change: Change,
}

fn proposal_path(project: &Project) -> PathBuf {
    project.root.join(".sley-tools").join(PROPOSAL_FILE)
}

fn validation_path(project: &Project) -> PathBuf {
    project.root.join(".sley-tools").join(VALIDATION_FILE)
}

fn strict_local_json(path: &Path) -> Result<Value, CliFailure> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| CliFailure::invalid(format!("cannot stat {}: {error}", path.display())))?;
    if !metadata.file_type().is_file() || metadata.len() > 1_048_576 {
        return Err(CliFailure::invalid(
            "authoring record must be a regular file of at most 1 MiB",
        ));
    }
    let bytes = fs::read(path)
        .map_err(|error| CliFailure::invalid(format!("cannot read {}: {error}", path.display())))?;
    super::super::rpc::parse_strict_json(&bytes)
        .map_err(|error| CliFailure::invalid(format!("invalid authoring JSON: {error}")))
}

fn write_new_json(path: &Path, value: &Value) -> Result<(), CliFailure> {
    let mut bytes = serde_json::to_vec_pretty(value)
        .map_err(|error| CliFailure::failed(format!("cannot encode authoring record: {error}")))?;
    bytes.push(b'\n');
    if bytes.len() > 1_048_576 {
        return Err(CliFailure::resource("authoring record exceeds 1 MiB"));
    }
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)
        .map_err(|error| {
            CliFailure::invalid(format!("cannot create new authoring record: {error}"))
        })?;
    if let Err(error) = output.write_all(&bytes).and_then(|()| output.sync_all()) {
        let _ = fs::remove_file(path);
        return Err(CliFailure::failed(format!(
            "cannot finish authoring record: {error}"
        )));
    }
    Ok(())
}

fn proposal_document(
    project: &Project,
    edit: Value,
    field: &str,
    value: Value,
    old_object: &str,
) -> Result<Value, CliFailure> {
    let record_hex = edit["candidate_record_hex"]
        .as_str()
        .ok_or_else(|| CliFailure::failed("builder omitted candidate record"))?;
    let record = decode_candidate_record(&unhex(record_hex)?).map_err(|error| {
        CliFailure::failed(format!("builder produced invalid candidate: {error}"))
    })?;
    Ok(json!({
        "contract": "sley-tools.proposal.v0", "program_id": project.manifest["program_id"],
        "base_root": project.state["state_root"], "expected_parent": project.state["transaction_id"],
        "base_object_id": old_object, "change": {"field": field, "value": value},
        "candidate_id": edit["candidate_id"], "expires_unix_millis": record.expiry.not_after,
        "builder": edit,
    }))
}

fn check_no_proposal(project: &Project) -> Result<(), CliFailure> {
    if proposal_path(project).exists() || validation_path(project).exists() {
        return Err(CliFailure::invalid(
            "a proposal already exists; commit or discard it first",
        ));
    }
    Ok(())
}

fn graph_opcode_profile(name: &str) -> Option<(Opcode, u8)> {
    Some(match name {
        "bool_and" => (Opcode::BoolAnd, 1),
        "bool_or" => (Opcode::BoolOr, 1),
        "equal" => (Opcode::Equal, 2),
        "not_equal" => (Opcode::NotEqual, 2),
        "less_than" => (Opcode::LessThan, 3),
        "less_equal" => (Opcode::LessEqual, 3),
        "greater_than" => (Opcode::GreaterThan, 3),
        "greater_equal" => (Opcode::GreaterEqual, 3),
        "int_add_checked" => (Opcode::IntAddChecked, 4),
        "int_sub_checked" => (Opcode::IntSubChecked, 4),
        "int_mul_checked" => (Opcode::IntMulChecked, 4),
        "int_neg_checked" => (Opcode::IntNegChecked, 5),
        "result_ok" => (Opcode::ResultOk, 5),
        _ => return None,
    })
}

fn bound_graph_reference(
    reference: &str,
    spec: &Value,
    block: &str,
    ordinal: usize,
    parameters: &[EntityId],
    operations: &[EntityId],
) -> Result<ValueRef, CliFailure> {
    if let Some(name) = reference.strip_prefix("param.") {
        let index = spec["parameters"]
            .as_array()
            .and_then(|parameters| parameters.iter().position(|item| item["name"] == name))
            .ok_or_else(|| CliFailure::invalid("graph operand references an unknown parameter"))?;
        return Ok(ValueRef::Parameter(*parameters.get(index).ok_or_else(
            || CliFailure::invalid("accepted function omits parameter"),
        )?));
    }
    if let Some(name) = reference.strip_prefix("op.") {
        let index = spec["blocks"]
            .as_array()
            .and_then(|blocks| blocks.iter().find(|item| item["name"] == block))
            .and_then(|block| block["operations"].as_array())
            .and_then(|items| {
                items[..ordinal]
                    .iter()
                    .position(|item| item["name"] == name)
            })
            .ok_or_else(|| {
                CliFailure::invalid("graph operand must reference an earlier operation")
            })?;
        return Ok(ValueRef::OperationResult(OperationResultRef {
            operation: *operations
                .get(index)
                .ok_or_else(|| CliFailure::invalid("accepted block omits operation"))?,
            result_index: 0,
        }));
    }
    Err(CliFailure::invalid("graph operand reference is invalid"))
}

pub(crate) fn propose_release(path: &Path, threshold: u32) -> Result<Value, CliFailure> {
    let project = load_project(path)?;
    if project.manifest["program_id"] != "release-readiness"
        || project.manifest["authoring_profile"] == "graph_v0"
    {
        return Err(CliFailure::invalid(
            "threshold proposal requires Release Readiness",
        ));
    }
    check_no_proposal(&project)?;
    let old = project.state["threshold"]
        .as_u64()
        .and_then(|value| u32::try_from(value).ok())
        .ok_or_else(|| CliFailure::invalid("project threshold is invalid"))?;
    if old == threshold {
        return Err(CliFailure::invalid(
            "proposed threshold matches accepted threshold",
        ));
    }
    let edit = builder(
        &project.builder,
        "edit",
        &[
            state_text(&project.state, "workspace_id")?,
            state_text(&project.state, "principal_id")?,
            state_text(&project.state, "state_root")?,
            state_text(&project.state, "policy_root")?,
            state_text(&project.state, "transaction_id")?,
            state_text(&project.state, "schema_epoch")?,
            state_text(&project.state, "threshold_id")?,
            state_text(&project.state, "threshold_object_id")?,
            &threshold.to_string(),
        ],
    )?;
    let proposal = proposal_document(
        &project,
        edit,
        "max_blocking_findings",
        json!(threshold),
        state_text(&project.state, "threshold_object_id")?,
    )?;
    verify_proposal(&project, &proposal)?;
    write_new_json(&proposal_path(&project), &proposal)?;
    Ok(
        json!({"contract": "sley-tools.propose.v0", "phase": "PROPOSED",
        "program_id": "release-readiness", "candidate_id": proposal["candidate_id"],
        "base_root": proposal["base_root"], "base_object_id": proposal["base_object_id"],
        "old_threshold": old, "new_threshold": threshold,
        "expires_unix_millis": proposal["expires_unix_millis"], "accepted_root_unchanged": true}),
    )
}

pub(crate) fn propose_resource(path: &Path, require_spare: bool) -> Result<Value, CliFailure> {
    let project = load_project(path)?;
    if project.manifest["program_id"] != "resource-admission"
        || project.manifest["authoring_profile"] == "graph_v0"
    {
        return Err(CliFailure::invalid(
            "memory policy proposal requires Resource Admission",
        ));
    }
    check_no_proposal(&project)?;
    let old = project.state["require_memory_spare"]
        .as_bool()
        .ok_or_else(|| CliFailure::invalid("project memory policy is invalid"))?;
    if old == require_spare {
        return Err(CliFailure::invalid(
            "proposed memory policy matches accepted policy",
        ));
    }
    let repository = project.root.join("workspace");
    let edit = builder(
        &project.builder,
        "edit-resource",
        &[
            state_text(&project.state, "workspace_id")?,
            state_text(&project.state, "principal_id")?,
            state_text(&project.state, "state_root")?,
            state_text(&project.state, "policy_root")?,
            state_text(&project.state, "transaction_id")?,
            state_text(&project.state, "schema_epoch")?,
            repository
                .to_str()
                .ok_or_else(|| CliFailure::invalid("project path is not UTF-8"))?,
            state_text(&project.state, "memory_comparison_id")?,
            state_text(&project.state, "memory_comparison_object_id")?,
            &require_spare.to_string(),
        ],
    )?;
    let proposal = proposal_document(
        &project,
        edit,
        "require_memory_spare",
        json!(require_spare),
        state_text(&project.state, "memory_comparison_object_id")?,
    )?;
    verify_proposal(&project, &proposal)?;
    write_new_json(&proposal_path(&project), &proposal)?;
    Ok(
        json!({"contract": "sley-tools.propose.v0", "phase": "PROPOSED",
        "program_id": "resource-admission", "candidate_id": proposal["candidate_id"],
        "base_root": proposal["base_root"], "base_object_id": proposal["base_object_id"],
        "old_require_memory_spare": old, "new_require_memory_spare": require_spare,
        "expires_unix_millis": proposal["expires_unix_millis"], "accepted_root_unchanged": true}),
    )
}

pub(crate) fn propose_graph_constant(
    path: &Path,
    name: &str,
    value_file: &Path,
    cases_file: &Path,
) -> Result<Value, CliFailure> {
    let new: Value = serde_json::from_slice(&graph_project::read_regular(value_file, 128)?)
        .map_err(|error| CliFailure::invalid(format!("invalid constant value JSON: {error}")))?;
    let cases: Value = serde_json::from_slice(&graph_project::read_regular(cases_file, 65_536)?)
        .map_err(|error| CliFailure::invalid(format!("invalid proposed graph cases: {error}")))?;
    propose_graph_constant_values(path, name, &new, &cases)
}

pub(crate) fn propose_graph_constant_values(
    path: &Path,
    name: &str,
    new: &Value,
    cases: &Value,
) -> Result<Value, CliFailure> {
    let project = load_project(path)?;
    if project.manifest["authoring_profile"] != "graph_v0" {
        return Err(CliFailure::feature(
            "constant proposal requires a native graph project",
        ));
    }
    check_no_proposal(&project)?;
    let spec_path = project.root.join("build/graph.json");
    let spec = read_json(&spec_path)?;
    let selected = spec["constants"]
        .as_array()
        .and_then(|constants| constants.iter().find(|constant| constant["name"] == name))
        .ok_or_else(|| CliFailure::invalid(format!("graph constant {name:?} is absent")))?;
    let old = selected["value"].clone();
    let encoded_value = serde_json::to_string(new)
        .map_err(|error| CliFailure::invalid(format!("cannot encode graph constant: {error}")))?;
    if encoded_value.len() > 128 {
        return Err(CliFailure::resource(
            "graph constant value exceeds 128 bytes",
        ));
    }
    if graph_project::constant_body(&spec, name, &old)?
        == graph_project::constant_body(&spec, name, new)?
    {
        return Err(CliFailure::invalid(
            "proposed constant equals accepted constant",
        ));
    }
    let encoded_cases = serde_json::to_vec(cases)
        .map_err(|error| CliFailure::invalid(format!("cannot encode graph cases: {error}")))?;
    if encoded_cases.len() > 65_536 {
        return Err(CliFailure::resource("graph proposal cases exceed 64 KiB"));
    }
    let case_count = graph_project::validate_case_values(&spec, cases)?;
    let constant_id = project.state["constant_ids"][name]
        .as_str()
        .ok_or_else(|| CliFailure::feature("project predates graph constant identity bindings"))?;
    let repository = project.root.join("workspace");
    let edit = builder(
        &project.builder,
        "edit-graph-constant",
        &[
            repository
                .to_str()
                .ok_or_else(|| CliFailure::invalid("repository path is not UTF-8"))?,
            spec_path
                .to_str()
                .ok_or_else(|| CliFailure::invalid("recipe path is not UTF-8"))?,
            name,
            &encoded_value,
            state_text(&project.state, "workspace_id")?,
            state_text(&project.state, "principal_id")?,
            state_text(&project.state, "state_root")?,
            state_text(&project.state, "policy_root")?,
            state_text(&project.state, "transaction_id")?,
            state_text(&project.state, "schema_epoch")?,
            constant_id,
        ],
    )?;
    if edit["contract"] != "sley-tools.edit-graph-constant.v0"
        || edit["old_value"] != old
        || edit["new_value"] != *new
        || edit["constant_id"] != constant_id
    {
        return Err(CliFailure::failed(
            "graph edit builder returned an inconsistent target",
        ));
    }
    let old_object = edit["old_object_id"]
        .as_str()
        .ok_or_else(|| CliFailure::failed("graph edit builder omitted old object"))?
        .to_owned();
    let mut proposal = proposal_document(
        &project,
        edit,
        &format!("constant.{name}"),
        new.clone(),
        &old_object,
    )?;
    proposal["graph_cases"] = cases.clone();
    verify_proposal(&project, &proposal)?;
    write_new_json(&proposal_path(&project), &proposal)?;
    Ok(json!({
        "contract": "sley-tools.propose.v0", "phase": "PROPOSED",
        "program_id": project.manifest["program_id"], "candidate_id": proposal["candidate_id"],
        "base_root": proposal["base_root"], "base_object_id": old_object,
        "constant": name, "old_value": old, "new_value": new,
        "proposed_case_count": case_count,
        "expires_unix_millis": proposal["expires_unix_millis"], "accepted_root_unchanged": true,
    }))
}

pub(crate) fn propose_graph_opcode(
    path: &Path,
    block: &str,
    name: &str,
    opcode: &str,
    cases_file: &Path,
) -> Result<Value, CliFailure> {
    let cases: Value = serde_json::from_slice(&graph_project::read_regular(cases_file, 65_536)?)
        .map_err(|error| CliFailure::invalid(format!("invalid proposed graph cases: {error}")))?;
    propose_graph_opcode_values(path, block, name, opcode, &cases)
}

pub(crate) fn propose_graph_opcode_values(
    path: &Path,
    block: &str,
    name: &str,
    opcode: &str,
    cases: &Value,
) -> Result<Value, CliFailure> {
    let project = load_project(path)?;
    if project.manifest["authoring_profile"] != "graph_v0" {
        return Err(CliFailure::feature(
            "opcode proposal requires a native graph project",
        ));
    }
    check_no_proposal(&project)?;
    let spec_path = project.root.join("build/graph.json");
    let spec = read_json(&spec_path)?;
    let selected = spec["blocks"]
        .as_array()
        .and_then(|blocks| blocks.iter().find(|item| item["name"] == block))
        .and_then(|selected| selected["operations"].as_array())
        .and_then(|operations| operations.iter().find(|item| item["name"] == name))
        .ok_or_else(|| CliFailure::invalid("selected graph operation is absent"))?;
    let old = selected["op"]
        .as_str()
        .ok_or_else(|| CliFailure::invalid("selected graph operation has no opcode"))?;
    if old == opcode {
        return Err(CliFailure::invalid("proposed graph opcode is unchanged"));
    }
    let (_, old_family) = graph_opcode_profile(old)
        .ok_or_else(|| CliFailure::invalid("selected graph opcode is not editable"))?;
    let (_, new_family) = graph_opcode_profile(opcode)
        .ok_or_else(|| CliFailure::invalid("proposed graph opcode is not editable"))?;
    if old_family != new_family {
        return Err(CliFailure::invalid(
            "proposed graph opcode crosses operand/result profiles",
        ));
    }
    if opcode.len() > 32 {
        return Err(CliFailure::invalid("proposed graph opcode is too long"));
    }
    let encoded_cases = serde_json::to_vec(cases)
        .map_err(|error| CliFailure::invalid(format!("cannot encode graph cases: {error}")))?;
    if encoded_cases.len() > 65_536 {
        return Err(CliFailure::resource("graph proposal cases exceed 64 KiB"));
    }
    let case_count = graph_project::validate_case_values(&spec, cases)?;
    let operation_id = project.state["operation_ids"][block][name]
        .as_str()
        .ok_or_else(|| CliFailure::feature("project predates graph operation identity bindings"))?;
    let repository = project.root.join("workspace");
    let edit = builder(
        &project.builder,
        "edit-graph-opcode",
        &[
            repository
                .to_str()
                .ok_or_else(|| CliFailure::invalid("repository path is not UTF-8"))?,
            spec_path
                .to_str()
                .ok_or_else(|| CliFailure::invalid("recipe path is not UTF-8"))?,
            block,
            name,
            opcode,
            state_text(&project.state, "workspace_id")?,
            state_text(&project.state, "principal_id")?,
            state_text(&project.state, "state_root")?,
            state_text(&project.state, "policy_root")?,
            state_text(&project.state, "transaction_id")?,
            state_text(&project.state, "schema_epoch")?,
            operation_id,
        ],
    )?;
    if edit["contract"] != "sley-tools.edit-graph-opcode.v0"
        || edit["block_name"] != block
        || edit["operation_name"] != name
        || edit["old_opcode"] != old
        || edit["new_opcode"] != opcode
        || edit["operation_id"] != operation_id
    {
        return Err(CliFailure::failed(
            "graph opcode builder returned an inconsistent target",
        ));
    }
    let old_object = edit["old_object_id"]
        .as_str()
        .ok_or_else(|| CliFailure::failed("graph opcode builder omitted old object"))?
        .to_owned();
    let mut proposal = proposal_document(
        &project,
        edit,
        &format!("operation.{block}.{name}.opcode"),
        json!(opcode),
        &old_object,
    )?;
    proposal["graph_cases"] = cases.clone();
    verify_proposal(&project, &proposal)?;
    write_new_json(&proposal_path(&project), &proposal)?;
    Ok(
        json!({"contract": "sley-tools.propose.v0", "phase": "PROPOSED",
        "program_id": project.manifest["program_id"], "candidate_id": proposal["candidate_id"],
        "base_root": proposal["base_root"], "base_object_id": old_object,
        "block": block, "operation": name, "old_opcode": old, "new_opcode": opcode,
        "proposed_case_count": case_count,
        "expires_unix_millis": proposal["expires_unix_millis"], "accepted_root_unchanged": true}),
    )
}

pub(crate) fn propose_graph_operand(
    path: &Path,
    block: &str,
    name: &str,
    slot: &str,
    reference: &str,
    cases_file: &Path,
) -> Result<Value, CliFailure> {
    let cases: Value = serde_json::from_slice(&graph_project::read_regular(cases_file, 65_536)?)
        .map_err(|error| CliFailure::invalid(format!("invalid proposed graph cases: {error}")))?;
    propose_graph_operand_values(path, block, name, slot, reference, &cases)
}

pub(crate) fn propose_graph_operand_values(
    path: &Path,
    block: &str,
    name: &str,
    slot: &str,
    reference: &str,
    cases: &Value,
) -> Result<Value, CliFailure> {
    let project = load_project(path)?;
    if project.manifest["authoring_profile"] != "graph_v0" {
        return Err(CliFailure::feature(
            "operand proposal requires a native graph project",
        ));
    }
    check_no_proposal(&project)?;
    let spec_path = project.root.join("build/graph.json");
    let spec = read_json(&spec_path)?;
    let (old, _) = graph_project::update_operand_spec(&spec, block, name, slot, reference)?;
    if reference.len() > 70 || block.contains('.') || name.contains('.') {
        return Err(CliFailure::invalid("graph operand selector is invalid"));
    }
    let encoded_cases = serde_json::to_vec(cases)
        .map_err(|error| CliFailure::invalid(format!("cannot encode graph cases: {error}")))?;
    if encoded_cases.len() > 65_536 {
        return Err(CliFailure::resource("graph proposal cases exceed 64 KiB"));
    }
    let case_count = graph_project::validate_case_values(&spec, cases)?;
    let operation_id = project.state["operation_ids"][block][name]
        .as_str()
        .ok_or_else(|| CliFailure::feature("project predates graph operation identity bindings"))?;
    let repository = project.root.join("workspace");
    let edit = builder(
        &project.builder,
        "edit-graph-operand",
        &[
            repository
                .to_str()
                .ok_or_else(|| CliFailure::invalid("repository path is not UTF-8"))?,
            spec_path
                .to_str()
                .ok_or_else(|| CliFailure::invalid("recipe path is not UTF-8"))?,
            block,
            name,
            slot,
            reference,
            state_text(&project.state, "workspace_id")?,
            state_text(&project.state, "principal_id")?,
            state_text(&project.state, "state_root")?,
            state_text(&project.state, "policy_root")?,
            state_text(&project.state, "transaction_id")?,
            state_text(&project.state, "schema_epoch")?,
            operation_id,
        ],
    )?;
    if edit["contract"] != "sley-tools.edit-graph-operand.v0"
        || edit["block_name"] != block
        || edit["operation_name"] != name
        || edit["operand_slot"] != slot
        || edit["old_reference"] != old
        || edit["new_reference"] != reference
        || edit["operation_id"] != operation_id
    {
        return Err(CliFailure::failed(
            "graph operand builder returned an inconsistent target",
        ));
    }
    let old_object = edit["old_object_id"]
        .as_str()
        .ok_or_else(|| CliFailure::failed("graph operand builder omitted old object"))?
        .to_owned();
    let mut proposal = proposal_document(
        &project,
        edit,
        &format!("operation.{block}.{name}.{slot}"),
        json!(reference),
        &old_object,
    )?;
    proposal["graph_cases"] = cases.clone();
    verify_proposal(&project, &proposal)?;
    write_new_json(&proposal_path(&project), &proposal)?;
    Ok(
        json!({"contract": "sley-tools.propose.v0", "phase": "PROPOSED",
        "program_id": project.manifest["program_id"], "candidate_id": proposal["candidate_id"],
        "base_root": proposal["base_root"], "base_object_id": old_object,
        "block": block, "operation": name, "operand": slot,
        "old_reference": old, "new_reference": reference,
        "proposed_case_count": case_count,
        "expires_unix_millis": proposal["expires_unix_millis"], "accepted_root_unchanged": true}),
    )
}

pub(crate) fn propose_graph_branch(
    path: &Path,
    block: &str,
    targets_file: &Path,
    cases_file: &Path,
) -> Result<Value, CliFailure> {
    let targets: Value = serde_json::from_slice(&graph_project::read_regular(targets_file, 256)?)
        .map_err(|error| {
        CliFailure::invalid(format!("invalid proposed branch targets: {error}"))
    })?;
    let cases: Value = serde_json::from_slice(&graph_project::read_regular(cases_file, 65_536)?)
        .map_err(|error| CliFailure::invalid(format!("invalid proposed graph cases: {error}")))?;
    propose_graph_branch_values(path, block, &targets, &cases)
}

pub(crate) fn propose_graph_branch_values(
    path: &Path,
    block: &str,
    targets: &Value,
    cases: &Value,
) -> Result<Value, CliFailure> {
    let project = load_project(path)?;
    if project.manifest["authoring_profile"] != "graph_v0" {
        return Err(CliFailure::feature(
            "branch proposal requires a native graph project",
        ));
    }
    check_no_proposal(&project)?;
    let spec_path = project.root.join("build/graph.json");
    let spec = read_json(&spec_path)?;
    let (old, _) = graph_project::update_branch_spec(&spec, block, targets)?;
    let case_count = graph_project::validate_case_values(&spec, cases)?;
    let encoded_cases = serde_json::to_vec(cases)
        .map_err(|error| CliFailure::invalid(format!("cannot encode graph cases: {error}")))?;
    if encoded_cases.len() > 65_536 {
        return Err(CliFailure::resource("graph proposal cases exceed 64 KiB"));
    }
    let target_text = serde_json::to_string(targets)
        .map_err(|error| CliFailure::invalid(format!("cannot encode branch targets: {error}")))?;
    if target_text.len() > 256 || block.contains('.') {
        return Err(CliFailure::invalid(
            "branch selector or target data is invalid",
        ));
    }
    let repository = project.root.join("workspace");
    let edit = builder(
        &project.builder,
        "edit-graph-branch",
        &[
            repository
                .to_str()
                .ok_or_else(|| CliFailure::invalid("repository path is not UTF-8"))?,
            spec_path
                .to_str()
                .ok_or_else(|| CliFailure::invalid("recipe path is not UTF-8"))?,
            block,
            &target_text,
            state_text(&project.state, "workspace_id")?,
            state_text(&project.state, "principal_id")?,
            state_text(&project.state, "state_root")?,
            state_text(&project.state, "policy_root")?,
            state_text(&project.state, "transaction_id")?,
            state_text(&project.state, "schema_epoch")?,
            state_text(&project.state, "function_id")?,
        ],
    )?;
    if edit["contract"] != "sley-tools.edit-graph-branch.v0"
        || edit["block_name"] != block
        || edit["old_targets"] != old
        || edit["new_targets"] != *targets
    {
        return Err(CliFailure::failed(
            "graph branch builder returned inconsistent targets",
        ));
    }
    let old_object = edit["old_object_id"]
        .as_str()
        .ok_or_else(|| CliFailure::failed("graph branch builder omitted old object"))?
        .to_owned();
    let mut proposal = proposal_document(
        &project,
        edit,
        &format!("branch.{block}.targets"),
        targets.clone(),
        &old_object,
    )?;
    proposal["graph_cases"] = cases.clone();
    verify_proposal(&project, &proposal)?;
    write_new_json(&proposal_path(&project), &proposal)?;
    Ok(
        json!({"contract": "sley-tools.propose.v0", "phase": "PROPOSED",
        "program_id": project.manifest["program_id"], "candidate_id": proposal["candidate_id"],
        "base_root": proposal["base_root"], "base_object_id": old_object,
        "block": block, "old_targets": old, "new_targets": targets,
        "proposed_case_count": case_count,
        "expires_unix_millis": proposal["expires_unix_millis"], "accepted_root_unchanged": true}),
    )
}

fn verify_proposal(project: &Project, document: &Value) -> Result<Prepared, CliFailure> {
    if document["contract"] != "sley-tools.proposal.v0"
        || document["program_id"] != project.manifest["program_id"]
        || document["base_root"] != project.state["state_root"]
        || document["expected_parent"] != project.state["transaction_id"]
    {
        return Err(CliFailure::stale(
            "proposal is not bound to the accepted project head",
        ));
    }
    let change = match document["program_id"].as_str() {
        Some("release-readiness")
            if project.manifest["authoring_profile"] != "graph_v0"
                && document["change"]["field"] == "max_blocking_findings" =>
        {
            let old = project.state["threshold"]
                .as_u64()
                .and_then(|value| u32::try_from(value).ok())
                .ok_or_else(|| CliFailure::invalid("accepted threshold is invalid"))?;
            let new = document["change"]["value"]
                .as_u64()
                .and_then(|value| u32::try_from(value).ok())
                .ok_or_else(|| CliFailure::invalid("proposed threshold must be u32"))?;
            if old == new {
                return Err(CliFailure::invalid("proposed threshold is unchanged"));
            }
            Change::Release { old, new }
        }
        Some("resource-admission")
            if project.manifest["authoring_profile"] != "graph_v0"
                && document["change"]["field"] == "require_memory_spare" =>
        {
            let old = project.state["require_memory_spare"]
                .as_bool()
                .ok_or_else(|| CliFailure::invalid("accepted memory policy is invalid"))?;
            let new = document["change"]["value"]
                .as_bool()
                .ok_or_else(|| CliFailure::invalid("proposed memory policy must be Boolean"))?;
            if old == new {
                return Err(CliFailure::invalid("proposed memory policy is unchanged"));
            }
            Change::Resource { old, new }
        }
        Some(_) if project.manifest["authoring_profile"] == "graph_v0" => {
            let field = document["change"]["field"]
                .as_str()
                .ok_or_else(|| CliFailure::invalid("graph proposal has no typed field"))?;
            let spec = read_json(&project.root.join("build/graph.json"))?;
            graph_project::validate_case_values(&spec, &document["graph_cases"])?;
            if let Some(name) = field.strip_prefix("constant.") {
                let old = spec["constants"]
                    .as_array()
                    .and_then(|constants| constants.iter().find(|constant| constant["name"] == name))
                    .ok_or_else(|| CliFailure::invalid("proposed graph constant is absent"))?["value"]
                    .clone();
                let new = document["change"]["value"].clone();
                if graph_project::constant_body(&spec, name, &old)?
                    == graph_project::constant_body(&spec, name, &new)?
                {
                    return Err(CliFailure::invalid("proposed graph constant is unchanged"));
                }
                Change::GraphConstant {
                    name: name.to_owned(),
                    old,
                    new,
                    cases: document["graph_cases"].clone(),
                }
            } else if let Some(selector) = field
                .strip_prefix("operation.")
                .and_then(|suffix| suffix.strip_suffix(".opcode"))
            {
                let (block, name) = selector.split_once('.').ok_or_else(|| {
                    CliFailure::invalid("graph opcode selector needs block and operation")
                })?;
                let selected = spec["blocks"]
                    .as_array()
                    .and_then(|blocks| blocks.iter().find(|item| item["name"] == block))
                    .and_then(|block| block["operations"].as_array())
                    .and_then(|operations| operations.iter().find(|item| item["name"] == name))
                    .ok_or_else(|| CliFailure::invalid("proposed graph operation is absent"))?;
                let old = selected["op"]
                    .as_str()
                    .ok_or_else(|| CliFailure::invalid("bound graph operation has no opcode"))?;
                let new = document["change"]["value"]
                    .as_str()
                    .ok_or_else(|| CliFailure::invalid("proposed graph opcode must be text"))?;
                let (old_op, old_family) = graph_opcode_profile(old)
                    .ok_or_else(|| CliFailure::invalid("bound graph opcode is not editable"))?;
                let (new_op, new_family) = graph_opcode_profile(new)
                    .ok_or_else(|| CliFailure::invalid("proposed graph opcode is not editable"))?;
                if old_family != new_family || old_op == new_op {
                    return Err(CliFailure::invalid(
                        "graph opcode edit crosses profiles or is unchanged",
                    ));
                }
                Change::GraphOpcode {
                    block: block.to_owned(),
                    name: name.to_owned(),
                    old: old.to_owned(),
                    new: new.to_owned(),
                    cases: document["graph_cases"].clone(),
                }
            } else if let Some(selector) = field.strip_prefix("operation.") {
                let (target, slot) = selector
                    .rsplit_once('.')
                    .ok_or_else(|| CliFailure::invalid("graph operand selector needs a slot"))?;
                let (block, name) = target.split_once('.').ok_or_else(|| {
                    CliFailure::invalid("graph operand selector needs block and operation")
                })?;
                if block.is_empty() || name.is_empty() || name.contains('.') {
                    return Err(CliFailure::invalid("graph operand selector is invalid"));
                }
                let new = document["change"]["value"]
                    .as_str()
                    .ok_or_else(|| CliFailure::invalid("graph operand reference must be text"))?;
                let (old, _) = graph_project::update_operand_spec(&spec, block, name, slot, new)?;
                Change::GraphOperand {
                    block: block.to_owned(),
                    name: name.to_owned(),
                    slot: slot.to_owned(),
                    old,
                    new: new.to_owned(),
                    cases: document["graph_cases"].clone(),
                }
            } else if let Some(block) = field
                .strip_prefix("branch.")
                .and_then(|suffix| suffix.strip_suffix(".targets"))
            {
                if block.is_empty() || block.contains('.') {
                    return Err(CliFailure::invalid("graph branch selector needs one block"));
                }
                let new = document["change"]["value"].clone();
                let (old, _) = graph_project::update_branch_spec(&spec, block, &new)?;
                Change::GraphBranch {
                    block: block.to_owned(),
                    old,
                    new,
                    cases: document["graph_cases"].clone(),
                }
            } else {
                return Err(CliFailure::invalid(
                    "graph proposal has an unsupported typed selector",
                ));
            }
        }
        _ => {
            return Err(CliFailure::invalid(
                "proposal has an unsupported typed change",
            ));
        }
    };
    let edit = document["builder"].clone();
    let candidate_hex = edit["candidate_record_hex"]
        .as_str()
        .ok_or_else(|| CliFailure::invalid("proposal omits candidate record"))?;
    let candidate = decode_candidate_record(&unhex(candidate_hex)?)
        .map_err(|error| CliFailure::invalid(format!("proposal candidate is invalid: {error}")))?;
    let built = build_candidate(&candidate).map_err(|error| {
        CliFailure::invalid(format!("proposal candidate cannot be built: {error}"))
    })?;
    if edit["candidate_stored_hex"].as_str() != Some(hex(&built.stored_bytes).as_str())
        || edit["candidate_id"].as_str() != Some(hex(built.candidate_id.as_bytes()).as_str())
        || document["candidate_id"] != edit["candidate_id"]
        || document["expires_unix_millis"] != candidate.expiry.not_after
    {
        return Err(CliFailure::invalid(
            "proposal candidate bytes or identity differ",
        ));
    }
    if candidate.expiry.not_after <= current_millis()? {
        return Err(CliFailure::stale(
            "proposal expired; discard it and propose again",
        ));
    }
    for (observed, field) in [
        (hex(candidate.workspace_id.as_bytes()), "workspace_id"),
        (hex(candidate.principal_id.as_bytes()), "principal_id"),
        (hex(candidate.base_root.as_bytes()), "state_root"),
        (
            hex(candidate.base_transaction_id.as_bytes()),
            "transaction_id",
        ),
        (hex(candidate.policy_root_id.as_bytes()), "policy_root"),
        (hex(candidate.schema_epoch_id.as_bytes()), "schema_epoch"),
    ] {
        if observed != state_text(&project.state, field)? {
            return Err(CliFailure::stale(format!("proposal differs from {field}")));
        }
    }
    if candidate.validation_profile_id
        != full_validation_profile_id()
            .map_err(|error| CliFailure::failed(format!("validation profile: {error}")))?
        || candidate.operations.len() != 1
        || candidate.preconditions.len() != 1
    {
        return Err(CliFailure::invalid(
            "proposal is outside the one-object edit profile",
        ));
    }
    let operation = &candidate.operations[0];
    let precondition = &candidate.preconditions[0];
    let (entity_text, object_text, kind, object_output) = match &change {
        Change::Release { .. } => (
            state_text(&project.state, "threshold_id")?.to_owned(),
            state_text(&project.state, "threshold_object_id")?.to_owned(),
            9,
            "threshold_object_id",
        ),
        Change::Resource { .. } => (
            state_text(&project.state, "memory_comparison_id")?.to_owned(),
            state_text(&project.state, "memory_comparison_object_id")?.to_owned(),
            8,
            "memory_comparison_object_id",
        ),
        Change::GraphConstant { name, .. } => (
            project.state["constant_ids"][name]
                .as_str()
                .ok_or_else(|| CliFailure::invalid("graph project omits constant identity"))?
                .to_owned(),
            edit["old_object_id"]
                .as_str()
                .ok_or_else(|| CliFailure::invalid("graph edit omits old object"))?
                .to_owned(),
            9,
            "new_object_id",
        ),
        Change::GraphOpcode { block, name, .. } | Change::GraphOperand { block, name, .. } => (
            project.state["operation_ids"][block][name]
                .as_str()
                .ok_or_else(|| CliFailure::invalid("graph project omits operation identity"))?
                .to_owned(),
            edit["old_object_id"]
                .as_str()
                .ok_or_else(|| CliFailure::invalid("graph opcode edit omits old object"))?
                .to_owned(),
            8,
            "new_object_id",
        ),
        Change::GraphBranch { .. } => (
            edit["block_id"]
                .as_str()
                .ok_or_else(|| CliFailure::invalid("graph branch edit omits block identity"))?
                .to_owned(),
            edit["old_object_id"]
                .as_str()
                .ok_or_else(|| CliFailure::invalid("graph branch edit omits old object"))?
                .to_owned(),
            7,
            "new_object_id",
        ),
    };
    if document["base_object_id"] != object_text
        || operation.class != MutationClass::ReplaceEntityVersion
        || operation.target_kind != kind
        || hex(operation.target_entity.as_bytes()) != entity_text
        || operation.field_tag.is_some()
        || precondition.requirement != PreimageRequirement::ExactEntityVersion
    {
        return Err(CliFailure::invalid(
            "proposal target or exact-object requirement differs",
        ));
    }
    let PreconditionPayload::ExactEntityVersion(old_version) = &precondition.payload else {
        return Err(CliFailure::invalid(
            "proposal omits exact old entity version",
        ));
    };
    if old_version.entity_id != operation.target_entity
        || hex(old_version.object_id.as_bytes()) != object_text
    {
        return Err(CliFailure::invalid(
            "proposal old object differs from accepted project",
        ));
    }
    let MutationPayload::ReplaceEntityVersion(body) = &operation.payload else {
        return Err(CliFailure::invalid(
            "proposal is not an exact object replacement",
        ));
    };
    let repository = TransactionRepository::new(&project.root.join("workspace"));
    let head = repository
        .accepted_head()
        .map_err(|error| CliFailure::stale(format!("cannot read accepted head: {error}")))?;
    let old_object = head
        .objects()
        .iter()
        .find(|object| object.record().entity_id == operation.target_entity)
        .ok_or_else(|| CliFailure::stale("proposed entity is absent from accepted head"))?;
    if old_object.object_id() != old_version.object_id {
        return Err(CliFailure::stale(
            "proposed exact old object is no longer accepted",
        ));
    }
    match &change {
        Change::Release { old, new } => {
            let count = |number: u32| {
                EntityBodyValue::Constant(ConstantBody {
                    value: ConstValue {
                        value_type: TypeExpr::UInt(IntegerWidth::from_bits(32)),
                        data: ConstData::UInt(u128::from(number)),
                    },
                })
            };
            if old_object.record().body != count(*old)
                || *body != count(*new)
                || edit["new_threshold"] != *new
            {
                return Err(CliFailure::invalid(
                    "proposal does not replace the typed native threshold",
                ));
            }
        }
        Change::Resource { old, new } => {
            let EntityBodyValue::Operation(old_operation) = &old_object.record().body else {
                return Err(CliFailure::invalid(
                    "accepted memory comparison is not an operation",
                ));
            };
            if old_operation.ordinal != 6
                || old_operation.opcode
                    != if *old {
                        Opcode::GreaterEqual.tag()
                    } else {
                        Opcode::GreaterThan.tag()
                    }
            {
                return Err(CliFailure::invalid(
                    "accepted memory comparison is outside the edit profile",
                ));
            }
            let mut expected = old_operation.clone();
            expected.opcode = if *new {
                Opcode::GreaterEqual.tag()
            } else {
                Opcode::GreaterThan.tag()
            };
            if *body != EntityBodyValue::Operation(expected) || edit["require_memory_spare"] != *new
            {
                return Err(CliFailure::invalid(
                    "proposal does not replace the typed native memory comparison",
                ));
            }
        }
        Change::GraphConstant { name, old, new, .. } => {
            let spec = read_json(&project.root.join("build/graph.json"))?;
            if old_object.record().body != graph_project::constant_body(&spec, name, old)?
                || *body != graph_project::constant_body(&spec, name, new)?
                || edit["contract"] != "sley-tools.edit-graph-constant.v0"
                || edit["constant_name"] != *name
                || edit["constant_id"] != entity_text
                || edit["old_object_id"] != object_text
                || edit["old_value"] != *old
                || edit["new_value"] != *new
            {
                return Err(CliFailure::invalid(
                    "proposal does not replace the selected native graph constant",
                ));
            }
        }
        Change::GraphOpcode {
            block,
            name,
            old,
            new,
            ..
        } => {
            let EntityBodyValue::Operation(old_operation) = &old_object.record().body else {
                return Err(CliFailure::invalid(
                    "accepted graph operation has the wrong entity type",
                ));
            };
            let spec = read_json(&project.root.join("build/graph.json"))?;
            let ordinal = spec["blocks"]
                .as_array()
                .and_then(|blocks| blocks.iter().find(|item| item["name"] == *block))
                .and_then(|block| block["operations"].as_array())
                .and_then(|operations| operations.iter().position(|item| item["name"] == *name))
                .ok_or_else(|| CliFailure::invalid("bound graph operation is absent"))?;
            let old_opcode = graph_opcode_profile(old).expect("verified old opcode").0;
            let new_opcode = graph_opcode_profile(new).expect("verified new opcode").0;
            let mut expected = old_operation.clone();
            expected.opcode = new_opcode.tag();
            if old_operation.ordinal
                != u32::try_from(ordinal)
                    .map_err(|_| CliFailure::invalid("graph operation ordinal exceeds profile"))?
                || old_operation.opcode != old_opcode.tag()
                || *body != EntityBodyValue::Operation(expected)
                || edit["contract"] != "sley-tools.edit-graph-opcode.v0"
                || edit["block_name"] != *block
                || edit["operation_name"] != *name
                || edit["operation_id"] != entity_text
                || edit["old_object_id"] != object_text
                || edit["old_opcode"] != *old
                || edit["new_opcode"] != *new
            {
                return Err(CliFailure::invalid(
                    "proposal does not replace the selected native graph opcode",
                ));
            }
        }
        Change::GraphOperand {
            block,
            name,
            slot,
            old,
            new,
            ..
        } => {
            let EntityBodyValue::Operation(old_operation) = &old_object.record().body else {
                return Err(CliFailure::invalid(
                    "accepted graph operand owner is not an operation",
                ));
            };
            let spec = read_json(&project.root.join("build/graph.json"))?;
            let blocks = spec["blocks"]
                .as_array()
                .ok_or_else(|| CliFailure::invalid("graph recipe has no blocks"))?;
            let block_index = blocks
                .iter()
                .position(|item| item["name"] == *block)
                .ok_or_else(|| CliFailure::invalid("bound graph block is absent"))?;
            let source_operations = blocks[block_index]["operations"]
                .as_array()
                .ok_or_else(|| CliFailure::invalid("bound graph block has no operations"))?;
            let ordinal = source_operations
                .iter()
                .position(|item| item["name"] == *name)
                .ok_or_else(|| CliFailure::invalid("bound graph operation is absent"))?;
            let source_opcode = source_operations[ordinal]["op"]
                .as_str()
                .and_then(graph_opcode_profile)
                .ok_or_else(|| CliFailure::invalid("bound graph opcode is not editable"))?
                .0;
            let slot_index = match slot.as_str() {
                "left" | "operand" => 0,
                "right" => 1,
                _ => return Err(CliFailure::invalid("bound graph operand slot is invalid")),
            };
            let block_object = head
                .objects()
                .iter()
                .find(|object| object.record().entity_id == old_operation.block)
                .ok_or_else(|| CliFailure::stale("graph operation owner block is absent"))?;
            let EntityBodyValue::Block(block_body) = &block_object.record().body else {
                return Err(CliFailure::invalid("graph operation owner is not a block"));
            };
            let function_object = head
                .objects()
                .iter()
                .find(|object| object.record().entity_id == block_body.function)
                .ok_or_else(|| CliFailure::stale("graph operation owner function is absent"))?;
            let EntityBodyValue::Function(function_body) = &function_object.record().body else {
                return Err(CliFailure::invalid(
                    "graph operation owner is not a function",
                ));
            };
            if function_body.blocks.len() != blocks.len()
                || function_body.blocks[block_index] != old_operation.block
                || function_body.parameters.len()
                    != spec["parameters"].as_array().map_or(0, Vec::len)
                || block_body.operations.len() != source_operations.len()
                || block_body.operations[ordinal] != operation.target_entity
                || old_operation.ordinal
                    != u32::try_from(ordinal).map_err(|_| {
                        CliFailure::invalid("graph operation ordinal exceeds profile")
                    })?
                || old_operation.opcode != source_opcode.tag()
                || old_operation.operands.len() <= slot_index
            {
                return Err(CliFailure::invalid(
                    "accepted graph operation differs from bound recipe",
                ));
            }
            let old_reference = bound_graph_reference(
                old,
                &spec,
                block,
                ordinal,
                &function_body.parameters,
                &block_body.operations,
            )?;
            let new_reference = bound_graph_reference(
                new,
                &spec,
                block,
                ordinal,
                &function_body.parameters,
                &block_body.operations,
            )?;
            let mut expected = old_operation.clone();
            expected.operands[slot_index] = new_reference;
            if old_operation.operands[slot_index] != old_reference
                || *body != EntityBodyValue::Operation(expected)
                || edit["contract"] != "sley-tools.edit-graph-operand.v0"
                || edit["block_name"] != *block
                || edit["operation_name"] != *name
                || edit["operand_slot"] != *slot
                || edit["old_reference"] != *old
                || edit["new_reference"] != *new
                || edit["operation_id"] != entity_text
                || edit["old_object_id"] != object_text
            {
                return Err(CliFailure::invalid(
                    "proposal does not replace the selected native graph operand",
                ));
            }
        }
        Change::GraphBranch {
            block, old, new, ..
        } => {
            let spec = read_json(&project.root.join("build/graph.json"))?;
            let blocks = spec["blocks"]
                .as_array()
                .ok_or_else(|| CliFailure::invalid("graph recipe has no blocks"))?;
            let index = blocks
                .iter()
                .position(|item| item["name"] == *block)
                .ok_or_else(|| CliFailure::invalid("bound branch block is absent"))?;
            let function_id = state_text(&project.state, "function_id")?;
            let function = head
                .objects()
                .iter()
                .find(|object| hex(object.record().entity_id.as_bytes()) == function_id)
                .ok_or_else(|| CliFailure::stale("branch owner function is absent"))?;
            let EntityBodyValue::Function(function_body) = &function.record().body else {
                return Err(CliFailure::invalid("branch owner is not a function"));
            };
            if function_body.blocks.len() != blocks.len()
                || function_body.blocks[index] != operation.target_entity
            {
                return Err(CliFailure::invalid(
                    "branch block differs from accepted function",
                ));
            }
            let target_id = |name: &str| -> Result<EntityId, CliFailure> {
                let position = blocks
                    .iter()
                    .position(|item| item["name"] == name)
                    .ok_or_else(|| CliFailure::invalid("branch target is absent"))?;
                Ok(function_body.blocks[position])
            };
            let EntityBodyValue::Block(old_block) = &old_object.record().body else {
                return Err(CliFailure::invalid("accepted branch entity is not a block"));
            };
            if old_block.function != function.record().entity_id {
                return Err(CliFailure::invalid(
                    "accepted branch belongs to a different function",
                ));
            }
            let mut expected = old_block.clone();
            match (&mut expected.terminator, old.get("target")) {
                (sley_ssmc::Terminator::Branch(branch), Some(_)) => {
                    let old_target = old["target"]
                        .as_str()
                        .ok_or_else(|| CliFailure::invalid("bound branch target is not text"))?;
                    let new_target = new["target"]
                        .as_str()
                        .ok_or_else(|| CliFailure::invalid("new branch target is not text"))?;
                    if branch.edge.target
                        != target_id(old_target)
                            .map_err(|_| CliFailure::invalid("bound branch target is invalid"))?
                        || !branch.edge.arguments.is_empty()
                    {
                        return Err(CliFailure::invalid(
                            "accepted branch differs from bound recipe",
                        ));
                    }
                    branch.edge.target = target_id(new_target)?;
                }
                (sley_ssmc::Terminator::CondBranch(branch), None) => {
                    let old_true = old["if_true"]
                        .as_str()
                        .ok_or_else(|| CliFailure::invalid("bound true target is not text"))?;
                    let old_false = old["if_false"]
                        .as_str()
                        .ok_or_else(|| CliFailure::invalid("bound false target is not text"))?;
                    let new_true = new["if_true"]
                        .as_str()
                        .ok_or_else(|| CliFailure::invalid("new true target is not text"))?;
                    let new_false = new["if_false"]
                        .as_str()
                        .ok_or_else(|| CliFailure::invalid("new false target is not text"))?;
                    if branch.if_true.target != target_id(old_true)?
                        || branch.if_false.target != target_id(old_false)?
                        || !branch.if_true.arguments.is_empty()
                        || !branch.if_false.arguments.is_empty()
                    {
                        return Err(CliFailure::invalid(
                            "accepted conditional branch differs from bound recipe",
                        ));
                    }
                    branch.if_true.target = target_id(new_true)?;
                    branch.if_false.target = target_id(new_false)?;
                }
                _ => {
                    return Err(CliFailure::invalid(
                        "accepted branch kind differs from recipe",
                    ));
                }
            }
            if *body != EntityBodyValue::Block(expected)
                || edit["contract"] != "sley-tools.edit-graph-branch.v0"
                || edit["block_name"] != *block
                || edit["block_id"] != entity_text
                || edit["old_object_id"] != object_text
                || edit["old_targets"] != *old
                || edit["new_targets"] != *new
            {
                return Err(CliFailure::invalid(
                    "proposal does not replace the selected native branch",
                ));
            }
        }
    }
    let epoch_bytes: [u8; 32] = unhex(state_text(&project.state, "schema_epoch")?)?
        .try_into()
        .map_err(|_| CliFailure::invalid("schema identity must be 32 bytes"))?;
    let epoch = sley_id::SchemaEpochId::from_bytes(epoch_bytes);
    let new_object = build_entity_object(
        epoch,
        &EntityObjectRecord {
            entity_id: operation.target_entity,
            body: body.clone(),
            label: None,
            semantic_fingerprint: None,
        },
    )
    .map_err(|error| CliFailure::invalid(format!("proposal new object: {error}")))?;
    if edit[object_output] != hex(new_object.object_id().as_bytes()) {
        return Err(CliFailure::invalid(
            "proposal new object identity differs from replacement",
        ));
    }
    Ok(Prepared {
        document: document.clone(),
        edit,
        candidate,
        change,
    })
}

fn read_prepared(project: &Project) -> Result<Prepared, CliFailure> {
    let document = strict_local_json(&proposal_path(project))?;
    verify_proposal(project, &document)
}

pub(crate) fn validate_proposal(
    path: &Path,
    expected_candidate_id: Option<&str>,
) -> Result<Value, CliFailure> {
    let project = load_project(path)?;
    let prepared = read_prepared(&project)?;
    if expected_candidate_id.is_some_and(|expected| prepared.document["candidate_id"] != expected) {
        return Err(CliFailure::invalid(
            "requested candidate identity differs from pending proposal",
        ));
    }
    if validation_path(&project).exists() {
        return Err(CliFailure::invalid(
            "proposal already has a validation record",
        ));
    }
    let handshake_dir = project
        .root
        .join(".sley-tools")
        .join(format!("handshake-validate-{}", &hex(&random32()?)[..16]));
    fs::create_dir(&handshake_dir).map_err(|error| {
        CliFailure::failed(format!("cannot create validation handshake: {error}"))
    })?;
    let hello = offered_hello(&project.core)?;
    let handshake_id = handshake(&project.core, &handshake_dir, &hello)?;
    let mut endpoint = Smp::start(&project.core, &project.root.join("workspace"), &hello)?;
    let session = endpoint.request("session.open", &handshake_id, 0, None)?;
    let opened = endpoint.request("workspace.open", "", 1, Some(&session))?;
    let fields = record_fields(&unhex(&opened)?, 8)?;
    if hex(&fields[0]) != state_text(&project.state, "transaction_id")?
        || hex(&fields[1]) != state_text(&project.state, "state_root")?
    {
        return Err(CliFailure::stale(
            "accepted head advanced before proposal validation",
        ));
    }
    let stored = endpoint.request(
        "candidate.create",
        prepared.edit["candidate_record_hex"]
            .as_str()
            .ok_or_else(|| CliFailure::invalid("proposal omitted candidate bytes"))?,
        2,
        Some(&session),
    )?;
    if stored != prepared.edit["candidate_stored_hex"] {
        return Err(CliFailure::failed(
            "core candidate storage differs from proposal",
        ));
    }
    let validated_at = current_millis()?;
    let body = hex(&encode_record(&[
        (1, unhex(state_text(&project.state, "transaction_id")?)?),
        (2, unhex(state_text(&project.state, "principal_id")?)?),
        (3, encode_uvar(validated_at)),
        (4, unhex(&stored)?),
    ])
    .map_err(|error| CliFailure::failed(format!("validation request: {error}")))?);
    let result = endpoint.request("candidate.validate", &body, 3, Some(&session))?;
    require_valid_candidate(
        &result,
        prepared.document["candidate_id"]
            .as_str()
            .ok_or_else(|| CliFailure::invalid("proposal omitted candidate identity"))?,
    )?;
    let _ = endpoint.request("session.close", "", 4, Some(&session));
    endpoint.finish()?;
    let root = state_text(&project.state, "state_root")?;
    let head = TransactionRepository::new(&project.root.join("workspace"))
        .accepted_head()
        .map_err(|error| CliFailure::stale(format!("accepted head after validation: {error}")))?;
    if hex(head.state_root().root.as_bytes()) != root {
        return Err(CliFailure::stale(
            "accepted root advanced during proposal validation",
        ));
    }
    let validation = json!({"contract": "sley-tools.validation.v0", "result": "PASS",
        "candidate_id": prepared.document["candidate_id"], "base_root": root,
        "validated_at_unix_millis": validated_at,
        "expires_unix_millis": prepared.candidate.expiry.not_after});
    write_new_json(&validation_path(&project), &validation)?;
    Ok(
        json!({"contract": "sley-tools.validate.v0", "result": "PASS",
        "candidate_id": validation["candidate_id"], "base_root": root,
        "validated_at_unix_millis": validated_at,
        "accepted_root_unchanged": true,
        "commit_will_revalidate": true}),
    )
}

fn prepared_for_commit(
    project: &Project,
    expected_candidate_id: Option<&str>,
) -> Result<Prepared, CliFailure> {
    let prepared = read_prepared(&project)?;
    if expected_candidate_id.is_some_and(|expected| prepared.document["candidate_id"] != expected) {
        return Err(CliFailure::invalid(
            "requested candidate identity differs from pending proposal",
        ));
    }
    let validation = strict_local_json(&validation_path(&project))?;
    if validation["contract"] != "sley-tools.validation.v0"
        || validation["result"] != "PASS"
        || validation["candidate_id"] != prepared.document["candidate_id"]
        || validation["base_root"] != prepared.document["base_root"]
        || validation["expires_unix_millis"] != prepared.candidate.expiry.not_after
    {
        return Err(CliFailure::invalid(
            "validation record differs from exact proposal",
        ));
    }
    Ok(prepared)
}

pub(crate) fn preflight_commit_proposal(
    path: &Path,
    expected_candidate_id: Option<&str>,
) -> Result<(), CliFailure> {
    let project = load_project(path)?;
    prepared_for_commit(&project, expected_candidate_id).map(|_| ())
}

pub(crate) fn commit_proposal_observed(
    path: &Path,
    expected_candidate_id: Option<&str>,
) -> Result<Value, CliFailure> {
    let project = load_project(path)?;
    let prepared = prepared_for_commit(&project, expected_candidate_id)?;
    let mut result = match prepared.change {
        Change::Release { new, .. } => edit_with_prepared(path, new, Some(&prepared.edit)),
        Change::Resource { new, .. } => {
            edit_resource_with_prepared(path, new, Some(&prepared.edit))
        }
        Change::GraphConstant {
            name, new, cases, ..
        } => graph_project::commit_constant(path, &name, &new, &cases, &prepared.edit),
        Change::GraphOpcode {
            block,
            name,
            new,
            cases,
            ..
        } => graph_project::commit_opcode(path, &block, &name, &new, &cases, &prepared.edit),
        Change::GraphOperand {
            block,
            name,
            slot,
            new,
            cases,
            ..
        } => {
            graph_project::commit_operand(path, &block, &name, &slot, &new, &cases, &prepared.edit)
        }
        Change::GraphBranch {
            block, new, cases, ..
        } => graph_project::commit_branch(path, &block, &new, &cases, &prepared.edit),
    }?;
    if result["candidate_id"] != prepared.document["candidate_id"] {
        return Err(CliFailure::outcome_unknown(
            "native commit returned a different candidate identity; inspect accepted head before retrying",
        ));
    }
    let _ = fs::remove_file(proposal_path(&project));
    let _ = fs::remove_file(validation_path(&project));
    result["proposal_validated_separately"] = json!(true);
    result["validation_rechecked_on_commit"] = json!(true);
    Ok(result)
}

pub(crate) fn discard_proposal(
    path: &Path,
    expected_candidate_id: Option<&str>,
) -> Result<Value, CliFailure> {
    let project = load_project(path)?;
    let proposal = strict_local_json(&proposal_path(&project))?;
    if proposal["contract"] != "sley-tools.proposal.v0" {
        return Err(CliFailure::invalid(
            "local authoring record is not a proposal",
        ));
    }
    let candidate_id = proposal["candidate_id"].clone();
    if expected_candidate_id.is_some_and(|expected| candidate_id != expected) {
        return Err(CliFailure::invalid(
            "requested candidate identity differs from pending proposal",
        ));
    }
    if validation_path(&project).exists() {
        fs::remove_file(validation_path(&project))
            .map_err(|error| CliFailure::failed(format!("cannot discard validation: {error}")))?;
    }
    fs::remove_file(proposal_path(&project))
        .map_err(|error| CliFailure::failed(format!("cannot discard proposal: {error}")))?;
    Ok(
        json!({"contract": "sley-tools.proposal-discard.v0", "candidate_id": candidate_id,
        "accepted_root": project.state["state_root"], "accepted_root_unchanged": true}),
    )
}
