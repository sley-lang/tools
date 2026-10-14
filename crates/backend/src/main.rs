//! First native program constructor. The host builds SSMC1 objects; Sley owns
//! validation, accepted state, and all Release Readiness computation.

use std::fs::File;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use sha2::{Digest, Sha256};
use sley_check::TypeEnvironment;
use sley_id::{
    CandidateNonce, EntityId, ObjectId, PolicyRootId, PrincipalId, SchemaEpochId, StateRoot,
    TransactionId, WorkspaceId,
};
use sley_mutate::value::{
    BlockBody, ConstantBody, EntityBodyValue, EntityIdSet, FunctionBody, OperationBody,
    ParameterBody, TypeDefBody,
};
use sley_mutate::{
    BoundPrecondition, CandidateExpiry, CandidateRecord, EntityObjectRecord, ExactEntityVersion,
    ExpectedIdentityAbsent, MutationClass, MutationOperation, MutationPayload, PreconditionPayload,
    PreimageRequirement, build_candidate, build_entity_object, encode_candidate_record,
    full_validation_profile_id,
};
use sley_policy::build_capability_summary_projection;
use sley_repo::CompleteRootRequest;
use sley_ssmc::{
    ConstData, ConstValue, Immediate, IntegerWidth, MemberId, NamedType, Opcode,
    OperationResultRef, ParameterRole, Reachability, RecordField, ReturnTerminator, Terminator,
    TypeDefForm, TypeExpr, ValueRef, Visibility,
};
use sley_tests::NativeTestReportV1;
use sley_txn::TransactionRepository;
use sley_vm::{
    CacheProfile, ExecutionLimits, ExecutionRequest, ExecutionTermination, LoweringInput,
    execute_function,
};

#[path = "core_pin.rs"]
mod core_pin;
mod graph;
mod graph_portable;
mod portable;
mod resource;

type DynError = Box<dyn std::error::Error>;

fn random32() -> Result<[u8; 32], DynError> {
    let mut bytes = [0; 32];
    File::open("/dev/urandom")?.read_exact(&mut bytes)?;
    Ok(bytes)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn bytes32(value: &str) -> Result<[u8; 32], DynError> {
    if value.len() != 64 {
        return Err("expected 32-byte hex identity".into());
    }
    let mut result = [0; 32];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        result[index] = u8::from_str_radix(std::str::from_utf8(pair)?, 16)?;
    }
    Ok(result)
}

fn count_type() -> TypeExpr {
    TypeExpr::UInt(IntegerWidth::from_bits(32))
}

const RESULT_FIELDS: [&str; 5] = [
    "allow",
    "test_run_incomplete",
    "tests_failed",
    "artifact_unverified",
    "blocking_findings_exceeded",
];

fn result_member(index: usize) -> MemberId {
    MemberId::from_bytes([u8::try_from(index + 1).expect("five result fields"); 32])
}

fn result_type(definition: EntityId) -> TypeExpr {
    TypeExpr::Named(NamedType {
        definition,
        arguments: vec![],
    })
}

fn count_value(count: u32) -> ConstValue {
    ConstValue {
        value_type: count_type(),
        data: ConstData::UInt(u128::from(count)),
    }
}

fn flag_value(flag: bool) -> ConstValue {
    ConstValue {
        value_type: TypeExpr::Bool,
        data: ConstData::Bool(flag),
    }
}

struct Context {
    workspace: WorkspaceId,
    principal: PrincipalId,
    root: StateRoot,
    policy: PolicyRootId,
    transaction: TransactionId,
    epoch: SchemaEpochId,
}

impl Context {
    fn parse(args: &[String]) -> Result<Self, DynError> {
        if args.len() != 6 {
            return Err("expected workspace principal root policy transaction schema".into());
        }
        Ok(Self {
            workspace: WorkspaceId::from_bytes(bytes32(&args[0])?),
            principal: PrincipalId::from_bytes(bytes32(&args[1])?),
            root: StateRoot::from_bytes(bytes32(&args[2])?),
            policy: PolicyRootId::from_bytes(bytes32(&args[3])?),
            transaction: TransactionId::from_bytes(bytes32(&args[4])?),
            epoch: SchemaEpochId::from_bytes(bytes32(&args[5])?),
        })
    }

