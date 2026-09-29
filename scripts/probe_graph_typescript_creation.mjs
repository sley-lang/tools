/** Construct two fresh native graph projects through the TypeScript SDK. */

import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { isDeepStrictEqual } from 'node:util';

import { GraphBuilder, openRuntime, SleyToolsError } from '../packages/typescript/src/index.js';

const args = Object.fromEntries(process.argv.slice(2).map((item) => item.split('=', 2)));
for (const name of ['tools', 'core', 'fixed_spec', 'fixed_cases', 'fixed_project',
  'numeric_spec', 'numeric_cases', 'numeric_project']) {
  if (!args[name]) throw new Error(`missing ${name}=PATH`);
}

async function expectInputError(operation) {
  try { await operation(); }
  catch (error) {
    if (error instanceof SleyToolsError && error.code === 'ST_INPUT_INVALID') return;
    throw error;
  }
  throw new Error('invalid graph creation was accepted');
}

const tools = resolve(args.tools);
const core = resolve(args.core);
const fixedBuilder = new GraphBuilder({ programId: 'fixed-limit', title: 'Fixed Limit',
  purpose: 'Decide whether a nonnegative count is within a locally configured limit' });
fixedBuilder.parameter('count', 'UInt32').constant('limit', 'UInt32', 10n).primitiveResult('Bool');
fixedBuilder.block('entry').constantRef('accepted_limit', 'limit')
  .binary('less_equal', 'within', 'param.count', 'op.accepted_limit').returnValue('op.within');
const fixedRecipe = fixedBuilder.build();
const fixedFixtureRecipe = JSON.parse(await readFile(resolve(args.fixed_spec), 'utf8'));
if (!isDeepStrictEqual(JSON.parse(JSON.stringify(fixedRecipe,
  (_, value) => typeof value === 'bigint' ? value.toString() : value)), fixedFixtureRecipe)) {
  throw new Error('TypeScript fluent builder differs from the checked-in Fixed Limit recipe');
}
await expectInputError(() => fixedBuilder.parameter('count', 'Bool'));
await expectInputError(() => new GraphBuilder({ programId: 'bad-id', title: 'Bad', purpose: 'Bad' })
  .primitiveResult('Bool').block('entry').end().build());
await expectInputError(() => fixedBuilder.block('extra').returnValue('param.count').binary('equal',
  'late', 'param.count', 'param.count'));
const fixedFixture = JSON.parse(await readFile(resolve(args.fixed_cases), 'utf8'));
const fixedCases = fixedFixture.cases.map((row) => ({
  id: row.id, inputs: { count: Number(row.input[0]) }, expected: row.expected,
}));

const fixedRuntime = openRuntime({ tools, core });
let fixedRoot;
try {
  await expectInputError(() => fixedRuntime.createGraph({ project: resolve(args.fixed_project),
    recipe: { ...fixedRecipe, constants: [{ ...fixedRecipe.constants[0], value: '10' }] }, cases: fixedCases }));
  await expectInputError(() => fixedRuntime.createGraph({ project: resolve(args.fixed_project),
    recipe: fixedRecipe, cases: [{ ...fixedCases[0], inputs: { count: '0' } }] }));
  const fixed = await fixedRuntime.createGraph({ project: resolve(args.fixed_project),
    recipe: fixedRecipe, cases: fixedCases });
  if (!fixed.graphProfile || fixed.graphConstants[0].value !== '10') {
    throw new Error('TypeScript graph construction lost the native constant');
  }
  fixedRoot = fixed.stateRoot;
  for (const row of fixedCases) {
    const observed = await fixed.callGraph('evaluate', { inputs: row.inputs });
    if (observed.value !== row.expected || observed.metadata.state_root !== fixedRoot) {
      throw new Error(`TypeScript created Fixed Limit differs from ${row.id}`);
    }
  }
  if ((await fixed.test()).external_assertions_against_native_execution !== fixedCases.length) {
    throw new Error('TypeScript created Fixed Limit omitted external assertions');
  }
} finally {
  await fixedRuntime.close();
}
const duplicateRuntime = openRuntime({ tools, core });
await expectInputError(() => duplicateRuntime.createGraph({ project: resolve(args.fixed_project),
  recipe: fixedRecipe, cases: fixedCases }));
await duplicateRuntime.close();

const numericRecipe = JSON.parse(await readFile(resolve(args.numeric_spec), 'utf8'));
const numericFixture = JSON.parse(await readFile(resolve(args.numeric_cases), 'utf8'));
const numericCases = numericFixture.cases.map((row) => ({
  id: row.id,
  inputs: { signed: BigInt(row.input[0]), unsigned: Number(row.input[1]) },
  expected: { signed: BigInt(row.expected.signed), unsigned: Number(row.expected.unsigned) },
}));
const numericRuntime = openRuntime({ tools, core });
let numericRoot;
try {
  const numeric = await numericRuntime.createGraph({ project: resolve(args.numeric_project),
    recipe: numericRecipe, cases: numericCases });
  numericRoot = numeric.stateRoot;
  for (const row of numericCases) {
    const observed = await numeric.callGraph('evaluate', { inputs: row.inputs });
    if (observed.value.signed !== row.expected.signed || observed.value.unsigned !== row.expected.unsigned
      || observed.metadata.state_root !== numericRoot) {
      throw new Error(`TypeScript created Numeric Echo differs from ${row.id}`);
    }
  }
  if ((await numeric.test()).external_assertions_against_native_execution !== numericCases.length) {
    throw new Error('TypeScript created Numeric Echo omitted external assertions');
  }
} finally {
  await numericRuntime.close();
}

process.stdout.write(JSON.stringify({ contract: 'sley-tools.graph-typescript-creation-probe.v1', result: 'PASS',
  fixed_root: fixedRoot, numeric_root: numericRoot,
  fixed_native_cases: fixedCases.length, numeric_native_cases: numericCases.length,
  fluent_recipe_matched: true, fluent_rejections: 3,
  invalid_input_rejected: true, existing_project_rejected: true }) + '\n');
