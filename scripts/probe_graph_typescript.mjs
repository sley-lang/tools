/** Exercise both public graph result shapes through the TypeScript SDK. */

import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

import { openRuntime, SleyToolsError } from '../packages/typescript/src/index.js';

const args = Object.fromEntries(process.argv.slice(2).map((item) => item.split('=', 2)));
for (const name of ['tools', 'core', 'range_project', 'range_cases', 'unsigned_project', 'unsigned_cases', 'numeric_project', 'numeric_cases']) {
  if (!args[name]) throw new Error(`missing ${name}=PATH`);
}

async function expectInputError(operation) {
  try { await operation(); }
  catch (error) {
    if (error instanceof SleyToolsError && error.code === 'ST_INPUT_INVALID') return;
    throw error;
  }
  throw new Error('TypeScript graph SDK accepted invalid input');
}

async function profile(projectPath, casesPath) {
  const fixture = JSON.parse(await readFile(resolve(casesPath), 'utf8'));
  const runtime = openRuntime({ tools: resolve(args.tools), core: resolve(args.core) });
  try {
    const program = await runtime.openProject({ project: resolve(projectPath) });
    if (!program.graphProfile) throw new Error('TypeScript SDK opened the wrong profile');
    const workers = new Set();
    for (const row of fixture.cases) {
      const inputs = Object.fromEntries(program.graphParameters.map((parameter, index) => [
        parameter.name,
        parameter.value_type === 'SInt64' ? BigInt(row.input[index])
          : parameter.value_type === 'UInt32' ? Number(row.input[index]) : row.input[index],
      ]));
      const observed = await program.callGraph('evaluate', { inputs });
      const expected = program.graphResult.kind === 'primitive'
        ? row.expected
        : Object.fromEntries(program.graphResult.fields.map((field) => [
          field.name,
          field.value_type === 'SInt64' ? BigInt(row.expected[field.name])
            : field.value_type === 'UInt32' ? Number(row.expected[field.name]) : row.expected[field.name],
        ]));
      const equal = typeof expected === 'object'
        ? observed.value && Object.keys(observed.value).length === Object.keys(expected).length
          && Object.keys(expected).every((name) => observed.value[name] === expected[name])
        : observed.value === expected;
      if (!equal) {
        throw new Error(`TypeScript native graph differs from ${row.id}`);
      }
      if (observed.metadata.state_root !== program.stateRoot || observed.metadata.function_id !== program.functionId) {
        throw new Error('TypeScript native graph result lost accepted identity');
      }
      workers.add(observed.metadata.worker_pid);
    }
    if (workers.size !== 1) throw new Error('TypeScript graph worker was not reused');
    if ((await program.test()).external_assertions_against_native_execution !== fixture.cases.length) {
      throw new Error('TypeScript graph SDK omitted external assertions');
    }
    const first = Object.fromEntries(program.graphParameters.map((parameter, index) => [
      parameter.name, parameter.value_type === 'SInt64' ? BigInt(fixture.cases[0].input[index])
        : parameter.value_type === 'UInt32' ? Number(fixture.cases[0].input[index]) : fixture.cases[0].input[index],
    ]));
    const name = program.graphParameters[0].name;
    await expectInputError(() => program.callGraph('evaluate', { inputs: { ...first, [name]: '01' } }));
    await expectInputError(() => program.callGraph('evaluate', { inputs: { ...first, extra: true } }));
    return fixture.cases.length;
  } finally {
    await runtime.close();
  }
}

const range = await profile(args.range_project, args.range_cases);
const unsigned = await profile(args.unsigned_project, args.unsigned_cases);
const numeric = await profile(args.numeric_project, args.numeric_cases);
process.stdout.write(JSON.stringify({ contract: 'sley-tools.graph-typescript-probe.v1', result: 'PASS', range, unsigned, numeric }) + '\n');
