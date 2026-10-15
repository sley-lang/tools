# Typed graph authoring backend v0

The Rust native builder now accepts a bounded JSON authoring recipe and lowers it to a real SSMC1 candidate. The recipe is an input to the tools backend, not a Sley source language or an accepted artifact. The core validates and commits the candidate; the accepted graph and its identities remain authoritative.

The [Range Predicate recipe](../examples/graph/range-predicate.json) declares three signed 64 bit parameters, a named Boolean result record, constants, operations, and three blocks. Its entry block branches on reversed bounds. The valid block checks inclusive endpoints; the invalid block returns an explicit invalid-bounds reason. The [unsigned limit recipe](../examples/graph/unsigned-limit.json) adds a second function with unsigned inputs and a primitive Boolean result. The [probe](../evidence/graph-builder/range-predicate-v2.json) creates a disposable local workspace, submits both candidates through the selected release's public `candidate.create`, `candidate.validate`, and `commit` methods, then runs twelve cases through the real VM. Five malformed recipes and invalid integer inputs are rejected.

`sley-tools.graph-authoring.v0` currently has these fields:

| Field | Meaning |
|---|---|
| `program_id`, `title`, `purpose` | Local project identity and human description. |
| `parameters` | Ordered `{name, value_type}` entries. Input types: `Bool`, `UInt32`, `SInt64`. |
| `constants` | Named typed values. Booleans use JSON booleans; integers use canonical decimal strings. |
| `result` | `{kind:"primitive", value_type}` or `{kind:"record", fields:[{name,value_type},...]}`. Result fields may also use `CheckedSInt64`. |
| `entry_block` | Name of the first block. |
| `blocks` | Named blocks with ordered operations and a terminator. |

Operations are `constant_ref`, `bool_not`, `bool_and`, `bool_or`, `equal`, `not_equal`, `less_than`, `less_equal`, `greater_than`, `greater_equal`, `int_add_checked`, `int_sub_checked`, `int_mul_checked`, `int_neg_checked`, `result_ok`, and `record_new`. A value reference is `param.NAME` or `op.NAME`. An operation may read earlier operations in its own block and any function parameter. Terminators are `return`, `branch`, and `cond_branch`; branch targets have no block arguments in this profile. The builder checks names, duplicate labels, references, operand/result types, target existence, reachability, and cycles before submitting anything to the core. Limits are 16 parameters, 64 constants, 16 blocks, 128 operations, and 16 result fields, with a 64 KiB recipe cap. The execution helper accepts up to 4 KiB of typed JSON input and uses the core VM's `extended_v1` profile with fixed fuel/value/output limits.

The CLI now accepts a graph recipe and independently expected test cases through the shared project lifecycle:

```sh
sley-tools init my-range --graph examples/graph/range-predicate.json \
  --tests examples/graph/range-predicate-cases.json --core /path/to/sley
sley-tools run --project my-range --input my-range/inputs/example.json
sley-tools test --project my-range
sley-tools inspect --project my-range
```

`init` creates a fresh workspace, lowers the recipe to a native candidate, asks the selected core to validate and commit it, and runs every declared case against the accepted graph. The project binds the recipe digest to its manifest and local accepted state; a changed recipe makes later operations fail with a stale-state error. Case results are exact value comparisons; `test` reports the fixture digest and the number of external assertions. The inputs and expected results in a case file are JSON arrays and native result JSON values. Integer values use canonical decimal strings.

For a Boolean graph, `sley-tools test native-candidate --project my-limit --case zero` constructs a real core `TestCase` candidate from one declared case and runs `candidate.create` and `candidate.validate`. The pinned core library also derives the read-only native test plan and confirms that the new case is selected within the accepted resource grant. It returns `STATICALLY_VALID` only when the core accepts the candidate's structure and authority. It leaves the accepted root unchanged and reports native admission and execution as `NOT_RUN`; the selected 2.1.1 core has no configured production test executor. The [F0 native test probe](../evidence/compatibility/f0-native-test-route-v1.json) separately calls the v3 test route and records its `NATIVE_EXECUTOR_UNAVAILABLE` refusal.

