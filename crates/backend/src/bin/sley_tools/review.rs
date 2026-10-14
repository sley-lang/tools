//! Bounded native artifact review; accepted imports and executions are evidence.

use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use super::{CliFailure, TESTED_CORE_COMMIT, core_check, rpc, starter};

const MAX_INPUT_BYTES: u64 = 1_048_576;
const MAX_GRAPH_ARTIFACT_BYTES: u64 = 8_388_608;
const MAX_CASES: usize = 128;

struct Package {
    manifest: Value,
    artifact: Value,
    manifest_sha256: String,
    artifact_sha256: String,
    artifact_path: PathBuf,
}

struct Scratch(PathBuf);

impl Scratch {
    fn new() -> Result<Self, CliFailure> {
        let mut random = [0_u8; 12];
        File::open("/dev/urandom")
            .and_then(|mut source| source.read_exact(&mut random))
            .map_err(|error| {
                CliFailure::failed(format!("cannot create review workspace: {error}"))
            })?;
        let suffix: String = random.iter().map(|byte| format!("{byte:02x}")).collect();
        let path = std::env::temp_dir().join(format!("sley-tools-review-{suffix}"));
        fs::create_dir(&path).map_err(|error| {
            CliFailure::failed(format!("cannot create review workspace: {error}"))
        })?;
        Ok(Self(path))
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn read_regular(path: &Path, maximum: u64) -> Result<Vec<u8>, CliFailure> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| CliFailure::invalid(format!("cannot stat {}: {error}", path.display())))?;
    if !metadata.file_type().is_file() || metadata.len() > maximum {
        return Err(CliFailure::invalid(format!(
            "{} must be a regular file of at most {maximum} bytes",
            path.display(),
        )));
    }
    let mut bytes = Vec::new();
    File::open(path)
        .and_then(|file| file.take(maximum + 1).read_to_end(&mut bytes))
        .map_err(|error| CliFailure::invalid(format!("cannot read {}: {error}", path.display())))?;
    if bytes.len() as u64 > maximum {
        return Err(CliFailure::invalid("review input exceeds its size limit"));
    }
    Ok(bytes)
}

fn strict_file(path: &Path) -> Result<(Value, String), CliFailure> {
    strict_file_limited(path, MAX_INPUT_BYTES)
}

fn strict_file_limited(path: &Path, maximum: u64) -> Result<(Value, String), CliFailure> {
    let bytes = read_regular(path, maximum)?;
    let parsed = rpc::parse_strict_json(&bytes).map_err(|error| {
        CliFailure::invalid(format!(
            "invalid or duplicate JSON in {}: {error}",
            path.display()
        ))
    })?;
    Ok((parsed, digest(&bytes)))
}

fn field<'a>(value: &'a Value, name: &str) -> Result<&'a str, CliFailure> {
    value[name]
        .as_str()
        .ok_or_else(|| CliFailure::invalid(format!("review package omits {name}")))
}

