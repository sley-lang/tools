/** Check Resource Admission's native signed-64 path through the TypeScript SDK. */

import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { SleyToolsError, openRuntime } from '../packages/typescript/src/index.js';

const args = Object.fromEntries(process.argv.slice(2).map((item) => item.split('=', 2)));
for (const name of ['tools', 'core', 'fixtures']) {
  if (!args[name]) throw new Error(`missing ${name}=PATH`);
}
if (Boolean(args.project) === Boolean(args.artifact)) throw new Error('provide exactly one of project=PATH or artifact=PATH');
const fixtures = JSON.parse(await readFile(resolve(args.fixtures), 'utf8'));
if (fixtures.contract !== 'sley-tools.resource-admission-fixtures.v1' || fixtures.cases.length !== 49) {
  throw new Error('Resource Admission fixture contract differs');
}
const runtime = openRuntime({ tools: resolve(args.tools), core: resolve(args.core) });
let pid;
let count = 0;
try {
  const program = args.artifact
    ? await runtime.load({ artifact: resolve(args.artifact) })
    : await runtime.openProject({ project: resolve(args.project) });
  if (program.name !== 'resource-admission') throw new Error('opened the wrong native program');
  pid = runtime.process.pid;
  const workers = new Set();
  for (const testCase of fixtures.cases) {
    const inputs = Object.fromEntries(Object.entries(testCase.input).map(([name, value]) => [name, BigInt(value)]));
    const outcome = await program.call('evaluate', { inputs });
    const observed = outcome.value;
    for (const [name, expected] of Object.entries(testCase.expected)) {
      if (name.endsWith('_memory_mib')) {
        if (observed[name] !== BigInt(expected)) throw new Error(`signed output lost precision: ${testCase.id} ${name}`);
      } else if (observed[name] !== expected) throw new Error(`native decision differs: ${testCase.id} ${name}`);
    }
    if (Object.keys(observed).length !== Object.keys(testCase.expected).length || outcome.metadata.state_root !== program.stateRoot) {
      throw new Error(`native result shape or root differs: ${testCase.id}`);
    }
    workers.add(outcome.metadata.worker_pid);
    count += 1;
  }
  if (workers.size !== 1 || (await program.test()).external_assertions_against_native_execution !== 12) {
    throw new Error('worker reuse or native oracle call differs');
  }
  const base = { requested_workers: 1n, available_workers: 1n, requested_memory_mib: 1n, available_memory_mib: 1n };
  for (const input of [
    { ...base, requested_memory_mib: -1n },
    { ...base, requested_memory_mib: 2n ** 63n },
    { ...base, requested_memory_mib: Number.MAX_SAFE_INTEGER + 1 },
    { ...base, requested_memory_mib: true },
    { requested_workers: 1n },
  ]) {
    try { await program.call('evaluate', { inputs: input }); throw new Error('invalid signed input was accepted'); }
    catch (error) {
      if (!(error instanceof SleyToolsError) || error.code !== 'ST_INPUT_INVALID') throw error;
    }
  }
} finally {
  await runtime.close();
}
try { process.kill(pid, 0); throw new Error('TypeScript SDK leaked Resource Admission RPC process'); }
catch (error) { if (error.code !== 'ESRCH') throw error; }
process.stdout.write(JSON.stringify({
  contract: 'sley-tools.resource-typescript-probe.v1', result: 'PASS', native_cases: count,
  one_worker_for_matrix: true, signed_result_bigint_exact: true, typed_input_negatives: 5,
  runtime_closed_process: true,
}) + '\n');