`int_add_checked`, `int_sub_checked`, and `int_mul_checked` accept two `SInt64` operands and return `CheckedSInt64`, the core's `Result<SInt64, ArithmeticFailure>` type. `int_neg_checked` takes one `SInt64` and returns the same checked type. `result_ok` wraps an `SInt64` value in the success arm. A native success appears in test JSON as `{"ok":"42"}`; arithmetic overflow appears as `{"error":{"code":1}}`. `CheckedSInt64` cannot be an input parameter or constant, and this graph profile has no operation that extracts a checked result for further arithmetic. The RPC and SDKs preserve both arms with exact integers. The [Benchmark Comparator recipe](../examples/graph/benchmark-comparator.json) uses native subtraction for three measurement deltas and native comparisons for separate regression and negative-input flags. A negative measurement sets `invalid_input` while retaining exact deltas; a missing measurement is rejected as an invalid call. Its [nine cases](../examples/graph/benchmark-comparator-cases.json) include equality, mixed regressions, signed boundaries, negative inputs, and overflow. The [probe](../evidence/graph-builder/benchmark-comparator-v4.json) checks those results through CLI, Python, and TypeScript, then separately verifies native checked addition, multiplication, negation, and result construction, including overflow and wrong-type rejection. The [N03 comparator](../programs/benchmark-comparator/README.md) and [N05 reducer](../programs/benchmark-summary-reducer/README.md) catalog packages add portable artifacts, public cases, and worked native policy edits. The [Data Pipe host pipeline](DATA_PIPE_V0.md) and [portable graph profile](ARTIFACT_V0.md#authored-graph-profile-v1) are implemented.

The JSON recipe is an authoring interface, not canonical Sley source. Sley 2 does not provide a canonical text source file: the accepted SSMC1 graph is the program. Rust constructs and validates native objects through the selected public core; the decision or calculation executes in the Sley VM. Python and TypeScript remain host clients. A native program may use host input/output through those clients without moving its advertised computation out of Sley.

This is a bounded first public graph authoring path. Locally authored graph projects can now be opened and called through [local RPC](RPC_V0.md) and both SDKs. The [SDK probe](../evidence/bridge/graph-sdk-v2.json) runs fifteen cases across Range Predicate, Unsigned Limit, and Numeric Echo through each client, including signed boundaries, primitive and mixed numeric record results, malformed input rejection, altered-recipe rejection, and worker reuse. Python `Runtime.create_graph(project=..., recipe=..., cases=...)` and TypeScript `Runtime.createGraph({ project, recipe, cases })` initialize a fresh project from a recipe object and host-valued cases. The SDKs encode exact integers, submit the recipe and cases to the shared Rust builder through `sley-tools init`, and open the accepted graph. The destination must not exist. The [creation probe](../evidence/bridge/graph-sdk-creation-v2.json) uses the fluent builders below to create the branching Range Predicate in Python and primitive Fixed Limit in TypeScript, compares both recipes with checked-in fixtures, and executes fifteen native cases across three projects. It also checks six malformed builder uses, invalid inputs, and existing-destination rejection. Adding or removing blocks and native core test admission remain open. These graphs are local executable witnesses, not admitted catalog packages. `artifact export/import` supports the bounded native graph profile; `run --check` remains unavailable for graph projects.

Python and TypeScript builders assemble the same `sley-tools.graph-authoring.v0` recipe. They check basic shape and limits locally; the Rust builder and selected core still decide semantic validity and native acceptance. `build()` returns an independent recipe value that can be passed to the existing project creation method:

```python
from sley_tools import GraphBuilder, open_runtime

graph = GraphBuilder(program_id="fixed-limit", title="Fixed Limit",
                     purpose="Decide whether a count is within the configured limit")
graph.parameter("count", "UInt32").constant("limit", "UInt32", 10).primitive_result("Bool")
graph.block("entry").constant_ref("accepted_limit", "limit") \
    .binary("less_equal", "within", "param.count", "op.accepted_limit") \
    .return_value("op.within")
with open_runtime(tools="/path/to/sley-tools", core="/path/to/sley") as runtime:
    program = runtime.create_graph(project="my-limit", recipe=graph.build(), cases=[
        {"id": "at-limit", "inputs": {"count": 10}, "expected": True},
        {"id": "above-limit", "inputs": {"count": 11}, "expected": False},
    ])
    assert program.call("evaluate", inputs={"count": 10}).value is True
```

