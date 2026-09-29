/** Validate, commit, and reopen one native graph constant edit in TypeScript. */

import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

import { openRuntime, SleyToolsError } from '../packages/typescript/src/index.js';

const args = Object.fromEntries(process.argv.slice(2).map((item) => item.split('=', 2)));
for (const name of ['tools', 'core', 'project', 'cases']) {
  if (!args[name]) throw new Error(`missing ${name}=PATH`);
}

async function expectInputError(operation) {
  try { await operation(); }
  catch (error) {
    if (error instanceof SleyToolsError && error.code === 'ST_INPUT_INVALID') return;
    throw error;
  }
  throw new Error('invalid TypeScript graph proposal was accepted');
}

const fixture = JSON.parse(await readFile(resolve(args.cases), 'utf8'));
const cases = fixture.cases.map((row) => ({
  id: row.id, inputs: { count: Number(row.input[0]) }, expected: row.expected,
}));
const runtime = openRuntime({ tools: resolve(args.tools), core: resolve(args.core) });
try {
  const program = await runtime.openProject({ project: resolve(args.project) });
  const oldRoot = program.stateRoot;
  const oldHandle = runtime.handle;
  if ((await program.callGraph('evaluate', { inputs: { count: 11 } })).value !== false) {
    throw new Error('initial native limit did not reject 11');
  }
  await expectInputError(() => program.proposeGraphConstant('limit', true, cases));
  await expectInputError(() => program.proposeGraphConstant('absent', 12, cases));
  const proposal = await program.proposeGraphConstant('limit', 12, cases);
  if (proposal.phase !== 'PROPOSED' || proposal.base_root !== oldRoot) {
    throw new Error('TypeScript graph proposal lost its accepted base');
  }
  await expectInputError(() => program.proposeGraphConstant('limit', 13, cases));
  const validation = await program.validate(proposal.candidate_id);
  if (validation.result !== 'PASS' || validation.accepted_root_unchanged !== true) {
    throw new Error('TypeScript graph validation changed accepted state');
  }
  const committed = await program.commit(proposal.candidate_id);
  if (committed.candidate_id !== proposal.candidate_id || committed.new_root === oldRoot
    || committed.changed_case_count !== 2 || committed.external_assertions !== 6
    || committed.postcommit_assertions_status !== 'PASS'
    || program.stateRoot !== committed.new_root || runtime.handle === oldHandle) {
    throw new Error('TypeScript graph commit or reopen lost native identity');
  }
  for (const [count, expected] of [[11, true], [12, true], [13, false]]) {
    const observed = await program.callGraph('evaluate', { inputs: { count } });
    if (observed.value !== expected || observed.metadata.state_root !== committed.new_root) {
      throw new Error('TypeScript graph changed the wrong native boundary');
    }
  }
  if ((await program.test()).external_assertions_against_native_execution !== 6) {
    throw new Error('TypeScript graph commit omitted updated external assertions');
  }
  process.stdout.write(JSON.stringify({
    contract: 'sley-tools.graph-typescript-authoring-probe.v1', result: 'PASS',
    old_root: oldRoot, new_root: committed.new_root,
    candidate_id: proposal.candidate_id, changed_case_count: committed.changed_case_count,
  }) + '\n');
} finally {
  await runtime.close();
}
