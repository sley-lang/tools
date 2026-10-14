//! Public local project lifecycle for a typed native graph recipe.

use std::collections::BTreeSet;

use super::*;

const MAX_GRAPH_SPEC: u64 = 65_536;
const MAX_GRAPH_CASES: u64 = 1_048_576;

pub(super) fn read_regular(path: &Path, limit: u64) -> Result<Vec<u8>, CliFailure> {
    let metadata = fs::symlink_metadata(path).map_err(|error| {
        CliFailure::invalid(format!("cannot inspect {}: {error}", path.display()))
    })?;
    if !metadata.file_type().is_file() || metadata.len() > limit {
        return Err(CliFailure::invalid(format!(
            "{} must be a regular file of at most {limit} bytes",
            path.display()
        )));
    }
    fs::read(path)
        .map_err(|error| CliFailure::invalid(format!("cannot read {}: {error}", path.display())))
}

fn validate_cases(cases: &Value) -> Result<&Vec<Value>, CliFailure> {
    let object = cases
        .as_object()
        .ok_or_else(|| CliFailure::invalid("graph test fixture must be an object"))?;
    if object.len() != 2 || cases["contract"] != "sley-tools.graph-cases.v0" {
        return Err(CliFailure::invalid(
            "graph test fixture contract or fields differ",
        ));
    }
    let rows = cases["cases"]
        .as_array()
        .ok_or_else(|| CliFailure::invalid("graph test cases must be an array"))?;
    if rows.is_empty() || rows.len() > 256 {
        return Err(CliFailure::invalid("graph test fixture needs 1..256 cases"));
    }
    let mut names = BTreeSet::new();
    for row in rows {
        let item = row
            .as_object()
            .ok_or_else(|| CliFailure::invalid("graph case must be an object"))?;
        if item.len() != 3
            || !item.contains_key("id")
            || !item.contains_key("input")
            || !item.contains_key("expected")
        {
            return Err(CliFailure::invalid(
                "graph case needs exactly id, input, and expected",
            ));
        }
        let name = row["id"]
            .as_str()
            .ok_or_else(|| CliFailure::invalid("graph case id must be text"))?;
        if name.is_empty() || name.len() > 64 || !names.insert(name) {
            return Err(CliFailure::invalid(
                "graph case id is empty, duplicate, or too long",
            ));
        }
        if !row["input"].is_array() {
            return Err(CliFailure::invalid("graph case input must be an array"));
        }
    }
    Ok(rows)
}

fn input_types(spec: &Value, input: &Value) -> Result<(), CliFailure> {
    let parameters = spec["parameters"]
        .as_array()
        .ok_or_else(|| CliFailure::invalid("graph recipe has no parameters"))?;
    let values = input
        .as_array()
        .ok_or_else(|| CliFailure::invalid("graph input must be an array"))?;
    if values.len() != parameters.len() {
        return Err(CliFailure::invalid(
            "graph input count differs from function signature",
        ));
    }
    for (index, (parameter, value)) in parameters.iter().zip(values).enumerate() {
        let kind = parameter["value_type"].as_str();
        let valid = kind != Some("CheckedSInt64") && matches_type(value, kind);
        if !valid {
            return Err(CliFailure::invalid(format!(
                "graph input {index} has the wrong type or noncanonical integer text"
            )));
        }
    }
    Ok(())
}

fn matches_type(value: &Value, kind: Option<&str>) -> bool {
    match kind {
        Some("Bool") => value.is_boolean(),
        Some("UInt32") => value
            .as_str()
            .and_then(|text| {
                text.parse::<u32>()
                    .ok()
                    .map(|number| number.to_string() == text)
            })
            .unwrap_or(false),
        Some("SInt64") => value
            .as_str()
            .and_then(|text| {
                text.parse::<i64>()
                    .ok()
                    .map(|number| number.to_string() == text)
            })
            .unwrap_or(false),
        Some("CheckedSInt64") => value.as_object().is_some_and(|result| {
            if result.len() != 1 {
                return false;
            }
            if let Some(ok) = result.get("ok") {
                return matches_type(ok, Some("SInt64"));
            }
            result
                .get("error")
                .and_then(Value::as_object)
                .is_some_and(|failure| {
                    failure.len() == 1 && failure.get("code").and_then(Value::as_u64) == Some(1)
                })
        }),
        _ => false,
    }
}

fn validate_expected(spec: &Value, expected: &Value) -> Result<(), CliFailure> {
    let result = &spec["result"];
    if result["kind"] == "primitive" {
        if matches_type(expected, result["value_type"].as_str()) {
            return Ok(());
        }
        return Err(CliFailure::invalid(
            "graph case expected value has the wrong primitive type",
        ));
    }
    if result["kind"] != "record" {
        return Err(CliFailure::invalid(
            "graph recipe result kind is unsupported",
        ));
    }
    let fields = result["fields"]
        .as_array()
        .ok_or_else(|| CliFailure::invalid("graph result fields are missing"))?;
    let values = expected
        .as_object()
        .ok_or_else(|| CliFailure::invalid("graph case expected record must be an object"))?;
    if fields.len() != values.len() {
        return Err(CliFailure::invalid(
            "graph case expected record has the wrong fields",
        ));
    }
    for field in fields {
        let name = field["name"]
            .as_str()
            .ok_or_else(|| CliFailure::invalid("graph result field lacks a name"))?;
        if !values
            .get(name)
            .is_some_and(|value| matches_type(value, field["value_type"].as_str()))
        {
            return Err(CliFailure::invalid(format!(
                "graph case expected field {name} has the wrong type or is missing"
            )));
        }
    }
    Ok(())
}

