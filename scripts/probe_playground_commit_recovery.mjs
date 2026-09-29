#!/usr/bin/env node
/** Verify a real native commit after a lost reply and preserve an unknown outcome. */

import { spawn, execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { chmod, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const exec = promisify(execFile);
const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
function option(name) {
  const index = args.indexOf(name);
  return index < 0 ? null : args[index + 1];
}
const tools = option('--tools');
const core = option('--core');
const output = option('--output');
if (!tools || !core || !output || args.length !== 6)
  throw new Error('usage: node scripts/probe_playground_commit_recovery.mjs --tools TOOLS --core CORE --output FILE');
const binary = resolve(tools);
const sha = value => createHash('sha256').update(value).digest('hex');
const quote = value => `'${value.replaceAll("'", "'\\''")}'`;
const scratch = await mkdtemp(join(tmpdir(), 'sley-playground-recovery-'));

async function start(mode) {
  const wrapper = join(scratch, `tools-${mode}`);
  const injected = mode === 'timeout-after-commit'
    ? `${quote(binary)} "$@" >/dev/null || exit $?\nsleep 35\nexit 0`
    : `printf '%s\\n' '{"code":"ST_COMMIT_OUTCOME_UNKNOWN","category":"transport","message":"Simulated missing commit reply."}' >&2\nexit 4`;
  await writeFile(wrapper, `#!/bin/sh\nif [ "$1" = commit ]; then\n${injected}\nfi\nexec ${quote(binary)} "$@"\n`);
  await chmod(wrapper, 0o700);
  const child = spawn(process.execPath, [join(repo, 'apps/playground/server.mjs'),
    '--core', resolve(core), '--tools', wrapper, '--port', '0'], {
    cwd: repo, stdio: ['ignore', 'pipe', 'pipe'],
  });
  let stderr = '';
  const url = await new Promise((done, fail) => {
    let stdout = '';
    const timer = setTimeout(() => fail(new Error(`Playground startup timed out: ${stderr}`)), 15_000);
    child.stdout.on('data', chunk => {
      stdout += chunk;
      const match = stdout.match(/http:\/\/127\.0\.0\.1:[0-9]+/);
      if (match) { clearTimeout(timer); done(match[0]); }
    });
    child.stderr.on('data', chunk => { stderr += chunk; });
    child.once('exit', code => { clearTimeout(timer); fail(new Error(`Playground exited ${code}: ${stderr}`)); });
  });
  return { child, url };
}

async function stop(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  await new Promise(done => child.once('exit', done));
}

async function http(url, path, cookie, body) {
  const response = await fetch(url + path, body === undefined
    ? { headers: cookie ? { cookie } : {} }
    : { method: 'POST', headers: { origin: url, 'x-sley-request': 'playground',
      'content-type': 'application/json', ...(cookie ? { cookie } : {}) },
    body: JSON.stringify(body) });
  return { status: response.status, value: await response.json(),
    cookie: response.headers.get('set-cookie')?.split(';')[0] };
}

async function flow(mode) {
  const { child, url } = await start(mode);
  try {
    const opened = await http(url, '/api/sessions', null, { example: 'release-readiness' });
    if (opened.status !== 201 || !opened.cookie) throw new Error('could not open recovery session');
    const cookie = opened.cookie;
    const inputs = opened.value.samples[0].inputs;
    const before = await http(url, '/api/run', cookie, { inputs });
    if (before.status !== 200 || before.value.native.result.allow !== false)
      throw new Error('recovery baseline was not the native false decision');
    const proposed = await http(url, '/api/propose', cookie, { value: '1' });
    const validated = await http(url, '/api/validate', cookie, {});
    if (proposed.status !== 200 || validated.status !== 200
        || proposed.value.proposal.candidate_id !== validated.value.validation.candidate_id)
      throw new Error('recovery candidate was not separately validated');
    const started = Date.now();
    const commit = await http(url, '/api/commit', cookie, {});
    const commitElapsedMs = Date.now() - started;
    const resumed = await http(url, '/api/session', cookie);
    if (resumed.status !== 200) throw new Error('recovery session could not resume');
    if (mode === 'timeout-after-commit') {
      if (commit.status !== 200 || commit.value.status !== 'committed'
          || commit.value.recovered_from_verified_receipt !== true
          || commit.value.root === opened.value.root
          || commit.value.commit.candidate_id !== proposed.value.proposal.candidate_id
          || commit.value.state?.root !== commit.value.root
          || commit.value.state?.last_run?.result?.allow !== true
          || resumed.value.root !== commit.value.root
          || commitElapsedMs < 28_000)
        throw new Error(`verified timeout recovery failed: ${JSON.stringify(commit.value)}`);
      const duplicate = await http(url, '/api/reconcile', cookie, {});
      if (duplicate.status !== 409) throw new Error('reconciled commit still appeared pending');
      return { timeout_after_actual_native_commit: true,
        candidate_id: commit.value.commit.candidate_id,
        receipt_id: commit.value.commit.receipt_id,
        initial_root: opened.value.root, committed_root: commit.value.root,
        commit_elapsed_ms: commitElapsedMs };
    }
    if (commit.status !== 504 || commit.value.kind !== 'incomplete_operation'
        || commit.value.detail?.reconciliation_state !== 'outcome_unknown'
        || resumed.value.pending?.uncertain !== true
        || resumed.value.root !== opened.value.root)
      throw new Error(`unknown commit outcome was misreported: ${JSON.stringify(commit.value)}`);
    const checked = await http(url, '/api/reconcile', cookie, {});
    const retry = await http(url, '/api/commit', cookie, {});
    if (checked.status !== 200 || checked.value.status !== 'outcome_unknown'
        || checked.value.reconciliation.state !== 'outcome_unknown'
        || checked.value.reconciliation.observed_root !== opened.value.root
        || retry.status !== 409)
      throw new Error('unknown commit was treated as retry-safe');
    return { unobserved_commit_remains_unknown: true,
      candidate_id: proposed.value.proposal.candidate_id,
      observed_root: checked.value.reconciliation.observed_root,
      retry_rejected: true };
  } finally { await stop(child); }
}

try {
  const completed = await flow('timeout-after-commit');
  const unknown = await flow('unknown-before-commit');
  const report = {
    contract: 'sley-tools.playground-commit-recovery-probe.v1', result: 'PASS',
    tools_source_commit: (await exec('git', ['rev-parse', 'HEAD'], { cwd: repo })).stdout.trim(),
    tools_source_dirty: Boolean((await exec('git', ['status', '--porcelain', '--untracked-files=all'],
      { cwd: repo })).stdout.trim()),
    tools_binary_sha256: sha(await readFile(binary)),
    core_binary_sha256: sha(await readFile(resolve(core))),
    server_sha256: sha(await readFile(join(repo, 'apps/playground/server.mjs'))),
    client_sha256: sha(await readFile(join(repo, 'apps/playground/public/app.js'))),
    probe_sha256: sha(await readFile(fileURLToPath(import.meta.url))),
    completed, unknown, hosted_ready: false,
  };
  await writeFile(resolve(output), JSON.stringify(report, null, 2) + '\n');
  console.log(`wrote ${resolve(output)}: verified commit timeout and unknown outcome preserved`);
} finally { await rm(scratch, { recursive: true, force: true }); }