fn load_package(path: &Path, core_sha256: &str) -> Result<Package, CliFailure> {
    let (manifest, manifest_sha256) = strict_file(path)?;
    let graph_profile = manifest["authoring_profile"] == "graph_v0";
    let artifact_contract = if graph_profile {
        "sley-tools.native-graph-artifact.v1"
    } else {
        "sley-tools.native-program-artifact.v0"
    };
    if manifest["contract"] != "sley-tools.program.v0"
        || manifest["status"] != "portable_native_artifact"
        || manifest["core"]["commit"] != TESTED_CORE_COMMIT
        || manifest["core"]["profile"] != "extended_v1"
        || manifest["core"]["binary_sha256"] != core_sha256
        || manifest["entrypoint"]["name"] != "evaluate"
        || manifest["artifact"]["contract"] != artifact_contract
    {
        return Err(CliFailure::invalid(
            "review manifest is outside the selected native profile",
        ));
    }
    let program_id = field(&manifest, "program_id")?;
    if !graph_profile && !["release-readiness", "resource-admission"].contains(&program_id) {
        return Err(CliFailure::import_unsupported(
            "review program profile is unavailable",
        ));
    }
    let relative = field(&manifest["artifact"], "path")?;
    let mut parts = relative.split('/');
    let name = match (parts.next(), parts.next(), parts.next()) {
        (Some("artifact"), Some(name), None)
            if !name.is_empty()
                && name != "."
                && name != ".."
                && name
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte)) =>
        {
            name
        }
        _ => {
            return Err(CliFailure::invalid(
                "review artifact path must be artifact/FILE",
            ));
        }
    };
    let root = path
        .parent()
        .ok_or_else(|| CliFailure::invalid("review manifest has no parent"))?
        .canonicalize()
        .map_err(|error| {
            CliFailure::invalid(format!("cannot resolve manifest directory: {error}"))
        })?;
    let folder = root.join("artifact");
    if !fs::symlink_metadata(&folder)
        .map_err(|error| CliFailure::invalid(format!("cannot stat artifact directory: {error}")))?
        .file_type()
        .is_dir()
    {
        return Err(CliFailure::invalid(
            "review artifact directory is a symlink or not a directory",
        ));
    }
    let artifact_path = folder.join(name);
    let maximum = if graph_profile {
        MAX_GRAPH_ARTIFACT_BYTES
    } else {
        MAX_INPUT_BYTES
    };
    let (artifact, artifact_sha256) = strict_file_limited(&artifact_path, maximum)?;
    if field(&manifest["artifact"], "sha256")? != artifact_sha256
        || artifact["contract"] != artifact_contract
        || artifact["program_id"] != program_id
        || artifact["core_commit"] != TESTED_CORE_COMMIT
        || artifact["source_state_root"] != manifest["entrypoint"]["state_root"]
        || artifact["source_function_id"] != manifest["entrypoint"]["function_id"]
        || artifact["result_fields"] != manifest["result_fields"]
        || (graph_profile
            && (artifact["source_result_type_id"] != manifest["result_type_id"]
                || artifact["recipe_sha256"] != manifest["graph_recipe"]["sha256"]))
    {
        return Err(CliFailure::invalid(
            "review manifest and native artifact identities disagree",
        ));
    }
    if graph_profile {
        if manifest["graph_recipe"]["path"] != "build/graph.json" {
            return Err(CliFailure::invalid(
                "review graph recipe path must be build/graph.json",
            ));
        }
        let build_dir = root.join("build");
        if !fs::symlink_metadata(&build_dir)
            .map_err(|error| CliFailure::invalid(format!("cannot stat build directory: {error}")))?
            .file_type()
            .is_dir()
        {
            return Err(CliFailure::invalid(
                "review graph build directory is a symlink or not a directory",
            ));
        }
        let (_, recipe_sha256) = strict_file_limited(&build_dir.join("graph.json"), 65_536)?;
        if recipe_sha256 != field(&artifact, "recipe_sha256")? {
            return Err(CliFailure::invalid(
                "review graph recipe differs from the accepted artifact",
            ));
        }
    }
    Ok(Package {
        manifest,
        artifact,
        manifest_sha256,
        artifact_sha256,
        artifact_path,
    })
}

pub(super) fn verified_artifact_path(
    manifest: &Path,
    core_sha256: &str,
) -> Result<PathBuf, CliFailure> {
    Ok(load_package(manifest, core_sha256)?.artifact_path)
}

fn objects(package: &Package) -> Result<BTreeMap<String, (u64, String)>, CliFailure> {
    let items = package.artifact["objects"]
        .as_array()
        .ok_or_else(|| CliFailure::invalid("review artifact has no native objects"))?;
    let mut result = BTreeMap::new();
    for item in items {
        let entity = field(item, "entity_id")?.to_owned();
        let object = field(item, "object_id")?.to_owned();
        let kind = item["kind"]
            .as_u64()
            .ok_or_else(|| CliFailure::invalid("review artifact object has no kind"))?;
        if result.insert(entity, (kind, object)).is_some() {
            return Err(CliFailure::invalid(
                "review artifact repeats an entity identity",
            ));
        }
    }
    Ok(result)
}