```js
import { GraphBuilder, openRuntime } from '@sley-lang/tools';

const graph = new GraphBuilder({ programId: 'fixed-limit', title: 'Fixed Limit',
  purpose: 'Decide whether a count is within the configured limit' });
graph.parameter('count', 'UInt32').constant('limit', 'UInt32', 10n).primitiveResult('Bool');
graph.block('entry').constantRef('accepted_limit', 'limit')
  .binary('less_equal', 'within', 'param.count', 'op.accepted_limit').returnValue('op.within');
const runtime = openRuntime({ tools: '/path/to/sley-tools', core: '/path/to/sley' });
try {
  const program = await runtime.createGraph({ project: 'my-limit', recipe: graph.build(), cases: [
    { id: 'at-limit', inputs: { count: 10 }, expected: true },
    { id: 'above-limit', inputs: { count: 11 }, expected: false },
  ] });
  console.log((await program.callGraph('evaluate', { inputs: { count: 10 } })).value);
} finally {
  await runtime.close();
}
```

Each recipe has one ordered set of parameters and constants, one primitive or record result, and named blocks. A block can append constant references, unary or binary operations, and a record construction, then end with `return_value`/`returnValue`, `jump`, or `conditional`. References use `param.NAME` and `op.NAME`; `GraphBuilder.param(NAME)` and `GraphBuilder.op(NAME)` construct those strings. For branch targets, create each target block and give every block one terminator before calling `build()`. Python accepts exact `int` values; TypeScript accepts `bigint` or safe integer `number` values for integer constants and cases.

A named constant can now be changed through the same separate native proposal lifecycle as the worked Rules edits. The [Fixed Limit recipe](../examples/graph/fixed-limit.json) contains a `UInt32` limit of `10`; its [initial cases](../examples/graph/fixed-limit-cases.json) and [updated cases](../examples/graph/fixed-limit-after-cases.json) cover the boundary and preserved behavior:

```sh
sley-tools init my-limit --graph examples/graph/fixed-limit.json \
  --tests examples/graph/fixed-limit-cases.json --core /path/to/sley
printf '"12"\n' > /tmp/new-limit.json
sley-tools propose --project my-limit --constant limit \
  --value-file /tmp/new-limit.json --tests examples/graph/fixed-limit-after-cases.json
sley-tools validate --project my-limit
sley-tools commit --project my-limit
sley-tools test --project my-limit
```

The proposal selects the constant by name, verifies its accepted native value, and records an exact old-object precondition. The core validates the one-object replacement and revalidates it at commit. The project updates its recipe binding, state root, manifest, and cases after the accepted commit. Behavioral assertions run after commit; `validate` proves native semantic validity, not the correctness of the supplied expectations. If the expectations are false, CLI `commit` reports `ST_EXECUTION_FAILED` with the accepted root and leaves the committed project runnable for correction. RPC and SDK `commit` return the accepted root with `postcommit_assertions_status: "FAIL"`, then reopen the project. `test` still reports failed assertions. Python and TypeScript expose `propose_graph_constant` and `proposeGraphConstant`, respectively, using host values and cases with named inputs. The [SDK edit probe](../evidence/bridge/graph-sdk-authoring-v2.json) verifies both edit paths, changed native boundaries, and a false expectation after an accepted commit.

A second targeted edit replaces an operation's opcode while preserving its operands, result type, block, ordinal, and entity ID. `inspect` lists editable block/operation selectors and their accepted entity IDs. This covers changes within one compatible family: Boolean `and/or`, equality `equal/not_equal`, ordered comparisons, checked `add/sub/mul`, and checked unary `neg/result_ok`. For example, changing Fixed Limit from an inclusive to an exclusive boundary edits one native operation object:

```sh
sley-tools init exclusive-limit --graph examples/graph/fixed-limit.json \
  --tests examples/graph/fixed-limit-cases.json --core /path/to/sley
sley-tools propose --project exclusive-limit --operation entry.within \
  --opcode less_than --tests examples/graph/fixed-limit-exclusive-cases.json
sley-tools validate --project exclusive-limit
sley-tools commit --project exclusive-limit
sley-tools test --project exclusive-limit
```

Python `program.propose_graph_opcode("entry", "within", "less_than", cases)` and TypeScript `program.proposeGraphOpcode('entry', 'within', 'less_than', cases)` use the same RPC proposal and separate validation/commit. The [opcode edit evidence](../evidence/graph-builder/fixed-limit-opcode-v4.json) exercises CLI and both SDKs, five native boundary cases per adapter plus Boolean, equality, checked binary, and checked unary native matrices, exact one-entity replacement, stale and tampered proposal rejection, and cross-family rejection.

A branch target edit replaces one accepted native Block terminator while preserving the branch condition, arguments, operations, block identity, and all other blocks. `inspect` lists branch selectors and their current targets. The new targets must name existing blocks and keep the graph acyclic and fully reachable. An unconditional branch takes `{"target":"BLOCK"}`; a conditional branch takes `{"if_true":"BLOCK","if_false":"BLOCK"}`. Supply expected cases for the behavior after the edit:

```sh
sley-tools init my-range --graph examples/graph/range-predicate.json \
  --tests examples/graph/range-predicate-cases.json --core /path/to/sley
printf '%s\n' '{"if_true":"valid_bounds","if_false":"invalid_bounds"}' > /tmp/new-targets.json
sley-tools propose --project my-range --branch entry \
  --targets-file /tmp/new-targets.json --tests examples/graph/range-predicate-swapped-cases.json
sley-tools validate --project my-range
sley-tools commit --project my-range
```

Python `program.propose_graph_branch("entry", targets, cases)`, TypeScript `program.proposeGraphBranch('entry', targets, cases)`, and MCP `sley_propose_branch_edit` use the same proposal lifecycle. The [branch edit probe](../evidence/graph-builder/range-predicate-branch-v2.json) verifies CLI and both SDKs with eight native conditional cases each, plus four unconditional cases, malformed and unreachable target rejection, tamper and stale base rejection, and real core commits. The [MCP reference probe](../evidence/mcp/reference-v5.json) also commits and executes the conditional branch edit. Adding/removing blocks, block arguments, and native core test admission remain open.

An operation operand edit replaces one `left`, `right`, or unary `operand` reference in an accepted native operation. It accepts a function parameter (`param.NAME`) or an earlier operation in the same block (`op.NAME`); the selected core checks type compatibility before commit. `constant_ref`, `bool_not`, and `record_new` operand/field edits are outside this targeted profile. The CLI accepts `sley-tools propose --project PROJECT --operation BLOCK.NAME --operand right --reference param.upper --tests CASES`, followed by separate `validate` and `commit`. Python `program.propose_graph_operand(...)`, TypeScript `program.proposeGraphOperand(...)`, and MCP `sley_propose_operand_edit` use the same candidate path. The [operand probe](../evidence/graph-builder/range-predicate-operand-v3.json) repairs Range Predicate's upper-bound comparison through CLI and both SDKs, with seven independent cases per adapter, one changed native operation, a core type refusal, a forward-reference refusal, and proposal-tamper refusal. It separately verifies four checked unary cases and four cases that select an earlier operation. The [MCP probe](../evidence/mcp/reference-v5.json) also changes an accepted operand and observes changed native behavior.

```sh
python3 scripts/probe_graph_operand_edit.py \
  --tools target/debug/sley-tools \
  --core /path/to/sley-2.1.1-linux-x86_64/bin/sley \
  --spec examples/graph/range-predicate.json \
  --output evidence/graph-builder/range-predicate-operand-v3.json
```

Reproduce the bounded witness with:

