//! Native Resource Admission graph and exact signed-64 execution profile.

use super::*;

pub(crate) const RESOURCE_FIELDS: [&str; 7] = [
    "allow",
    "invalid_request",
    "invalid_capacity",
    "insufficient_workers",
    "insufficient_memory",
    "requested_memory_mib",
    "available_memory_mib",
];

fn signed_type() -> TypeExpr {
    TypeExpr::SInt(IntegerWidth::from_bits(64))
}

fn signed_value(value: i64) -> ConstValue {
    ConstValue {
        value_type: signed_type(),
        data: ConstData::SInt(i128::from(value)),
    }
}

pub(crate) fn construct(args: &[String]) -> Result<(), DynError> {
    let context = Context::parse(args)?;
    let nonce = CandidateNonce::from_bytes(random32()?);
    let id = |kind, ordinal| EntityId::derive(context.workspace, nonce, kind, ordinal);
    let function = id(5, 0);
    let parameters: Vec<_> = (0..4).map(|i| id(6, i + 1)).collect();
    let block = id(7, 5);
    let zero = id(9, 6);
    let operation_ids: Vec<_> = (0..14).map(|i| id(8, i + 7)).collect();
    let result_definition = id(4, 21);
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
    for (index, parameter) in parameters.iter().enumerate() {
        entities.push((
            *parameter,
            6,
            EntityBodyValue::Parameter(ParameterBody {
                owner: function,
                role: ParameterRole::Function,
                ordinal: u32::try_from(index)?,
                value_type: signed_type(),
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
                value: result_ref(operation_ids[13]),
            }),
            reachability: Reachability::Required,
        }),
    ));
    entities.push((
        zero,
        9,
        EntityBodyValue::Constant(ConstantBody {
            value: signed_value(0),
        }),
    ));
    let p = |index: usize| ValueRef::Parameter(parameters[index]);
    let r = |index: usize| result_ref(operation_ids[index]);
    let definitions = vec![
        (
            Opcode::ConstantRef,
            vec![],
            signed_type(),
            Immediate::Entity(zero),
        ),
        (
            Opcode::LessThan,
            vec![p(0), r(0)],
            TypeExpr::Bool,
            Immediate::None,
        ),
        (
            Opcode::LessThan,
            vec![p(1), r(0)],
            TypeExpr::Bool,
            Immediate::None,
        ),
        (
            Opcode::LessThan,
            vec![p(2), r(0)],
            TypeExpr::Bool,
            Immediate::None,
        ),
        (
            Opcode::LessThan,
            vec![p(3), r(0)],
            TypeExpr::Bool,
            Immediate::None,
        ),
        (
            Opcode::GreaterThan,
            vec![p(0), p(1)],
            TypeExpr::Bool,
            Immediate::None,
        ),
        (
            Opcode::GreaterThan,
            vec![p(2), p(3)],
            TypeExpr::Bool,
            Immediate::None,
        ),
        (
            Opcode::BoolOr,
            vec![r(1), r(3)],
            TypeExpr::Bool,
            Immediate::None,
        ),
        (
            Opcode::BoolOr,
            vec![r(2), r(4)],
            TypeExpr::Bool,
            Immediate::None,
        ),
        (
            Opcode::BoolOr,
            vec![r(7), r(8)],
            TypeExpr::Bool,
            Immediate::None,
        ),
        (
            Opcode::BoolOr,
            vec![r(5), r(6)],
            TypeExpr::Bool,
            Immediate::None,
        ),
        (
            Opcode::BoolOr,
            vec![r(9), r(10)],
            TypeExpr::Bool,
            Immediate::None,
        ),
        (
            Opcode::BoolNot,
            vec![r(11)],
            TypeExpr::Bool,
            Immediate::None,
        ),
        (
            Opcode::RecordNew,
            vec![r(12), r(7), r(8), r(5), r(6), p(2), p(3)],
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
                (0..RESOURCE_FIELDS.len())
                    .map(|index| RecordField {
                        member_id: result_member(index),
                        value_type: if index < 5 {
                            TypeExpr::Bool
                        } else {
                            signed_type()
                        },
                        visibility: Visibility::Private,
                    })
                    .collect(),
            ),
            invariants: EntityIdSet::from_unsorted(vec![])?,
            visibility: Visibility::Private,
        }),
    ));
    let memory_comparison = operation_ids[6];
    let comparison_body = entities
        .iter()
        .find(|(entity, _, _)| *entity == memory_comparison)
        .map(|(_, _, body)| body.clone())
        .ok_or("memory comparison operation missing")?;
    let comparison_object = build_entity_object(
        context.epoch,
        &EntityObjectRecord {
            entity_id: memory_comparison,
            body: comparison_body,
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
            "memory_comparison_id": hex(memory_comparison.as_bytes()),
            "memory_comparison_object_id": hex(comparison_object.object_id().as_bytes()),
            "require_memory_spare": false,
            "result_type_id": hex(result_definition.as_bytes()),
            "result_fields": RESOURCE_FIELDS.iter().enumerate().map(|(index, name)| serde_json::json!({"name": name, "member_id": hex(result_member(index).as_bytes()), "type": if index < 5 { "Bool" } else { "SInt64" }})).collect::<Vec<_>>(),
            "now_unix_millis": now,
            "program": "resource-admission",
            "native_operation_count": 14,
        })
    );
    Ok(())
}