pub(super) fn validate_case_values(spec: &Value, cases: &Value) -> Result<usize, CliFailure> {
    let rows = validate_cases(cases)?;
    for row in rows {
        input_types(spec, &row["input"])?;
        validate_expected(spec, &row["expected"])?;
    }
    Ok(rows.len())
}

pub(super) fn constant_body(
    spec: &Value,
    name: &str,
    value: &Value,
) -> Result<EntityBodyValue, CliFailure> {
    let constants = spec["constants"]
        .as_array()
        .ok_or_else(|| CliFailure::invalid("graph recipe has no constants array"))?;
    let selected = constants
        .iter()
        .find(|item| item["name"] == name)
        .ok_or_else(|| CliFailure::invalid(format!("graph constant {name:?} is absent")))?;
    let kind = selected["value_type"].as_str();
    if !matches_type(value, kind) {
        return Err(CliFailure::invalid(format!(
            "graph constant {name:?} has the wrong typed value"
        )));
    }
    let native = match kind {
        Some("Bool") => ConstValue {
            value_type: TypeExpr::Bool,
            data: ConstData::Bool(value.as_bool().expect("validated Boolean")),
        },
        Some("UInt32") => ConstValue {
            value_type: TypeExpr::UInt(IntegerWidth::from_bits(32)),
            data: ConstData::UInt(u128::from(
                value
                    .as_str()
                    .expect("validated text")
                    .parse::<u32>()
                    .expect("validated unsigned value"),
            )),
        },
        Some("SInt64") => ConstValue {
            value_type: TypeExpr::SInt(IntegerWidth::from_bits(64)),
            data: ConstData::SInt(i128::from(
                value
                    .as_str()
                    .expect("validated text")
                    .parse::<i64>()
                    .expect("validated signed value"),
            )),
        },
        _ => return Err(CliFailure::invalid("unsupported graph constant type")),
    };
    Ok(EntityBodyValue::Constant(ConstantBody { value: native }))
}

pub(super) fn verify_recipe(
    root: &Path,
    manifest: &Value,
    state: &Value,
) -> Result<(), CliFailure> {
    if manifest["graph_recipe"]["path"] != "build/graph.json" {
        return Err(CliFailure::invalid(
            "graph project has an unsupported recipe path",
        ));
    }
    let bytes = read_regular(&root.join("build/graph.json"), MAX_GRAPH_SPEC)?;
    let digest = format!("{:x}", Sha256::digest(&bytes));
    if manifest["graph_recipe"]["sha256"] != digest || state["graph_spec_sha256"] != digest {
        return Err(CliFailure::stale(
            "graph recipe differs from the committed project binding",
        ));
    }
    let spec: Value = serde_json::from_slice(&bytes)
        .map_err(|error| CliFailure::invalid(format!("graph recipe is invalid JSON: {error}")))?;
    if spec["contract"] != "sley-tools.graph-authoring.v0"
        || spec["program_id"] != manifest["program_id"]
    {
        return Err(CliFailure::stale(
            "graph recipe identity differs from project manifest",
        ));
    }
    Ok(())
}

fn native_run(project: &Project, input: &Value) -> Result<Value, CliFailure> {
    let spec_path = project.root.join("build/graph.json");
    let spec = read_json(&spec_path)?;
    input_types(&spec, input)?;
    let encoded = serde_json::to_string(input)
        .map_err(|error| CliFailure::invalid(format!("cannot encode graph input: {error}")))?;
    if encoded.len() > 4_096 {
        return Err(CliFailure::resource("graph input exceeds 4 KiB"));
    }
    let workspace = project.root.join("workspace");
    let result = builder(
        &project.builder,
        "run-graph",
        &[
            workspace
                .to_str()
                .ok_or_else(|| CliFailure::invalid("workspace path is not UTF-8"))?,
            state_text(&project.state, "state_root")?,
            state_text(&project.state, "function_id")?,
            spec_path
                .to_str()
                .ok_or_else(|| CliFailure::invalid("graph spec path is not UTF-8"))?,
            &encoded,
        ],
    )?;
    checked_graph_result(project, result)
}

/// Graph cases evaluated in order by one native worker.
struct GraphRunner {
    spec: Value,
    worker: NativeWorker,
}

impl GraphRunner {
    fn start(project: &Project) -> Result<Self, CliFailure> {
        Ok(Self {
            spec: read_json(&project.root.join("build/graph.json"))?,
            worker: case_worker(project)?,
        })
    }

    fn run(&mut self, project: &Project, input: &Value) -> Result<Value, CliFailure> {
        input_types(&self.spec, input)?;
        let values = input
            .as_array()
            .ok_or_else(|| CliFailure::invalid("graph case input is not an array"))?;
        let result = self
            .worker
            .call(NativeInput::Graph(values.clone()), CASE_DEADLINE)?;
        checked_graph_result(project, result)
    }
}

fn native_run_rows(project: &Project, rows: &[Value]) -> Result<Vec<Value>, CliFailure> {
    let mut runner = GraphRunner::start(project)?;
    rows.iter()
        .map(|row| runner.run(project, &row["input"]))
        .collect()
}

fn checked_graph_result(project: &Project, result: Value) -> Result<Value, CliFailure> {
    if result["contract"] != "sley-tools.run-graph.v0"
        || result["root"] != project.state["state_root"]
        || result["function_id"] != project.state["function_id"]
    {
        return Err(CliFailure::execution(
            "native graph result identity differs from accepted project",
        ));
    }
    Ok(result)
}

pub(super) fn run(project: &Project, input: &Path) -> Result<Value, CliFailure> {
    let native = native_run(project, &read_json(input)?)?;
    Ok(json!({
        "contract": "sley-tools.run.v0", "program_id": project.manifest["program_id"],
        "state_root": native["root"], "result": native["result"],
        "observation_id": native["observation_id"], "value_hash": native["value_hash"],
        "execution_profile": native["profile"], "test_evidence_kind": "direct native VM execution",
        "check_supported": false,
    }))
}

