/** Real worker cancellation for the asynchronous TypeScript SDK. */

import { readFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';

import { SleyToolsError, openRuntime } from '../packages/typescript/src/index.js';

const args = Object.fromEntries(process.argv.slice(2).map((item) => item.split('=', 2)));
const facts = { test_run_completed: true, tests_failed: 0, artifact_verified: true, blocking_findings: 0 };

async function stopped(pid) {
  try {
    const stat = await readFile(`/proc/${pid}/stat`, 'utf8');
    return stat.split(') ')[1][0] === 'Z';
  } catch (error) {
    if (error.code === 'ENOENT') return true;
    throw error;
  }
}

async function waitStopped(pid) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (await stopped(pid)) return;
    await delay(50);
  }
  throw new Error(`cancelled process ${pid} remains running`);
}

const runtime = openRuntime({ tools: args.tools, core: args.core });
let rpcPid;
let workerPid;
try {
  const program = await runtime.load({ artifact: args.artifact });
  rpcPid = runtime.process.pid;
  const preCancelled = new AbortController();
  preCancelled.abort();
  try {
    await program.call('evaluate', { inputs: facts, signal: preCancelled.signal });
    throw new Error('TypeScript pre-dispatch cancellation returned success');
  } catch (error) {
    if (!(error instanceof SleyToolsError) || error.code !== 'ST_CANCELLED' || error.category !== 'execution') throw error;
  }
  try {
    await program.call('evaluate', { inputs: facts, signal: { aborted: false } });
    throw new Error('TypeScript accepted a non-AbortSignal cancellation input');
  } catch (error) {
    if (!(error instanceof SleyToolsError) || error.code !== 'ST_INPUT_INVALID') throw error;
  }
  const first = await program.call('evaluate', { inputs: facts });
  try {
    await runtime.request('sleytools.program.propose', { handle: runtime.handle, change: {} },
      5000, new AbortController().signal);
    throw new Error('TypeScript accepted cancellation on a write operation');
  } catch (error) {
    if (!(error instanceof SleyToolsError) || error.code !== 'ST_INPUT_INVALID') throw error;
  }
  workerPid = first.metadata.worker_pid;
  process.kill(workerPid, 'SIGSTOP');
  const controller = new AbortController();
  const previousId = runtime.nextId;
  const pending = program.call('evaluate', { inputs: facts, deadlineMs: 10000, signal: controller.signal });
  for (let attempt = 0; runtime.nextId === previousId && attempt < 100; attempt += 1) await delay(10);
  if (runtime.nextId === previousId) throw new Error('TypeScript native call did not dispatch before cancellation');
  const cancelledAt = Date.now();
  controller.abort();
  try {
    await pending;
    throw new Error('TypeScript cancelled native call returned success');
  } catch (error) {
    if (!(error instanceof SleyToolsError) || error.code !== 'ST_CANCELLED' || error.category !== 'execution') throw error;
  }
  if (Date.now() - cancelledAt > 3000) throw new Error('TypeScript cancellation exceeded three seconds');
} finally {
  await runtime.close();
}
await waitStopped(rpcPid);
await waitStopped(workerPid);
process.stdout.write('native worker and RPC process stopped after AbortSignal');
