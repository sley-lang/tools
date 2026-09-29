//! Typed, label-based authoring of one pure native Sley function.

use std::collections::{BTreeMap, BTreeSet};
use std::fs::File;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::Path;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sley_check::TypeEnvironment;
use sley_id::{CandidateNonce, EntityId, StateRoot};
use sley_mutate::value::{
    BlockBody, ConstantBody, EntityBodyValue, EntityIdSet, FunctionBody, OperationBody,
    ParameterBody, TestCaseBody, TypeDefBody,
};
use sley_mutate::{
    BoundPrecondition, EntityObjectRecord, ExactEntityVersion, MutationClass, MutationOperation,
    MutationPayload, PreconditionPayload, PreimageRequirement, build_candidate,
    build_entity_object, encode_candidate_record,
};
use sley_policy::{
    CandidateValidationContext, CandidateValidationLimits, NativePlanInputs, native_test_plan,
    validate_candidate_bytes,
};
use sley_repo::CompleteRootRequest;
use sley_ssmc::{
    BranchTerminator, BuiltinFailureKind, CondBranchTerminator, ConstData, ConstValue,
    EffectEnvironment, ExpectedOutcome, Immediate, IntegerWidth, MemberId, NamedType, Opcode,
    OperationResultRef, ParameterRole, Reachability, RecordField, ResourceLimits, ResultConst,
    ReturnTerminator, TargetEdge, Terminator, TypeDefForm, TypeExpr, ValueRef, Visibility,
};
use sley_tests::NativeAggregateLimits;
use sley_txn::TransactionRepository;
use sley_vm::native_execution::NativeImplementationLimits;
use sley_vm::{
    CacheProfile, ExecutionLimits, ExecutionRequest, ExecutionTermination, LoweringInput,
    execute_function,
};

use crate::{Context, DynError, bytes32, created, hex, random32};

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
enum Primitive {
    Bool,
    UInt32,
    SInt64,
    CheckedSInt64,
}

