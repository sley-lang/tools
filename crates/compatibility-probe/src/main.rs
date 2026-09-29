//! Construct a fresh workspace.create request with the selected public core codecs.
//!
//! This is F0 probe material, not the eventual user-facing toolkit backend.

use std::fs::File;
use std::io::Read;
use std::time::{SystemTime, UNIX_EPOCH};

use sley_id::{
    CandidateNonce, EntityId, ObjectId, PolicyRootId, PrincipalId, SchemaEpochId, StateRoot,
    TransactionId, WorkspaceId,
};
use sley_mutate::value::{
    BlockBody, EntityBodyValue, EntityIdSet, FunctionBody, NamespaceBody, OperationBody,
    ParameterBody,
};
use sley_mutate::{
    BoundPrecondition, CandidateExpiry, CandidateRecord, EntityObjectRecord, ExactEntityVersion,
    ExpectedIdentityAbsent, MutationClass, MutationOperation, MutationPayload, PreconditionPayload,
    PreimageRequirement, build_candidate, build_entity_object, encode_candidate_record,
    encode_const_value, full_validation_profile_id,
};
use sley_policy::{
    PolicyResourceCeilings, PolicyRootBuilder, PrincipalGrantBuilder,
    build_capability_summary_projection, conformance_registry as policy_registry,
};
use sley_scb1::{encode_list, encode_record, encode_union, encode_uvar};
use sley_ssmc::{
    ConstData, ConstValue, Immediate, Opcode, OperationResultRef, ParameterRole, Reachability,
    ReturnTerminator, Terminator, TypeExpr, ValueRef, Visibility,
};
use sley_state_root::{StateRootBuilder, conformance_epoch_id, conformance_registry};

fn random32() -> Result<[u8; 32], Box<dyn std::error::Error>> {
    let mut bytes = [0; 32];
    File::open("/dev/urandom")?.read_exact(&mut bytes)?;
    Ok(bytes)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn bytes32(value: &str) -> Result<[u8; 32], Box<dyn std::error::Error>> {
    if value.len() != 64 {
        return Err("expected a 32-byte lowercase hex identity".into());
    }
    let mut result = [0_u8; 32];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        result[index] = u8::from_str_radix(std::str::from_utf8(pair)?, 16)?;
    }
    Ok(result)
}

fn genesis() -> Result<(), Box<dyn std::error::Error>> {
    let workspace = WorkspaceId::from_bytes(random32()?);
    let principal = PrincipalId::from_bytes(random32()?);
    let namespace = EntityId::from_bytes(random32()?);
    let epoch = conformance_epoch_id()?;
    let object = build_entity_object(
        epoch,
        &EntityObjectRecord {
            entity_id: namespace,
            body: EntityBodyValue::Namespace(NamespaceBody {
                parent: None,
                members: EntityIdSet::from_unsorted(vec![])?,
            }),
            label: Some("f0-workspace".to_owned()),
            semantic_fingerprint: None,
        },
    )?;
    let grant = PrincipalGrantBuilder::new(PolicyResourceCeilings::new(
        1_000_000, 16_777_216, 1_048_576, 0, 100, 0,
    ))
    .mutation_class(MutationClass::CreateEntity)
    .mutation_class(MutationClass::ReplaceEntityVersion)
    .mutation_class(MutationClass::AddEntryPoint)
    .mutation_class(MutationClass::AddTest)
    .build()?;
    let policy = PolicyRootBuilder::new(workspace)
        .principal_grant(principal, grant)
        .build(&policy_registry()?)?;
    let state = StateRootBuilder::new(
        workspace,
        object.object_id(),
        object.object_id(),
        policy.root(),
    )
    .entity_binding(namespace, object.object_id())
    .build(&conformance_registry()?)?;
    let body = encode_record(&[
        (1, state.stored_bytes.clone()),
        (2, policy.stored_bytes().to_vec()),
        (3, encode_list(&[object.stored_bytes().to_vec()])?),
        (4, encode_list(&[])?),
    ])?;
    let output = serde_json::json!({
        "workspace_create_hex": hex(&body),
        "workspace_id": hex(workspace.as_bytes()),
        "principal_id": hex(principal.as_bytes()),
        "state_root": hex(state.root.as_bytes()),
        "policy_root": hex(policy.root().as_bytes()),
        "namespace_entity": hex(namespace.as_bytes()),
        "schema_epoch": hex(epoch.as_bytes()),
    });
    println!("{}", output);
    Ok(())
}