pub(super) fn inspect_authoring(project: &Project) -> Result<Value, CliFailure> {
    let spec = read_json(&project.root.join("build/graph.json"))?;
    let constants = spec["constants"]
        .as_array()
        .ok_or_else(|| CliFailure::invalid("graph recipe has no constants array"))?;
    let selectors = constants
        .iter()
        .map(|item| {
            let name = item["name"]
                .as_str()
                .ok_or_else(|| CliFailure::invalid("graph constant has no name"))?;
            Ok(
                json!({"name": name, "value_type": item["value_type"], "value": item["value"],
            "entity_id": project.state["constant_ids"][name]}),
            )
        })
        .collect::<Result<Vec<_>, CliFailure>>()?;
    let editable = !selectors.is_empty()
        && selectors.iter().all(|item| {
            item["entity_id"]
                .as_str()
                .is_some_and(|id| id.len() == 64 && id.bytes().all(|byte| byte.is_ascii_hexdigit()))
        });
    let mut operations = Vec::new();
    let mut branches = Vec::new();
    for block in spec["blocks"]
        .as_array()
        .ok_or_else(|| CliFailure::invalid("graph recipe has no blocks array"))?
    {
        let block_name = state_text(block, "name")?;
        if let Some(targets) = branch_targets(&block["terminator"]) {
            branches.push(json!({"block": block_name, "targets": targets}));
        }
        for operation in block["operations"]
            .as_array()
            .ok_or_else(|| CliFailure::invalid("graph block has no operations array"))?
        {
            let name = state_text(operation, "name")?;
            let opcode = state_text(operation, "op")?;
            if matches!(
                opcode,
                "bool_and"
                    | "bool_or"
                    | "equal"
                    | "not_equal"
                    | "less_than"
                    | "less_equal"
                    | "greater_than"
                    | "greater_equal"
                    | "int_add_checked"
                    | "int_sub_checked"
                    | "int_mul_checked"
                    | "int_neg_checked"
                    | "result_ok"
            ) {
                operations.push(json!({"block": block_name, "name": name, "opcode": opcode,
                    "entity_id": project.state["operation_ids"][block_name][name],
                    "operands": operation_operands(operation)}));
            }
        }
    }
    let editable_operations = !operations.is_empty()
        && operations.iter().all(|item| {
            item["entity_id"]
                .as_str()
                .is_some_and(|id| id.len() == 64 && id.bytes().all(|byte| byte.is_ascii_hexdigit()))
        });
    let mut supported = Vec::new();
    if editable {
        supported.push("constant_value");
    }
    if editable_operations {
        supported.push("operation_opcode");
        supported.push("operation_operand");
    }
    if !branches.is_empty() {
        supported.push("branch_targets");
    }
    Ok(json!({
        "profile": "graph_v0", "recipe_path": "build/graph.json",
        "recipe_sha256": project.manifest["graph_recipe"]["sha256"],
        "parameters": spec["parameters"], "constants": selectors,
        "operations": operations, "branches": branches, "result": spec["result"],
        "supported_edits": supported,
    }))
}

fn operation_operands(operation: &Value) -> Value {
    if operation["operand"].is_string() {
        json!({"operand": operation["operand"]})
    } else if operation["left"].is_string() && operation["right"].is_string() {
        json!({"left": operation["left"], "right": operation["right"]})
    } else {
        json!({})
    }
}

pub(super) fn update_operand_spec(
    spec: &Value,
    block: &str,
    name: &str,
    slot: &str,
    reference: &str,
) -> Result<(String, Value), CliFailure> {
    let block_index = spec["blocks"]
        .as_array()
        .and_then(|blocks| blocks.iter().position(|item| item["name"] == block))
        .ok_or_else(|| CliFailure::invalid("graph block is absent from bound recipe"))?;
    let operations = spec["blocks"][block_index]["operations"]
        .as_array()
        .ok_or_else(|| CliFailure::invalid("graph block has no operations"))?;
    let ordinal = operations
        .iter()
        .position(|item| item["name"] == name)
        .ok_or_else(|| CliFailure::invalid("graph operation is absent from bound recipe"))?;
    let selected = &operations[ordinal];
    let opcode = state_text(selected, "op")?;
    let unary = matches!(opcode, "int_neg_checked" | "result_ok");
    let binary = matches!(
        opcode,
        "bool_and"
            | "bool_or"
            | "equal"
            | "not_equal"
            | "less_than"
            | "less_equal"
            | "greater_than"
            | "greater_equal"
            | "int_add_checked"
            | "int_sub_checked"
            | "int_mul_checked"
    );
    if !((unary && slot == "operand") || (binary && matches!(slot, "left" | "right"))) {
        return Err(CliFailure::invalid(
            "graph operation has no editable operand at that slot",
        ));
    }
    let old = selected[slot]
        .as_str()
        .ok_or_else(|| CliFailure::invalid("bound graph operation omits its operand"))?;
    if old == reference {
        return Err(CliFailure::invalid("graph operand edit is unchanged"));
    }
    let valid_reference = if let Some(parameter) = reference.strip_prefix("param.") {
        spec["parameters"]
            .as_array()
            .is_some_and(|parameters| parameters.iter().any(|item| item["name"] == parameter))
    } else if let Some(operation) = reference.strip_prefix("op.") {
        operations[..ordinal]
            .iter()
            .any(|item| item["name"] == operation)
    } else {
        false
    };
    if !valid_reference {
        return Err(CliFailure::invalid(
            "operand must reference a parameter or earlier operation in its block",
        ));
    }
    let mut updated = spec.clone();
    updated["blocks"][block_index]["operations"][ordinal][slot] = json!(reference);
    Ok((old.to_owned(), updated))
}