fn kind_name(kind: u64) -> &'static str {
    match kind {
        4 => "record type",
        5 => "function",
        6 => "parameter",
        7 => "block",
        8 => "operation",
        9 => "constant",
        _ => "unsupported entity",
    }
}

fn structural(before: &Package, after: &Package) -> Result<Value, CliFailure> {
    let old = objects(before)?;
    let new = objects(after)?;
    let mut added = Vec::new();
    let mut removed = Vec::new();
    let mut changed = Vec::new();
    let mut shared = 0_usize;
    for (entity, (kind, object)) in &old {
        match new.get(entity) {
            None => removed
                .push(json!({"entity_id": entity, "kind": kind_name(*kind), "object_id": object})),
            Some((next_kind, next_object)) => {
                shared += 1;
                if kind != next_kind || object != next_object {
                    changed.push(json!({"entity_id": entity, "kind_before": kind_name(*kind),
                        "kind_after": kind_name(*next_kind), "object_id_before": object,
                        "object_id_after": next_object}));
                }
            }
        }
    }
    for (entity, (kind, object)) in &new {
        if !old.contains_key(entity) {
            added.push(json!({"entity_id": entity, "kind": kind_name(*kind), "object_id": object}));
        }
    }
    Ok(
        json!({"shared_entity_ids": shared, "added": added, "removed": removed, "changed": changed,
        "matching_rule": "exact entity ID only; no inferred renames or cross-workspace semantic matching"}),
    )
}

fn fixtures(
    path: &Path,
    program_id: &str,
    graph_profile: bool,
) -> Result<(Vec<Value>, String), CliFailure> {
    let (document, checksum) = strict_file(path)?;
    let contract = if graph_profile {
        "sley-tools.graph-cases.v0"
    } else {
        match program_id {
            "release-readiness" => "sley-tools.release-readiness-fixtures.v1",
            "resource-admission" => "sley-tools.resource-admission-fixtures.v1",
            _ => {
                return Err(CliFailure::import_unsupported(
                    "review fixture program profile is unavailable",
                ));
            }
        }
    };
    let cases = document["cases"]
        .as_array()
        .ok_or_else(|| CliFailure::invalid("review fixtures have no cases"))?;
    if document["contract"] != contract || cases.is_empty() || cases.len() > MAX_CASES {
        return Err(CliFailure::invalid(
            "review fixtures have an unsupported contract or case count",
        ));
    }
    let mut ids = BTreeSet::new();
    for case in cases {
        let id = field(case, "id")?;
        let shape_ok = if graph_profile {
            case["input"].is_array()
                && case
                    .as_object()
                    .is_some_and(|item| item.contains_key("expected"))
        } else {
            case["input"].is_object() && case["expected"].is_object()
        };
        if id.len() > 128 || !ids.insert(id.to_owned()) || !shape_ok {
            return Err(CliFailure::invalid(
                "review fixtures repeat a case or omit input/expected",
            ));
        }
    }
    Ok((cases.clone(), checksum))
}

fn run_input(input: &Value, program_id: &str, graph_profile: bool) -> Result<Value, CliFailure> {
    if graph_profile || program_id != "resource-admission" {
        return Ok(input.clone());
    }
    let source = input
        .as_object()
        .ok_or_else(|| CliFailure::invalid("Resource Admission review input must be a record"))?;
    let names = [
        "requested_workers",
        "available_workers",
        "requested_memory_mib",
        "available_memory_mib",
    ];
    if source.len() != names.len() || names.iter().any(|name| !source.contains_key(*name)) {
        return Err(CliFailure::invalid(
            "Resource Admission review input has wrong fields",
        ));
    }
    let mut values = serde_json::Map::new();
    for name in names {
        let text = source[name].as_str().ok_or_else(|| {
            CliFailure::invalid("Resource Admission fixture integer must be text")
        })?;
        let parsed = text.parse::<i64>().map_err(|_| {
            CliFailure::invalid("Resource Admission fixture integer is outside i64")
        })?;
        if parsed < 0 || parsed.to_string() != text {
            return Err(CliFailure::invalid(
                "Resource Admission fixture integer is not canonical nonnegative i64",
            ));
        }
        values.insert(name.to_owned(), json!(parsed));
    }
    Ok(Value::Object(values))
}