pub(crate) fn edit(args: &[String]) -> Result<(), DynError> {
    if args.len() != 10 {
        return Err("edit-resource requires context repository operation_id expected_object require_memory_spare".into());
    }
    let context = Context::parse(&args[..6])?;
    let repository = TransactionRepository::new(Path::new(&args[6]));
    let operation = EntityId::from_bytes(bytes32(&args[7])?);
    let old_object = ObjectId::from_bytes(bytes32(&args[8])?);
    let require_spare = parse_flag(&args[9])?;
    let head = repository.accepted_head()?;
    if head.state_root().root != context.root {
        return Err("accepted root differs from requested edit base".into());
    }
    let selected = head
        .objects()
        .iter()
        .find(|object| object.record().entity_id == operation)
        .ok_or("memory comparison operation is absent")?;
    if selected.object_id() != old_object {
        return Err("memory comparison preimage differs from requested object".into());
    }
    let EntityBodyValue::Operation(current) = &selected.record().body else {
        return Err("memory comparison entity is not an operation".into());
    };
    if current.ordinal != 6
        || current.result_types != vec![TypeExpr::Bool]
        || ![Opcode::GreaterThan.tag(), Opcode::GreaterEqual.tag()].contains(&current.opcode)
    {
        return Err("memory comparison operation is outside the editable profile".into());
    }
    let expected_old = if require_spare {
        Opcode::GreaterThan
    } else {
        Opcode::GreaterEqual
    };
    if current.opcode != expected_old.tag() {
        return Err("memory comparison already has the requested policy".into());
    }
    let mut replacement = current.clone();
    replacement.opcode = if require_spare {
        Opcode::GreaterEqual
    } else {
        Opcode::GreaterThan
    }
    .tag();
    let body = EntityBodyValue::Operation(replacement);
    let nonce = CandidateNonce::from_bytes(random32()?);
    let record = context.candidate(
        nonce,
        vec![MutationOperation {
            ordinal: 0,
            class: MutationClass::ReplaceEntityVersion,
            target_kind: 8,
            target_entity: operation,
            field_tag: None,
            payload: MutationPayload::ReplaceEntityVersion(body.clone()),
            precondition_ordinal: 0,
        }],
        vec![BoundPrecondition {
            operation_ordinal: 0,
            requirement: PreimageRequirement::ExactEntityVersion,
            payload: PreconditionPayload::ExactEntityVersion(ExactEntityVersion {
                entity_id: operation,
                object_id: old_object,
            }),
        }],
    )?;
    let candidate = build_candidate(&record)?;
    let new_object = build_entity_object(
        context.epoch,
        &EntityObjectRecord {
            entity_id: operation,
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
            "memory_comparison_object_id": hex(new_object.object_id().as_bytes()),
            "require_memory_spare": require_spare,
            "now_unix_millis": now,
        })
    );
    Ok(())
}

pub(crate) fn run(args: &[String]) -> Result<(), DynError> {
    println!("{}", native_result(args)?);
    Ok(())
}

