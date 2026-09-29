/** Verify the async Node/TypeScript client against shared native fixtures. */

import { createHash } from 'node:crypto';
import { copyFile, chmod, mkdtemp, readFile, rm, writeFile, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';

import { SleyToolsError, encodeInteger, openRuntime } from '../packages/typescript/src/index.js';

const args = Object.fromEntries(process.argv.slice(2).map((item) => item.split('=', 2)));
for (const name of ['tools', 'core', 'artifact', 'fixtures', 'output']) {
  if (!args[name]) throw new Error(`missing ${name}=PATH`);
}

function digest(bytes) { return createHash('sha256').update(bytes).digest('hex'); }

async function expectInputError(operation) {
  try { await operation(); }
  catch (error) {
    if (error instanceof SleyToolsError && error.code === 'ST_INPUT_INVALID' && error.category === 'input_shape') return;
    throw new Error(`wrong SDK input error: ${error}`);
  }
  throw new Error('invalid TypeScript SDK input was accepted');
}

const tools = resolve(args.tools);
const core = resolve(args.core);
const artifact = resolve(args.artifact);
const fixturesPath = resolve(args.fixtures);
const fixtures = JSON.parse(await readFile(fixturesPath, 'utf8'));
if (fixtures.contract !== 'sley-tools.release-readiness-fixtures.v1' || fixtures.max_blocking_findings !== 0 || fixtures.cases?.length !== 48) {
  throw new Error('shared Release Readiness fixtures have an unexpected contract');
}

const folder = await mkdtemp(join(tmpdir(), 'sley-ts-sdk-probe-'));
try {
  const bin = join(folder, 'bin');
  await mkdir(bin);
  const copiedTools = join(bin, 'sley-tools');
  const copiedBuilder = join(bin, 'sley-tools-native-builder');
  await copyFile(tools, copiedTools);
  await copyFile(join(dirname(tools), 'sley-tools-native-builder'), copiedBuilder);
  await chmod(copiedTools, 0o755);
  await chmod(copiedBuilder, 0o755);

  const runtime = openRuntime({ tools: copiedTools, core });
  let count = 0;
  let rpcPid;
  try {
    const program = await runtime.load({ artifact });
    rpcPid = runtime.process.pid;
    const workerPids = new Set();
    for (const testCase of fixtures.cases) {
      const outcome = await program.call('evaluate', { inputs: testCase.input });
      if (JSON.stringify(outcome.value) !== JSON.stringify(testCase.expected)) {
        // Object insertion order is a presentation detail, so compare by field.
        if (Object.keys(testCase.expected).some((name) => outcome.value[name] !== testCase.expected[name])
          || Object.keys(outcome.value).length !== Object.keys(testCase.expected).length) {
          throw new Error(`TypeScript SDK native output differs from shared fixture ${testCase.id}`);
        }
      }
      if (outcome.metadata.state_root !== program.stateRoot) throw new Error('TypeScript SDK lost accepted root binding');
      workerPids.add(outcome.metadata.worker_pid);
      count += 1;
    }
    if (count !== 48 || workerPids.size !== 1) throw new Error('TypeScript SDK did not reuse one native worker');
    if ((await program.test()).external_assertions_against_native_execution !== 36) throw new Error('TypeScript SDK test call omitted native assertions');
    if ((await program.inspect()).accepted_state_root !== program.stateRoot) throw new Error('TypeScript SDK inspect lost exact root identity');
    const base = { test_run_completed: true, tests_failed: 0, artifact_verified: true, blocking_findings: 0 };
    for (const value of [true, -1, 2 ** 32, 1.5, '1']) {
      await expectInputError(() => program.call('evaluate', { inputs: { ...base, tests_failed: value } }));
    }
    await expectInputError(() => program.call('evaluate', { inputs: { ...base, artifact_verified: 1 } }));
    await expectInputError(() => program.call('wrong', { inputs: base }));
    await expectInputError(() => program.call('evaluate', { inputs: base, deadlineMs: 0 }));
    if (encodeInteger(-(2n ** 63n), 'i64').value !== (-(2n ** 63n)).toString()
      || encodeInteger(2n ** 63n - 1n, 'i64').value !== (2n ** 63n - 1n).toString()) {
      throw new Error('TypeScript SDK i64 wire encoding lost signed limits');
    }
    for (const value of [true, -(2n ** 63n) - 1n, 2n ** 63n, Number.MAX_SAFE_INTEGER + 1, 1.5, '42']) {
      await expectInputError(() => encodeInteger(value, 'i64'));
    }
  } finally {
    await runtime.close();
  }
  try { process.kill(rpcPid, 0); throw new Error('TypeScript SDK left its RPC process running'); }
  catch (error) { if (error.code !== 'ESRCH') throw error; }

  const failedRuntime = openRuntime({ tools: copiedTools, core });
  try {
    const program = await failedRuntime.load({ artifact });
    const facts = { test_run_completed: true, tests_failed: 0, artifact_verified: true, blocking_findings: 0 };
    const ready = await program.call('evaluate', { inputs: facts });
    process.kill(ready.metadata.worker_pid, 'SIGKILL');
    try { await program.call('evaluate', { inputs: facts }); throw new Error('TypeScript SDK hid native worker loss'); }
    catch (error) {
      if (!(error instanceof SleyToolsError) || error.code !== 'ST_TRANSPORT_FAILED') throw error;
    }
  } finally {
    await failedRuntime.close();
  }

  const report = {
    contract: 'sley-tools.typescript-sdk-probe.v1', result: 'PASS',
    core_binary_sha256: digest(await readFile(core)),
    artifact_sha256: digest(await readFile(artifact)),
    fixtures_sha256: digest(await readFile(fixturesPath)),
    native_cases: count,
    one_worker_for_matrix: true,
    copied_binaries_without_source_checkout: true,
    typed_input_negative_cases: 8,
    i64_wire_limits_exact: true,
    worker_loss_reported: true,
    runtime_closed_process: true,
    native_test_admission: 'NOT_RUN: production native executor unavailable',
  };
  await mkdir(dirname(resolve(args.output)), { recursive: true });
  await writeFile(resolve(args.output), JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(`wrote ${args.output}: TypeScript SDK native execution and failure paths passed\n`);
} finally {
  await rm(folder, { recursive: true, force: true });
}