pub(super) fn branch_targets(terminator: &Value) -> Option<Value> {
    match terminator["kind"].as_str()? {
        "branch" => Some(json!({"target": terminator["target"]})),
        "cond_branch" => Some(json!({"if_true": terminator["if_true"],
            "if_false": terminator["if_false"]})),
        _ => None,
    }
}

pub(super) fn update_branch_spec(
    spec: &Value,
    block: &str,
    targets: &Value,
) -> Result<(Value, Value), CliFailure> {
    let blocks = spec["blocks"]
        .as_array()
        .ok_or_else(|| CliFailure::invalid("graph recipe has no blocks"))?;
    let index = blocks
        .iter()
        .position(|item| item["name"] == block)
        .ok_or_else(|| CliFailure::invalid("selected graph branch block is absent"))?;
    let old = branch_targets(&blocks[index]["terminator"])
        .ok_or_else(|| CliFailure::invalid("selected graph block has no branch"))?;
    let names: Vec<_> = blocks.iter().map(|item| item["name"].as_str()).collect();
    let expected: &[&str] = if old.get("target").is_some() {
        &["target"]
    } else {
        &["if_true", "if_false"]
    };
    let object = targets
        .as_object()
        .ok_or_else(|| CliFailure::invalid("branch targets must be an object"))?;
    if object.len() != expected.len()
        || expected.iter().any(|key| {
            !object
                .get(*key)
                .and_then(Value::as_str)
                .is_some_and(|name| names.contains(&Some(name)))
        })
    {
        return Err(CliFailure::invalid(
            "branch targets must name existing blocks with exact fields",
        ));
    }
    if old == *targets {
        return Err(CliFailure::invalid(
            "proposed graph branch targets are unchanged",
        ));
    }
    let mut updated = spec.clone();
    for key in expected {
        updated["blocks"][index]["terminator"][*key] = targets[*key].clone();
    }
    let entry = updated["entry_block"]
        .as_str()
        .ok_or_else(|| CliFailure::invalid("graph entry block is absent"))?;
    let mut visiting = BTreeSet::new();
    let mut visited = BTreeSet::new();
    let mut stack = vec![(entry.to_owned(), false)];
    while let Some((name, exiting)) = stack.pop() {
        if exiting {
            visiting.remove(&name);
            visited.insert(name);
            continue;
        }
        if visited.contains(&name) {
            continue;
        }
        if !visiting.insert(name.clone()) {
            return Err(CliFailure::invalid("graph branch edit creates a cycle"));
        }
        let selected = updated["blocks"]
            .as_array()
            .and_then(|items| items.iter().find(|item| item["name"] == name))
            .ok_or_else(|| CliFailure::invalid("graph branch target is absent"))?;
        stack.push((name, true));
        if let Some(edges) = branch_targets(&selected["terminator"]) {
            for target in edges.as_object().expect("branch target object").values() {
                stack.push((
                    target
                        .as_str()
                        .ok_or_else(|| CliFailure::invalid("graph branch target is not text"))?
                        .to_owned(),
                    false,
                ));
            }
        }
    }
    if visited.len() != blocks.len() {
        return Err(CliFailure::invalid(
            "graph branch edit leaves a block unreachable",
        ));
    }
    Ok((old, updated))
}

pub(super) fn test(project: &Project) -> Result<Value, CliFailure> {
    let cases_path = project.root.join("tests/cases.json");
    let bytes = read_regular(&cases_path, MAX_GRAPH_CASES)?;
    let cases: Value = serde_json::from_slice(&bytes)
        .map_err(|error| CliFailure::invalid(format!("invalid graph test JSON: {error}")))?;
    let rows = validate_cases(&cases)?;
    let mut runner = GraphRunner::start(project)?;
    for row in rows {
        let observed = runner.run(project, &row["input"])?;
        if observed["result"] != row["expected"] {
            return Err(CliFailure::execution(format!(
                "native graph differs from expected case {}",
                row["id"]
            )));
        }
    }
    Ok(json!({
        "contract": "sley-tools.test.v0", "result": "PASS",
        "program_id": project.manifest["program_id"], "state_root": project.state["state_root"],
        "external_assertions_against_native_execution": rows.len(),
        "cases_sha256": format!("{:x}", Sha256::digest(&bytes)),
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
    }))
}

pub(super) fn init(
    project: &Path,
    core: &Path,
    spec: &Path,
    cases: &Path,
) -> Result<Value, CliFailure> {
    let spec_bytes = read_regular(spec, MAX_GRAPH_SPEC)?;
    let cases_bytes = read_regular(cases, MAX_GRAPH_CASES)?;
    initialize(project, core, &spec_bytes, &cases_bytes, None)
}

pub(super) fn import_artifact(
    artifact: &Path,
    artifact_bytes: &[u8],
    project: &Path,
    core: &Path,
) -> Result<Value, CliFailure> {
    let header: Value = serde_json::from_slice(artifact_bytes)
        .map_err(|error| CliFailure::invalid(format!("invalid graph artifact: {error}")))?;
    let recipe = header["recipe_json"]
        .as_str()
        .ok_or_else(|| CliFailure::invalid("graph artifact omits its bound recipe"))?;
    let cases = header["cases_json"]
        .as_str()
        .ok_or_else(|| CliFailure::invalid("graph artifact omits its test cases"))?;
    if recipe.len() > MAX_GRAPH_SPEC as usize || cases.len() > MAX_GRAPH_CASES as usize {
        return Err(CliFailure::invalid(
            "graph artifact recipe or cases exceed their limits",
        ));
    }
    if header["recipe_sha256"] != format!("{:x}", Sha256::digest(recipe.as_bytes()))
        || header["cases_sha256"] != format!("{:x}", Sha256::digest(cases.as_bytes()))
    {
        return Err(CliFailure::invalid(
            "graph artifact recipe or case digest differs",
        ));
    }
    initialize(
        project,
        core,
        recipe.as_bytes(),
        cases.as_bytes(),
        Some((artifact, artifact_bytes)),
    )
}

