/** Verify a targeted native graph opcode edit through the TypeScript SDK. */

import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

import { openRuntime, SleyToolsError } from '../packages/typescript/src/index.js';

const args = Object.fromEntries(process.argv.slice(2).map((part) => part.split('=', 2)));
for (const name of ['tools', 'core', 'project', 'cases']) {
  if (!args[name]) throw new Error(`missing ${name}=PATH`);
}
const fixture = JSON.parse(await readFile(resolve(args.cases), 'utf8'));
const cases = fixture.cases.map((row) => ({ id: row.id,
  inputs: { count: Number(row.input[0]) }, expected: row.expected }));
const runtime = openRuntime({ tools: resolve(args.tools), core: resolve(args.core) });
let report;
try {
  const program = await runtime.openProject({ project: resolve(args.project) });
  const oldRoot = program.stateRoot;
  const oldHandle = runtime.handle;
  try {
    await program.proposeGraphOpcode('entry', 'within', 'record_new', cases);
    throw new Error('unsupported graph opcode was accepted');
  } catch (error) {
    if (!(error instanceof SleyToolsError) || error.code !== 'ST_INPUT_INVALID') throw error;
  }
  const proposed = await program.proposeGraphOpcode('entry', 'within', 'less_than', cases);
  if (proposed.base_root !== oldRoot || proposed.old_opcode !== 'less_equal'
    || proposed.new_opcode !== 'less_than') throw new Error('TypeScript opcode proposal lost its base');
  const validation = await program.validate(proposed.candidate_id);
  if (validation.result !== 'PASS' || validation.accepted_root_unchanged !== true) {
    throw new Error('TypeScript opcode validation changed accepted state');
  }
  const committed = await program.commit(proposed.candidate_id);
  if (committed.new_root === oldRoot || committed.changed_case_count !== 1
    || committed.external_assertions !== cases.length || committed.postcommit_assertions_status !== 'PASS'
    || program.stateRoot !== committed.new_root || runtime.handle === oldHandle) {
    throw new Error('TypeScript opcode commit lost native identity or assertions');
  }
  for (const [count, expected] of [[9, true], [10, false], [11, false]]) {
    const observed = await program.callGraph('evaluate', { inputs: { count } });
    if (observed.value !== expected || observed.metadata.state_root !== committed.new_root) {
      throw new Error(`TypeScript opcode edit produced wrong value for ${count}`);
    }
  }
  report = { contract: 'sley-tools.graph-typescript-opcode-probe.v1', result: 'PASS',
    old_root: oldRoot, new_root: committed.new_root, candidate_id: proposed.candidate_id,
    changed_case_count: committed.changed_case_count, native_cases: cases.length,
    unsupported_opcode_rejected: true, handle_rotated: true };
} finally {
  await runtime.close();
}
process.stdout.write(`${JSON.stringify(report)}\n`);