pub(crate) fn serve(args: &[String]) -> Result<(), DynError> {
    if args.len() != 3 {
        return Err("serve-resource requires repository root function".into());
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
            return Err("resource worker request exceeds 4 KiB or lacks newline".into());
        }
        let request: serde_json::Value = serde_json::from_slice(&line)?;
        let object = request
            .as_object()
            .ok_or("resource worker request is not an object")?;
        let names = [
            "requested_workers",
            "available_workers",
            "requested_memory_mib",
            "available_memory_mib",
        ];
        if object.len() != 5 || names.iter().any(|name| !object.contains_key(*name)) {
            return Err("resource worker request has unexpected fields".into());
        }
        let id = request["id"]
            .as_u64()
            .ok_or("resource worker request has no ID")?;
        let mut run_args = args.to_vec();
        for name in names {
            let value = request[name]
                .as_str()
                .ok_or("resource worker integer is not decimal text")?;
            if value.parse::<i64>()?.to_string() != value {
                return Err("resource worker integer is not canonical SInt64 decimal".into());
            }
            run_args.push(value.to_owned());
        }
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
        writer.write_all(&serde_json::to_vec(&response)?)?;
        writer.write_all(b"\n")?;
        writer.flush()?;
    }
}

pub(crate) fn native_result(args: &[String]) -> Result<serde_json::Value, DynError> {
    if args.len() != 7 {
        return Err("run-resource requires repository root function requested_workers available_workers requested_memory_mib available_memory_mib".into());
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
    let TypeExpr::Named(named_result) = &graph.result_type else {
        return Err("Resource Admission result is not a named record".into());
    };
    let result_definition = entities
        .type_definitions
        .iter()
        .find(|definition| definition.entity_id == named_result.definition)
        .ok_or("Resource Admission result definition absent")?;
    if !matches!(&result_definition.form, TypeDefForm::Record(fields) if fields.len() == RESOURCE_FIELDS.len()
        && fields.iter().enumerate().all(|(index, field)| field.member_id == result_member(index)
            && field.value_type == if index < 5 { TypeExpr::Bool } else { signed_type() }))
    {
        return Err("Resource Admission result schema differs from contract".into());
    }
    let inputs = args[3..]
        .iter()
        .map(|value| value.parse::<i64>().map(signed_value))
        .collect::<Result<Vec<_>, _>>()?;
    let outcome = execute_function(
        LoweringInput {
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
        },
        ExecutionRequest {
            inputs,
            limits: ExecutionLimits {
                max_instructions: 1_000,
                max_fuel: 1_000,
                max_value_units: 10_000,
                max_output_units: 1_000,
                cancel_at_fuel: None,
            },
        },
    )?;
    let ExecutionTermination::Success(output) = outcome.termination else {
        return Err(format!("native execution ended with {:?}", outcome.termination).into());
    };
    if output.value_type != result_type(result_definition.entity_id) {
        return Err("Resource Admission output type differs from contract".into());
    }
    let ConstData::Record(record) = &output.data else {
        return Err("Resource Admission output is not a record".into());
    };
    if record.definition != result_definition.entity_id
        || record.fields.len() != RESOURCE_FIELDS.len()
    {
        return Err("Resource Admission record definition or field count differs".into());
    }
    let mut fields = serde_json::Map::new();
    for (index, field) in record.fields.iter().enumerate() {
        if field.member_id != result_member(index) {
            return Err("Resource Admission output field identity differs".into());
        }
        let value = match (&field.value.value_type, &field.value.data) {
            (TypeExpr::Bool, ConstData::Bool(value)) if index < 5 => serde_json::json!(value),
            (TypeExpr::SInt(width), ConstData::SInt(value)) if index >= 5 && width.bits() == 64 => {
                serde_json::json!(value.to_string())
            }
            _ => return Err("Resource Admission output field type differs".into()),
        };
        fields.insert(RESOURCE_FIELDS[index].to_owned(), value);
    }
    let reason_codes = RESOURCE_FIELDS[1..5]
        .iter()
        .filter(|name| fields[**name] == true)
        .copied()
        .collect::<Vec<_>>();
    Ok(serde_json::json!({
        "root": hex(complete.root().as_bytes()),
        "function_id": hex(function.as_bytes()),
        "profile": "extended_v1",
        "result": fields,
        "reason_codes": reason_codes,
        "value_hash": hex(sley_ssmc::fingerprint::hash_validated_value(complete.schema_epoch_id(), &output)?.as_bytes()),
        "observation_id": hex(outcome.observation_id.as_bytes()),
        "instruction_count": outcome.instruction_count,
    }))
}