fn imported_policy(
    project: &Path,
    program_id: &str,
    graph_profile: bool,
) -> Result<Value, CliFailure> {
    if graph_profile {
        let (recipe, _) = strict_file(&project.join("build/graph.json"))?;
        let constants = recipe["constants"]
            .as_array()
            .ok_or_else(|| CliFailure::execution("imported graph recipe has no constants"))?;
        let mut named = BTreeMap::new();
        for item in constants {
            let name = field(item, "name")?;
            if !item
                .as_object()
                .is_some_and(|value| value.contains_key("value"))
                || named
                    .insert(name.to_owned(), item["value"].clone())
                    .is_some()
            {
                return Err(CliFailure::execution(
                    "imported graph has invalid named constants",
                ));
            }
        }
        return Ok(json!({"named_constants": named}));
    }
    let (state, _) = strict_file(&project.join(".sley-tools/state.json"))?;
    match program_id {
        "release-readiness" => Ok(json!({"max_blocking_findings": state["threshold"]})),
        "resource-admission" => Ok(json!({"require_memory_spare": state["require_memory_spare"]})),
        _ => Err(CliFailure::import_unsupported(
            "review policy profile is unavailable",
        )),
    }
}

fn validation_summary(imported: &Value) -> Result<Value, CliFailure> {
    let checked = &imported["verification"];
    let count = checked["external_assertions_against_native_execution"]
        .as_u64()
        .ok_or_else(|| CliFailure::execution("fresh import omitted external assertion count"))?;
    Ok(json!({"result": checked["result"],
        "external_assertions_against_native_execution": count,
        "native_core_test_admission": checked["native_core_test_admission"]}))
}

fn validate_declared_interface(package: &Package, imported: &Value) -> Result<(), CliFailure> {
    let canonical = &imported["program"];
    if package.manifest["inputs"] != canonical["inputs"]
        || package.manifest["output"] != canonical["output"]
        || package.manifest["result_fields"] != canonical["result_fields"]
    {
        return Err(CliFailure::invalid(
            "review manifest interface differs from imported native profile",
        ));
    }
    let native_type = package.artifact["objects"]
        .as_array()
        .and_then(|objects| objects.iter().find(|object| object["kind"] == 4))
        .and_then(|object| object["entity_id"].as_str())
        .map_or(Value::Null, |identity| json!(identity));
    if package.manifest["result_type_id"] != native_type {
        return Err(CliFailure::invalid(
            "review manifest result type differs from native artifact",
        ));
    }
    Ok(())
}

