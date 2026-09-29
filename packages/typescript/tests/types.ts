import { GraphBuilder, encodeInteger, openRuntime, type GraphRecipe, type ReleaseReadinessInputs, type ResourceAdmissionInputs } from '../src/index.js';

const facts: ReleaseReadinessInputs = {
  test_run_completed: true,
  tests_failed: 0n,
  artifact_verified: true,
  blocking_findings: 2,
};

const encoded = encodeInteger(2n ** 53n, 'i64');
const runtime = openRuntime({ tools: '/path/to/sley-tools', core: '/path/to/sley' });
const program = await runtime.load({ artifact: '/path/to/native-program.json' });
const result = await program.call('evaluate', { inputs: facts });
const allowed: boolean = result.value.allow;
void [encoded, allowed];
const resources: ResourceAdmissionInputs = {
  requested_workers: 2n,
  available_workers: 4n,
  requested_memory_mib: 2n ** 53n + 1n,
  available_memory_mib: 2n ** 53n + 1n,
};
const resourceRuntime = openRuntime({ tools: '/path/to/sley-tools', core: '/path/to/sley' });
const resourceProgram = await resourceRuntime.openProject({ project: '/path/to/project' });
const resourceResult = await resourceProgram.call('evaluate', { inputs: resources });
if ('requested_memory_mib' in resourceResult.value) {
  const exact: bigint = resourceResult.value.requested_memory_mib;
  void exact;
}
await resourceRuntime.close();
await runtime.close();

const graphRuntime = openRuntime({ tools: '/path/to/sley-tools', core: '/path/to/sley' });
const graphProgram = await graphRuntime.openProject({ project: '/path/to/graph-project' });
const graphResult = await graphProgram.callGraph('evaluate', { inputs: { value: 2n ** 53n + 1n } });
void graphResult.value;
await graphProgram.proposeGraphConstant('limit', 12, [
  { id: 'at-limit', inputs: { count: 12 }, expected: true },
]);
await graphProgram.proposeGraphOpcode('entry', 'within', 'less_than', [
  { id: 'boundary-excluded', inputs: { count: 10 }, expected: false },
]);
await graphProgram.proposeGraphOperand('entry', 'within', 'right', 'param.count', [
  { id: 'self-comparison', inputs: { count: 10 }, expected: true },
]);
await graphProgram.proposeGraphBranch('entry',
  { if_true: 'valid_bounds', if_false: 'invalid_bounds' }, [
    { id: 'swapped', inputs: { value: 0, lower: -5, upper: 5 },
      expected: { within_range: false, invalid_bounds: true,
        below_lower: false, above_upper: false } },
  ]);
await graphRuntime.close();

const graphRecipe: GraphRecipe = {
  contract: 'sley-tools.graph-authoring.v0', program_id: 'limit', title: 'Limit', purpose: 'Compare with a limit',
  parameters: [{ name: 'count', value_type: 'UInt32' }],
  constants: [{ name: 'limit', value_type: 'UInt32', value: 12 }],
  result: { kind: 'primitive', value_type: 'Bool' }, entry_block: 'entry',
  blocks: [{ name: 'entry', operations: [
    { op: 'constant_ref', name: 'bound', constant: 'limit' },
    { op: 'less_equal', name: 'allowed', left: 'param.count', right: 'op.bound' },
  ], terminator: { kind: 'return', value: 'op.allowed' } }],
};
const fluentGraph = new GraphBuilder({ programId: 'limit', title: 'Limit', purpose: 'Compare with a limit' });
fluentGraph.parameter('count', 'UInt32').constant('limit', 'UInt32', 12n).primitiveResult('Bool');
fluentGraph.block('entry').constantRef('bound', 'limit')
  .binary('less_equal', 'allowed', GraphBuilder.param('count'), GraphBuilder.op('bound'))
  .returnValue(GraphBuilder.op('allowed'));
const fluentRecipe: GraphRecipe = fluentGraph.build();
void fluentRecipe;
const creationRuntime = openRuntime({ tools: '/path/to/sley-tools', core: '/path/to/sley' });
await creationRuntime.createGraph({ project: '/path/to/new-project', recipe: graphRecipe,
  cases: [{ id: 'at-limit', inputs: { count: 12 }, expected: true }] });
await creationRuntime.close();

// @ts-expect-error Checked arithmetic is only available as a result type.
fluentGraph.parameter('invalid', 'CheckedSInt64');

// @ts-expect-error Strings are not accepted as integer values.
encodeInteger('42', 'i64');
// @ts-expect-error The input record requires all four fields.
await program.call('evaluate', { inputs: { test_run_completed: true } });