```sh
python3 scripts/probe_graph_builder.py \
  --tools target/debug/sley-tools \
  --builder target/debug/sley-tools-native-builder \
  --core /path/to/sley-2.1.1-linux-x86_64/bin/sley \
  --spec examples/graph/range-predicate.json \
  --unsigned-spec examples/graph/unsigned-limit.json \
  --output evidence/graph-builder/range-predicate-v2.json
python3 scripts/probe_graph_project.py \
  --tools target/debug/sley-tools \
  --builder target/debug/sley-tools-native-builder \
  --core /path/to/sley-2.1.1-linux-x86_64/bin/sley \
  --schema schemas/program-v0.schema.json \
  --spec examples/graph/range-predicate.json \
  --cases examples/graph/range-predicate-cases.json \
  --unsigned-spec examples/graph/unsigned-limit.json \
  --unsigned-cases examples/graph/unsigned-limit-cases.json \
  --output evidence/graph-builder/public-project-v2.json
python3 scripts/probe_graph_sdk.py \
  --tools target/debug/sley-tools \
  --core /path/to/sley-2.1.1-linux-x86_64/bin/sley \
  --range-spec examples/graph/range-predicate.json \
  --range-cases examples/graph/range-predicate-cases.json \
  --unsigned-spec examples/graph/unsigned-limit.json \
  --unsigned-cases examples/graph/unsigned-limit-cases.json \
  --numeric-spec examples/graph/numeric-echo.json \
  --numeric-cases examples/graph/numeric-echo-cases.json \
  --output evidence/bridge/graph-sdk-v2.json
python3 scripts/probe_graph_edit.py \
  --tools target/debug/sley-tools \
  --builder target/debug/sley-tools-native-builder \
  --core /path/to/sley-2.1.1-linux-x86_64/bin/sley \
  --schema schemas/program-v0.schema.json \
  --spec examples/graph/fixed-limit.json \
  --initial-cases examples/graph/fixed-limit-cases.json \
  --updated-cases examples/graph/fixed-limit-after-cases.json \
  --output evidence/graph-builder/fixed-limit-edit-v4.json
python3 scripts/probe_graph_sdk_authoring.py \
  --tools target/debug/sley-tools \
  --core /path/to/sley-2.1.1-linux-x86_64/bin/sley \
  --spec examples/graph/fixed-limit.json \
  --initial-cases examples/graph/fixed-limit-cases.json \
  --updated-cases examples/graph/fixed-limit-after-cases.json \
  --output evidence/bridge/graph-sdk-authoring-v2.json
python3 scripts/probe_graph_opcode_edit.py \
  --tools target/debug/sley-tools \
  --core /path/to/sley-2.1.1-linux-x86_64/bin/sley \
  --spec examples/graph/fixed-limit.json \
  --initial-cases examples/graph/fixed-limit-cases.json \
  --updated-cases examples/graph/fixed-limit-exclusive-cases.json \
  --output evidence/graph-builder/fixed-limit-opcode-v4.json
python3 scripts/probe_graph_operand_edit.py \
  --tools target/debug/sley-tools \
  --core /path/to/sley-2.1.1-linux-x86_64/bin/sley \
  --spec examples/graph/range-predicate.json \
  --output evidence/graph-builder/range-predicate-operand-v3.json
python3 scripts/probe_graph_branch_edit.py \
  --tools target/debug/sley-tools \
  --core /path/to/sley-2.1.1-linux-x86_64/bin/sley \
  --spec examples/graph/range-predicate.json \
  --initial-cases examples/graph/range-predicate-cases.json \
  --updated-cases examples/graph/range-predicate-swapped-cases.json \
  --output evidence/graph-builder/range-predicate-branch-v2.json
python3 scripts/probe_graph_sdk_creation.py \
  --tools target/debug/sley-tools \
  --core /path/to/sley-2.1.1-linux-x86_64/bin/sley \
  --range-spec examples/graph/range-predicate.json \
  --range-cases examples/graph/range-predicate-cases.json \
  --fixed-spec examples/graph/fixed-limit.json \
  --fixed-cases examples/graph/fixed-limit-cases.json \
  --numeric-spec examples/graph/numeric-echo.json \
  --numeric-cases examples/graph/numeric-echo-cases.json \
  --output evidence/bridge/graph-sdk-creation-v2.json
```