impl Primitive {
    fn native(self) -> TypeExpr {
        match self {
            Self::Bool => TypeExpr::Bool,
            Self::UInt32 => TypeExpr::UInt(IntegerWidth::from_bits(32)),
            Self::SInt64 => TypeExpr::SInt(IntegerWidth::from_bits(64)),
            Self::CheckedSInt64 => TypeExpr::Result {
                ok: Box::new(TypeExpr::SInt(IntegerWidth::from_bits(64))),
                error: Box::new(TypeExpr::BuiltinFailure(BuiltinFailureKind::Arithmetic)),
            },
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NamedTypeSpec {
    name: String,
    value_type: Primitive,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ConstantSpec {
    name: String,
    value_type: Primitive,
    value: Value,
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum ResultSpec {
    Primitive { value_type: Primitive },
    Record { fields: Vec<NamedTypeSpec> },
}

#[derive(Deserialize, Serialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum OperationSpec {
    ConstantRef {
        name: String,
        constant: String,
    },
    BoolNot {
        name: String,
        operand: String,
    },
    BoolAnd {
        name: String,
        left: String,
        right: String,
    },
    BoolOr {
        name: String,
        left: String,
        right: String,
    },
    Equal {
        name: String,
        left: String,
        right: String,
    },
    NotEqual {
        name: String,
        left: String,
        right: String,
    },
    LessThan {
        name: String,
        left: String,
        right: String,
    },
    LessEqual {
        name: String,
        left: String,
        right: String,
    },
    GreaterThan {
        name: String,
        left: String,
        right: String,
    },
    GreaterEqual {
        name: String,
        left: String,
        right: String,
    },
    IntAddChecked {
        name: String,
        left: String,
        right: String,
    },
    IntSubChecked {
        name: String,
        left: String,
        right: String,
    },
    IntMulChecked {
        name: String,
        left: String,
        right: String,
    },
    IntNegChecked {
        name: String,
        operand: String,
    },
    ResultOk {
        name: String,
        operand: String,
    },
    RecordNew {
        name: String,
        fields: Vec<String>,
    },
}

impl OperationSpec {
    fn name(&self) -> &str {
        match self {
            Self::ConstantRef { name, .. }
            | Self::BoolNot { name, .. }
            | Self::BoolAnd { name, .. }
            | Self::BoolOr { name, .. }
            | Self::Equal { name, .. }
            | Self::NotEqual { name, .. }
            | Self::LessThan { name, .. }
            | Self::LessEqual { name, .. }
            | Self::GreaterThan { name, .. }
            | Self::GreaterEqual { name, .. }
            | Self::IntAddChecked { name, .. }
            | Self::IntSubChecked { name, .. }
            | Self::IntMulChecked { name, .. }
            | Self::IntNegChecked { name, .. }
            | Self::ResultOk { name, .. }
            | Self::RecordNew { name, .. } => name,
        }
    }
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum TerminatorSpec {
    Return {
        value: String,
    },
    Branch {
        target: String,
    },
    CondBranch {
        condition: String,
        if_true: String,
        if_false: String,
    },
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct BlockSpec {
    name: String,
    operations: Vec<OperationSpec>,
    terminator: TerminatorSpec,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct GraphSpec {
    contract: String,
    program_id: String,
    title: String,
    purpose: String,
    parameters: Vec<NamedTypeSpec>,
    constants: Vec<ConstantSpec>,
    result: ResultSpec,
    entry_block: String,
    blocks: Vec<BlockSpec>,
}

#[derive(Clone)]
struct TypedValue {
    reference: ValueRef,
    value_type: TypeExpr,
}

fn invalid(message: impl Into<String>) -> DynError {
    message.into().into()
}

fn label(label: &str) -> Result<(), DynError> {
    if label.is_empty()
        || label.len() > 64
        || !label
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
        || !label.as_bytes()[0].is_ascii_alphabetic()
    {
        return Err(invalid(format!("invalid graph label {label:?}")));
    }
    Ok(())
}

fn unique_label(seen: &mut BTreeSet<String>, name: &str) -> Result<(), DynError> {
    label(name)?;
    if !seen.insert(name.to_owned()) {
        return Err(invalid(format!("duplicate graph label {name:?}")));
    }
    Ok(())
}

fn constant_value(spec: &ConstantSpec) -> Result<ConstValue, DynError> {
    let data =
        match spec.value_type {
            Primitive::Bool => ConstData::Bool(
                spec.value
                    .as_bool()
                    .ok_or_else(|| invalid(format!("{} must be Boolean", spec.name)))?,
            ),
            Primitive::UInt32 => {
                let decimal = spec.value.as_str().ok_or_else(|| {
                    invalid(format!("{} must be canonical decimal text", spec.name))
                })?;
                let value: u32 = decimal.parse()?;
                if value.to_string() != decimal {
                    return Err(invalid(format!("{} is not canonical u32", spec.name)));
                }
                ConstData::UInt(u128::from(value))
            }
            Primitive::SInt64 => {
                let decimal = spec.value.as_str().ok_or_else(|| {
                    invalid(format!("{} must be canonical decimal text", spec.name))
                })?;
                let value: i64 = decimal.parse()?;
                if value.to_string() != decimal {
                    return Err(invalid(format!("{} is not canonical i64", spec.name)));
                }
                ConstData::SInt(i128::from(value))
            }
            Primitive::CheckedSInt64 => {
                return Err(invalid(
                    "CheckedSInt64 is an operation result, not a constant",
                ));
            }
        };
    Ok(ConstValue {
        value_type: spec.value_type.native(),
        data,
    })
}

fn resolve(
    name: &str,
    block: &BTreeMap<String, TypedValue>,
    params: &BTreeMap<String, TypedValue>,
) -> Result<TypedValue, DynError> {
    let (prefix, local) = name.split_once('.').ok_or_else(|| {
        invalid(format!(
            "value reference {name:?} needs param. or op. prefix"
        ))
    })?;
    match prefix {
        "param" => params.get(local),
        "op" => block.get(local),
        _ => None,
    }
    .cloned()
    .ok_or_else(|| invalid(format!("unknown or forward value reference {name:?}")))
}

fn binary(
    left: &str,
    right: &str,
    opcode: Opcode,
    block: &BTreeMap<String, TypedValue>,
    params: &BTreeMap<String, TypedValue>,
) -> Result<(Opcode, Vec<ValueRef>, TypeExpr, Immediate), DynError> {
    let lhs = resolve(left, block, params)?;
    let rhs = resolve(right, block, params)?;
    if lhs.value_type != rhs.value_type {
        return Err(invalid("binary operands have different types"));
    }
    let result_type = match opcode {
        Opcode::BoolAnd | Opcode::BoolOr => {
            if lhs.value_type != TypeExpr::Bool {
                return Err(invalid("Boolean operation needs Boolean operands"));
            }
            TypeExpr::Bool
        }
        Opcode::Equal | Opcode::NotEqual => {
            if !matches!(
                lhs.value_type,
                TypeExpr::Bool | TypeExpr::UInt(_) | TypeExpr::SInt(_)
            ) {
                return Err(invalid("equality operands need a supported primitive type"));
            }
            TypeExpr::Bool
        }
        Opcode::LessThan | Opcode::LessEqual | Opcode::GreaterThan | Opcode::GreaterEqual => {
            if !matches!(lhs.value_type, TypeExpr::UInt(_) | TypeExpr::SInt(_)) {
                return Err(invalid("ordered comparison needs integer operands"));
            }
            TypeExpr::Bool
        }
        Opcode::IntAddChecked | Opcode::IntSubChecked | Opcode::IntMulChecked => {
            if lhs.value_type != Primitive::SInt64.native() {
                return Err(invalid("checked arithmetic needs signed 64 bit operands"));
            }
            Primitive::CheckedSInt64.native()
        }
        _ => return Err(invalid("unsupported graph binary opcode")),
    };
    Ok((
        opcode,
        vec![lhs.reference, rhs.reference],
        result_type,
        Immediate::None,
    ))
}

fn check_reachability(spec: &GraphSpec) -> Result<(), DynError> {
    let mut edges = BTreeMap::new();
    for block in &spec.blocks {
        let targets = match &block.terminator {
            TerminatorSpec::Return { .. } => vec![],
            TerminatorSpec::Branch { target } => vec![target.as_str()],
            TerminatorSpec::CondBranch {
                if_true, if_false, ..
            } => vec![if_true.as_str(), if_false.as_str()],
        };
        for target in &targets {
            if !spec.blocks.iter().any(|block| &block.name == target) {
                return Err(invalid(format!("unknown branch target {target:?}")));
            }
        }
        edges.insert(block.name.as_str(), targets);
    }
    let mut seen = BTreeSet::new();
    let mut active = BTreeSet::new();
    fn visit<'a>(
        name: &'a str,
        edges: &BTreeMap<&'a str, Vec<&'a str>>,
        seen: &mut BTreeSet<&'a str>,
        active: &mut BTreeSet<&'a str>,
    ) -> Result<(), DynError> {
        if active.contains(name) {
            return Err(invalid(
                "graph contains a cycle; bounded v0 functions must terminate",
            ));
        }
        if seen.contains(name) {
            return Ok(());
        }
        active.insert(name);
        for target in &edges[name] {
            visit(target, edges, seen, active)?;
        }
        active.remove(name);
        seen.insert(name);
        Ok(())
    }
    visit(&spec.entry_block, &edges, &mut seen, &mut active)?;
    if seen.len() != spec.blocks.len() {
        return Err(invalid("graph has an unreachable block"));
    }
    Ok(())
}

pub(crate) fn construct(args: &[String]) -> Result<(), DynError> {
    if args.len() != 7 {
        return Err(invalid(
            "construct-graph requires spec path and six live context identities",
        ));
    }
    let path = Path::new(&args[0]);
    let mut bytes = Vec::new();
    File::open(path)?.take(65_537).read_to_end(&mut bytes)?;
    if bytes.len() > 65_536 {
        return Err(invalid("graph spec exceeds 64 KiB"));
    }
    let context = Context::parse(&args[1..])?;
    let result = construct_from_bytes(&bytes, &context)?;
    println!("{result}");
    Ok(())
}

pub(crate) fn construct_from_bytes(bytes: &[u8], context: &Context) -> Result<Value, DynError> {
    if bytes.len() > 65_536 {
        return Err(invalid("graph spec exceeds 64 KiB"));
    }
    let spec: GraphSpec = serde_json::from_slice(bytes)?;
    build(&spec, context)
}

fn build(spec: &GraphSpec, context: &Context) -> Result<Value, DynError> {
    if spec.contract != "sley-tools.graph-authoring.v0" {
        return Err(invalid("unknown graph authoring contract"));
    }
    if spec.program_id.is_empty()
        || spec.program_id.len() > 64
        || !spec
            .program_id
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
        || !spec.program_id.as_bytes()[0].is_ascii_lowercase()
        || spec.program_id.ends_with('-')
        || spec.program_id.contains("--")
        || spec.title.trim().is_empty()
        || spec.title.len() > 120
        || spec.purpose.trim().is_empty()
        || spec.purpose.len() > 500
    {
        return Err(invalid(
            "graph program identity, title, or purpose is invalid",
        ));
    }
    if spec.parameters.len() > 16
        || spec.constants.len() > 64
        || spec.blocks.is_empty()
        || spec.blocks.len() > 16
        || spec
            .blocks
            .iter()
            .map(|block| block.operations.len())
            .sum::<usize>()
            > 128
    {
        return Err(invalid("graph exceeds bounded authoring limits"));
    }
    let mut names = BTreeSet::new();
    for parameter in &spec.parameters {
        if parameter.value_type == Primitive::CheckedSInt64 {
            return Err(invalid(
                "checked arithmetic results cannot be graph parameters",
            ));
        }
        unique_label(&mut names, &parameter.name)?;
    }
    for constant in &spec.constants {
        if constant.value_type == Primitive::CheckedSInt64 {
            return Err(invalid(
                "checked arithmetic results cannot be graph constants",
            ));
        }
        unique_label(&mut names, &constant.name)?;
    }
    for block in &spec.blocks {
        unique_label(&mut names, &block.name)?;
    }
    let mut operation_names = BTreeSet::new();
    for block in &spec.blocks {
        for operation in &block.operations {
            unique_label(&mut operation_names, operation.name())?;
        }
    }
    if !spec
        .blocks
        .iter()
        .any(|block| block.name == spec.entry_block)
    {
        return Err(invalid("entry block is missing"));
    }
    check_reachability(spec)?;
    let fields = match &spec.result {
        ResultSpec::Primitive { .. } => None,
        ResultSpec::Record { fields } => {
            if fields.is_empty() || fields.len() > 16 {
                return Err(invalid("record result needs 1..16 fields"));
            }
            let mut field_names = BTreeSet::new();
            for field in fields {
                unique_label(&mut field_names, &field.name)?;
            }
            Some(fields)
        }
    };
    let nonce = CandidateNonce::from_bytes(random32()?);
    let mut ordinal = 0_u64;
    let mut allocation_order = Vec::new();
    let mut next_id = |kind| {
        let id = EntityId::derive(context.workspace, nonce, kind, ordinal);
        ordinal += 1;
        allocation_order.push(id);
        id
    };
    let function = next_id(5);
    let result_definition = fields.map(|_| next_id(4));
    let parameter_ids: Vec<_> = spec.parameters.iter().map(|_| next_id(6)).collect();
    let block_ids: Vec<_> = spec.blocks.iter().map(|_| next_id(7)).collect();
    let constant_ids: Vec<_> = spec.constants.iter().map(|_| next_id(9)).collect();
    let operation_ids: Vec<Vec<_>> = spec
        .blocks
        .iter()
        .map(|block| block.operations.iter().map(|_| next_id(8)).collect())
        .collect();
    let result_type = match &spec.result {
        ResultSpec::Primitive { value_type } => value_type.native(),
        ResultSpec::Record { .. } => TypeExpr::Named(NamedType {
            definition: result_definition.expect("record has definition"),
            arguments: vec![],
        }),
    };
    let block_lookup: BTreeMap<_, _> = spec
        .blocks
        .iter()
        .zip(&block_ids)
        .map(|(block, id)| (block.name.as_str(), *id))
        .collect();
    let empty = EntityIdSet::from_unsorted(vec![])?;
    let mut entities = vec![(
        function,
        5,
        EntityBodyValue::Function(FunctionBody {
            type_parameters: vec![],
            parameters: parameter_ids.clone(),
            result_type: result_type.clone(),
            effects: empty.clone(),
            entry_block: block_lookup[spec.entry_block.as_str()],
            blocks: block_ids.clone(),
            contracts: empty.clone(),
            visibility: Visibility::Private,
        }),
    )];
    let mut params = BTreeMap::new();
    for (index, (parameter, id)) in spec.parameters.iter().zip(&parameter_ids).enumerate() {
        entities.push((
            *id,
            6,
            EntityBodyValue::Parameter(ParameterBody {
                owner: function,
                role: ParameterRole::Function,
                ordinal: u32::try_from(index)?,
                value_type: parameter.value_type.native(),
            }),
        ));
        params.insert(
            parameter.name.clone(),
            TypedValue {
                reference: ValueRef::Parameter(*id),
                value_type: parameter.value_type.native(),
            },
        );
    }
    let mut constants = BTreeMap::new();
    for (constant, id) in spec.constants.iter().zip(&constant_ids) {
        let value = constant_value(constant)?;
        constants.insert(constant.name.as_str(), (*id, value.value_type.clone()));
        entities.push((*id, 9, EntityBodyValue::Constant(ConstantBody { value })));
    }
    if let Some(fields) = fields {
        let definition = result_definition.expect("record has definition");
        let native_fields = fields
            .iter()
            .enumerate()
            .map(|(index, field)| RecordField {
                member_id: MemberId::from_bytes(
                    [u8::try_from(index + 1).expect("at most 16 fields"); 32],
                ),
                value_type: field.value_type.native(),
                visibility: Visibility::Private,
            })
            .collect();
        entities.push((
            definition,
            4,
            EntityBodyValue::TypeDef(TypeDefBody {
                type_parameters: vec![],
                form: TypeDefForm::Record(native_fields),
                invariants: empty.clone(),
                visibility: Visibility::Private,
            }),
        ));
    }
    for (block_index, block) in spec.blocks.iter().enumerate() {
        let block_id = block_ids[block_index];
        let mut local = BTreeMap::new();
        for (index, (operation, id)) in block
            .operations
            .iter()
            .zip(&operation_ids[block_index])
            .enumerate()
        {
            let (opcode, operands, output_type, immediate) = match operation {
                OperationSpec::ConstantRef { constant, .. } => {
                    let (constant_id, value_type) = constants
                        .get(constant.as_str())
                        .ok_or_else(|| invalid(format!("unknown constant {constant:?}")))?;
                    (
                        Opcode::ConstantRef,
                        vec![],
                        value_type.clone(),
                        Immediate::Entity(*constant_id),
                    )
                }
                OperationSpec::BoolNot { operand, .. } => {
                    let value = resolve(operand, &local, &params)?;
                    if value.value_type != TypeExpr::Bool {
                        return Err(invalid("bool_not needs a Boolean operand"));
                    }
                    (
                        Opcode::BoolNot,
                        vec![value.reference],
                        TypeExpr::Bool,
                        Immediate::None,
                    )
                }
                OperationSpec::BoolAnd { left, right, .. } => {
                    binary(left, right, Opcode::BoolAnd, &local, &params)?
                }
                OperationSpec::BoolOr { left, right, .. } => {
                    binary(left, right, Opcode::BoolOr, &local, &params)?
                }
                OperationSpec::Equal { left, right, .. } => {
                    binary(left, right, Opcode::Equal, &local, &params)?
                }
                OperationSpec::NotEqual { left, right, .. } => {
                    binary(left, right, Opcode::NotEqual, &local, &params)?
                }
                OperationSpec::LessThan { left, right, .. } => {
                    binary(left, right, Opcode::LessThan, &local, &params)?
                }
                OperationSpec::LessEqual { left, right, .. } => {
                    binary(left, right, Opcode::LessEqual, &local, &params)?
                }
                OperationSpec::GreaterThan { left, right, .. } => {
                    binary(left, right, Opcode::GreaterThan, &local, &params)?
                }
                OperationSpec::GreaterEqual { left, right, .. } => {
                    binary(left, right, Opcode::GreaterEqual, &local, &params)?
                }
                OperationSpec::IntAddChecked { left, right, .. } => {
                    binary(left, right, Opcode::IntAddChecked, &local, &params)?
                }
                OperationSpec::IntSubChecked { left, right, .. } => {
                    binary(left, right, Opcode::IntSubChecked, &local, &params)?
                }
                OperationSpec::IntMulChecked { left, right, .. } => {
                    binary(left, right, Opcode::IntMulChecked, &local, &params)?
                }
                OperationSpec::IntNegChecked { operand, .. } => {
                    let value = resolve(operand, &local, &params)?;
                    if value.value_type != Primitive::SInt64.native() {
                        return Err(invalid("checked negation needs a signed 64 bit operand"));
                    }
                    (
                        Opcode::IntNegChecked,
                        vec![value.reference],
                        Primitive::CheckedSInt64.native(),
                        Immediate::None,
                    )
                }
                OperationSpec::ResultOk { operand, .. } => {
                    let value = resolve(operand, &local, &params)?;
                    if value.value_type != Primitive::SInt64.native() {
                        return Err(invalid("result_ok needs a signed 64 bit operand"));
                    }
                    (
                        Opcode::ResultOk,
                        vec![value.reference],
                        Primitive::CheckedSInt64.native(),
                        Immediate::None,
                    )
                }
                OperationSpec::RecordNew {
                    fields: operands, ..
                } => {
                    let expected =
                        fields.ok_or_else(|| invalid("record_new needs a record result"))?;
                    if operands.len() != expected.len() {
                        return Err(invalid("record_new field count differs from result"));
                    }
                    let values: Vec<_> = operands
                        .iter()
                        .map(|operand| resolve(operand, &local, &params))
                        .collect::<Result<_, _>>()?;
                    if values
                        .iter()
                        .zip(expected)
                        .any(|(value, field)| value.value_type != field.value_type.native())
                    {
                        return Err(invalid("record_new field type differs from result"));
                    }
                    (
                        Opcode::RecordNew,
                        values.iter().map(|value| value.reference).collect(),
                        result_type.clone(),
                        Immediate::Entity(result_definition.expect("record has definition")),
                    )
                }
            };
            entities.push((
                *id,
                8,
                EntityBodyValue::Operation(OperationBody {
                    block: block_id,
                    ordinal: u32::try_from(index)?,
                    opcode: opcode.tag(),
                    operands,
                    result_types: vec![output_type.clone()],
                    immediate,
                }),
            ));
            local.insert(
                operation.name().to_owned(),
                TypedValue {
                    reference: ValueRef::OperationResult(OperationResultRef {
                        operation: *id,
                        result_index: 0,
                    }),
                    value_type: output_type,
                },
            );
        }
        let edge = |target: &String| TargetEdge {
            target: block_lookup[target.as_str()],
            arguments: vec![],
        };
        let terminator = match &block.terminator {
            TerminatorSpec::Return { value } => {
                let selected = resolve(value, &local, &params)?;
                if selected.value_type != result_type {
                    return Err(invalid(format!(
                        "return type differs from function result in block {}",
                        block.name
                    )));
                }
                Terminator::Return(ReturnTerminator {
                    value: selected.reference,
                })
            }
            TerminatorSpec::Branch { target } => {
                Terminator::Branch(BranchTerminator { edge: edge(target) })
            }
            TerminatorSpec::CondBranch {
                condition,
                if_true,
                if_false,
            } => {
                let selected = resolve(condition, &local, &params)?;
                if selected.value_type != TypeExpr::Bool {
                    return Err(invalid("cond_branch condition must be Boolean"));
                }
                Terminator::CondBranch(CondBranchTerminator {
                    condition: selected.reference,
                    if_true: edge(if_true),
                    if_false: edge(if_false),
                })
            }
        };
        entities.push((
            block_id,
            7,
            EntityBodyValue::Block(BlockBody {
                function,
                parameters: vec![],
                operations: operation_ids[block_index].clone(),
                terminator,
                reachability: Reachability::Required,
            }),
        ));
    }
    entities.sort_by_key(|(entity, ..)| {
        allocation_order
            .iter()
            .position(|allocated| allocated == entity)
            .expect("every body has a derived identity")
    });
    let entity_count = entities.len();
    let operation_count: usize = operation_ids.iter().map(Vec::len).sum();
    let (operations, preconditions) = created(entities)?;
    let record = context.candidate(nonce, operations, preconditions)?;
    let candidate = build_candidate(&record)?;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)?
        .as_millis() as u64;
    Ok(json!({
        "contract": "sley-tools.construct-graph.v0",
        "program_id": spec.program_id,
        "title": spec.title,
        "purpose": spec.purpose,
        "candidate_record_hex": hex(&encode_candidate_record(&record)?),
        "candidate_stored_hex": hex(&candidate.stored_bytes),
        "candidate_id": hex(candidate.candidate_id.as_bytes()),
        "now_unix_millis": now,
        "function_id": hex(function.as_bytes()),
        "result_type_id": result_definition.map(|id| hex(id.as_bytes())),
        "result_fields": fields.map(|fields| fields.iter().enumerate().map(|(index, field)| json!({
            "name": field.name, "type": format!("{:?}", field.value_type),
            "member_id": hex(MemberId::from_bytes([u8::try_from(index + 1).expect("at most 16 fields"); 32]).as_bytes()),
        })).collect::<Vec<_>>()),
        "constant_ids": spec.constants.iter().zip(&constant_ids)
            .map(|(constant, id)| (constant.name.clone(), hex(id.as_bytes())))
            .collect::<BTreeMap<_, _>>(),
        "operation_ids": spec.blocks.iter().zip(&operation_ids)
            .map(|(block, ids)| (block.name.clone(), block.operations.iter().zip(ids)
                .map(|(operation, id)| (operation.name().to_owned(), hex(id.as_bytes())))
                .collect::<BTreeMap<_, _>>()))
            .collect::<BTreeMap<_, _>>(),
        "entity_count": entity_count,
        "operation_count": operation_count,
    }))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NativeTestCaseSpec {
    id: String,
    input: Vec<Value>,
    expected: Value,
    #[serde(default)]
    limits: NativeTestLimitOverrides,
}

#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct NativeTestLimitOverrides {
    memory_bytes: Option<u64>,
    wall_timeout_millis: Option<u64>,
}

/// Produces one real TestCase create candidate against an accepted graph.
/// This is an F0 construction probe, not native test admission or execution.
pub(crate) fn construct_native_test(args: &[String]) -> Result<(), DynError> {
    if args.len() != 10 || args[2].len() > 4096 {
        return Err(invalid(
            "construct-graph-native-test requires repository, recipe, bounded case JSON, six context identities, and function ID",
        ));
    }
    let spec: GraphSpec = serde_json::from_slice(&read_spec(&args[1])?)?;
    if spec.contract != "sley-tools.graph-authoring.v0" {
        return Err(invalid("unknown graph authoring contract"));
    }
    let case: NativeTestCaseSpec = serde_json::from_str(&args[2])?;
    if case.id.is_empty() || case.id.len() > 64 || case.input.len() != spec.parameters.len() {
        return Err(invalid("native test case ID or input arity is invalid"));
    }
    let ResultSpec::Primitive {
        value_type: Primitive::Bool,
    } = spec.result
    else {
        return Err(invalid(
            "native test construction probe needs a Boolean result",
        ));
    };
    let expected = case
        .expected
        .as_bool()
        .ok_or_else(|| invalid("native test expected value must be Boolean"))?;
    let inputs = spec
        .parameters
        .iter()
        .zip(&case.input)
        .map(|(parameter, value)| {
            constant_value(&ConstantSpec {
                name: parameter.name.clone(),
                value_type: parameter.value_type,
                value: value.clone(),
            })
        })
        .collect::<Result<Vec<_>, _>>()?;
    let context = Context::parse(&args[3..9])?;
    let function_id = EntityId::from_bytes(bytes32(&args[9])?);
    let repository = TransactionRepository::new(Path::new(&args[0]));
    let head = repository.accepted_head()?;
    if head.state_root().root != context.root {
        return Err(invalid("accepted graph root differs from native test base"));
    }
    if head.policy_root().root() != context.policy {
        return Err(invalid("accepted policy differs from native test base"));
    }
    let grant = head.policy_root().principal_grant(context.principal)?;
    let ceilings = grant.resource_ceilings();
    let function = head
        .objects()
        .iter()
        .find(|object| object.record().entity_id == function_id)
        .ok_or_else(|| invalid("native test target function is absent"))?;
    let EntityBodyValue::Function(body) = &function.record().body else {
        return Err(invalid("native test target is not a function"));
    };
    if body.result_type != TypeExpr::Bool || body.parameters.len() != inputs.len() {
        return Err(invalid("native test target signature differs from recipe"));
    }
    for (index, (parameter_id, input)) in body.parameters.iter().zip(&inputs).enumerate() {
        let parameter = head
            .objects()
            .iter()
            .find(|object| object.record().entity_id == *parameter_id)
            .ok_or_else(|| invalid("native test target parameter is absent"))?;
        let EntityBodyValue::Parameter(parameter_body) = &parameter.record().body else {
            return Err(invalid("native test parameter has the wrong entity kind"));
        };
        if parameter_body.owner != function_id
            || parameter_body.ordinal != u32::try_from(index)?
            || parameter_body.value_type != input.value_type
        {
            return Err(invalid("native test target parameter differs from recipe"));
        }
    }
    let nonce = CandidateNonce::from_bytes(random32()?);
    let test_id = EntityId::derive(context.workspace, nonce, 14, 0);
    let declared_limits = ResourceLimits {
        fuel: 10_000,
        memory_bytes: case.limits.memory_bytes.unwrap_or(16 * 1024 * 1024),
        output_bytes: 1_024,
        effect_count: 0,
        call_depth: 8,
        wall_timeout_millis: case.limits.wall_timeout_millis.unwrap_or(1_000),
    };
    if declared_limits.memory_bytes == 0 || declared_limits.wall_timeout_millis == 0 {
        return Err(invalid(
            "native test memory and wall limits must be positive",
        ));
    }
    if declared_limits.fuel > ceilings.max_fuel
        || declared_limits.memory_bytes > ceilings.max_memory_bytes
        || declared_limits.output_bytes > ceilings.max_output_bytes
        || declared_limits.effect_count > ceilings.max_effect_count
    {
        return Err(invalid(
            "native test declared limits exceed accepted principal grant",
        ));
    }
    let test = EntityBodyValue::TestCase(TestCaseBody {
        target: function_id,
        inputs,
        effect_environment: EffectEnvironment::Replay(vec![]),
        expected: ExpectedOutcome::Value(ConstValue {
            value_type: TypeExpr::Bool,
            data: ConstData::Bool(expected),
        }),
        observations: vec![],
        resource_limits: declared_limits,
    });
    let (operations, preconditions) = created(vec![(test_id, 14, test)])?;
    let record = context.candidate(nonce, operations, preconditions)?;
    let candidate = build_candidate(&record)?;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)?
        .as_millis() as u64;
    let validation_context = CandidateValidationContext::new(
        head.transaction_id(),
        head.state_root(),
        head.objects(),
        head.tombstoned_entities(),
        head.policy_root(),
        context.principal,
        &[],
        now,
        CandidateValidationLimits::full_v1(),
    )?;
    let static_output = validate_candidate_bytes(&validation_context, &candidate.stored_bytes)?;
    if !static_output.is_valid() {
        return Err(invalid(
            "native TestCase candidate failed local core validation",
        ));
    }
    let native_plan = native_test_plan(
        &static_output,
        &NativePlanInputs {
            base_transaction_id: head.transaction_id(),
            base_state: head.state_root(),
            base_objects: head.objects(),
            policy: head.policy_root(),
            capability_summary: validation_context.capability_summary_digest(),
            limits: CandidateValidationLimits::full_v1(),
            implementation_limits: NativeImplementationLimits::HARD_MAXIMA,
            aggregate: NativeAggregateLimits::HARD_MAXIMA,
        },
    )?;
    if native_plan.selected().len() != 1 || native_plan.selected()[0].test_entity != test_id {
        return Err(invalid("native plan did not select the created TestCase"));
    }
    println!(
        "{}",
        json!({
            "contract": "sley-tools.graph-native-test-construction-probe.v0",
            "candidate_record_hex": hex(&encode_candidate_record(&record)?),
            "candidate_stored_hex": hex(&candidate.stored_bytes),
            "candidate_id": hex(candidate.candidate_id.as_bytes()),
            "test_entity_id": hex(test_id.as_bytes()),
            "target_function_id": hex(function_id.as_bytes()),
            "native_execution_profile_id": hex(sley_vm::native_execution::profile_id().as_bytes()),
            "native_plan_id": hex(native_plan.plan_id().as_bytes()),
            "native_plan_selected_test_count": native_plan.selected().len(),
            "native_plan_preflight": "CORE_LIBRARY_ONLY_NO_EXECUTION",
            "declared_limits": {
                "fuel": declared_limits.fuel,
                "memory_bytes": declared_limits.memory_bytes,
                "output_bytes": declared_limits.output_bytes,
                "effect_count": declared_limits.effect_count,
                "call_depth": declared_limits.call_depth,
                "wall_timeout_millis": declared_limits.wall_timeout_millis,
            },
            "grant_ceilings": {
                "max_fuel": ceilings.max_fuel,
                "max_memory_bytes": ceilings.max_memory_bytes,
                "max_output_bytes": ceilings.max_output_bytes,
                "max_effect_count": ceilings.max_effect_count,
            },
            "case_id": case.id,
            "now_unix_millis": now,
        })
    );
    Ok(())
}

pub(crate) fn edit_constant(args: &[String]) -> Result<(), DynError> {
    if args.len() != 11 {
        return Err(invalid(
            "edit-graph-constant requires repository, recipe, label, JSON value, six context identities, and constant entity ID",
        ));
    }
    if args[3].len() > 128 {
        return Err(invalid("graph constant value exceeds 128 bytes"));
    }
    let repository = TransactionRepository::new(Path::new(&args[0]));
    let mut spec_bytes = Vec::new();
    File::open(&args[1])?
        .take(65_537)
        .read_to_end(&mut spec_bytes)?;
    if spec_bytes.len() > 65_536 {
        return Err(invalid("graph spec exceeds 64 KiB"));
    }
    let spec: GraphSpec = serde_json::from_slice(&spec_bytes)?;
    if spec.contract != "sley-tools.graph-authoring.v0" {
        return Err(invalid("unknown graph authoring contract"));
    }
    let constant = spec
        .constants
        .iter()
        .find(|item| item.name == args[2])
        .ok_or_else(|| invalid("graph constant label is absent"))?;
    let new_value: Value = serde_json::from_str(&args[3])?;
    let old_native = constant_value(constant)?;
    let new_native = constant_value(&ConstantSpec {
        name: constant.name.clone(),
        value_type: constant.value_type,
        value: new_value.clone(),
    })?;
    if old_native == new_native {
        return Err(invalid("new graph constant equals its accepted value"));
    }
    let context = Context::parse(&args[4..10])?;
    let entity = EntityId::from_bytes(bytes32(&args[10])?);
    let head = repository.accepted_head()?;
    if head.state_root().root != context.root {
        return Err(invalid("accepted graph root differs from edit base"));
    }
    let accepted = head
        .objects()
        .iter()
        .find(|object| object.record().entity_id == entity)
        .ok_or_else(|| invalid("graph constant entity is absent from accepted root"))?;
    if accepted.record().body != EntityBodyValue::Constant(ConstantBody { value: old_native }) {
        return Err(invalid("accepted constant differs from bound graph recipe"));
    }
    let old_object = accepted.object_id();
    let body = EntityBodyValue::Constant(ConstantBody { value: new_native });
    let record = context.candidate(
        CandidateNonce::from_bytes(random32()?),
        vec![MutationOperation {
            ordinal: 0,
            class: MutationClass::ReplaceEntityVersion,
            target_kind: 9,
            target_entity: entity,
            field_tag: None,
            payload: MutationPayload::ReplaceEntityVersion(body.clone()),
            precondition_ordinal: 0,
        }],
        vec![BoundPrecondition {
            operation_ordinal: 0,
            requirement: PreimageRequirement::ExactEntityVersion,
            payload: PreconditionPayload::ExactEntityVersion(ExactEntityVersion {
                entity_id: entity,
                object_id: old_object,
            }),
        }],
    )?;
    let candidate = build_candidate(&record)?;
    let new_object = build_entity_object(
        context.epoch,
        &EntityObjectRecord {
            entity_id: entity,
            body,
            label: None,
            semantic_fingerprint: None,
        },
    )?;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)?
        .as_millis() as u64;
    println!(
        "{}",
        json!({
            "contract": "sley-tools.edit-graph-constant.v0",
            "candidate_record_hex": hex(&encode_candidate_record(&record)?),
            "candidate_stored_hex": hex(&candidate.stored_bytes),
            "candidate_id": hex(candidate.candidate_id.as_bytes()),
            "now_unix_millis": now,
            "constant_name": constant.name,
            "constant_id": hex(entity.as_bytes()),
            "old_object_id": hex(old_object.as_bytes()),
            "new_object_id": hex(new_object.object_id().as_bytes()),
            "old_value": constant.value,
            "new_value": new_value,
        })
    );
    Ok(())
}

fn editable_opcode(name: &str) -> Option<(Opcode, u8)> {
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

fn operand_slot(operation: &Value, slot: &str) -> Result<usize, DynError> {
    let opcode = operation["op"]
        .as_str()
        .ok_or_else(|| invalid("graph operation has no opcode"))?;
    let unary = matches!(opcode, "int_neg_checked" | "result_ok");
    let binary = editable_opcode(opcode).is_some() && !unary;
    match (slot, unary, binary) {
        ("operand", true, _) => Ok(0),
        ("left", _, true) => Ok(0),
        ("right", _, true) => Ok(1),
        _ => Err(invalid(
            "graph operation has no editable operand at that slot",
        )),
    }
}

fn edit_reference(
    reference: &str,
    spec: &GraphSpec,
    block: &BlockSpec,
    ordinal: usize,
    function: &FunctionBody,
    accepted_block: &BlockBody,
) -> Result<ValueRef, DynError> {
    if let Some(name) = reference.strip_prefix("param.") {
        let position = spec
            .parameters
            .iter()
            .position(|parameter| parameter.name == name)
            .ok_or_else(|| invalid("operand references an unknown parameter"))?;
        return Ok(ValueRef::Parameter(
            *function
                .parameters
                .get(position)
                .ok_or_else(|| invalid("accepted function omits the bound parameter"))?,
        ));
    }
    if let Some(name) = reference.strip_prefix("op.") {
        let position = block.operations[..ordinal]
            .iter()
            .position(|operation| operation.name() == name)
            .ok_or_else(|| invalid("operand must reference an earlier operation in its block"))?;
        return Ok(ValueRef::OperationResult(OperationResultRef {
            operation: *accepted_block
                .operations
                .get(position)
                .ok_or_else(|| invalid("accepted block omits the bound operation"))?,
            result_index: 0,
        }));
    }
    Err(invalid(
        "operand must reference param.NAME or an earlier op.NAME",
    ))
}

pub(crate) fn edit_operand(args: &[String]) -> Result<(), DynError> {
    if args.len() != 13 {
        return Err(invalid(
            "edit-graph-operand requires repository, recipe, block, operation, slot, reference, six context identities, and operation entity ID",
        ));
    }
    let spec_bytes = read_spec(&args[1])?;
    let spec: GraphSpec = serde_json::from_slice(&spec_bytes)?;
    if spec.contract != "sley-tools.graph-authoring.v0" {
        return Err(invalid("unknown graph authoring contract"));
    }
    let block_index = spec
        .blocks
        .iter()
        .position(|block| block.name == args[2])
        .ok_or_else(|| invalid("graph block label is absent"))?;
    let block = &spec.blocks[block_index];
    let (ordinal, selected) = block
        .operations
        .iter()
        .enumerate()
        .find(|(_, operation)| operation.name() == args[3])
        .ok_or_else(|| invalid("graph operation label is absent from selected block"))?;
    let selected_json = serde_json::to_value(selected)?;
    let slot = operand_slot(&selected_json, &args[4])?;
    let old = selected_json[&args[4]]
        .as_str()
        .ok_or_else(|| invalid("bound graph operation omits its operand"))?;
    if old == args[5] {
        return Err(invalid("graph operand edit is unchanged"));
    }
    let context = Context::parse(&args[6..12])?;
    let entity = EntityId::from_bytes(bytes32(&args[12])?);
    let repository = TransactionRepository::new(Path::new(&args[0]));
    let head = repository.accepted_head()?;
    if head.state_root().root != context.root {
        return Err(invalid("accepted graph root differs from edit base"));
    }
    let accepted = head
        .objects()
        .iter()
        .find(|object| object.record().entity_id == entity)
        .ok_or_else(|| invalid("graph operation entity is absent from accepted root"))?;
    let EntityBodyValue::Operation(old_body) = &accepted.record().body else {
        return Err(invalid("selected graph entity is not an operation"));
    };
    let accepted_block = head
        .objects()
        .iter()
        .find(|object| object.record().entity_id == old_body.block)
        .ok_or_else(|| invalid("operation owner block is absent"))?;
    let EntityBodyValue::Block(block_body) = &accepted_block.record().body else {
        return Err(invalid("operation owner is not a block"));
    };
    let accepted_function = head
        .objects()
        .iter()
        .find(|object| object.record().entity_id == block_body.function)
        .ok_or_else(|| invalid("operation owner function is absent"))?;
    let EntityBodyValue::Function(function_body) = &accepted_function.record().body else {
        return Err(invalid("operation owner is not a function"));
    };
    if function_body.blocks.len() != spec.blocks.len()
        || function_body.blocks[block_index] != old_body.block
        || function_body.parameters.len() != spec.parameters.len()
        || block_body.operations.len() != block.operations.len()
        || block_body.operations[ordinal] != entity
        || old_body.ordinal != u32::try_from(ordinal)?
        || old_body.operands.len() <= slot
        || old_body.operands[slot]
            != edit_reference(old, &spec, block, ordinal, function_body, block_body)?
    {
        return Err(invalid(
            "accepted operation differs from bound graph recipe",
        ));
    }
    let replacement = edit_reference(&args[5], &spec, block, ordinal, function_body, block_body)?;
    let old_object = accepted.object_id();
    let mut new_body = old_body.clone();
    new_body.operands[slot] = replacement;
    let body = EntityBodyValue::Operation(new_body);
    let record = context.candidate(
        CandidateNonce::from_bytes(random32()?),
        vec![MutationOperation {
            ordinal: 0,
            class: MutationClass::ReplaceEntityVersion,
            target_kind: 8,
            target_entity: entity,
            field_tag: None,
            payload: MutationPayload::ReplaceEntityVersion(body.clone()),
            precondition_ordinal: 0,
        }],
        vec![BoundPrecondition {
            operation_ordinal: 0,
            requirement: PreimageRequirement::ExactEntityVersion,
            payload: PreconditionPayload::ExactEntityVersion(ExactEntityVersion {
                entity_id: entity,
                object_id: old_object,
            }),
        }],
    )?;
    let candidate = build_candidate(&record)?;
    let new_object = build_entity_object(
        context.epoch,
        &EntityObjectRecord {
            entity_id: entity,
            body,
            label: None,
            semantic_fingerprint: None,
        },
    )?;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)?
        .as_millis() as u64;
    println!(
        "{}",
        json!({
            "contract": "sley-tools.edit-graph-operand.v0",
            "candidate_record_hex": hex(&encode_candidate_record(&record)?),
            "candidate_stored_hex": hex(&candidate.stored_bytes),
            "candidate_id": hex(candidate.candidate_id.as_bytes()),
            "now_unix_millis": now,
            "block_name": block.name, "operation_name": selected.name(),
            "operand_slot": args[4], "old_reference": old, "new_reference": args[5],
            "operation_id": hex(entity.as_bytes()),
            "old_object_id": hex(old_object.as_bytes()),
            "new_object_id": hex(new_object.object_id().as_bytes()),
        })
    );
    Ok(())
}

pub(crate) fn edit_opcode(args: &[String]) -> Result<(), DynError> {
    if args.len() != 12 {
        return Err(invalid(
            "edit-graph-opcode requires repository, recipe, block, operation, new opcode, six context identities, and operation entity ID",
        ));
    }
    let spec_bytes = read_spec(&args[1])?;
    let spec: GraphSpec = serde_json::from_slice(&spec_bytes)?;
    if spec.contract != "sley-tools.graph-authoring.v0" {
        return Err(invalid("unknown graph authoring contract"));
    }
    let block = spec
        .blocks
        .iter()
        .find(|block| block.name == args[2])
        .ok_or_else(|| invalid("graph block label is absent"))?;
    let (ordinal, selected) = block
        .operations
        .iter()
        .enumerate()
        .find(|(_, operation)| operation.name() == args[3])
        .ok_or_else(|| invalid("graph operation label is absent from selected block"))?;
    let selected_json = serde_json::to_value(selected)?;
    let old_name = selected_json["op"]
        .as_str()
        .ok_or_else(|| invalid("graph operation has no opcode"))?;
    let (old_opcode, old_family) = editable_opcode(old_name)
        .ok_or_else(|| invalid("graph operation opcode has no targeted edit profile"))?;
    let (new_opcode, new_family) = editable_opcode(&args[4])
        .ok_or_else(|| invalid("proposed graph opcode has no targeted edit profile"))?;
    if old_family != new_family || old_opcode == new_opcode {
        return Err(invalid(
            "graph opcode edit is unchanged or crosses operand/result profiles",
        ));
    }
    let context = Context::parse(&args[5..11])?;
    let entity = EntityId::from_bytes(bytes32(&args[11])?);
    let repository = TransactionRepository::new(Path::new(&args[0]));
    let head = repository.accepted_head()?;
    if head.state_root().root != context.root {
        return Err(invalid("accepted graph root differs from edit base"));
    }
    let accepted = head
        .objects()
        .iter()
        .find(|object| object.record().entity_id == entity)
        .ok_or_else(|| invalid("graph operation entity is absent from accepted root"))?;
    let EntityBodyValue::Operation(old_body) = &accepted.record().body else {
        return Err(invalid("selected graph entity is not an operation"));
    };
    if old_body.opcode != old_opcode.tag() || old_body.ordinal != u32::try_from(ordinal)? {
        return Err(invalid(
            "accepted operation differs from bound graph recipe",
        ));
    }
    let old_object = accepted.object_id();
    let mut new_body = old_body.clone();
    new_body.opcode = new_opcode.tag();
    let body = EntityBodyValue::Operation(new_body);
    let record = context.candidate(
        CandidateNonce::from_bytes(random32()?),
        vec![MutationOperation {
            ordinal: 0,
            class: MutationClass::ReplaceEntityVersion,
            target_kind: 8,
            target_entity: entity,
            field_tag: None,
            payload: MutationPayload::ReplaceEntityVersion(body.clone()),
            precondition_ordinal: 0,
        }],
        vec![BoundPrecondition {
            operation_ordinal: 0,
            requirement: PreimageRequirement::ExactEntityVersion,
            payload: PreconditionPayload::ExactEntityVersion(ExactEntityVersion {
                entity_id: entity,
                object_id: old_object,
            }),
        }],
    )?;
    let candidate = build_candidate(&record)?;
    let new_object = build_entity_object(
        context.epoch,
        &EntityObjectRecord {
            entity_id: entity,
            body,
            label: None,
            semantic_fingerprint: None,
        },
    )?;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)?
        .as_millis() as u64;
    println!(
        "{}",
        json!({
            "contract": "sley-tools.edit-graph-opcode.v0",
            "candidate_record_hex": hex(&encode_candidate_record(&record)?),
            "candidate_stored_hex": hex(&candidate.stored_bytes),
            "candidate_id": hex(candidate.candidate_id.as_bytes()),
            "now_unix_millis": now,
            "block_name": block.name, "operation_name": selected.name(),
            "operation_id": hex(entity.as_bytes()),
            "old_object_id": hex(old_object.as_bytes()),
            "new_object_id": hex(new_object.object_id().as_bytes()),
            "old_opcode": old_name, "new_opcode": args[4],
        })
    );
    Ok(())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct BranchTargetEdit {
    target: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CondBranchTargetEdit {
    if_true: String,
    if_false: String,
}

pub(crate) fn edit_branch(args: &[String]) -> Result<(), DynError> {
    if args.len() != 11 {
        return Err(invalid(
            "edit-graph-branch requires repository, recipe, block, target JSON, six context identities, and function entity ID",
        ));
    }
    if args[3].len() > 256 {
        return Err(invalid("graph branch target JSON exceeds 256 bytes"));
    }
    let spec_bytes = read_spec(&args[1])?;
    let spec: GraphSpec = serde_json::from_slice(&spec_bytes)?;
    if spec.contract != "sley-tools.graph-authoring.v0" {
        return Err(invalid("unknown graph authoring contract"));
    }
    let block_index = spec
        .blocks
        .iter()
        .position(|block| block.name == args[2])
        .ok_or_else(|| invalid("graph branch block label is absent"))?;
    let block = &spec.blocks[block_index];
    let mut updated_spec_json: Value = serde_json::from_slice(&spec_bytes)?;
    let (old_targets, new_targets) = match &block.terminator {
        TerminatorSpec::Branch { target } => {
            let proposed: BranchTargetEdit = serde_json::from_str(&args[3])?;
            if proposed.target == *target {
                return Err(invalid("proposed graph branch target is unchanged"));
            }
            updated_spec_json["blocks"][block_index]["terminator"]["target"] =
                json!(proposed.target);
            (
                json!({"target": target}),
                json!({"target": proposed.target}),
            )
        }
        TerminatorSpec::CondBranch {
            if_true, if_false, ..
        } => {
            let proposed: CondBranchTargetEdit = serde_json::from_str(&args[3])?;
            if proposed.if_true == *if_true && proposed.if_false == *if_false {
                return Err(invalid("proposed graph branch targets are unchanged"));
            }
            updated_spec_json["blocks"][block_index]["terminator"]["if_true"] =
                json!(proposed.if_true);
            updated_spec_json["blocks"][block_index]["terminator"]["if_false"] =
                json!(proposed.if_false);
            (
                json!({"if_true": if_true, "if_false": if_false}),
                json!({"if_true": proposed.if_true, "if_false": proposed.if_false}),
            )
        }
        TerminatorSpec::Return { .. } => {
            return Err(invalid("selected graph block has no branch target"));
        }
    };
    let updated_spec: GraphSpec = serde_json::from_value(updated_spec_json)?;
    check_reachability(&updated_spec)?;

    let context = Context::parse(&args[4..10])?;
    let function_entity = EntityId::from_bytes(bytes32(&args[10])?);
    let repository = TransactionRepository::new(Path::new(&args[0]));
    let head = repository.accepted_head()?;
    if head.state_root().root != context.root {
        return Err(invalid("accepted graph root differs from edit base"));
    }
    let function = head
        .objects()
        .iter()
        .find(|object| object.record().entity_id == function_entity)
        .ok_or_else(|| invalid("graph branch owner function is absent"))?;
    let EntityBodyValue::Function(function_body) = &function.record().body else {
        return Err(invalid("graph branch owner is not a function"));
    };
    if function_body.blocks.len() != spec.blocks.len() {
        return Err(invalid("accepted graph block differs from bound recipe"));
    }
    let entity = function_body.blocks[block_index];
    let accepted = head
        .objects()
        .iter()
        .find(|object| object.record().entity_id == entity)
        .ok_or_else(|| invalid("graph branch block is absent from accepted root"))?;
    let EntityBodyValue::Block(old_body) = &accepted.record().body else {
        return Err(invalid("selected graph entity is not a block"));
    };
    if old_body.function != function_entity || old_body.operations.len() != block.operations.len() {
        return Err(invalid("accepted graph block differs from bound recipe"));
    }
    let target_id = |name: &str| -> Result<EntityId, DynError> {
        let index = spec
            .blocks
            .iter()
            .position(|block| block.name == name)
            .ok_or_else(|| invalid(format!("unknown graph branch target {name:?}")))?;
        Ok(function_body.blocks[index])
    };
    let mut new_body = old_body.clone();
    match (&block.terminator, &mut new_body.terminator) {
        (TerminatorSpec::Branch { target }, Terminator::Branch(branch)) => {
            if branch.edge.target != target_id(target)? || !branch.edge.arguments.is_empty() {
                return Err(invalid("accepted graph branch differs from bound recipe"));
            }
            branch.edge.target = target_id(
                new_targets["target"]
                    .as_str()
                    .ok_or_else(|| invalid("new graph branch target is missing"))?,
            )?;
        }
        (
            TerminatorSpec::CondBranch {
                if_true, if_false, ..
            },
            Terminator::CondBranch(branch),
        ) => {
            if branch.if_true.target != target_id(if_true)?
                || branch.if_false.target != target_id(if_false)?
                || !branch.if_true.arguments.is_empty()
                || !branch.if_false.arguments.is_empty()
            {
                return Err(invalid("accepted graph branch differs from bound recipe"));
            }
            branch.if_true.target = target_id(
                new_targets["if_true"]
                    .as_str()
                    .ok_or_else(|| invalid("new true target is missing"))?,
            )?;
            branch.if_false.target = target_id(
                new_targets["if_false"]
                    .as_str()
                    .ok_or_else(|| invalid("new false target is missing"))?,
            )?;
        }
        _ => {
            return Err(invalid(
                "accepted graph terminator differs from bound recipe",
            ));
        }
    }
    let old_object = accepted.object_id();
    let body = EntityBodyValue::Block(new_body);
    let record = context.candidate(
        CandidateNonce::from_bytes(random32()?),
        vec![MutationOperation {
            ordinal: 0,
            class: MutationClass::ReplaceEntityVersion,
            target_kind: 7,
            target_entity: entity,
            field_tag: None,
            payload: MutationPayload::ReplaceEntityVersion(body.clone()),
            precondition_ordinal: 0,
        }],
        vec![BoundPrecondition {
            operation_ordinal: 0,
            requirement: PreimageRequirement::ExactEntityVersion,
            payload: PreconditionPayload::ExactEntityVersion(ExactEntityVersion {
                entity_id: entity,
                object_id: old_object,
            }),
        }],
    )?;
    let candidate = build_candidate(&record)?;
    let new_object = build_entity_object(
        context.epoch,
        &EntityObjectRecord {
            entity_id: entity,
            body,
            label: None,
            semantic_fingerprint: None,
        },
    )?;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)?
        .as_millis() as u64;
    println!(
        "{}",
        json!({
            "contract": "sley-tools.edit-graph-branch.v0",
            "candidate_record_hex": hex(&encode_candidate_record(&record)?),
            "candidate_stored_hex": hex(&candidate.stored_bytes),
            "candidate_id": hex(candidate.candidate_id.as_bytes()),
            "now_unix_millis": now,
            "block_name": block.name,
            "block_id": hex(entity.as_bytes()),
            "old_object_id": hex(old_object.as_bytes()),
            "new_object_id": hex(new_object.object_id().as_bytes()),
            "old_targets": old_targets,
            "new_targets": new_targets,
        })
    );
    Ok(())
}

pub(crate) fn run(args: &[String]) -> Result<(), DynError> {
    println!("{}", native_result(args)?);
    Ok(())
}

pub(crate) fn serve(args: &[String]) -> Result<(), DynError> {
    if args.len() != 5 {
        return Err(invalid(
            "serve-graph requires repository root function spec path and bound digest",
        ));
    }
    let spec_bytes = read_spec(&args[3])?;
    if format!("{:x}", Sha256::digest(&spec_bytes)) != args[4] {
        return Err(invalid("graph worker recipe differs from bound digest"));
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
            return Err(invalid(
                "graph worker request exceeds 4 KiB or lacks newline",
            ));
        }
        let request: Value = serde_json::from_slice(&line)?;
        let object = request
            .as_object()
            .ok_or_else(|| invalid("graph worker request is not an object"))?;
        if object.len() != 2 || !object.contains_key("id") || !object.contains_key("input") {
            return Err(invalid("graph worker request has unexpected fields"));
        }
        let id = request["id"]
            .as_u64()
            .ok_or_else(|| invalid("graph worker request has no ID"))?;
        if !request["input"].is_array() {
            return Err(invalid("graph worker input is not an array"));
        }
        let mut run_args = args[..4].to_vec();
        run_args.push(serde_json::to_string(&request["input"])?);
        let response = match native_result_with_spec(&run_args, &spec_bytes) {
            Ok(value) => json!({"id": id, "ok": true, "result": value}),
            Err(error) => {
                let message = error.to_string();
                let code = if message == "accepted root differs from requested root" {
                    "NATIVE_STALE_ROOT"
                } else {
                    "NATIVE_EXECUTION_FAILED"
                };
                json!({"id": id, "ok": false, "code": code, "message": message})
            }
        };
        let encoded = serde_json::to_vec(&response)?;
        if encoded.len() > 65_536 {
            return Err(invalid("graph worker response exceeds 64 KiB"));
        }
        writer.write_all(&encoded)?;
        writer.write_all(b"\n")?;
        writer.flush()?;
    }
}

fn native_result(args: &[String]) -> Result<Value, DynError> {
    if args.len() != 5 {
        return Err(invalid(
            "run-graph requires repository root function spec path and JSON input array",
        ));
    }
    let spec_bytes = read_spec(&args[3])?;
    native_result_with_spec(args, &spec_bytes)
}

fn read_spec(path: &str) -> Result<Vec<u8>, DynError> {
    let mut spec_bytes = Vec::new();
    File::open(path)?
        .take(65_537)
        .read_to_end(&mut spec_bytes)?;
    if spec_bytes.len() > 65_536 {
        return Err(invalid("graph spec exceeds 64 KiB"));
    }
    Ok(spec_bytes)
}

fn native_result_with_spec(args: &[String], spec_bytes: &[u8]) -> Result<Value, DynError> {
    if args.len() != 5 {
        return Err(invalid(
            "run-graph requires repository root function spec path and JSON input array",
        ));
    }
    if args[4].len() > 4_096 {
        return Err(invalid("graph input array exceeds 4 KiB"));
    }
    let repository = TransactionRepository::new(Path::new(&args[0]));
    let expected_root = StateRoot::from_bytes(bytes32(&args[1])?);
    let function = EntityId::from_bytes(bytes32(&args[2])?);
    let spec: GraphSpec = serde_json::from_slice(spec_bytes)?;
    if spec.contract != "sley-tools.graph-authoring.v0" {
        return Err(invalid("unknown graph authoring contract"));
    }
    let inputs: Vec<Value> = serde_json::from_str(&args[4])?;
    if inputs.len() != spec.parameters.len() {
        return Err(invalid("input count differs from graph spec"));
    }
    let native_inputs: Vec<_> = spec
        .parameters
        .iter()
        .zip(&inputs)
        .map(|(parameter, value)| {
            constant_value(&ConstantSpec {
                name: parameter.name.clone(),
                value_type: parameter.value_type,
                value: value.clone(),
            })
        })
        .collect::<Result<_, _>>()?;
    let revision = repository.accepted_head()?.into_verified_revision();
    let complete = CompleteRootRequest::extract(&revision)?;
    if complete.root() != expected_root {
        return Err(invalid("accepted root differs from requested root"));
    }
    let entities = complete.entities();
    let graph = entities
        .functions
        .iter()
        .find(|item| item.entity_id == function)
        .ok_or_else(|| invalid("graph function is absent from accepted root"))?;
    if graph.parameters.len() != spec.parameters.len() {
        return Err(invalid(
            "accepted function signature differs from graph spec",
        ));
    }
    for (expected, identity) in spec.parameters.iter().zip(&graph.parameters) {
        let actual = entities
            .parameters
            .iter()
            .find(|item| item.entity_id == *identity)
            .ok_or_else(|| invalid("accepted function parameter is absent"))?;
        if actual.value_type != expected.value_type.native() {
            return Err(invalid(
                "accepted function parameter type differs from graph spec",
            ));
        }
    }
    let fields = match &spec.result {
        ResultSpec::Primitive { value_type } => {
            if graph.result_type != value_type.native() {
                return Err(invalid("accepted result type differs from graph spec"));
            }
            None
        }
        ResultSpec::Record { fields } => {
            let TypeExpr::Named(named) = &graph.result_type else {
                return Err(invalid("accepted result is not a named record"));
            };
            let definition = entities
                .type_definitions
                .iter()
                .find(|item| item.entity_id == named.definition)
                .ok_or_else(|| invalid("accepted result definition is absent"))?;
            let TypeDefForm::Record(actual_fields) = &definition.form else {
                return Err(invalid("accepted result definition is not a record"));
            };
            if actual_fields.len() != fields.len()
                || actual_fields.iter().zip(fields).enumerate().any(
                    |(index, (actual, expected))| {
                        actual.member_id
                            != MemberId::from_bytes(
                                [u8::try_from(index + 1).expect("bounded field index"); 32],
                            )
                            || actual.value_type != expected.value_type.native()
                    },
                )
            {
                return Err(invalid("accepted record fields differ from graph spec"));
            }
            Some(fields)
        }
    };
    let types = TypeEnvironment::new(entities.type_definitions.clone())?;
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
            inputs: native_inputs,
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
        return Err(invalid(format!(
            "native graph execution ended with {:?}",
            outcome.termination
        )));
    };
    if output.value_type != graph.result_type {
        return Err(invalid("native result type differs from accepted function"));
    }
    let result = if let Some(fields) = fields {
        let ConstData::Record(record) = &output.data else {
            return Err(invalid("native result is not a record"));
        };
        let TypeExpr::Named(named) = &graph.result_type else {
            return Err(invalid("accepted result is not a named record"));
        };
        if record.definition != named.definition {
            return Err(invalid("native result record definition differs"));
        }
        if record.fields.len() != fields.len() {
            return Err(invalid("native record field count differs"));
        }
        let mut values = serde_json::Map::new();
        for (index, (field, expected)) in record.fields.iter().zip(fields).enumerate() {
            if field.member_id
                != MemberId::from_bytes([u8::try_from(index + 1).expect("bounded field index"); 32])
            {
                return Err(invalid("native result field identity differs"));
            }
            values.insert(
                expected.name.clone(),
                json_value(&field.value, expected.value_type)?,
            );
        }
        Value::Object(values)
    } else {
        let ResultSpec::Primitive { value_type } = spec.result else {
            unreachable!()
        };
        json_value(&output, value_type)?
    };
    Ok(json!({
        "contract": "sley-tools.run-graph.v0", "root": hex(complete.root().as_bytes()),
        "function_id": hex(function.as_bytes()), "profile": "extended_v1",
        "result": result, "observation_id": hex(outcome.observation_id.as_bytes()),
        "value_hash": hex(sley_ssmc::fingerprint::hash_validated_value(complete.schema_epoch_id(), &output)?.as_bytes()),
        "instruction_count": outcome.instruction_count,
    }))
}

fn json_value(value: &ConstValue, expected: Primitive) -> Result<Value, DynError> {
    if value.value_type != expected.native() {
        return Err(invalid("native value type differs from graph spec"));
    }
    match (&value.data, expected) {
        (ConstData::Bool(bit), Primitive::Bool) => Ok(json!(bit)),
        (ConstData::UInt(integer), Primitive::UInt32) => Ok(json!(integer.to_string())),
        (ConstData::SInt(integer), Primitive::SInt64) => Ok(json!(integer.to_string())),
        (ConstData::Result(ResultConst::Ok(inner)), Primitive::CheckedSInt64)
            if inner.value_type == Primitive::SInt64.native() =>
        {
            let ConstData::SInt(integer) = &inner.data else {
                return Err(invalid("checked native success is not signed integer"));
            };
            Ok(json!({"ok": integer.to_string()}))
        }
        (ConstData::Result(ResultConst::Err(inner)), Primitive::CheckedSInt64)
            if inner.value_type == TypeExpr::BuiltinFailure(BuiltinFailureKind::Arithmetic) =>
        {
            let ConstData::BuiltinFailure(failure) = &inner.data else {
                return Err(invalid("checked native error is not arithmetic failure"));
            };
            if failure.kind != BuiltinFailureKind::Arithmetic || failure.code != 1 {
                return Err(invalid(
                    "checked native arithmetic failure code is unavailable",
                ));
            }
            Ok(json!({"error": {"code": failure.code}}))
        }
        _ => Err(invalid("native value data differs from graph spec")),
    }
}