    fn candidate(
        &self,
        nonce: CandidateNonce,
        operations: Vec<MutationOperation>,
        preconditions: Vec<BoundPrecondition>,
    ) -> Result<CandidateRecord, DynError> {
        let projection = build_capability_summary_projection(
            self.principal,
            self.workspace,
            self.policy,
            self.root,
            &[],
        )?;
        let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis() as u64;
        Ok(CandidateRecord {
            format_version: 1,
            workspace_id: self.workspace,
            base_transaction_id: self.transaction,
            base_root: self.root,
            schema_epoch_id: self.epoch,
            policy_root_id: self.policy,
            principal_id: self.principal,
            capability_summary_digest: projection.digest(),
            operations,
            preconditions,
            validation_profile_id: full_validation_profile_id()?,
            candidate_nonce: nonce,
            expiry: CandidateExpiry::unix_millis(now + 600_000),
        })
    }
}

fn created(
    entities: Vec<(EntityId, u16, EntityBodyValue)>,
) -> Result<(Vec<MutationOperation>, Vec<BoundPrecondition>), DynError> {
    let mut operations = Vec::new();
    let mut preconditions = Vec::new();
    for (index, (entity, kind, body)) in entities.into_iter().enumerate() {
        let ordinal = u32::try_from(index)?;
        operations.push(MutationOperation {
            ordinal,
            class: MutationClass::CreateEntity,
            target_kind: kind,
            target_entity: entity,
            field_tag: None,
            payload: MutationPayload::CreateEntity(body),
            precondition_ordinal: ordinal,
        });
        preconditions.push(BoundPrecondition {
            operation_ordinal: ordinal,
            requirement: PreimageRequirement::ExpectedIdentityAbsent,
            payload: PreconditionPayload::ExpectedIdentityAbsent(ExpectedIdentityAbsent {
                entity_id: entity,
            }),
        });
    }
    Ok((operations, preconditions))
}

fn result_ref(operation: EntityId) -> ValueRef {
    ValueRef::OperationResult(OperationResultRef {
        operation,
        result_index: 0,
    })
}