fn candidate(args: &[String]) -> Result<(), Box<dyn std::error::Error>> {
    if args.len() != 6 {
        return Err("candidate requires workspace principal root policy transaction schema".into());
    }
    let workspace = WorkspaceId::from_bytes(bytes32(&args[0])?);
    let principal = PrincipalId::from_bytes(bytes32(&args[1])?);
    let root = StateRoot::from_bytes(bytes32(&args[2])?);
    let policy = PolicyRootId::from_bytes(bytes32(&args[3])?);
    let transaction = TransactionId::from_bytes(bytes32(&args[4])?);
    let epoch = SchemaEpochId::from_bytes(bytes32(&args[5])?);
    let nonce = CandidateNonce::from_bytes(random32()?);
    let function = EntityId::derive(workspace, nonce, 5, 0);
    let parameter = EntityId::derive(workspace, nonce, 6, 1);
    let block = EntityId::derive(workspace, nonce, 7, 2);
    let empty = EntityIdSet::from_unsorted(vec![])?;
    let bodies = [
        EntityBodyValue::Function(FunctionBody {
            type_parameters: vec![],
            parameters: vec![parameter],
            result_type: TypeExpr::Bool,
            effects: empty.clone(),
            entry_block: block,
            blocks: vec![block],
            contracts: empty,
            visibility: Visibility::Private,
        }),
        EntityBodyValue::Parameter(ParameterBody {
            owner: function,
            role: ParameterRole::Function,
            ordinal: 0,
            value_type: TypeExpr::Bool,
        }),
        EntityBodyValue::Block(BlockBody {
            function,
            parameters: vec![],
            operations: vec![],
            terminator: Terminator::Return(ReturnTerminator {
                value: ValueRef::Parameter(parameter),
            }),
            reachability: Reachability::Required,
        }),
    ];
    let targets = [(function, 5_u16), (parameter, 6), (block, 7)];
    let mut operations = Vec::new();
    let mut preconditions = Vec::new();
    for (index, (entity, kind)) in targets.iter().enumerate() {
        let ordinal = u32::try_from(index)?;
        operations.push(MutationOperation {
            ordinal,
            class: MutationClass::CreateEntity,
            target_kind: *kind,
            target_entity: *entity,
            field_tag: None,
            payload: MutationPayload::CreateEntity(bodies[index].clone()),
            precondition_ordinal: ordinal,
        });
        preconditions.push(BoundPrecondition {
            operation_ordinal: ordinal,
            requirement: PreimageRequirement::ExpectedIdentityAbsent,
            payload: PreconditionPayload::ExpectedIdentityAbsent(ExpectedIdentityAbsent {
                entity_id: *entity,
            }),
        });
    }
    let projection = build_capability_summary_projection(principal, workspace, policy, root, &[])?;
    let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis() as u64;
    let record = CandidateRecord {
        format_version: 1,
        workspace_id: workspace,
        base_transaction_id: transaction,
        base_root: root,
        schema_epoch_id: epoch,
        policy_root_id: policy,
        principal_id: principal,
        capability_summary_digest: projection.digest(),
        operations,
        preconditions,
        validation_profile_id: full_validation_profile_id()?,
        candidate_nonce: nonce,
        expiry: CandidateExpiry::unix_millis(now + 60_000),
    };
    let candidate = build_candidate(&record)?;
    let block_object = build_entity_object(
        epoch,
        &EntityObjectRecord {
            entity_id: block,
            body: bodies[2].clone(),
            label: None,
            semantic_fingerprint: None,
        },
    )?;
    println!(
        "{}",
        serde_json::json!({
            "candidate_record_hex": hex(&encode_candidate_record(&record)?),
            "candidate_stored_hex": hex(&candidate.stored_bytes),
            "candidate_id": hex(candidate.candidate_id.as_bytes()),
            "function_id": hex(function.as_bytes()),
            "parameter_id": hex(parameter.as_bytes()),
            "block_id": hex(block.as_bytes()),
            "block_object_id": hex(block_object.object_id().as_bytes()),
            "now_unix_millis": now,
        })
    );
    Ok(())
}

