/** Exercise both native authoring profiles through the TypeScript SDK. */

import { createHash } from 'node:crypto';
import { execFile as execFileCallback } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { promisify } from 'node:util';

import { SleyToolsError, openRuntime } from '../packages/typescript/src/index.js';

const execFile = promisify(execFileCallback);
const args = Object.fromEntries(process.argv.slice(2).map((item) => item.split('=', 2)));
for (const name of ['tools', 'core', 'output']) if (!args[name]) throw new Error(`missing ${name}=PATH`);
const tools = resolve(args.tools);
const core = resolve(args.core);

async function expectError(operation, code) {
  try { await operation(); }
  catch (error) {
    if (error instanceof SleyToolsError && error.code === code) return;
    throw new Error(`expected ${code}, got ${error}`);
  }
  throw new Error(`expected ${code}`);
}

async function one(root, name) {
  const project = join(root, name);
  await execFile(tools, ['init', project, '--template', name, '--core', core], { timeout: 30000 });
  const facts = name === 'release-readiness'
    ? { test_run_completed: true, tests_failed: 0, artifact_verified: true, blocking_findings: 1 }
    : { requested_workers: 1, available_workers: 1, requested_memory_mib: 4096, available_memory_mib: 4096 };
  const change = name === 'release-readiness' ? { max_blocking_findings: 2 } : { require_memory_spare: true };
  const reverse = name === 'release-readiness' ? { max_blocking_findings: 0 } : { require_memory_spare: false };
  const invalid = name === 'release-readiness' ? { max_blocking_findings: true } : { require_memory_spare: 'true' };
  const runtime = openRuntime({ tools, core });
  try {
    const program = await runtime.openProject({ project });
    const initialRoot = program.stateRoot;
    const before = (await program.call('evaluate', { inputs: facts })).value.allow;
    await expectError(() => program.propose(invalid), 'ST_INPUT_INVALID');
    const proposed = await program.propose(change);
    const candidate = proposed.candidate_id;
    if (proposed.base_root !== initialRoot || candidate.length !== 64) throw new Error('proposal lost exact identity');
    await expectError(() => program.validate('0'.repeat(64)), 'ST_INPUT_INVALID');
    await expectError(() => program.commit(candidate), 'ST_INPUT_INVALID');
    if ((await program.validate(candidate)).candidate_id !== candidate) throw new Error('validation changed candidate');
    if ((await program.call('evaluate', { inputs: facts })).value.allow !== before) throw new Error('validation changed accepted behavior');
    const priorHandle = runtime.handle;
    const committed = await program.commit(candidate);
    const afterRoot = program.stateRoot;
    if (committed.candidate_id !== candidate || afterRoot === initialRoot || runtime.handle === priorHandle) {
      throw new Error('SDK failed to reopen changed native root');
    }
    const after = (await program.call('evaluate', { inputs: facts })).value.allow;
    if (before === after || before !== (name === 'resource-admission')) throw new Error('native decision boundary did not change');
    const expected = name === 'release-readiness' ? 60 : 12;
    if ((await program.test()).external_assertions_against_native_execution !== expected) throw new Error('edited native assertions failed');
    const second = await program.propose(reverse);
    const discarded = await program.discard(second.candidate_id);
    if (discarded.candidate_id !== second.candidate_id || program.stateRoot !== afterRoot) throw new Error('discard changed root');
    return { program_id: name, candidate_id: candidate, initial_root: initialRoot, committed_root: afterRoot,
      before_allow: before, after_allow: after, external_assertions: expected,
      typed_rejection: true, wrong_candidate_rejected: true, commit_before_validation_rejected: true,
      handle_rotated: true, discard_preserved_root: true };
  } finally {
    await runtime.close();
  }
}

const root = await mkdtemp(join(tmpdir(), 'sley-typescript-authoring-'));
try {
  const programs = [];
  for (const name of ['release-readiness', 'resource-admission']) programs.push(await one(root, name));
  const digest = (data) => createHash('sha256').update(data).digest('hex');
  const report = { contract: 'sley-tools.typescript-authoring-probe.v1', result: 'PASS',
    tools_binary_sha256: digest(await readFile(tools)), core_binary_sha256: digest(await readFile(core)),
    programs, native_core_test_admission: 'NOT_RUN: production native executor unavailable' };
  await mkdir(dirname(resolve(args.output)), { recursive: true });
  await writeFile(resolve(args.output), JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(`wrote ${args.output}: both TypeScript SDK authoring lifecycles passed\n`);
} finally {
  await rm(root, { recursive: true, force: true });
}
