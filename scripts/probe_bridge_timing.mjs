/** Measure a correctness-checked Bridge startup and warm call matrix in Node. */

import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

import { openRuntime } from '../packages/typescript/src/index.js';

const args = Object.fromEntries(process.argv.slice(2).map((item) => item.split('=', 2)));
for (const name of ['tools', 'core', 'artifact', 'fixtures']) {
  if (!args[name]) throw new Error(`missing ${name}=PATH`);
}
const fixture = JSON.parse(await readFile(resolve(args.fixtures), 'utf8'));
if (fixture.contract !== 'sley-tools.release-readiness-fixtures.v1' || fixture.cases?.length !== 48) {
  throw new Error('timing requires the 48-case native Release Readiness fixture');
}
const COLD_RUNS = 5;
const RSS_SAMPLE_EVERY = 12;

function summarize(samples) {
  const ordered = [...samples].sort((a, b) => a - b);
  const middle = Math.floor(ordered.length / 2);
  const median = ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2;
  return { samples: ordered.length, min_ms: ordered[0], median_ms: median,
    p95_ms: ordered[Math.ceil(ordered.length * 0.95) - 1], max_ms: ordered.at(-1) };
}

async function residentKib(pid) {
  const status = await readFile(`/proc/${pid}/status`, 'utf8');
  const match = status.match(/^VmRSS:\s+(\d+) kB$/m);
  if (!match) throw new Error(`process ${pid} has no VmRSS measurement`);
  return Number(match[1]);
}

function sameValue(actual, expected) {
  return Object.keys(actual).length === Object.keys(expected).length
    && Object.keys(expected).every((name) => actual[name] === expected[name]);
}

const runtime = openRuntime({ tools: resolve(args.tools), core: resolve(args.core) });
const started = process.hrtime.bigint();
let program;
const warm = [];
const rpc = [];
const rssSamples = [];
let workerPid;
let coldLoadMs;
let limits;
let batchSeconds;
try {
  program = await runtime.load({ artifact: resolve(args.artifact) });
  coldLoadMs = Number(process.hrtime.bigint() - started) / 1e6;
  limits = runtime.hello.limits;
  const batchStarted = process.hrtime.bigint();
  for (const [index, testCase] of fixture.cases.entries()) {
    const callStarted = process.hrtime.bigint();
    const result = await program.call('evaluate', { inputs: testCase.input });
    warm.push(Number(process.hrtime.bigint() - callStarted) / 1e6);
    if (!sameValue(result.value, testCase.expected) || result.metadata.state_root !== program.stateRoot) {
      throw new Error(`native result differs from fixture or root at ${testCase.id}`);
    }
    if (workerPid === undefined) workerPid = result.metadata.worker_pid;
    else if (workerPid !== result.metadata.worker_pid) throw new Error('native worker changed during warm calls');
    if (index === 0 || (index + 1) % RSS_SAMPLE_EVERY === 0) {
      rssSamples.push({ after_calls: index + 1,
        client_kib: await residentKib(process.pid), rpc_kib: await residentKib(runtime.process.pid),
        worker_kib: await residentKib(workerPid) });
    }
    if (!Number.isSafeInteger(result.metadata.end_to_end_ms) || result.metadata.end_to_end_ms < 0) {
      throw new Error('RPC duration is absent or invalid');
    }
    rpc.push(result.metadata.end_to_end_ms);
  }
  batchSeconds = Number(process.hrtime.bigint() - batchStarted) / 1e9;
} finally {
  await runtime.close();
}
const coldSamples = [coldLoadMs];
for (let run = 1; run < COLD_RUNS; run += 1) {
  const freshStarted = process.hrtime.bigint();
  const fresh = openRuntime({ tools: resolve(args.tools), core: resolve(args.core) });
  try {
    const freshProgram = await fresh.load({ artifact: resolve(args.artifact) });
    coldSamples.push(Number(process.hrtime.bigint() - freshStarted) / 1e6);
    const first = await freshProgram.call('evaluate', { inputs: fixture.cases[0].input });
    if (!sameValue(first.value, fixture.cases[0].expected)
        || first.metadata.state_root !== freshProgram.stateRoot) {
      throw new Error('fresh Node cold load differs from native fixture');
    }
  } finally {
    await fresh.close();
  }
}
process.stdout.write(`${JSON.stringify({ cold_load_ms: coldLoadMs,
  node_version: process.version,
  cold_load_samples_ms: coldSamples, cold_load_summary: summarize(coldSamples),
  cold_load_scope: 'openRuntime construction through artifact import and first opened RPC/native worker; excludes first call',
  warm_client_call: summarize(warm), rpc_dispatch_to_worker_result: summarize(rpc),
  rpc_scope: 'coarse integer milliseconds from RPC dispatch through native worker result; zero denotes less than one millisecond, not zero elapsed time',
  batch_elapsed_seconds: batchSeconds, batch_calls_per_second: fixture.cases.length / batchSeconds,
  declared_rpc_limits: limits, rss_samples: rssSamples,
  sampled_peak_client_rpc_worker_rss_kib: Math.max(...rssSamples.map((sample) =>
    sample.client_kib + sample.rpc_kib + sample.worker_kib)),
  fixture_cases_checked: fixture.cases.length, worker_reused: true })}\n`);