fn initialize(
    project: &Path,
    core: &Path,
    spec_bytes: &[u8],
    cases_bytes: &[u8],
    source_artifact: Option<(&Path, &[u8])>,
) -> Result<Value, CliFailure> {
    let spec_document: Value = serde_json::from_slice(&spec_bytes)
        .map_err(|error| CliFailure::invalid(format!("graph recipe is invalid JSON: {error}")))?;
    let cases_document: Value = serde_json::from_slice(&cases_bytes).map_err(|error| {
        CliFailure::invalid(format!("graph test fixture is invalid JSON: {error}"))
    })?;
    validate_case_values(&spec_document, &cases_document)?;
    let rows = validate_cases(&cases_document)?;
    let example_input = rows[0]["input"].clone();
    let core_report = live_core_check(core.to_path_buf())?;
    if core_report["status"] != "compatible" {
        return Err(CliFailure::incompatible(
            "graph init requires the pinned Sley 2.0.1 core",
        ));
    }
    let core = PathBuf::from(
        core_report["core"]["path"]
            .as_str()
            .ok_or_else(|| CliFailure::failed("doctor omitted core path"))?,
    );
    let core_sha = core_report["core"]["binary_sha256"]
        .as_str()
        .ok_or_else(|| CliFailure::failed("doctor omitted binary digest"))?;
    let current = std::env::current_exe()
        .map_err(|error| CliFailure::failed(format!("cannot locate tools binary: {error}")))?;
    let builder_path = current.with_file_name("sley-tools-native-builder");
    if !builder_path.is_file() {
        return Err(CliFailure::failed(
            "sley-tools-native-builder is missing beside sley-tools",
        ));
    }
    fs::create_dir(project).map_err(|error| {
        CliFailure::invalid(format!(
            "cannot create new graph project directory: {error}"
        ))
    })?;
    let project = project.canonicalize().map_err(|error| {
        CliFailure::failed(format!("cannot resolve project directory: {error}"))
    })?;
    let state_dir = project.join(".sley-tools");
    let outcome = (|| {
        for name in [".sley-tools", "workspace", "build", "tests", "inputs"] {
            fs::create_dir(project.join(name)).map_err(|error| {
                CliFailure::failed(format!("cannot create project {name}: {error}"))
            })?;
        }
        fs::write(
            project.join(".gitignore"),
            "/workspace/\n/.sley-tools/\n/config.local.json\n",
        )
        .map_err(|error| CliFailure::failed(format!("cannot write project .gitignore: {error}")))?;
        write_json(
            &project.join("config.local.json"),
            &json!({"core": core, "core_binary_sha256": core_sha}),
        )?;
        let spec_path = project.join("build/graph.json");
        fs::write(&spec_path, &spec_bytes)
            .map_err(|error| CliFailure::failed(format!("cannot copy graph recipe: {error}")))?;
        fs::write(project.join("tests/cases.json"), &cases_bytes)
            .map_err(|error| CliFailure::failed(format!("cannot copy graph cases: {error}")))?;
        write_json(&project.join("inputs/example.json"), &example_input)?;
        let imported_path = if let Some((_, bytes)) = source_artifact {
            fs::create_dir(project.join("artifact")).map_err(|error| {
                CliFailure::failed(format!("cannot create artifact directory: {error}"))
            })?;
            let path = project.join("artifact/source-native-graph.json");
            fs::write(&path, bytes).map_err(|error| {
                CliFailure::failed(format!("cannot preserve source artifact: {error}"))
            })?;
            Some(path)
        } else {
            None
        };
        let opened = NewWorkspace::open(&core, &state_dir, &project.join("workspace"))?;
        let context = opened.context();
        let mut args = vec![
            imported_path
                .as_deref()
                .unwrap_or(&spec_path)
                .to_str()
                .ok_or_else(|| CliFailure::invalid("graph builder input path is not UTF-8"))?,
        ];
        args.extend(context);
        let constructed = builder(
            &builder_path,
            if imported_path.is_some() {
                "portable-import-graph"
            } else {
                "construct-graph"
            },
            &args,
        )?;
        if constructed["contract"]
            != if imported_path.is_some() {
                "sley-tools.portable-import-graph.v1"
            } else {
                "sley-tools.construct-graph.v0"
            }
            || constructed["program_id"] != spec_document["program_id"]
        {
            return Err(CliFailure::failed(
                "typed builder returned an inconsistent graph identity",
            ));
        }
        let CommittedWorkspace {
            genesis,
            accepted_transaction,
            accepted_root,
            commit_fields,
        } = opened.commit(&constructed)?;
        let function = state_text(&constructed, "function_id")?;
        let mut inputs = serde_json::Map::new();
        for parameter in spec_document["parameters"]
            .as_array()
            .ok_or_else(|| CliFailure::invalid("graph recipe has no parameter array"))?
        {
            let name = parameter["name"]
                .as_str()
                .ok_or_else(|| CliFailure::invalid("graph parameter has no name"))?;
            let kind = parameter["value_type"]
                .as_str()
                .ok_or_else(|| CliFailure::invalid("graph parameter has no type"))?;
            inputs.insert(name.to_owned(), json!(kind));
        }
        let output = if spec_document["result"]["kind"] == "record" {
            let mut fields = serde_json::Map::new();
            for field in spec_document["result"]["fields"]
                .as_array()
                .ok_or_else(|| CliFailure::invalid("graph result fields are absent"))?
            {
                fields.insert(
                    state_text(field, "name")?.to_owned(),
                    field["value_type"].clone(),
                );
            }
            json!({"type": "Record", "fields": fields})
        } else {
            json!({"type": spec_document["result"]["value_type"]})
        };
        let spec_sha = format!("{:x}", Sha256::digest(&spec_bytes));
        let mut manifest = json!({
            "contract": "sley-tools.program.v0", "authoring_profile": "graph_v0",
            "program_id": constructed["program_id"], "title": constructed["title"], "purpose": constructed["purpose"],
            "program_version": "0.1.0-local", "status": "local_workspace_only", "tools_contract": "sley-tools.cli.v0",
            "core": {"commit": crate::TESTED_CORE_COMMIT, "profile": "extended_v1", "binary_sha256": core_sha},
            "entrypoint": {"name": "evaluate", "function_id": function, "state_root": accepted_root},
            "result_type_id": constructed["result_type_id"],
            "result_fields": constructed["result_fields"].as_array().cloned().unwrap_or_default(),
            "inputs": inputs, "output": output, "artifact": null,
            "native_logic": "pure typed graph declared by the bound local recipe",
            "host_responsibility": "typed JSON input, local workspace lifecycle, and external assertions",
            "test_cases": {"external_assertions": "sley-tools test --project PROJECT",
                "independent_oracle": "tests/cases.json", "native_core_test_admission": "PENDING_CORE_EXECUTOR"},
            "worked_change": {"status": "pending"},
            "source_provenance": {"builder": "crates/backend/src/graph.rs", "core_commit": crate::TESTED_CORE_COMMIT},
            "graph_recipe": {"path": "build/graph.json", "sha256": spec_sha},
            "license_status": "Apache-2.0",
        });
        if let Some((_, bytes)) = source_artifact {
            manifest["imported_source_artifact"] = json!({
                "contract": "sley-tools.native-graph-artifact.v1",
                "path": "artifact/source-native-graph.json",
                "sha256": format!("{:x}", Sha256::digest(bytes)),
            });
        }
        write_json(&project.join("program.json"), &manifest)?;
        let state = json!({
            "workspace_id": genesis.workspace_id, "principal_id": genesis.principal_id,
            "policy_root": genesis.policy, "schema_epoch": genesis.epoch,
            "transaction_id": accepted_transaction, "state_root": accepted_root,
            "candidate_id": constructed["candidate_id"], "commit_receipt_id": hex(&commit_fields[1]),
            "commit_history": [{"kind": if source_artifact.is_some() { "graph_artifact_import" } else { "graph_construct" }, "candidate_id": constructed["candidate_id"],
                "transaction_id": accepted_transaction, "state_root": accepted_root,
                "receipt_id": hex(&commit_fields[1])}],
            "function_id": function, "graph_spec_sha256": spec_sha,
            "constant_ids": constructed["constant_ids"],
            "operation_ids": constructed["operation_ids"],
        });
        write_json(&state_dir.join("state.json"), &state)?;
        let status = json!({"state": "READY_EXTERNAL_ASSERTIONS_ONLY", "native_commit_count": 1,
            "native_test_admission": "PENDING_CORE_EXECUTOR", "state_root": accepted_root});
        write_json(&state_dir.join("status.json"), &status)?;
        let verified = test(&load_project(&project)?)?;
        Ok(
            json!({"contract": if source_artifact.is_some() { "sley-tools.artifact-import.v1" } else { "sley-tools.graph-init.v0" }, "project": project,
            "program": manifest, "status": status, "verification": verified,
            "candidate_id": constructed["candidate_id"], "commit_receipt_id": hex(&commit_fields[1]),
            "source_state_root": constructed["source_state_root"]}),
        )
    })();
    if let Err(error) = &outcome {
        let _ = fs::create_dir_all(&state_dir);
        let _ = write_json(
            &state_dir.join("status.json"),
            &json!({"state": "INCOMPLETE", "code": error.code, "reason": error.message}),
        );
    }
    outcome
}