fn edit(args: &[String]) -> Result<(), Box<dyn std::error::Error>> {
    if args.len() != 11 {
        return Err("edit requires workspace principal root policy transaction schema function parameter block block_object valid|invalid".into());
    }
    let workspace = WorkspaceId::from_bytes(bytes32(&args[0])?);
    let principal = PrincipalId::from_bytes(bytes32(&args[1])?);
    let root = StateRoot::from_bytes(bytes32(&args[2])?);
    let policy = PolicyRootId::from_bytes(bytes32(&args[3])?);
    let transaction = TransactionId::from_bytes(bytes32(&args[4])?);
    let epoch = SchemaEpochId::from_bytes(bytes32(&args[5])?);
    let function = EntityId::from_bytes(bytes32(&args[6])?);
    let parameter = EntityId::from_bytes(bytes32(&args[7])?);
    let block = EntityId::from_bytes(bytes32(&args[8])?);
    let block_object = ObjectId::from_bytes(bytes32(&args[9])?);
    let valid = match args[10].as_str() {
        "valid" => true,
        "invalid" => false,
        _ => return Err("edit kind must be valid or invalid".into()),
    };
    let nonce = CandidateNonce::from_bytes(random32()?);
    let operation = EntityId::derive(workspace, nonce, 8, 0);
    let operation_body = EntityBodyValue::Operation(OperationBody {
        block,
        ordinal: 0,
        opcode: Opcode::BoolNot.tag(),
        operands: vec![ValueRef::Parameter(parameter)],
        result_types: vec![if valid {
            TypeExpr::Bool
        } else {
            TypeExpr::SInt(sley_ssmc::IntegerWidth::from_bits(64))
        }],
        immediate: Immediate::None,
    });
    let block_body = EntityBodyValue::Block(BlockBody {
        function,
        parameters: vec![],
        operations: vec![operation],
        terminator: Terminator::Return(ReturnTerminator {
            value: ValueRef::OperationResult(OperationResultRef {
                operation,
                result_index: 0,
            }),
        }),
        reachability: Reachability::Required,
    });
    let projection = build_capability_summary_projection(principal, workspace, policy, root, &[])?;
    let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis() as u64;
    let record = CandidateRecord {
        format_version: 1,
        workspace_id: workspace,
        base_transaction_id: transaction,
        base_root: root,
        schema_epoch_id: epoch,
        policy_root_id: policy,
        principal_id: principal,
        capability_summary_digest: projection.digest(),
        operations: vec![
            MutationOperation {
                ordinal: 0,
                class: MutationClass::CreateEntity,
                target_kind: 8,
                target_entity: operation,
                field_tag: None,
                payload: MutationPayload::CreateEntity(operation_body),
                precondition_ordinal: 0,
            },
            MutationOperation {
                ordinal: 1,
                class: MutationClass::ReplaceEntityVersion,
                target_kind: 7,
                target_entity: block,
                field_tag: None,
                payload: MutationPayload::ReplaceEntityVersion(block_body),
                precondition_ordinal: 1,
            },
        ],
        preconditions: vec![
            BoundPrecondition {
                operation_ordinal: 0,
                requirement: PreimageRequirement::ExpectedIdentityAbsent,
                payload: PreconditionPayload::ExpectedIdentityAbsent(ExpectedIdentityAbsent {
                    entity_id: operation,
                }),
            },
            BoundPrecondition {
                operation_ordinal: 1,
                requirement: PreimageRequirement::ExactEntityVersion,
                payload: PreconditionPayload::ExactEntityVersion(ExactEntityVersion {
                    entity_id: block,
                    object_id: block_object,
                }),
            },
        ],
        validation_profile_id: full_validation_profile_id()?,
        candidate_nonce: nonce,
        expiry: CandidateExpiry::unix_millis(now + 60_000),
    };
    let candidate = build_candidate(&record)?;
    println!(
        "{}",
        serde_json::json!({
            "candidate_record_hex": hex(&encode_candidate_record(&record)?),
            "candidate_stored_hex": hex(&candidate.stored_bytes),
            "candidate_id": hex(candidate.candidate_id.as_bytes()),
            "operation_id": hex(operation.as_bytes()),
            "now_unix_millis": now,
            "valid": valid,
        })
    );
    Ok(())
}