fn construct(args: &[String]) -> Result<(), DynError> {
    let context = Context::parse(args)?;
    let nonce = CandidateNonce::from_bytes(random32()?);
    let id = |kind, ordinal| EntityId::derive(context.workspace, nonce, kind, ordinal);
    let function = id(5, 0);
    let parameters: Vec<_> = (0..4).map(|i| id(6, i + 1)).collect();
    let block = id(7, 5);
    let zero = id(9, 6);
    let threshold = id(9, 7);
    let operation_ids: Vec<_> = (0..11).map(|i| id(8, i + 8)).collect();
    let result_definition = id(4, 19);
    let value_type = result_type(result_definition);
    let empty = EntityIdSet::from_unsorted(vec![])?;
    let mut entities = vec![(
        function,
        5,
        EntityBodyValue::Function(FunctionBody {
            type_parameters: vec![],
            parameters: parameters.clone(),
            result_type: value_type.clone(),
            effects: empty.clone(),
            entry_block: block,
            blocks: vec![block],
            contracts: empty,
            visibility: Visibility::Private,
        }),
    )];
    let parameter_types = [TypeExpr::Bool, count_type(), TypeExpr::Bool, count_type()];
    for (index, parameter) in parameters.iter().enumerate() {
        entities.push((
            *parameter,
            6,
            EntityBodyValue::Parameter(ParameterBody {
                owner: function,
                role: ParameterRole::Function,
                ordinal: u32::try_from(index)?,
                value_type: parameter_types[index].clone(),
            }),
        ));
    }
    entities.push((
        block,
        7,
        EntityBodyValue::Block(BlockBody {
            function,
            parameters: vec![],
            operations: operation_ids.clone(),
            terminator: Terminator::Return(ReturnTerminator {
                value: result_ref(operation_ids[10]),
            }),
            reachability: Reachability::Required,
        }),
    ));
    for (entity, value) in [(zero, 0), (threshold, 0)] {
        entities.push((
            entity,
            9,
            EntityBodyValue::Constant(ConstantBody {
                value: count_value(value),
            }),
        ));
    }
    let p = |index: usize| ValueRef::Parameter(parameters[index]);
    let r = |index: usize| result_ref(operation_ids[index]);
    let definitions = vec![
        (
            Opcode::ConstantRef,
            vec![],
            count_type(),
            Immediate::Entity(zero),
        ),
        (
            Opcode::ConstantRef,
            vec![],
            count_type(),
            Immediate::Entity(threshold),
        ),
        (Opcode::BoolNot, vec![p(0)], TypeExpr::Bool, Immediate::None),
        (
            Opcode::GreaterThan,
            vec![p(1), r(0)],
            TypeExpr::Bool,
            Immediate::None,
        ),
        (Opcode::BoolNot, vec![p(2)], TypeExpr::Bool, Immediate::None),
        (
            Opcode::GreaterThan,
            vec![p(3), r(1)],
            TypeExpr::Bool,
            Immediate::None,
        ),
        (
            Opcode::BoolOr,
            vec![r(2), r(3)],
            TypeExpr::Bool,
            Immediate::None,
        ),
        (
            Opcode::BoolOr,
            vec![r(4), r(5)],
            TypeExpr::Bool,
            Immediate::None,
        ),
        (
            Opcode::BoolOr,
            vec![r(6), r(7)],
            TypeExpr::Bool,
            Immediate::None,
        ),
        (Opcode::BoolNot, vec![r(8)], TypeExpr::Bool, Immediate::None),
        (
            Opcode::RecordNew,
            vec![r(9), r(2), r(3), r(4), r(5)],
            value_type,
            Immediate::Entity(result_definition),
        ),
    ];
    for (index, (opcode, operands, result_type, immediate)) in definitions.into_iter().enumerate() {
        entities.push((
            operation_ids[index],
            8,
            EntityBodyValue::Operation(OperationBody {
                block,
                ordinal: u32::try_from(index)?,
                opcode: opcode.tag(),
                operands,
                result_types: vec![result_type],
                immediate,
            }),
        ));
    }
    entities.push((
        result_definition,
        4,
        EntityBodyValue::TypeDef(TypeDefBody {
            type_parameters: vec![],
            form: TypeDefForm::Record(
                (0..RESULT_FIELDS.len())
                    .map(|index| RecordField {
                        member_id: result_member(index),
                        value_type: TypeExpr::Bool,
                        visibility: Visibility::Private,
                    })
                    .collect(),
            ),
            invariants: EntityIdSet::from_unsorted(vec![])?,
            visibility: Visibility::Private,
        }),
    ));
    let threshold_object = build_entity_object(
        context.epoch,
        &EntityObjectRecord {
            entity_id: threshold,
            body: EntityBodyValue::Constant(ConstantBody {
                value: count_value(0),
            }),
            label: None,
            semantic_fingerprint: None,
        },
    )?;
    let (operations, preconditions) = created(entities)?;
    let record = context.candidate(nonce, operations, preconditions)?;
    let candidate = build_candidate(&record)?;
    let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis() as u64;
    println!(
        "{}",
        serde_json::json!({
            "candidate_record_hex": hex(&encode_candidate_record(&record)?),
            "candidate_stored_hex": hex(&candidate.stored_bytes),
            "candidate_id": hex(candidate.candidate_id.as_bytes()),
            "function_id": hex(function.as_bytes()),
            "threshold_id": hex(threshold.as_bytes()),
            "threshold_object_id": hex(threshold_object.object_id().as_bytes()),
            "now_unix_millis": now,
            "program": "release-readiness",
            "native_operation_count": 11,
            "result_type_id": hex(result_definition.as_bytes()),
            "result_fields": RESULT_FIELDS.iter().enumerate().map(|(index, name)| serde_json::json!({"name": name, "member_id": hex(result_member(index).as_bytes())})).collect::<Vec<_>>(),
            "count_max": u32::MAX,
        })
    );
    Ok(())
}

