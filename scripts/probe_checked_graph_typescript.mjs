/** Create and verify the checked native comparator through the TypeScript SDK. */

import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { isDeepStrictEqual } from 'node:util';

import { openRuntime } from '../packages/typescript/src/index.js';

const args = Object.fromEntries(process.argv.slice(2).map((item) => item.split('=', 2)));
for (const name of ['tools', 'core', 'spec', 'cases', 'project']) {
  if (!args[name]) throw new Error(`missing ${name}=PATH`);
}
const recipe = JSON.parse(await readFile(args.spec, 'utf8'));
recipe.constants[0].value = 0n;
const fixture = JSON.parse(await readFile(args.cases, 'utf8'));
const rows = fixture.cases.map((row) => ({
  id: row.id,
  inputs: Object.fromEntries(recipe.parameters.map((parameter, index) =>
    [parameter.name, BigInt(row.input[index])])),
  expected: Object.fromEntries(Object.entries(row.expected).map(([name, value]) =>
    [name, value && typeof value === 'object' && Object.hasOwn(value, 'ok')
      ? { ok: BigInt(value.ok) } : value])),
}));
const runtime = openRuntime({ tools: resolve(args.tools), core: resolve(args.core) });
try {
  const program = await runtime.createGraph({ project: resolve(args.project), recipe, cases: rows });
  if (!program.graphProfile || program.name !== 'benchmark-comparator') {
    throw new Error('TypeScript comparator did not create a native graph');
  }
  for (const row of rows) {
    const result = await program.callGraph('evaluate', { inputs: row.inputs });
    if (!isDeepStrictEqual(result.value, row.expected)
      || result.metadata.state_root !== program.stateRoot) {
      throw new Error(`TypeScript native comparator differs from independent case ${row.id}`);
    }
  }
  const tested = await program.test();
  if (tested.external_assertions_against_native_execution !== rows.length
    || tested.native_core_test_admission !== 'NOT_RUN: production native executor unavailable') {
    throw new Error('TypeScript comparator test status differs');
  }
  process.stdout.write(JSON.stringify({ contract: 'sley-tools.checked-graph-typescript-probe.v1',
    result: 'PASS', accepted_root: program.stateRoot, native_cases: rows.length,
    exact_overflow: true }) + '\n');
} finally {
  await runtime.close();
}
