//! Transfer a graph recipe only when its exact native objects agree with it.
//! The accepted source objects, not the readable recipe, are the import authority.

use std::collections::{BTreeMap, BTreeSet};
use std::fs::File;
use std::io::Read;
use std::path::Path;

use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sley_id::EntityId;
use sley_mutate::value::EntityBodyValue;
use sley_mutate::{
    MutationPayload, build_candidate, decode_candidate_record, encode_candidate_record,
    import_entity_object,
};
use sley_ssmc::{Immediate, Terminator, TypeExpr, ValueRef};

use crate::{Context, DynError, bytes32, graph, hex};

pub(crate) const MAX_GRAPH_ARTIFACT_BYTES: u64 = 8_388_608;
pub(crate) const CONTRACT: &str = "sley-tools.native-graph-artifact.v1";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct GraphArtifact {
    contract: String,
    program_id: String,
    core_commit: String,
    schema_epoch_id: String,
    source_state_root: String,
    source_function_id: String,
    source_result_type_id: Option<String>,
    source_constant_ids: BTreeMap<String, String>,
    result_fields: Vec<Value>,
    recipe_json: String,
    recipe_sha256: String,
    cases_json: String,
    cases_sha256: String,
    objects: Vec<GraphObject>,
    test_evidence: TestEvidence,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct GraphObject {
    entity_id: String,
    object_id: String,
    kind: u16,
    stored_hex: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TestEvidence {
    kind: String,
    count: usize,
    native_core_test_admission: String,
}

fn decode_hex(value: &str) -> Result<Vec<u8>, DynError> {
    if !value.len().is_multiple_of(2) || value.len() > MAX_GRAPH_ARTIFACT_BYTES as usize * 2 {
        return Err("invalid graph object hex length".into());
    }
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| Ok(u8::from_str_radix(std::str::from_utf8(pair)?, 16)?))
        .collect()
}

fn load(path: &Path) -> Result<GraphArtifact, DynError> {
    let metadata = std::fs::symlink_metadata(path)?;
    if !metadata.file_type().is_file() || metadata.len() > MAX_GRAPH_ARTIFACT_BYTES {
        return Err("graph artifact must be a regular file of at most 8 MiB".into());
    }
    let mut bytes = Vec::new();
    File::open(path)?
        .take(MAX_GRAPH_ARTIFACT_BYTES + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_GRAPH_ARTIFACT_BYTES {
        return Err("graph artifact exceeds 8 MiB".into());
    }
    let artifact: GraphArtifact = serde_json::from_slice(&bytes)?;
    if artifact.contract != CONTRACT
        || artifact.core_commit != crate::core_pin::TESTED_CORE_COMMIT
        || artifact.recipe_json.len() > 65_536
        || artifact.cases_json.len() > 1_048_576
        || artifact.objects.is_empty()
        || artifact.objects.len() > 226
        || artifact.test_evidence.kind != "external_assertions_against_native_execution"
        || artifact.test_evidence.count == 0
        || artifact.test_evidence.count > 256
        || artifact.test_evidence.native_core_test_admission
            != "NOT_RUN: production native executor unavailable"
    {
        return Err("unsupported native graph artifact profile".into());
    }
    let _ = bytes32(&artifact.source_state_root)?;
    if hex(&Sha256::digest(artifact.recipe_json.as_bytes())) != artifact.recipe_sha256
        || hex(&Sha256::digest(artifact.cases_json.as_bytes())) != artifact.cases_sha256
    {
        return Err("graph artifact recipe or case digest differs".into());
    }
    let recipe: Value = serde_json::from_str(&artifact.recipe_json)?;
    let cases: Value = serde_json::from_str(&artifact.cases_json)?;
    if recipe["contract"] != "sley-tools.graph-authoring.v0"
        || recipe["program_id"] != artifact.program_id
        || cases["contract"] != "sley-tools.graph-cases.v0"
        || cases["cases"].as_array().map(Vec::len) != Some(artifact.test_evidence.count)
    {
        return Err("graph artifact recipe or cases differ from header".into());
    }
    Ok(artifact)
}

fn mapped(ids: &BTreeMap<EntityId, EntityId>, old: EntityId) -> Result<EntityId, DynError> {
    ids.get(&old)
        .copied()
        .ok_or_else(|| "graph artifact contains an external entity reference".into())
}

fn remap_type(value: &mut TypeExpr, ids: &BTreeMap<EntityId, EntityId>) -> Result<(), DynError> {
    match value {
        TypeExpr::Bool | TypeExpr::UInt(_) | TypeExpr::SInt(_) | TypeExpr::BuiltinFailure(_) => {
            Ok(())
        }
        TypeExpr::Named(named) if named.arguments.is_empty() => {
            named.definition = mapped(ids, named.definition)?;
            Ok(())
        }
        TypeExpr::Result { ok, error } => {
            remap_type(ok, ids)?;
            remap_type(error, ids)
        }
        _ => Err("graph artifact uses a type outside graph authoring v0".into()),
    }
}

fn remap_value(value: &mut ValueRef, ids: &BTreeMap<EntityId, EntityId>) -> Result<(), DynError> {
    match value {
        ValueRef::Parameter(entity) => *entity = mapped(ids, *entity)?,
        ValueRef::OperationResult(result) => result.operation = mapped(ids, result.operation)?,
    }
    Ok(())
}

fn remap_edge(
    edge: &mut sley_ssmc::TargetEdge,
    ids: &BTreeMap<EntityId, EntityId>,
) -> Result<(), DynError> {
    if !edge.arguments.is_empty() {
        return Err("graph artifact has unsupported block arguments".into());
    }
    edge.target = mapped(ids, edge.target)?;
    Ok(())
}

fn remap_body(
    body: &mut EntityBodyValue,
    ids: &BTreeMap<EntityId, EntityId>,
) -> Result<(), DynError> {
    match body {
        EntityBodyValue::TypeDef(definition) => {
            let sley_ssmc::TypeDefForm::Record(fields) = &mut definition.form else {
                return Err("graph artifact type definition is not a record".into());
            };
            for field in fields {
                remap_type(&mut field.value_type, ids)?;
            }
        }
        EntityBodyValue::Function(function) => {
            for parameter in &mut function.parameters {
                *parameter = mapped(ids, *parameter)?;
            }
            remap_type(&mut function.result_type, ids)?;
            function.entry_block = mapped(ids, function.entry_block)?;
            for block in &mut function.blocks {
                *block = mapped(ids, *block)?;
            }
        }
        EntityBodyValue::Parameter(parameter) => {
            parameter.owner = mapped(ids, parameter.owner)?;
            remap_type(&mut parameter.value_type, ids)?;
        }
        EntityBodyValue::Block(block) => {
            block.function = mapped(ids, block.function)?;
            for parameter in &mut block.parameters {
                *parameter = mapped(ids, *parameter)?;
            }
            for operation in &mut block.operations {
                *operation = mapped(ids, *operation)?;
            }
            match &mut block.terminator {
                Terminator::Return(returned) => remap_value(&mut returned.value, ids)?,
                Terminator::Branch(branch) => remap_edge(&mut branch.edge, ids)?,
                Terminator::CondBranch(branch) => {
                    remap_value(&mut branch.condition, ids)?;
                    remap_edge(&mut branch.if_true, ids)?;
                    remap_edge(&mut branch.if_false, ids)?;
                }
                _ => return Err("graph artifact has an unsupported terminator".into()),
            }
        }
        EntityBodyValue::Operation(operation) => {
            operation.block = mapped(ids, operation.block)?;
            for operand in &mut operation.operands {
                remap_value(operand, ids)?;
            }
            for result in &mut operation.result_types {
                remap_type(result, ids)?;
            }
            match &mut operation.immediate {
                Immediate::None => {}
                Immediate::Entity(entity) => *entity = mapped(ids, *entity)?,
                _ => return Err("graph artifact has an unsupported operation immediate".into()),
            }
        }
        EntityBodyValue::Constant(constant) => remap_type(&mut constant.value.value_type, ids)?,
        _ => return Err("graph artifact contains an authority or nonprogram entity".into()),
    }
    Ok(())
}

fn bind(
    ids: &mut BTreeMap<EntityId, EntityId>,
    used: &mut BTreeSet<EntityId>,
    generated: EntityId,
    source: EntityId,
) -> Result<(), DynError> {
    if ids.insert(generated, source).is_some() || !used.insert(source) {
        return Err("graph artifact contains duplicate graph identities".into());
    }
    Ok(())
}

pub(crate) fn import(args: &[String]) -> Result<(), DynError> {
    if args.len() != 7 {
        return Err(
            "portable-import-graph requires ARTIFACT and six live context identities".into(),
        );
    }
    let artifact = load(Path::new(&args[0]))?;
    let context = Context::parse(&args[1..])?;
    if artifact.schema_epoch_id != hex(context.epoch.as_bytes()) {
        return Err("graph artifact schema epoch differs from destination core".into());
    }
    let mut source = BTreeMap::new();
    let mut previous = None;
    for item in &artifact.objects {
        let id = EntityId::from_bytes(bytes32(&item.entity_id)?);
        if previous.is_some_and(|prior| id <= prior) || !(4..=9).contains(&item.kind) {
            return Err("graph artifact objects are not in unique canonical program order".into());
        }
        previous = Some(id);
        let object = import_entity_object(context.epoch, &decode_hex(&item.stored_hex)?)?;
        if object.record().entity_id != id
            || object.object_id().as_bytes() != &bytes32(&item.object_id)?
            || object.record().body.kind_tag() != item.kind
            || object.record().label.is_some()
            || object.record().semantic_fingerprint.is_some()
        {
            return Err("graph artifact object identity, kind, or metadata differs".into());
        }
        source.insert(id, object.record().body.clone());
    }
    let constructed = graph::construct_from_bytes(artifact.recipe_json.as_bytes(), &context)?;
    if constructed["program_id"] != artifact.program_id
        || constructed["result_fields"]
            .as_array()
            .cloned()
            .unwrap_or_default()
            != artifact.result_fields
    {
        return Err("graph artifact declaration differs from bound recipe".into());
    }
    let mut candidate = decode_candidate_record(&decode_hex(
        constructed["candidate_record_hex"]
            .as_str()
            .ok_or("graph builder omitted candidate record")?,
    )?)?;
    let mut generated = BTreeMap::new();
    for operation in &candidate.operations {
        let MutationPayload::CreateEntity(body) = &operation.payload else {
            return Err("graph builder produced a noncreate mutation".into());
        };
        if generated
            .insert(operation.target_entity, body.clone())
            .is_some()
        {
            return Err("graph builder produced duplicate entities".into());
        }
    }
    if generated.len() != source.len() {
        return Err("graph artifact inventory differs from bound recipe".into());
    }
    let generated_function = EntityId::from_bytes(bytes32(
        constructed["function_id"]
            .as_str()
            .ok_or("graph builder omitted function")?,
    )?);
    let source_function = EntityId::from_bytes(bytes32(&artifact.source_function_id)?);
    let (Some(EntityBodyValue::Function(gfn)), Some(EntityBodyValue::Function(sfn))) = (
        generated.get(&generated_function),
        source.get(&source_function),
    ) else {
        return Err("graph artifact or generated graph lacks its declared function".into());
    };
    if gfn.parameters.len() != sfn.parameters.len() || gfn.blocks.len() != sfn.blocks.len() {
        return Err("graph artifact function shape differs from recipe".into());
    }
    let mut ids = BTreeMap::new();
    let mut used = BTreeSet::new();
    bind(&mut ids, &mut used, generated_function, source_function)?;
    match (
        constructed["result_type_id"].as_str(),
        artifact.source_result_type_id.as_deref(),
    ) {
        (Some(generated_id), Some(source_id)) => bind(
            &mut ids,
            &mut used,
            EntityId::from_bytes(bytes32(generated_id)?),
            EntityId::from_bytes(bytes32(source_id)?),
        )?,
        (None, None) => {}
        _ => return Err("graph artifact result type differs from recipe".into()),
    }
    for (generated_id, source_id) in gfn.parameters.iter().zip(&sfn.parameters) {
        bind(&mut ids, &mut used, *generated_id, *source_id)?;
    }
    for (generated_id, source_id) in gfn.blocks.iter().zip(&sfn.blocks) {
        bind(&mut ids, &mut used, *generated_id, *source_id)?;
        let (Some(EntityBodyValue::Block(gblock)), Some(EntityBodyValue::Block(sblock))) =
            (generated.get(generated_id), source.get(source_id))
        else {
            return Err("graph artifact block mapping is invalid".into());
        };
        if gblock.operations.len() != sblock.operations.len() {
            return Err("graph artifact operation count differs from recipe".into());
        }
        for (generated_op, source_op) in gblock.operations.iter().zip(&sblock.operations) {
            bind(&mut ids, &mut used, *generated_op, *source_op)?;
        }
    }
    let generated_constants = constructed["constant_ids"]
        .as_object()
        .ok_or("graph builder omitted constant map")?;
    if generated_constants.len() != artifact.source_constant_ids.len() {
        return Err("graph artifact constant map differs from recipe".into());
    }
    let mut destination_constants = serde_json::Map::new();
    for (name, generated_id) in generated_constants {
        let source_id = artifact
            .source_constant_ids
            .get(name)
            .ok_or("graph artifact omits a named constant")?;
        let generated_id = EntityId::from_bytes(bytes32(
            generated_id.as_str().ok_or("invalid generated constant")?,
        )?);
        bind(
            &mut ids,
            &mut used,
            generated_id,
            EntityId::from_bytes(bytes32(source_id)?),
        )?;
        destination_constants.insert(name.clone(), json!(hex(generated_id.as_bytes())));
    }
    if ids.len() != source.len() || used.len() != source.len() {
        return Err("graph artifact has entities outside the bound recipe".into());
    }
    for (generated_id, body) in &generated {
        let mut expected = body.clone();
        remap_body(&mut expected, &ids)?;
        if source.get(&mapped(&ids, *generated_id)?) != Some(&expected) {
            return Err("native graph object differs from its bound recipe".into());
        }
    }
    let inverse: BTreeMap<_, _> = ids
        .iter()
        .map(|(generated, source)| (*source, *generated))
        .collect();
    for operation in &mut candidate.operations {
        let source_id = mapped(&ids, operation.target_entity)?;
        let mut body = source
            .get(&source_id)
            .ok_or("source graph entity missing")?
            .clone();
        remap_body(&mut body, &inverse)?;
        operation.payload = MutationPayload::CreateEntity(body);
    }
    let built = build_candidate(&candidate)?;
    let mut result = constructed;
    result["contract"] = json!("sley-tools.portable-import-graph.v1");
    result["candidate_record_hex"] = json!(hex(&encode_candidate_record(&candidate)?));
    result["candidate_stored_hex"] = json!(hex(&built.stored_bytes));
    result["candidate_id"] = json!(hex(built.candidate_id.as_bytes()));
    result["source_state_root"] = json!(artifact.source_state_root);
    result["source_object_count"] = json!(source.len());
    result["constant_ids"] = json!(destination_constants);
    println!("{result}");
    Ok(())
}