fn edit(args: &[String]) -> Result<(), DynError> {
    if args.len() != 9 {
        return Err("edit requires context threshold_id threshold_object new_threshold".into());
    }
    let context = Context::parse(&args[..6])?;
    let threshold = EntityId::from_bytes(bytes32(&args[6])?);
    let old_object = ObjectId::from_bytes(bytes32(&args[7])?);
    let value: u32 = args[8].parse()?;
    let nonce = CandidateNonce::from_bytes(random32()?);
    let body = EntityBodyValue::Constant(ConstantBody {
        value: count_value(value),
    });
    let record = context.candidate(
        nonce,
        vec![MutationOperation {
            ordinal: 0,
            class: MutationClass::ReplaceEntityVersion,
            target_kind: 9,
            target_entity: threshold,
            field_tag: None,
            payload: MutationPayload::ReplaceEntityVersion(body.clone()),
            precondition_ordinal: 0,
        }],
        vec![BoundPrecondition {
            operation_ordinal: 0,
            requirement: PreimageRequirement::ExactEntityVersion,
            payload: PreconditionPayload::ExactEntityVersion(ExactEntityVersion {
                entity_id: threshold,
                object_id: old_object,
            }),
        }],
    )?;
    let candidate = build_candidate(&record)?;
    let new_object = build_entity_object(
        context.epoch,
        &EntityObjectRecord {
            entity_id: threshold,
            body,
            label: None,
            semantic_fingerprint: None,
        },
    )?;
    let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis() as u64;
    println!(
        "{}",
        serde_json::json!({
            "candidate_record_hex": hex(&encode_candidate_record(&record)?),
            "candidate_stored_hex": hex(&candidate.stored_bytes),
            "candidate_id": hex(candidate.candidate_id.as_bytes()),
            "threshold_object_id": hex(new_object.object_id().as_bytes()),
            "now_unix_millis": now,
            "new_threshold": value,
        })
    );
    Ok(())
}

fn parse_flag(value: &str) -> Result<bool, DynError> {
    match value {
        "true" => Ok(true),
        "false" => Ok(false),
        _ => Err("expected true or false".into()),
    }
}