pub(super) fn compute(
    before_manifest: &Path,
    after_manifest: &Path,
    core: &Path,
    cases_path: &Path,
) -> Result<Value, CliFailure> {
    let core_report = core_check(core.to_path_buf())?;
    if core_report["status"] != "compatible" {
        return Err(CliFailure::incompatible(
            "review requires the pinned Sley 2.0.1 core",
        ));
    }
    let core_sha256 = field(&core_report["core"], "binary_sha256")?;
    let before = load_package(before_manifest, core_sha256)?;
    let after = load_package(after_manifest, core_sha256)?;
    let program_id = field(&before.manifest, "program_id")?;
    let graph_profile = before.manifest["authoring_profile"] == "graph_v0";
    if after.manifest["program_id"] != program_id
        || (after.manifest["authoring_profile"] == "graph_v0") != graph_profile
    {
        return Err(CliFailure::invalid(
            "review packages are different native programs",
        ));
    }
    let (cases, cases_sha256) = fixtures(cases_path, program_id, graph_profile)?;
    let structure = structural(&before, &after)?;
    let scratch = Scratch::new()?;
    let old_project = scratch.0.join("before");
    let new_project = scratch.0.join("after");
    let imported_before = starter::artifact_import(&before.artifact_path, &old_project, core)?;
    let imported_after = starter::artifact_import(&after.artifact_path, &new_project, core)?;
    for imported in [&imported_before, &imported_after] {
        if imported["verification"]["result"] != "PASS" {
            return Err(CliFailure::execution(
                "core import or external native assertions did not pass",
            ));
        }
    }
    validate_declared_interface(&before, &imported_before)?;
    validate_declared_interface(&after, &imported_after)?;
    let before_policy = imported_policy(&old_project, program_id, graph_profile)?;
    let after_policy = imported_policy(&new_project, program_id, graph_profile)?;
    let before_validation = validation_summary(&imported_before)?;
    let after_validation = validation_summary(&imported_after)?;
    let input_path = scratch.0.join("case.json");
    let mut changed_cases = Vec::new();
    for case in &cases {
        let input = run_input(&case["input"], program_id, graph_profile)?;
        let encoded = serde_json::to_vec(&input)
            .map_err(|error| CliFailure::invalid(format!("cannot encode review case: {error}")))?;
        fs::write(&input_path, encoded)
            .map_err(|error| CliFailure::failed(format!("cannot write review case: {error}")))?;
        let old_run = starter::run(&old_project, &input_path)?;
        let new_run = starter::run(&new_project, &input_path)?;
        if old_run["result"] != case["expected"] {
            return Err(CliFailure::execution(format!(
                "baseline native result differs from fixture {}",
                field(case, "id")?
            )));
        }
        if old_run["result"] != new_run["result"] {
            changed_cases.push(json!({"id": case["id"], "input": case["input"],
                "before": old_run["result"], "after": new_run["result"]}));
        }
    }
    let tools_binary = std::env::current_exe()
        .map_err(|error| CliFailure::failed(format!("cannot locate tools binary: {error}")))?;
    let tools_sha256 = super::sha256(&tools_binary)?;
    let interfaces_changed = before.manifest["inputs"] != after.manifest["inputs"]
        || before.manifest["output"] != after.manifest["output"]
        || before.manifest["result_fields"] != after.manifest["result_fields"]
        || before.manifest["entrypoint"]["name"] != after.manifest["entrypoint"]["name"];
    let count = cases.len();
    let changed_count = changed_cases.len();
    let interpretation_basis = if graph_profile {
        "named constants from recipe bodies verified during fresh core-validated artifact imports"
    } else {
        "native objects parsed during fresh core-validated artifact imports"
    };
    let report_contract = if graph_profile {
        "sley-tools.review.v1"
    } else {
        "sley-tools.review.v0"
    };
    Ok(json!({
        "contract": report_contract, "result": "PASS_BOUNDED_LOCAL",
        "summary": format!("{program_id}: {changed_count} of {count} executed cases changed; {} exact entity versions changed", structure["changed"].as_array().map_or(0, Vec::len)),
        "identity": {
            "program_id": program_id, "core_commit": TESTED_CORE_COMMIT,
            "core_binary_sha256": core_sha256, "core_profile": "extended_v1",
            "tools_binary_sha256": tools_sha256, "tools_source_revision": Value::Null,
            "before": {"manifest_sha256": before.manifest_sha256, "artifact_sha256": before.artifact_sha256,
                "claimed_source_root": before.artifact["source_state_root"], "source_function_id": before.artifact["source_function_id"]},
            "after": {"manifest_sha256": after.manifest_sha256, "artifact_sha256": after.artifact_sha256,
                "claimed_source_root": after.artifact["source_state_root"], "source_function_id": after.artifact["source_function_id"]},
            "fixtures_sha256": cases_sha256,
        },
        "structural_change": structure,
        "interface_change": {"changed": interfaces_changed,
            "before": {"inputs": before.manifest["inputs"], "output": before.manifest["output"],
                "result_fields": before.manifest["result_fields"], "entrypoint_name": before.manifest["entrypoint"]["name"]},
            "after": {"inputs": after.manifest["inputs"], "output": after.manifest["output"],
                "result_fields": after.manifest["result_fields"], "entrypoint_name": after.manifest["entrypoint"]["name"]}},
        "profile_interpretation": {"before": before_policy, "after": after_policy,
            "basis": interpretation_basis},
        "behavioral_evidence": {"case_count": count, "baseline_fixture_matches": true,
            "changed_case_count": changed_count, "changed_cases": changed_cases,
            "meaning": "observed results on finite declared inputs, not a proof of equivalence"},
        "validation": {"before": before_validation, "after": after_validation,
            "core_candidate_validate_and_commit": "PASS on fresh imports",
            "native_core_test_admission": "NOT_RUN: production native executor unavailable"},
        "integrity": {"before_manifest_artifact_binding": "PASS", "after_manifest_artifact_binding": "PASS",
            "manifest_interface_matches_imported_native_profile": "PASS",
            "artifact_native_object_validation": "PASS on fresh imports", "generated_from_current_inputs": true,
            "full_manifest_json_schema": "NOT_RUN: standalone CLI has no JSON Schema validator"},
        "limitations": [
            "Source roots are exporter claims; v0 artifacts carry no signed source receipt.",
            "Entity matching uses exact IDs only; cross-workspace renames are not inferred.",
            "Finite executed cases do not prove equivalence for all inputs.",
            "Fresh import roots differ by workspace authority and are omitted from the reproducible report.",
            "Native core test admission and full manifest JSON Schema validation are not available in this command.",
            "The executable SHA-256 identifies the tools build; a source revision is not embedded."
        ]
    }))
}