pub(super) fn commit_constant(
    path: &Path,
    name: &str,
    new_value: &Value,
    cases: &Value,
    edit: &Value,
) -> Result<Value, CliFailure> {
    commit_edit(path, GraphEdit::Constant { name, new_value }, cases, edit)
}

pub(super) fn commit_opcode(
    path: &Path,
    block: &str,
    name: &str,
    new_opcode: &str,
    cases: &Value,
    edit: &Value,
) -> Result<Value, CliFailure> {
    commit_edit(
        path,
        GraphEdit::Opcode {
            block,
            name,
            new_opcode,
        },
        cases,
        edit,
    )
}

pub(super) fn commit_operand(
    path: &Path,
    block: &str,
    name: &str,
    slot: &str,
    reference: &str,
    cases: &Value,
    edit: &Value,
) -> Result<Value, CliFailure> {
    commit_edit(
        path,
        GraphEdit::Operand {
            block,
            name,
            slot,
            reference,
        },
        cases,
        edit,
    )
}

pub(super) fn commit_branch(
    path: &Path,
    block: &str,
    targets: &Value,
    cases: &Value,
    edit: &Value,
) -> Result<Value, CliFailure> {
    commit_edit(path, GraphEdit::Branch { block, targets }, cases, edit)
}

enum GraphEdit<'a> {
    Constant {
        name: &'a str,
        new_value: &'a Value,
    },
    Opcode {
        block: &'a str,
        name: &'a str,
        new_opcode: &'a str,
    },
    Operand {
        block: &'a str,
        name: &'a str,
        slot: &'a str,
        reference: &'a str,
    },
    Branch {
        block: &'a str,
        targets: &'a Value,
    },
}