fn execute(args: &[String]) -> Result<(), Box<dyn std::error::Error>> {
    if args.len() != 3 {
        return Err("execute requires function schema_epoch true|false".into());
    }
    let function = EntityId::from_bytes(bytes32(&args[0])?);
    let epoch = SchemaEpochId::from_bytes(bytes32(&args[1])?);
    let bit = match args[2].as_str() {
        "true" => true,
        "false" => false,
        _ => return Err("execute input must be true or false".into()),
    };
    let value = ConstValue {
        value_type: TypeExpr::Bool,
        data: ConstData::Bool(bit),
    };
    let request = encode_record(&[
        (1, function.as_bytes().to_vec()),
        (2, encode_list(&[encode_const_value(&value)?])?),
        (
            3,
            encode_record(&[
                (1, encode_uvar(1_000)),
                (2, encode_uvar(1_000)),
                (3, encode_uvar(10_000)),
                (4, encode_uvar(100)),
                (5, encode_union(0, &[])?),
                (6, encode_uvar(1)),
            ])?,
        ),
    ])?;
    println!(
        "{}",
        serde_json::json!({
            "execute_request_hex": hex(&request),
            "expected_output_hash": hex(sley_ssmc::fingerprint::hash_validated_value(epoch, &value)?.as_bytes()),
            "input": bit,
        })
    );
    Ok(())
}

fn native_tests_request(args: &[String]) -> Result<(), Box<dyn std::error::Error>> {
    if args.len() != 1 {
        return Err("native-tests-request requires root".into());
    }
    let root = StateRoot::from_bytes(bytes32(&args[0])?);
    let profile = sley_vm::native_execution::profile_id();
    let mut attempt = [0_u8; 16];
    File::open("/dev/urandom")?.read_exact(&mut attempt)?;
    let request = encode_record(&[
        (1, root.as_bytes().to_vec()),
        (2, encode_list(&[])?),
        (3, profile.as_bytes().to_vec()),
        (4, attempt.to_vec()),
    ])?;
    println!(
        "{}",
        serde_json::json!({
            "tests_selected_request_hex": hex(&request),
            "native_execution_profile": hex(profile.as_bytes()),
        })
    );
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        None | Some("genesis") if args.len() <= 1 => genesis(),
        Some("candidate") => candidate(&args[1..]),
        Some("edit") => edit(&args[1..]),
        Some("execute") => execute(&args[1..]),
        Some("native-tests-request") => native_tests_request(&args[1..]),
        _ => Err("usage: f0-genesis-request [genesis|candidate WORKSPACE PRINCIPAL ROOT POLICY TRANSACTION SCHEMA|execute FUNCTION SCHEMA true|false]".into()),
    }
}