fn native_result(args: &[String]) -> Result<serde_json::Value, DynError> {
    if args.len() != 7 {
        return Err(
            "run requires repository root function completed failed verified blocking".into(),
        );
    }
    let repository = TransactionRepository::new(Path::new(&args[0]));
    let expected_root = StateRoot::from_bytes(bytes32(&args[1])?);
    let function = EntityId::from_bytes(bytes32(&args[2])?);
    let revision = repository.accepted_head()?.into_verified_revision();
    let complete = CompleteRootRequest::extract(&revision)?;
    if complete.root() != expected_root {
        return Err("accepted root differs from requested root".into());
    }
    let entities = complete.entities();
    let types = TypeEnvironment::new(entities.type_definitions.clone())?;
    let graph = entities
        .functions
        .iter()
        .find(|graph| graph.entity_id == function)
        .ok_or("function is absent from accepted root")?;
    let input = LoweringInput {
        types: &types,
        function: graph,
        parameters: &entities.parameters,
        blocks: &entities.blocks,
        operations: &entities.operations,
        schema_epoch: complete.schema_epoch_id(),
        state_root: complete.root(),
        profile: CacheProfile::EXTENDED_V1,
        constants: &entities.constants,
        globals: &entities.globals,
        functions: &entities.functions,
        contracts: &entities.contracts,
        adapters: &[],
    };
    let request = ExecutionRequest {
        inputs: vec![
            flag_value(parse_flag(&args[3])?),
            count_value(args[4].parse()?),
            flag_value(parse_flag(&args[5])?),
            count_value(args[6].parse()?),
        ],
        limits: ExecutionLimits {
            max_instructions: 1_000,
            max_fuel: 1_000,
            max_value_units: 10_000,
            max_output_units: 1_000,
            cancel_at_fuel: None,
        },
    };
    let outcome = execute_function(input, request)?;
    let ExecutionTermination::Success(output) = outcome.termination else {
        return Err(format!("native execution ended with {:?}", outcome.termination).into());
    };
    let TypeExpr::Named(named_result) = &graph.result_type else {
        return Err("Release Readiness function does not declare a named result".into());
    };
    let result_definition = entities
        .type_definitions
        .iter()
        .find(|definition| definition.entity_id == named_result.definition)
        .ok_or("Release Readiness result definition absent from accepted root")?;
    if !matches!(&result_definition.form, TypeDefForm::Record(fields) if fields.len() == RESULT_FIELDS.len()
        && fields.iter().enumerate().all(|(index, field)| field.member_id == result_member(index) && field.value_type == TypeExpr::Bool))
    {
        return Err("Release Readiness result schema differs from manifest".into());
    }
    if output.value_type != result_type(result_definition.entity_id) {
        return Err("native output type differs from Release Readiness contract".into());
    }
    let ConstData::Record(record) = &output.data else {
        return Err("native output is not a record".into());
    };
    if record.definition != result_definition.entity_id {
        return Err("native output record has the wrong definition".into());
    }
    let flags = record
        .fields
        .iter()
        .enumerate()
        .map(|(index, field)| {
            match (
                field.member_id == result_member(index),
                &field.value.value_type,
                &field.value.data,
            ) {
                (true, TypeExpr::Bool, ConstData::Bool(flag)) => Ok(*flag),
                _ => Err("native output field is not Bool"),
            }
        })
        .collect::<Result<Vec<_>, _>>()?;
    if flags.len() != 5 {
        return Err("native output field count differs from contract".into());
    }
    let fields: serde_json::Map<String, serde_json::Value> = RESULT_FIELDS
        .iter()
        .zip(flags.iter())
        .map(|(name, flag)| ((*name).to_owned(), serde_json::Value::Bool(*flag)))
        .collect();
    let reason_codes: Vec<_> = RESULT_FIELDS
        .iter()
        .skip(1)
        .zip(flags.iter().skip(1))
        .filter_map(|(name, active)| active.then_some(*name))
        .collect();
    Ok(serde_json::json!({
        "root": hex(complete.root().as_bytes()),
        "function_id": hex(function.as_bytes()),
        "profile": "extended_v1",
        "flags": flags,
        "result": fields,
        "reason_codes": reason_codes,
        "value_hash": hex(sley_ssmc::fingerprint::hash_validated_value(complete.schema_epoch_id(), &output)?.as_bytes()),
        "observation_id": hex(outcome.observation_id.as_bytes()),
        "instruction_count": outcome.instruction_count,
    }))
}

fn run_native(args: &[String]) -> Result<(), DynError> {
    println!("{}", native_result(args)?);
    Ok(())
}

fn serve_native(args: &[String]) -> Result<(), DynError> {
    if args.len() != 3 {
        return Err("serve requires repository root function".into());
    }
    let input = std::io::stdin();
    let mut reader = BufReader::new(input.lock());
    let output = std::io::stdout();
    let mut writer = output.lock();
    loop {
        let mut line = Vec::new();
        let count = (&mut reader).take(4_097).read_until(b'\n', &mut line)?;
        if count == 0 {
            return Ok(());
        }
        if count > 4_096 || !line.ends_with(b"\n") {
            return Err("native worker request exceeds 4 KiB or lacks newline".into());
        }
        let request: serde_json::Value = serde_json::from_slice(&line)?;
        let object = request
            .as_object()
            .ok_or("native worker request is not an object")?;
        if object.len() != 5 {
            return Err("native worker request has unexpected fields".into());
        }
        let id = request["id"]
            .as_u64()
            .ok_or("native worker request has no ID")?;
        let completed = request["completed"]
            .as_bool()
            .ok_or("completed is not Boolean")?;
        let failed = request["failed"]
            .as_str()
            .ok_or("failed is not decimal text")?;
        let verified = request["verified"]
            .as_bool()
            .ok_or("verified is not Boolean")?;
        let blocking = request["blocking"]
            .as_str()
            .ok_or("blocking is not decimal text")?;
        for value in [failed, blocking] {
            if value.parse::<u32>()?.to_string() != value {
                return Err("native worker count is not canonical UInt32 decimal".into());
            }
        }
        let run_args = vec![
            args[0].clone(),
            args[1].clone(),
            args[2].clone(),
            completed.to_string(),
            failed.to_owned(),
            verified.to_string(),
            blocking.to_owned(),
        ];
        let response = match native_result(&run_args) {
            Ok(value) => serde_json::json!({"id": id, "ok": true, "result": value}),
            Err(error) => {
                let message = error.to_string();
                let code = if message == "accepted root differs from requested root" {
                    "NATIVE_STALE_ROOT"
                } else {
                    "NATIVE_EXECUTION_FAILED"
                };
                serde_json::json!({"id": id, "ok": false, "code": code, "message": message})
            }
        };
        let encoded = serde_json::to_vec(&response)?;
        if encoded.len() > 65_536 {
            return Err("native worker response exceeds 64 KiB".into());
        }
        writer.write_all(&encoded)?;
        writer.write_all(b"\n")?;
        writer.flush()?;
    }
}

