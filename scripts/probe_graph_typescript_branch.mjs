/** Exercise one native graph branch target edit through the TypeScript SDK. */

import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { openRuntime, SleyToolsError } from '../packages/typescript/src/index.js';

const args = Object.fromEntries(process.argv.slice(2).map((part) => part.split('=', 2)));
for (const name of ['tools', 'core', 'project', 'cases']) {
  if (!args[name]) throw new Error(`missing ${name}=PATH`);
}
const fixture = JSON.parse(await readFile(resolve(args.cases), 'utf8'));
const cases = fixture.cases.map((row) => ({ id: row.id,
  inputs: Object.fromEntries(['value', 'lower', 'upper'].map((name, index) => [name, BigInt(row.input[index])])),
  expected: row.expected }));
const runtime = openRuntime({ tools: resolve(args.tools), core: resolve(args.core) });
let report;
try {
  const program = await runtime.openProject({ project: resolve(args.project) });
  const oldRoot = program.stateRoot;
  try {
    await program.proposeGraphBranch('entry', { target: 'valid_bounds' }, cases);
    throw new Error('wrong branch target shape was accepted');
  } catch (error) {
    if (!(error instanceof SleyToolsError) || error.code !== 'ST_INPUT_INVALID') throw error;
  }
  const proposed = await program.proposeGraphBranch('entry',
    { if_true: 'valid_bounds', if_false: 'invalid_bounds' }, cases);
  const validation = await program.validate(proposed.candidate_id);
  if (validation.result !== 'PASS' || program.stateRoot !== oldRoot) {
    throw new Error('TypeScript branch validation changed accepted state');
  }
  const committed = await program.commit(proposed.candidate_id);
  if (committed.new_root === oldRoot || committed.external_assertions !== cases.length
      || program.stateRoot !== committed.new_root) {
    throw new Error('TypeScript branch commit lost accepted root or assertions');
  }
  for (const row of cases) {
    const observed = await program.callGraph('evaluate', { inputs: row.inputs });
    if (JSON.stringify(observed.value) !== JSON.stringify(row.expected)) {
      throw new Error(`TypeScript branch result differs for ${row.id}`);
    }
  }
  report = { contract: 'sley-tools.graph-typescript-branch-probe.v1', result: 'PASS',
    old_root: oldRoot, new_root: committed.new_root, candidate_id: proposed.candidate_id,
    native_cases: cases.length, wrong_shape_rejected: true };
} finally {
  await runtime.close();
}
process.stdout.write(`${JSON.stringify(report)}\n`);
