//! Static core validation of a genuine graph TestCase candidate.
//!
//! The selected core has no production test executor. This diagnostic never
//! commits the TestCase and never presents static validation as test passage.

use super::*;

struct HandshakeDirectory(PathBuf);

impl Drop for HandshakeDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

pub(crate) fn native_test_candidate(path: &Path, case_id: &str) -> Result<Value, CliFailure> {
    if case_id.is_empty() || case_id.len() > 64 {
        return Err(CliFailure::invalid(
            "native test case ID must have 1..64 bytes",
        ));
    }
    let project = load_project(path)?;
    if project.manifest["authoring_profile"] != "graph_v0" {
        return Err(CliFailure::invalid(
            "native TestCase construction requires a graph project",
        ));
    }
    let spec_path = project.root.join("build/graph.json");
    let spec: Value = serde_json::from_slice(&graph_project::read_regular(&spec_path, 65_536)?)
        .map_err(|error| CliFailure::invalid(format!("graph recipe is invalid JSON: {error}")))?;
    if spec["result"]["kind"] != "primitive" || spec["result"]["value_type"] != "Bool" {
        return Err(CliFailure::invalid(
            "native TestCase construction currently supports Boolean graph results",
        ));
    }
    let cases: Value = serde_json::from_slice(&graph_project::read_regular(
        &project.root.join("tests/cases.json"),
        1_048_576,
    )?)
    .map_err(|error| CliFailure::invalid(format!("graph cases are invalid JSON: {error}")))?;
    graph_project::validate_case_values(&spec, &cases)?;
    let case = cases["cases"]
        .as_array()
        .expect("validated graph cases")
        .iter()
        .find(|row| row["id"] == case_id)
        .ok_or_else(|| CliFailure::invalid("native test case ID is absent"))?;
    let case_json = serde_json::to_string(case)
        .map_err(|error| CliFailure::failed(format!("cannot encode native test case: {error}")))?;
    let repository = project.root.join("workspace");
    let constructed = builder(
        &project.builder,
        "construct-graph-native-test",
        &[
            repository
                .to_str()
                .ok_or_else(|| CliFailure::invalid("workspace path is not UTF-8"))?,
            spec_path
                .to_str()
                .ok_or_else(|| CliFailure::invalid("graph spec path is not UTF-8"))?,
            &case_json,
            state_text(&project.state, "workspace_id")?,
            state_text(&project.state, "principal_id")?,
            state_text(&project.state, "state_root")?,
            state_text(&project.state, "policy_root")?,
            state_text(&project.state, "transaction_id")?,
            state_text(&project.state, "schema_epoch")?,
            state_text(&project.state, "function_id")?,
        ],
    )?;
    if constructed["contract"] != "sley-tools.graph-native-test-construction-probe.v0"
        || constructed["case_id"] != case_id
        || constructed["target_function_id"] != project.state["function_id"]
    {
        return Err(CliFailure::failed(
            "native TestCase builder returned a different case or target",
        ));
    }
    let hello = offered_hello(&project.core)?;
    let handshake_dir = project.root.join(".sley-tools").join(format!(
        "handshake-native-test-{}",
        &hex(&random32()?)[..16]
    ));
    fs::create_dir(&handshake_dir)
        .map_err(|error| CliFailure::failed(format!("cannot create handshake: {error}")))?;
    let _handshake_directory = HandshakeDirectory(handshake_dir.clone());
    let handshake_id = handshake(&project.core, &handshake_dir, &hello)?;
    let mut endpoint = Smp::start(&project.core, &repository, &hello)?;
    let session = endpoint.request("session.open", &handshake_id, 0, None)?;
    let opened = endpoint.request("workspace.open", "", 1, Some(&session))?;
    let before = record_fields(&unhex(&opened)?, 8)?;
    if hex(&before[0]) != state_text(&project.state, "transaction_id")?
        || hex(&before[1]) != state_text(&project.state, "state_root")?
    {
        return Err(CliFailure::stale(
            "accepted head advanced before native TestCase validation",
        ));
    }
    let stored = endpoint.request(
        "candidate.create",
        constructed["candidate_record_hex"]
            .as_str()
            .ok_or_else(|| CliFailure::failed("builder omitted candidate record"))?,
        2,
        Some(&session),
    )?;
    if stored != constructed["candidate_stored_hex"] {
        return Err(CliFailure::failed(
            "core stored candidate differs from native TestCase builder",
        ));
    }
    let body = hex(&encode_record(&[
        (1, unhex(state_text(&project.state, "transaction_id")?)?),
        (2, unhex(state_text(&project.state, "principal_id")?)?),
        (3, encode_uvar(current_millis()?)),
        (4, unhex(&stored)?),
    ])
    .map_err(|error| CliFailure::failed(format!("native test validation request: {error}")))?);
    let validation = endpoint.request("candidate.validate", &body, 3, Some(&session))?;
    require_valid_candidate(
        &validation,
        constructed["candidate_id"]
            .as_str()
            .ok_or_else(|| CliFailure::failed("builder omitted candidate identity"))?,
    )?;
    let after = endpoint.request("workspace.open", "", 4, Some(&session))?;
    let after_fields = record_fields(&unhex(&after)?, 8)?;
    if after_fields[0] != before[0] || after_fields[1] != before[1] {
        return Err(CliFailure::stale(
            "accepted head advanced during native TestCase validation",
        ));
    }
    endpoint.request("session.close", "", 5, Some(&session))?;
    endpoint.finish()?;
    let head = TransactionRepository::new(&repository)
        .accepted_head()
        .map_err(|error| CliFailure::failed(format!("cannot recheck accepted head: {error}")))?;
    if hex(head.state_root().root.as_bytes()) != state_text(&project.state, "state_root")? {
        return Err(CliFailure::stale(
            "accepted core root advanced after native TestCase validation",
        ));
    }
    Ok(json!({
        "contract": "sley-tools.native-test-candidate.v0",
        "phase": "STATICALLY_VALID",
        "program_id": project.manifest["program_id"],
        "case_id": case_id,
        "candidate_id": constructed["candidate_id"],
        "test_entity_id": constructed["test_entity_id"],
        "target_function_id": constructed["target_function_id"],
        "declared_limits": constructed["declared_limits"],
        "grant_ceilings": constructed["grant_ceilings"],
        "native_plan_id": constructed["native_plan_id"],
        "native_plan_selected_test_count": constructed["native_plan_selected_test_count"],
        "native_plan_preflight": constructed["native_plan_preflight"],
        "base_root": project.state["state_root"],
        "accepted_root_unchanged": true,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
        "native_test_execution": "NOT_RUN",
    }))
}