fn inspect_native_test_report(args: &[String]) -> Result<(), DynError> {
    if args.len() != 1 {
        return Err("inspect-native-test-report requires one stored report path".into());
    }
    let mut stored = Vec::new();
    File::open(&args[0])?
        .take(4 * 1024 * 1024 + 1)
        .read_to_end(&mut stored)?;
    if stored.len() > 4 * 1024 * 1024 {
        return Err("native test report exceeds 4 MiB".into());
    }
    let report = NativeTestReportV1::parse(&stored)?;
    let entries: Vec<_> = report
        .entries()
        .iter()
        .map(|entry| {
            serde_json::json!({
                "test_entity_id": hex(entry.test_entity.as_bytes()),
                "test_object_id": hex(entry.test_object.as_bytes()),
                "execution_report_id": hex(entry.execution_report_id.as_bytes()),
                "comparison_tag": entry.comparison.tag(),
            })
        })
        .collect();
    println!(
        "{}",
        serde_json::json!({
            "contract": "sley-tools.native-test-report-inspection.v0",
            "report_id": hex(report.report_id().as_bytes()),
            "plan_id": hex(report.plan_id().as_bytes()),
            "schema_epoch": hex(report.schema_epoch().as_bytes()),
            "proposed_root": hex(report.proposed_root().as_bytes()),
            "entries": entries,
            "match_count": report.match_count(),
            "mismatch_count": report.mismatch_count(),
            "rejected_count": report.rejected_count(),
            "stored_sha256": hex(&Sha256::digest(&stored)),
        })
    );
    Ok(())
}

fn main() -> Result<(), DynError> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("construct") => construct(&args[1..]),
        Some("construct-resource") => resource::construct(&args[1..]),
        Some("construct-graph") => graph::construct(&args[1..]),
        Some("construct-graph-native-test") => graph::construct_native_test(&args[1..]),
        Some("edit-graph-constant") => graph::edit_constant(&args[1..]),
        Some("edit-graph-opcode") => graph::edit_opcode(&args[1..]),
        Some("edit-graph-operand") => graph::edit_operand(&args[1..]),
        Some("edit-graph-branch") => graph::edit_branch(&args[1..]),
        Some("edit-resource") => resource::edit(&args[1..]),
        Some("edit") => edit(&args[1..]),
        Some("run") => run_native(&args[1..]),
        Some("run-resource") => resource::run(&args[1..]),
        Some("run-graph") => graph::run(&args[1..]),
        Some("serve") => serve_native(&args[1..]),
        Some("serve-resource") => resource::serve(&args[1..]),
        Some("serve-graph") => graph::serve(&args[1..]),
        Some("portable-import") => portable::import(&args[1..]),
        Some("portable-import-graph") => graph_portable::import(&args[1..]),
        Some("inspect-native-test-report") => inspect_native_test_report(&args[1..]),
        _ => Err(
            "usage: sley-tools-native-builder construct|construct-resource|construct-graph|construct-graph-native-test|edit|edit-resource|edit-graph-constant|edit-graph-opcode|edit-graph-operand|edit-graph-branch|run|run-resource|run-graph|serve|serve-resource|serve-graph|portable-import|inspect-native-test-report ...".into(),
        ),
    }
}
