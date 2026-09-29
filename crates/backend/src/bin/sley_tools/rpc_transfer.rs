//! Portable package operations confined to the workspace selected at RPC startup.

use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use super::{CliFailure, review, rpc, starter};

pub(super) struct Scope {
    workspace: PathBuf,
    core: PathBuf,
    core_sha256: String,
}

impl Scope {
    pub(super) fn new(
        project: &Path,
        workspace: &Path,
        inspection: &Value,
    ) -> Result<Self, CliFailure> {
        let metadata = fs::symlink_metadata(workspace).map_err(|error| {
            CliFailure::invalid(format!("RPC workspace is unavailable: {error}"))
        })?;
        if !metadata.file_type().is_dir() || metadata.permissions().mode() & 0o022 != 0 {
            return Err(CliFailure::invalid(
                "RPC workspace must be a real directory without group or other write access",
            ));
        }
        let workspace = workspace.canonicalize().map_err(|error| {
            CliFailure::invalid(format!("cannot resolve RPC workspace: {error}"))
        })?;
        if project.parent() != Some(workspace.as_path()) {
            return Err(CliFailure::invalid(
                "RPC project must be a direct child of the configured workspace",
            ));
        }
        let core = starter::selected_core(project)?
            .canonicalize()
            .map_err(|error| {
                CliFailure::incompatible(format!("configured core is unavailable: {error}"))
            })?;
        let core_sha256 = inspection["program"]["core"]["binary_sha256"]
            .as_str()
            .ok_or_else(|| CliFailure::invalid("project inspection omits core identity"))?
            .to_owned();
        if super::sha256(&core)? != core_sha256 {
            return Err(CliFailure::incompatible(
                "configured core differs from the inspected project identity",
            ));
        }
        Ok(Self {
            workspace,
            core,
            core_sha256,
        })
    }