pub(super) fn write_report(report: &Value, output: &Path) -> Result<Value, CliFailure> {
    let mut bytes = serde_json::to_vec_pretty(report)
        .map_err(|error| CliFailure::failed(format!("cannot encode review report: {error}")))?;
    bytes.push(b'\n');
    if bytes.len() > MAX_INPUT_BYTES as usize {
        return Err(CliFailure::invalid("review report exceeds 1 MiB"));
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(output)
        .map_err(|error| {
            CliFailure::invalid(format!("cannot create new review report: {error}"))
        })?;
    if let Err(error) = file.write_all(&bytes).and_then(|()| file.sync_all()) {
        let _ = fs::remove_file(output);
        return Err(CliFailure::failed(format!(
            "cannot finish review report: {error}"
        )));
    }
    let contract = if report["contract"] == "sley-tools.review.v1" {
        "sley-tools.review-result.v1"
    } else {
        "sley-tools.review-result.v0"
    };
    Ok(json!({"contract": contract, "output": output,
        "report_sha256": digest(&bytes), "summary": report["summary"],
        "changed_case_count": report["behavioral_evidence"]["changed_case_count"],
        "completeness": "BOUNDED_LOCAL_REVIEW"}))
}

pub(super) fn verify_report(
    report_path: &Path,
    before: &Path,
    after: &Path,
    core: &Path,
    cases: &Path,
) -> Result<Value, CliFailure> {
    let (stored, stored_sha256) = strict_file(report_path)?;
    let recomputed = compute(before, after, core, cases)?;
    if stored != recomputed {
        return Err(CliFailure::execution(
            "review report differs from current native artifacts, manifests, fixtures, or tools/core identity",
        ));
    }
    let contract = if recomputed["contract"] == "sley-tools.review.v1" {
        "sley-tools.review-verification.v1"
    } else {
        "sley-tools.review-verification.v0"
    };
    Ok(json!({"contract": contract, "result": "PASS",
        "report_sha256": stored_sha256, "report_recomputed_from_inputs": true,
        "changed_case_count": recomputed["behavioral_evidence"]["changed_case_count"]}))
}
