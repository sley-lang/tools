//! Rebase bounded, authority-free native object bundles into fresh workspaces.
//! Core candidate validation remains authoritative.

use std::collections::BTreeMap;
use std::fs::File;
use std::io::Read;
use std::path::Path;

use serde::{Deserialize, Serialize};
use sley_id::{CandidateNonce, EntityId, ObjectId};
use sley_mutate::import_entity_object;
use sley_mutate::value::EntityBodyValue;
use sley_ssmc::{Immediate, IntegerWidth, Opcode, Terminator, TypeDefForm, TypeExpr, ValueRef};

use super::*;

const MAX_ARTIFACT_BYTES: u64 = 1_048_576;

#[derive(Clone, Copy, PartialEq, Eq)]
enum Profile {
    Release,
    Resource,
}

impl Profile {
    fn parse(program: &str) -> Result<Self, DynError> {
        match program {
            "release-readiness" => Ok(Self::Release),
            "resource-admission" => Ok(Self::Resource),
            _ => Err("unsupported native artifact program profile".into()),
        }
    }

    fn counts(self) -> [usize; 6] {
        match self {
            Self::Release => [1, 1, 4, 1, 11, 2],
            Self::Resource => [1, 1, 4, 1, 14, 1],
        }
    }

    fn fields(self) -> &'static [&'static str] {
        match self {
            Self::Release => &RESULT_FIELDS,
            Self::Resource => &resource::RESOURCE_FIELDS,
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Artifact {
    contract: String,
    program_id: String,
    core_commit: String,
    schema_epoch_id: String,
    source_state_root: String,
    source_function_id: String,
    source_threshold_id: Option<String>,
    source_threshold_object_id: Option<String>,
    threshold: Option<u32>,
    result_fields: Vec<ResultField>,
    objects: Vec<ArtifactObject>,
    test_evidence: TestEvidence,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ResultField {
    name: String,
    member_id: String,
    #[serde(rename = "type", skip_serializing_if = "Option::is_none")]
    value_type: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TestEvidence {
    kind: String,
    count: u32,
    native_core_test_admission: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ArtifactObject {
    entity_id: String,
    object_id: String,
    kind: u16,
    stored_hex: String,
}

fn load(path: &Path) -> Result<Artifact, DynError> {
    let metadata = std::fs::symlink_metadata(path)?;
    if !metadata.file_type().is_file() || metadata.len() > MAX_ARTIFACT_BYTES {
        return Err("artifact must be a regular file of at most 1 MiB".into());
    }
    let mut bytes = Vec::new();
    File::open(path)?
        .take(MAX_ARTIFACT_BYTES + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_ARTIFACT_BYTES {
        return Err("artifact exceeds 1 MiB".into());
    }
    let artifact: Artifact = serde_json::from_slice(&bytes)?;
    let profile = Profile::parse(&artifact.program_id)?;
    if artifact.contract != "sley-tools.native-program-artifact.v0"
        || artifact.core_commit != "c748dda18c1276687cc3b0d1ef9b7f257eb09b24"
        || artifact.objects.len() != profile.counts().iter().sum::<usize>()
    {
        return Err("unsupported native artifact contract or object count".into());
    }
    let _ = bytes32(&artifact.source_state_root)?;
    if artifact.result_fields.len() != profile.fields().len()
        || artifact
            .result_fields
            .iter()
            .enumerate()
            .any(|(index, field)| {
                field.name != profile.fields()[index]
                    || field.member_id != hex(result_member(index).as_bytes())
                    || field.value_type.as_deref()
                        != match profile {
                            Profile::Release => None,
                            Profile::Resource => Some(if index < 5 { "Bool" } else { "SInt64" }),
                        }
            })
    {
        return Err("artifact result field mapping differs from native profile".into());
    }
    if artifact.test_evidence.kind != "external_assertions_against_native_execution"
        || artifact.test_evidence.count < if profile == Profile::Release { 36 } else { 12 }
        || artifact.test_evidence.native_core_test_admission
            != "NOT_RUN: production native executor unavailable"
    {
        return Err("artifact test evidence is outside the supported profile".into());
    }
    match profile {
        Profile::Release
            if artifact.source_threshold_id.is_none()
                || artifact.source_threshold_object_id.is_none()
                || artifact.threshold.is_none() =>
        {
            return Err("Release Readiness artifact omits threshold identity".into());
        }
        Profile::Resource
            if artifact.source_threshold_id.is_some()
                || artifact.source_threshold_object_id.is_some()
                || artifact.threshold.is_some() =>
        {
            return Err(
                "Resource Admission artifact contains Release Readiness threshold metadata".into(),
            );
        }
        _ => {}
    }
    Ok(artifact)
}

fn decode_hex(value: &str) -> Result<Vec<u8>, DynError> {
    if !value.len().is_multiple_of(2) || value.len() > MAX_ARTIFACT_BYTES as usize * 2 {
        return Err("invalid artifact object hex length".into());
    }
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| Ok(u8::from_str_radix(std::str::from_utf8(pair)?, 16)?))
        .collect()
}

fn mapped(ids: &BTreeMap<EntityId, EntityId>, old: EntityId) -> Result<EntityId, DynError> {
    ids.get(&old)
        .copied()
        .ok_or_else(|| "artifact contains an external entity reference".into())
}

fn remap_type(
    value: &mut TypeExpr,
    ids: &BTreeMap<EntityId, EntityId>,
    profile: Profile,
) -> Result<(), DynError> {
    match value {
        TypeExpr::Bool => Ok(()),
        TypeExpr::UInt(width) if profile == Profile::Release && width.bits() == 32 => Ok(()),
        TypeExpr::SInt(width) if profile == Profile::Resource && width.bits() == 64 => Ok(()),
        TypeExpr::Named(named) if named.arguments.is_empty() => {
            named.definition = mapped(ids, named.definition)?;
            Ok(())
        }
        _ => Err("artifact uses a type outside its selected native profile".into()),
    }
}

fn remap_value(value: &mut ValueRef, ids: &BTreeMap<EntityId, EntityId>) -> Result<(), DynError> {
    match value {
        ValueRef::Parameter(entity) => *entity = mapped(ids, *entity)?,
        ValueRef::OperationResult(result) => result.operation = mapped(ids, result.operation)?,
    }
    Ok(())
}

fn remap_body(
    body: &mut EntityBodyValue,
    ids: &BTreeMap<EntityId, EntityId>,
    profile: Profile,
) -> Result<(), DynError> {
    match body {
        EntityBodyValue::TypeDef(definition) => {
            if !definition.type_parameters.is_empty()
                || !definition.invariants.as_slice().is_empty()
            {
                return Err("artifact type definition exceeds the portable profile".into());
            }
            let TypeDefForm::Record(fields) = &mut definition.form else {
                return Err("artifact result type is not a record".into());
            };
            if fields.len() != profile.fields().len() {
                return Err("artifact result record has wrong field count".into());
            }
            for (index, field) in fields.iter_mut().enumerate() {
                if field.member_id != result_member(index) {
                    return Err("artifact result record has unexpected member identity".into());
                }
                let expected_type = if profile == Profile::Resource && index >= 5 {
                    TypeExpr::SInt(IntegerWidth::from_bits(64))
                } else {
                    TypeExpr::Bool
                };
                if field.value_type != expected_type {
                    return Err("artifact result record field type differs from profile".into());
                }
                remap_type(&mut field.value_type, ids, profile)?;
            }
        }
        EntityBodyValue::Function(function) => {
            if !function.type_parameters.is_empty()
                || !function.effects.as_slice().is_empty()
                || !function.contracts.as_slice().is_empty()
            {
                return Err("artifact function exceeds the pure portable profile".into());
            }
            for parameter in &mut function.parameters {
                *parameter = mapped(ids, *parameter)?;
            }
            remap_type(&mut function.result_type, ids, profile)?;
            function.entry_block = mapped(ids, function.entry_block)?;
            for block in &mut function.blocks {
                *block = mapped(ids, *block)?;
            }
        }
        EntityBodyValue::Parameter(parameter) => {
            parameter.owner = mapped(ids, parameter.owner)?;
            remap_type(&mut parameter.value_type, ids, profile)?;
        }
        EntityBodyValue::Block(block) => {
            block.function = mapped(ids, block.function)?;
            for parameter in &mut block.parameters {
                *parameter = mapped(ids, *parameter)?;
            }
            for operation in &mut block.operations {
                *operation = mapped(ids, *operation)?;
            }
            let Terminator::Return(returned) = &mut block.terminator else {
                return Err("artifact block terminator exceeds the portable profile".into());
            };
            remap_value(&mut returned.value, ids)?;
        }
        EntityBodyValue::Operation(operation) => {
            operation.block = mapped(ids, operation.block)?;
            for operand in &mut operation.operands {
                remap_value(operand, ids)?;
            }
            for result_type in &mut operation.result_types {
                remap_type(result_type, ids, profile)?;
            }
            match &mut operation.immediate {
                Immediate::None => {}
                Immediate::Entity(entity) => *entity = mapped(ids, *entity)?,
                _ => return Err("artifact operation immediate exceeds the portable profile".into()),
            }
        }
        EntityBodyValue::Constant(constant) => match profile {
            Profile::Release => {
                if constant.value.value_type != count_type()
                    || !matches!(constant.value.data, ConstData::UInt(number) if number <= u128::from(u32::MAX))
                {
                    return Err("Release Readiness artifact constant is not UInt32".into());
                }
            }
            Profile::Resource => {
                if constant.value.value_type != TypeExpr::SInt(IntegerWidth::from_bits(64))
                    || constant.value.data != ConstData::SInt(0)
                {
                    return Err("Resource Admission artifact constant is not signed zero".into());
                }
            }
        },
        _ => return Err("artifact contains a non-program or authority entity".into()),
    }
    Ok(())
}

pub(super) fn import(args: &[String]) -> Result<(), DynError> {
    if args.len() != 7 {
        return Err(
            "portable-import requires ARTIFACT workspace principal root policy transaction schema"
                .into(),
        );
    }
    let artifact = load(Path::new(&args[0]))?;
    let profile = Profile::parse(&artifact.program_id)?;
    let context = Context::parse(&args[1..])?;
    if artifact.schema_epoch_id != hex(context.epoch.as_bytes()) {
        return Err("artifact schema epoch differs from destination core".into());
    }
    let source_function = EntityId::from_bytes(bytes32(&artifact.source_function_id)?);
    let source_threshold = artifact
        .source_threshold_id
        .as_deref()
        .map(|value| bytes32(value).map(EntityId::from_bytes))
        .transpose()?;
    let source_threshold_object = artifact
        .source_threshold_object_id
        .as_deref()
        .map(|value| bytes32(value).map(ObjectId::from_bytes))
        .transpose()?;
    let nonce = CandidateNonce::from_bytes(random32()?);
    let mut source = Vec::with_capacity(artifact.objects.len());
    let mut counts = [0_usize; 6];
    let mut previous = None;
    let mut source_memory_comparison = None;
    let mut require_memory_spare = false;
    for item in &artifact.objects {
        let old = EntityId::from_bytes(bytes32(&item.entity_id)?);
        if previous.is_some_and(|prior| old <= prior) {
            return Err("artifact objects are not in unique canonical entity order".into());
        }
        previous = Some(old);
        if !(4..=9).contains(&item.kind) {
            return Err("artifact contains an entity kind outside the program profile".into());
        }
        counts[usize::from(item.kind - 4)] += 1;
        let object = import_entity_object(context.epoch, &decode_hex(&item.stored_hex)?)?;
        if object.record().entity_id != old
            || object.object_id().as_bytes() != &bytes32(&item.object_id)?
            || object.record().body.kind_tag() != item.kind
            || object.record().label.is_some()
            || object.record().semantic_fingerprint.is_some()
        {
            return Err("artifact object identity, kind, or metadata differs from payload".into());
        }
        if Some(old) == source_threshold {
            if Some(object.object_id()) != source_threshold_object {
                return Err("artifact threshold object identity differs from header".into());
            }
            let EntityBodyValue::Constant(constant) = &object.record().body else {
                return Err("artifact threshold is not a constant".into());
            };
            if constant.value
                != count_value(artifact.threshold.ok_or("missing artifact threshold")?)
            {
                return Err("artifact threshold value differs from native object".into());
            }
        }
        if profile == Profile::Resource
            && let EntityBodyValue::Operation(operation) = &object.record().body
            && operation.ordinal == 6
        {
            if source_memory_comparison.is_some() {
                return Err(
                    "Resource Admission artifact has duplicate memory comparison ordinal".into(),
                );
            }
            require_memory_spare = if operation.opcode == Opcode::GreaterThan.tag() {
                false
            } else if operation.opcode == Opcode::GreaterEqual.tag() {
                true
            } else {
                return Err("Resource Admission memory comparison opcode is unsupported".into());
            };
            source_memory_comparison = Some(old);
        }
        source.push((old, item.kind, object.record().body.clone()));
    }
    if counts != profile.counts()
        || !source
            .iter()
            .any(|(entity, kind, _)| *entity == source_function && *kind == 5)
        || source_threshold.is_some_and(|threshold| {
            !source
                .iter()
                .any(|(entity, kind, _)| *entity == threshold && *kind == 9)
        })
        || (profile == Profile::Resource && source_memory_comparison.is_none())
    {
        return Err("artifact is missing its declared native graph".into());
    }
    let ids: BTreeMap<_, _> = source
        .iter()
        .enumerate()
        .map(|(index, (old, kind, _))| {
            (
                *old,
                EntityId::derive(context.workspace, nonce, u32::from(*kind), index as u64),
            )
        })
        .collect();
    let source_result_type = source
        .iter()
        .find(|(_, kind, _)| *kind == 4)
        .map(|(entity, _, _)| *entity)
        .ok_or("artifact result type not found")?;
    let mut rebased = Vec::with_capacity(source.len());
    let mut threshold_object = None;
    let mut comparison_object = None;
    for (old, kind, mut body) in source {
        remap_body(&mut body, &ids, profile)?;
        let new = mapped(&ids, old)?;
        if Some(old) == source_threshold {
            threshold_object = Some(build_entity_object(
                context.epoch,
                &EntityObjectRecord {
                    entity_id: new,
                    body: body.clone(),
                    label: None,
                    semantic_fingerprint: None,
                },
            )?);
        }
        if Some(old) == source_memory_comparison {
            comparison_object = Some(build_entity_object(
                context.epoch,
                &EntityObjectRecord {
                    entity_id: new,
                    body: body.clone(),
                    label: None,
                    semantic_fingerprint: None,
                },
            )?);
        }
        rebased.push((new, kind, body));
    }
    let (operations, preconditions) = created(rebased)?;
    let record = context.candidate(nonce, operations, preconditions)?;
    let candidate = build_candidate(&record)?;
    let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis() as u64;
    let mut result = serde_json::json!({
            "candidate_record_hex": hex(&encode_candidate_record(&record)?),
            "candidate_stored_hex": hex(&candidate.stored_bytes),
            "candidate_id": hex(candidate.candidate_id.as_bytes()),
            "function_id": hex(mapped(&ids, source_function)?.as_bytes()),
            "now_unix_millis": now,
            "program": artifact.program_id,
            "native_operation_count": profile.counts()[4],
            "result_type_id": hex(mapped(&ids, source_result_type)?.as_bytes()),
            "result_fields": artifact.result_fields,
            "source_state_root": artifact.source_state_root,
            "source_object_count": artifact.objects.len(),
    });
    if let Some(threshold) = source_threshold {
        result["threshold_id"] = serde_json::json!(hex(mapped(&ids, threshold)?.as_bytes()));
        result["threshold_object_id"] = serde_json::json!(hex(threshold_object
            .ok_or("threshold object not found")?
            .object_id()
            .as_bytes()));
        result["threshold"] = serde_json::json!(artifact.threshold.ok_or("threshold absent")?);
    }
    if let Some(comparison) = source_memory_comparison {
        result["memory_comparison_id"] =
            serde_json::json!(hex(mapped(&ids, comparison)?.as_bytes()));
        result["memory_comparison_object_id"] = serde_json::json!(hex(comparison_object
            .ok_or("memory comparison object not found")?
            .object_id()
            .as_bytes()));
        result["require_memory_spare"] = serde_json::json!(require_memory_spare);
    }
    println!("{result}");
    Ok(())
}