    fn child(&self, name: &str) -> Result<PathBuf, CliFailure> {
        if name.is_empty()
            || name.len() > 64
            || !name.as_bytes()[0].is_ascii_alphanumeric()
            || !name
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"_-".contains(&byte))
        {
            return Err(CliFailure::invalid(
                "package or project name must be 1..64 ASCII letters, digits, underscore or dash and start with a letter or digit",
            ));
        }
        Ok(self.workspace.join(name))
    }

    fn package_manifest(&self, name: &str) -> Result<PathBuf, CliFailure> {
        let package = self.child(name)?;
        let metadata = fs::symlink_metadata(&package)
            .map_err(|error| CliFailure::invalid(format!("RPC package is unavailable: {error}")))?;
        if !metadata.file_type().is_dir() {
            return Err(CliFailure::invalid("RPC package must be a real directory"));
        }
        Ok(package.join("program.json"))
    }

    pub(super) fn export(
        &self,
        project: &Path,
        inspection: &Value,
        name: &str,
    ) -> Result<Value, CliFailure> {
        let latest = starter::inspect(project)?;
        if latest["accepted_state_root"] != inspection["accepted_state_root"]
            || latest["program"] != inspection["program"]
        {
            return Err(CliFailure::stale("project changed during RPC session"));
        }
        let package = self.child(name)?;
        fs::create_dir(&package).map_err(|error| {
            CliFailure::invalid(format!("cannot create new RPC package: {error}"))
        })?;
        let result = (|| -> Result<Value, CliFailure> {
            let artifact_dir = package.join("artifact");
            fs::create_dir(&artifact_dir).map_err(|error| {
                CliFailure::failed(format!("cannot create package artifact directory: {error}"))
            })?;
            let artifact_path = artifact_dir.join("native-rpc-export.json");
            let exported = starter::artifact_export(project, &artifact_path, false)?;
            if exported["source_state_root"] != inspection["accepted_state_root"] {
                return Err(CliFailure::stale("accepted root changed during RPC export"));
            }
            let after = starter::inspect(project)?;
            if after["accepted_state_root"] != inspection["accepted_state_root"]
                || after["program"] != inspection["program"]
            {
                return Err(CliFailure::stale("project changed during RPC export"));
            }
            let mut manifest = inspection["program"].clone();
            if !manifest.is_object() {
                return Err(CliFailure::failed(
                    "project inspection has no portable manifest",
                ));
            }
            manifest["artifact"] = json!({
                "contract": if manifest["authoring_profile"] == "graph_v0" {
                    "sley-tools.native-graph-artifact.v1"
                } else {
                    "sley-tools.native-program-artifact.v0"
                },
                "path": "artifact/native-rpc-export.json",
                "sha256": exported["sha256"],
            });
            manifest["status"] = json!("portable_native_artifact");
            if manifest["authoring_profile"] == "graph_v0" {
                let build = package.join("build");
                fs::create_dir(&build).map_err(|error| {
                    CliFailure::failed(format!("cannot create package build directory: {error}"))
                })?;
                fs::copy(project.join("build/graph.json"), build.join("graph.json")).map_err(
                    |error| CliFailure::failed(format!("cannot copy bound graph recipe: {error}")),
                )?;
            }
            if let Some(source) = manifest["imported_source_artifact"]["path"].as_str() {
                let name = source.strip_prefix("artifact/").ok_or_else(|| {
                    CliFailure::invalid(
                        "imported source artifact path is outside artifact directory",
                    )
                })?;
                if name.is_empty()
                    || name == "native-rpc-export.json"
                    || !name
                        .bytes()
                        .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
                {
                    return Err(CliFailure::invalid(
                        "imported source artifact name is invalid",
                    ));
                }
                let source_path = project.join(source);
                let metadata = fs::symlink_metadata(&source_path).map_err(|error| {
                    CliFailure::invalid(format!("imported source artifact is unavailable: {error}"))
                })?;
                if !metadata.file_type().is_file() || metadata.len() > 8_388_608 {
                    return Err(CliFailure::invalid(
                        "imported source artifact must be bounded regular file",
                    ));
                }
                let bytes = fs::read(&source_path).map_err(|error| {
                    CliFailure::failed(format!("cannot read imported source artifact: {error}"))
                })?;
                if format!("{:x}", Sha256::digest(&bytes))
                    != manifest["imported_source_artifact"]["sha256"]
                {
                    return Err(CliFailure::invalid(
                        "imported source artifact digest differs",
                    ));
                }
                fs::write(artifact_dir.join(name), bytes).map_err(|error| {
                    CliFailure::failed(format!("cannot copy imported source artifact: {error}"))
                })?;
            }
            let manifest_bytes = serde_json::to_vec_pretty(&manifest).map_err(|error| {
                CliFailure::failed(format!("cannot encode RPC package manifest: {error}"))
            })?;
            let manifest_path = package.join("program.json");
            write_new(&manifest_path, &manifest_bytes)?;
            let verified = review::verified_artifact_path(&manifest_path, &self.core_sha256)?;
            if verified != artifact_path {
                return Err(CliFailure::failed(
                    "RPC package verified a different artifact",
                ));
            }
            Ok(json!({
                "contract": "sley-tools.rpc-artifact-export.v0",
                "package": name,
                "manifest_sha256": format!("{:x}", Sha256::digest(&manifest_bytes)),
                "artifact_sha256": exported["sha256"],
                "source_state_root": exported["source_state_root"],
                "external_assertions_against_native_execution": exported["external_assertions_against_native_execution"],
                "accepted_root_unchanged": true,
            }))
        })();
        if result.is_err() {
            let _ = fs::remove_dir_all(&package);
        }
        result
    }

    pub(super) fn import(&self, package: &str, destination: &str) -> Result<Value, CliFailure> {
        let manifest = self.package_manifest(package)?;
        let artifact = review::verified_artifact_path(&manifest, &self.core_sha256)?;
        let project = self.child(destination)?;
        let manifest_sha256 = super::sha256(&manifest)?;
        let artifact_sha256 = super::sha256(&artifact)?;
        let operation_id = format!(
            "{:x}",
            Sha256::digest(format!(
                "sley-tools.rpc-artifact-import.v0\n{package}\n{destination}\n{manifest_sha256}\n{artifact_sha256}\n{}",
                self.core_sha256
            ))
        );
        let record_path = self
            .workspace
            .join(format!(".rpc-import-{destination}.json"));
        if let Ok(metadata) = fs::symlink_metadata(&record_path) {
            if !metadata.file_type().is_file() || metadata.len() > 1_048_576 {
                return Err(CliFailure::outcome_unknown(
                    "RPC import record is not a bounded regular file; inspect the destination",
                ));
            }
            let record = rpc::parse_strict_json(&fs::read(&record_path).map_err(|error| {
                CliFailure::outcome_unknown(format!("cannot read RPC import record: {error}"))
            })?)
            .map_err(|error| {
                CliFailure::outcome_unknown(format!("RPC import record is invalid: {error}"))
            })?;
            if record["contract"] != "sley-tools.rpc-import-operation.v0"
                || record["operation_id"] != operation_id
                || record["source_package"] != package
                || record["destination_project"] != destination
            {
                return Err(CliFailure::stale(
                    "RPC import destination is reserved for a different package or core",
                ));
            }
            return self.reconcile_import(&project, &record_path, &record, &artifact_sha256);
        }
        if fs::symlink_metadata(&project).is_ok() {
            return Err(CliFailure::invalid(
                "RPC import destination already exists; inspect it before choosing another name",
            ));
        }
        let record = json!({
            "contract": "sley-tools.rpc-import-operation.v0",
            "operation_id": operation_id,
            "source_package": package,
            "destination_project": destination,
            "manifest_sha256": manifest_sha256,
            "artifact_sha256": artifact_sha256,
            "core_binary_sha256": self.core_sha256,
            "phase": "started",
        });
        let mut record_bytes = serde_json::to_vec(&record).map_err(|error| {
            CliFailure::failed(format!("cannot encode RPC import operation: {error}"))
        })?;
        record_bytes.push(b'\n');
        write_new(&record_path, &record_bytes)?;
        let imported = starter::artifact_import(&artifact, &project, &self.core).map_err(|error| {
            CliFailure::outcome_unknown(format!(
                "RPC import {operation_id} did not complete observably ({}: {}); inspect the named destination and operation record",
                error.code, error.message
            ))
        })?;
        let result = json!({
            "contract": "sley-tools.rpc-artifact-import.v0",
            "source_package": package,
            "destination_project": destination,
            "operation_id": operation_id,
            "import": imported,
            "reconciled": false,
        });
        let accepted = starter::inspect(&project).map_err(|error| {
            CliFailure::outcome_unknown(format!(
                "RPC import {operation_id} returned but its destination cannot be verified ({}: {})",
                error.code, error.message
            ))
        })?;
        if accepted["program"]["imported_source_artifact"]["sha256"] != artifact_sha256 {
            return Err(CliFailure::outcome_unknown(
                "RPC import committed but the source artifact binding cannot be verified",
            ));
        }
        self.finish_import(&record_path, &record, &result, &accepted)?;
        Ok(result)
    }

    fn reconcile_import(
        &self,
        project: &Path,
        record_path: &Path,
        record: &Value,
        artifact_sha256: &str,
    ) -> Result<Value, CliFailure> {
        let accepted = starter::inspect(project).map_err(|_| {
            CliFailure::outcome_unknown(
                "RPC import outcome is unknown; inspect the named destination and operation record before retrying",
            )
        })?;
        if accepted["program"]["imported_source_artifact"]["sha256"] != artifact_sha256
            || accepted["program"]["core"]["binary_sha256"] != self.core_sha256
        {
            return Err(CliFailure::outcome_unknown(
                "RPC import destination does not verify against the reserved package and core",
            ));
        }
        if record["phase"] == "completed" {
            if record["accepted_root"] != accepted["accepted_state_root"] {
                return Err(CliFailure::outcome_unknown(
                    "RPC import destination root differs from the recorded completion",
                ));
            }
            let mut result = record["result"].clone();
            if !result.is_object() {
                return Err(CliFailure::outcome_unknown(
                    "RPC import completion record has no result",
                ));
            }
            result["reconciled"] = json!(true);
            return Ok(result);
        }
        if record["phase"] != "started" {
            return Err(CliFailure::outcome_unknown(
                "RPC import operation has an unknown phase",
            ));
        }
        let verification = starter::test(project)?;
        if verification["result"] != "PASS" {
            return Err(CliFailure::outcome_unknown(
                "RPC import destination is accepted but external assertions do not pass",
            ));
        }
        let result = json!({
            "contract": "sley-tools.rpc-artifact-import.v0",
            "source_package": record["source_package"],
            "destination_project": record["destination_project"],
            "operation_id": record["operation_id"],
            "import": {
                "program": accepted["program"],
                "verification": verification,
                "status": accepted["initialization"],
            },
            "reconciled": true,
        });
        self.finish_import(record_path, record, &result, &accepted)?;
        Ok(result)
    }

    fn finish_import(
        &self,
        path: &Path,
        record: &Value,
        result: &Value,
        accepted: &Value,
    ) -> Result<(), CliFailure> {
        let mut completed = record.clone();
        completed["phase"] = json!("completed");
        completed["accepted_root"] = accepted["accepted_state_root"].clone();
        completed["result"] = result.clone();
        let bytes = serde_json::to_vec(&completed).map_err(|error| {
            CliFailure::failed(format!("cannot encode RPC import completion: {error}"))
        })?;
        let temporary = path.with_extension(format!("{}.tmp", random_suffix()?));
        write_new(&temporary, &bytes)?;
        fs::rename(&temporary, path).map_err(|error| {
            let _ = fs::remove_file(&temporary);
            CliFailure::outcome_unknown(format!(
                "RPC import committed but its operation record could not be finalized: {error}"
            ))
        })?;
        Ok(())
    }

    pub(super) fn review(
        &self,
        before: &str,
        after: &str,
        cases: &Value,
    ) -> Result<Value, CliFailure> {
        let before = self.package_manifest(before)?;
        let after = self.package_manifest(after)?;
        let encoded = serde_json::to_vec(cases)
            .map_err(|error| CliFailure::invalid(format!("cannot encode review cases: {error}")))?;
        if encoded.len() > 65_536 {
            return Err(CliFailure::resource("RPC review cases exceed 64 KiB"));
        }
        let temporary = self
            .workspace
            .join(format!(".rpc-review-{}.json", random_suffix()?));
        write_new(&temporary, &encoded)?;
        let result = review::compute(&before, &after, &self.core, &temporary);
        let _ = fs::remove_file(&temporary);
        result
    }
}

fn write_new(path: &Path, bytes: &[u8]) -> Result<(), CliFailure> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)
        .map_err(|error| {
            CliFailure::invalid(format!("cannot create {}: {error}", path.display()))
        })?;
    if let Err(error) = file.write_all(bytes).and_then(|()| file.sync_all()) {
        let _ = fs::remove_file(path);
        return Err(CliFailure::failed(format!(
            "cannot write {}: {error}",
            path.display()
        )));
    }
    Ok(())
}

fn random_suffix() -> Result<String, CliFailure> {
    let mut bytes = [0_u8; 12];
    File::open("/dev/urandom")
        .and_then(|mut file| file.read_exact(&mut bytes))
        .map_err(|error| {
            CliFailure::failed(format!("cannot create review scratch identity: {error}"))
        })?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}