fn commit_edit(
    path: &Path,
    target: GraphEdit<'_>,
    cases: &Value,
    edit: &Value,
) -> Result<Value, CliFailure> {
    let mut project = load_project(path)?;
    if project.manifest["authoring_profile"] != "graph_v0" {
        return Err(CliFailure::feature(
            "graph edit commit requires a graph project",
        ));
    }
    let spec_path = project.root.join("build/graph.json");
    let spec = read_json(&spec_path)?;
    validate_case_values(&spec, cases)?;
    let mut updated_spec = spec.clone();
    let (history_kind, change) = match target {
        GraphEdit::Constant { name, new_value } => {
            let index = spec["constants"]
                .as_array()
                .and_then(|constants| constants.iter().position(|item| item["name"] == name))
                .ok_or_else(|| CliFailure::invalid("graph constant is absent from bound recipe"))?;
            let old = spec["constants"][index]["value"].clone();
            if constant_body(&spec, name, &old)? == constant_body(&spec, name, new_value)? {
                return Err(CliFailure::invalid("graph constant edit is unchanged"));
            }
            updated_spec["constants"][index]["value"] = new_value.clone();
            (
                "graph_constant_edit",
                json!({"constant": name, "old_value": old,
                "new_value": new_value}),
            )
        }
        GraphEdit::Opcode {
            block,
            name,
            new_opcode,
        } => {
            let block_index = spec["blocks"]
                .as_array()
                .and_then(|blocks| blocks.iter().position(|item| item["name"] == block))
                .ok_or_else(|| CliFailure::invalid("graph block is absent from bound recipe"))?;
            let index = spec["blocks"][block_index]["operations"]
                .as_array()
                .and_then(|operations| operations.iter().position(|item| item["name"] == name))
                .ok_or_else(|| {
                    CliFailure::invalid("graph operation is absent from bound recipe")
                })?;
            let old = spec["blocks"][block_index]["operations"][index]["op"]
                .as_str()
                .ok_or_else(|| CliFailure::invalid("graph operation has no opcode"))?;
            if old == new_opcode {
                return Err(CliFailure::invalid("graph opcode edit is unchanged"));
            }
            updated_spec["blocks"][block_index]["operations"][index]["op"] = json!(new_opcode);
            (
                "graph_opcode_edit",
                json!({"block": block, "operation": name,
                "old_opcode": old, "new_opcode": new_opcode}),
            )
        }
        GraphEdit::Operand {
            block,
            name,
            slot,
            reference,
        } => {
            let (old, updated) = update_operand_spec(&spec, block, name, slot, reference)?;
            updated_spec = updated;
            (
                "graph_operand_edit",
                json!({"block": block, "operation": name, "operand": slot,
                    "old_reference": old, "new_reference": reference}),
            )
        }
        GraphEdit::Branch { block, targets } => {
            let (old, updated) = update_branch_spec(&spec, block, targets)?;
            updated_spec = updated;
            (
                "graph_branch_edit",
                json!({"block": block,
                "old_targets": old, "new_targets": targets}),
            )
        }
    };
    let serialized_spec = serde_json::to_vec_pretty(&updated_spec).map_err(|error| {
        CliFailure::invalid(format!("cannot encode updated graph recipe: {error}"))
    })?;
    if serialized_spec.len() + 1 > MAX_GRAPH_SPEC as usize {
        return Err(CliFailure::resource("updated graph recipe exceeds 64 KiB"));
    }
    let serialized_cases = serde_json::to_vec_pretty(cases)
        .map_err(|error| CliFailure::invalid(format!("cannot encode graph cases: {error}")))?;
    if serialized_cases.len() + 1 > MAX_GRAPH_CASES as usize {
        return Err(CliFailure::resource("updated graph cases exceed 1 MiB"));
    }
    let old_root = state_text(&project.state, "state_root")?.to_owned();
    let old_transaction = state_text(&project.state, "transaction_id")?.to_owned();
    let rows = validate_cases(cases)?;
    let before: Vec<Value> = native_run_rows(&project, rows)?
        .into_iter()
        .map(|observed| observed["result"].clone())
        .collect();
    let status_path = project.root.join(".sley-tools/status.json");
    let previous_status = project.status.clone();
    project.status["state"] = json!("EDIT_IN_PROGRESS");
    write_json(&status_path, &project.status)?;
    let pending_path = project.root.join(".sley-tools/pending-edit.json");
    let candidate_id = state_text(edit, "candidate_id")?.to_owned();
    let mut committed = false;
    let outcome = (|| {
        let mut pending = json!({"phase": "PROPOSED", "candidate_id": candidate_id,
            "base_root": old_root, "expected_parent": old_transaction});
        pending
            .as_object_mut()
            .expect("pending object")
            .extend(change.as_object().expect("change object").clone());
        write_json(&pending_path, &pending)?;
        let handshake_dir = project
            .root
            .join(".sley-tools")
            .join(format!("handshake-edit-{}", &hex(&random32()?)[..16]));
        fs::create_dir(&handshake_dir).map_err(|error| {
            CliFailure::failed(format!("cannot create graph edit handshake: {error}"))
        })?;
        let hello = offered_hello(&project.core)?;
        let handshake_id = handshake(&project.core, &handshake_dir, &hello)?;
        let mut endpoint = Smp::start(&project.core, &project.root.join("workspace"), &hello)?;
        let session = endpoint.request("session.open", &handshake_id, 0, None)?;
        let opened = endpoint.request("workspace.open", "", 1, Some(&session))?;
        let fields = record_fields(&unhex(&opened)?, 8)?;
        if hex(&fields[0]) != old_transaction || hex(&fields[1]) != old_root {
            return Err(CliFailure::stale(
                "accepted graph head advanced before edit commit",
            ));
        }
        let stored = endpoint.request(
            "candidate.create",
            state_text(edit, "candidate_record_hex")?,
            2,
            Some(&session),
        )?;
        if stored != edit["candidate_stored_hex"] {
            return Err(CliFailure::failed(
                "graph candidate differs from public core storage",
            ));
        }
        let validation_body = hex(&encode_record(&[
            (1, unhex(&old_transaction)?),
            (2, unhex(state_text(&project.state, "principal_id")?)?),
            (3, encode_uvar(current_millis()?)),
            (4, unhex(&stored)?),
        ])
        .map_err(|error| CliFailure::failed(format!("graph validation request: {error}")))?);
        let result = endpoint.request("candidate.validate", &validation_body, 3, Some(&session))?;
        require_valid_candidate(&result, &candidate_id)?;
        pending["phase"] = json!("COMMIT_REQUESTED");
        write_json(&pending_path, &pending)?;
        let committed_body = match endpoint.request("commit", &validation_body, 4, Some(&session)) {
            Ok(value) => value,
            Err(error) if error.core_symbol.is_none() => {
                return Err(CliFailure::outcome_unknown(format!(
                    "graph candidate {candidate_id} commit outcome is unknown: {}",
                    error.message
                )));
            }
            Err(error) => return Err(error),
        };
        committed = true;
        let receipt = record_fields(&unhex(&committed_body)?, 4)?;
        if receipt.iter().take(3).any(|field| field.len() != 32) {
            return Err(CliFailure::failed(
                "graph commit returned invalid identities",
            ));
        }
        let transaction = hex(&receipt[0]);
        let receipt_id = hex(&receipt[1]);
        let root = hex(&receipt[2]);
        if root == old_root {
            return Err(CliFailure::failed(
                "graph edit commit did not advance accepted root",
            ));
        }
        let _ = endpoint.request("session.close", "", 5, Some(&session));
        let _ = endpoint.finish();
        write_json(&spec_path, &updated_spec)?;
        let spec_sha = format!(
            "{:x}",
            Sha256::digest(read_regular(&spec_path, MAX_GRAPH_SPEC)?)
        );
        write_json(&project.root.join("tests/cases.json"), cases)?;
        project.state["transaction_id"] = json!(transaction);
        project.state["state_root"] = json!(root);
        project.state["candidate_id"] = json!(candidate_id);
        project.state["commit_receipt_id"] = json!(receipt_id);
        project.state["graph_spec_sha256"] = json!(spec_sha);
        let mut history = json!({"kind": history_kind,
            "candidate_id": candidate_id, "transaction_id": transaction,
            "state_root": root, "receipt_id": receipt_id});
        history
            .as_object_mut()
            .expect("history object")
            .extend(change.as_object().expect("change object").clone());
        project.state["commit_history"]
            .as_array_mut()
            .ok_or_else(|| CliFailure::stale("graph commit history is absent"))?
            .push(history);
        project.manifest["entrypoint"]["state_root"] = json!(root);
        project.manifest["graph_recipe"]["sha256"] = json!(spec_sha);
        project.manifest["program_version"] = json!(format!(
            "0.1.{}-local",
            project.state["commit_history"]
                .as_array()
                .map_or(0, Vec::len)
                - 1
        ));
        write_json(&project.root.join(".sley-tools/state.json"), &project.state)?;
        write_json(&project.root.join("program.json"), &project.manifest)?;
        project.status["state"] = json!("READY_EXTERNAL_ASSERTIONS_ONLY");
        project.status["state_root"] = json!(root);
        project.status["native_commit_count"] = json!(
            project.state["commit_history"]
                .as_array()
                .map_or(0, Vec::len)
        );
        project.status["last_external_assertions"] = json!("PENDING");
        write_json(&status_path, &project.status)?;
        let reopened = load_project(&project.root)?;
        let changed = native_run_rows(&reopened, rows)?
            .iter()
            .zip(&before)
            .filter(|(observed, previous)| observed["result"] != **previous)
            .count();
        let verified = test(&reopened);
        project.status["last_external_assertions"] =
            json!(if verified.is_ok() { "PASS" } else { "FAIL" });
        write_json(&status_path, &project.status)?;
        let _ = fs::remove_file(&pending_path);
        let mut report = json!({
            "contract": "sley-tools.edit.v0", "program_id": project.manifest["program_id"],
            "candidate_id": candidate_id, "receipt_id": receipt_id,
            "old_root": old_root, "new_root": root,
            "native_commit_count": project.status["native_commit_count"],
            "changed_case_count": changed,
            "external_assertions": verified.as_ref().ok().map(|value| value["external_assertions_against_native_execution"].clone()),
            "verification_error": verified.err().map(|error| error.message),
            "native_core_test_admission": "NOT_RUN: production native executor unavailable",
        });
        report
            .as_object_mut()
            .expect("report object")
            .extend(change.as_object().expect("change object").clone());
        Ok(report)
    })();
    if let Err(error) = &outcome {
        if committed {
            let _ = write_json(
                &status_path,
                &json!({
                    "state": "INCOMPLETE", "code": error.code, "reason": error.message,
                    "candidate_id": candidate_id, "base_root": old_root,
                }),
            );
            return Err(CliFailure::outcome_unknown(format!(
                "native graph edit was accepted, but local project update is incomplete: {}",
                error.message
            )));
        }
        if error.code != "ST_COMMIT_OUTCOME_UNKNOWN" {
            let _ = write_json(&status_path, &previous_status);
            let _ = fs::remove_file(&pending_path);
        }
    }
    outcome
}
