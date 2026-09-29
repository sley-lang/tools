/** Measure bounded Node SDK worker reuse, descriptor stability, and cleanup. */

import { readFile, readdir } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import { resolve } from 'node:path';

import { openRuntime } from '../packages/typescript/src/index.js';

const args = Object.fromEntries(process.argv.slice(2).map((item) => item.split('=', 2)));
for (const name of ['tools', 'core', 'artifact', 'fixtures']) {
  if (!args[name]) throw new Error(`missing ${name}=PATH`);
}
const CALLS = 256;
const SAMPLE_EVERY = 64;
const MAX_FD_DRIFT = 2;
const cases = JSON.parse(await readFile(resolve(args.fixtures), 'utf8'));
if (cases.contract !== 'sley-tools.release-readiness-fixtures.v1' || cases.cases?.length !== 48) {
  throw new Error('soak requires the 48-case Release Readiness native fixture');
}

async function descriptorCount(pid) { return (await readdir(`/proc/${pid}/fd`)).length; }
async function gone(pid) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try { process.kill(pid, 0); }
    catch (error) { if (error.code === 'ESRCH') return true; throw error; }
    await delay(10);
  }
  return false;
}
function sameValue(actual, expected) {
  return Object.keys(actual).length === Object.keys(expected).length
    && Object.keys(expected).every((name) => actual[name] === expected[name]);
}

const runtime = openRuntime({ tools: resolve(args.tools), core: resolve(args.core) });
let rpcPid;
let workerPid;
let baseline;
const samples = [];
try {
  const program = await runtime.load({ artifact: resolve(args.artifact) });
  rpcPid = runtime.process.pid;
  for (const testCase of cases.cases.slice(0, 16)) {
    const result = await program.call('evaluate', { inputs: testCase.input });
    if (!sameValue(result.value, testCase.expected)) throw new Error(`Node warmup differs from native fixture ${testCase.id}`);
    if (workerPid === undefined) workerPid = result.metadata.worker_pid;
    else if (result.metadata.worker_pid !== workerPid) throw new Error('Node warmup replaced its native worker');
  }
  baseline = { client: await descriptorCount(process.pid), rpc: await descriptorCount(rpcPid),
    worker: await descriptorCount(workerPid) };
  for (let index = 0; index < CALLS; index += 1) {
    const testCase = cases.cases[index % cases.cases.length];
    const result = await program.call('evaluate', { inputs: testCase.input });
    if (!sameValue(result.value, testCase.expected)) throw new Error(`Node soak differs from native fixture ${testCase.id}`);
    if (result.metadata.worker_pid !== workerPid || result.metadata.state_root !== program.stateRoot) {
      throw new Error('Node soak changed its native worker or accepted root');
    }
    if ((index + 1) % SAMPLE_EVERY === 0) {
      const sample = { calls: index + 1, client: await descriptorCount(process.pid),
        rpc: await descriptorCount(rpcPid), worker: await descriptorCount(workerPid) };
      if (['client', 'rpc', 'worker'].some((name) => sample[name] > baseline[name] + MAX_FD_DRIFT)) {
        throw new Error(`Node soak grew file descriptors: ${JSON.stringify(baseline)} -> ${JSON.stringify(sample)}`);
      }
      samples.push(sample);
    }
  }
} finally {
  await runtime.close();
}
if (!(await gone(rpcPid)) || !(await gone(workerPid))) {
  throw new Error('Node SDK left its RPC process or native worker alive');
}
process.stdout.write(`${JSON.stringify({ calls: CALLS, worker_reused: true, root_pinned: true,
  baseline_fds: baseline, fd_samples: samples, max_allowed_fd_drift: MAX_FD_DRIFT,
  processes_exited: true })}\n`);
