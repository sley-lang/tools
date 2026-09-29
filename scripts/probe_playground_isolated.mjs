#!/usr/bin/env node
/** Exercise the isolated Playground boundary and a real two-session native edit. */

import { spawn, execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { request as httpRequest } from 'node:http';
import { access, chmod, copyFile, mkdir, mkdtemp, open, readFile, readdir, rm, statfs, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { bundledFiles, isolatedCommand, MAX_WORKSPACE_BYTES, verifyIsolatedBundle,
  verifyIsolatedMountNamespace, workspaceBudget } from '../apps/playground/isolated-worker.mjs';

const exec = promisify(execFile);
const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
function option(name) {
  const index = args.indexOf(name);
  return index < 0 ? null : args[index + 1];
}
const bundle = option('--bundle');
const output = option('--output');
if (!bundle || !output || args.length !== 4)
  throw new Error('usage: node scripts/probe_playground_isolated.mjs --bundle BUNDLE --output FILE');
const sha = value => createHash('sha256').update(value).digest('hex');
const quote = value => `'${value.replaceAll("'", "'\\''")}'`;
const manifest = await verifyIsolatedBundle(resolve(bundle));
await verifyIsolatedMountNamespace();
const scratch = await mkdtemp(join(tmpdir(), 'sley-playground-isolated-probe-'));
let child;
let firstWorkspace;
let systemdRunWrapped = false;
try {
  // A private mount-namespace wrapper delays only the commit reply, after the
  // real scoped worker exits. The sealed bundle and production server stay exact.
  const realSystemdRun = join(scratch, 'systemd-run-real');
  const wrappedSystemdRun = join(scratch, 'systemd-run-wrapper');
  const armCommitTimeout = join(scratch, 'delay-next-commit');
  await copyFile('/usr/bin/systemd-run', realSystemdRun);
  await writeFile(wrappedSystemdRun, `#!/bin/sh
if [ -f ${quote(armCommitTimeout)} ]; then
  previous=''
  for argument in "$@"; do
    if [ "$previous" = /app/bin/sley-tools ] && [ "$argument" = commit ]; then
      ${quote(realSystemdRun)} "$@"
      status=$?
      if [ "$status" -eq 0 ]; then
        rm -f ${quote(armCommitTimeout)}
        exec /usr/bin/sleep 35
      fi
      exit "$status"
    fi
    previous="$argument"
  done
fi
exec ${quote(realSystemdRun)} "$@"
`);
  await chmod(wrappedSystemdRun, 0o700);
  await exec('/usr/bin/mount', ['--bind', wrappedSystemdRun, '/usr/bin/systemd-run']);
  systemdRunWrapped = true;
  const shapeRoot = join(scratch, 'bundle-shape');
  await mkdir(shapeRoot);
  const shapeFiles = {};
  for (const path of bundledFiles) {
    const content = Buffer.from(`isolated-bundle-check:${path}`);
    const destination = join(shapeRoot, path);
    await mkdir(dirname(destination), { recursive: true });
    await writeFile(destination, content);
    shapeFiles[path] = sha(content);
  }
  await writeFile(join(shapeRoot, 'BUNDLE.json'), JSON.stringify({
    contract: 'sley-tools.playground-isolated-bundle.v1', files: shapeFiles,
  }));
  await verifyIsolatedBundle(shapeRoot);
  const extra = join(shapeRoot, 'unlisted');
  await writeFile(extra, 'not in manifest');
  let extraRejected = false;
  try { await verifyIsolatedBundle(shapeRoot); }
  catch { extraRejected = true; }
  if (!extraRejected) throw new Error('isolated bundle accepted an unlisted file');
  await rm(extra);
  const listed = join(shapeRoot, bundledFiles[0]);
  await rm(listed);
  await symlink('sley-tools-native-builder', listed);
  let bundleSymlinkRejected = false;
  try { await verifyIsolatedBundle(shapeRoot); }
  catch { bundleSymlinkRejected = true; }
  if (!bundleSymlinkRejected) throw new Error('isolated bundle accepted a listed symlink');
  await rm(shapeRoot, { recursive: true });
  const budgetRoot = join(scratch, 'workspace-budget');
  await mkdir(budgetRoot);
  const budgetFile = join(budgetRoot, 'oversized');
  const file = await open(budgetFile, 'w');
  try { await file.truncate(MAX_WORKSPACE_BYTES + 1); }
  finally { await file.close(); }
  if (!(await workspaceBudget(budgetRoot)).overLimit)
    throw new Error('isolated workspace budget accepted an oversized file');
  await rm(budgetFile);
  await symlink('/etc/passwd', join(budgetRoot, 'escaped'));
  let symlinkRejected = false;
  try { await workspaceBudget(budgetRoot); }
  catch { symlinkRejected = true; }
  if (!symlinkRejected) throw new Error('isolated workspace budget accepted a symlink');
  await rm(budgetRoot, { recursive: true });
  const command = isolatedCommand(resolve(bundle), scratch, repo, ['inspect', '--project', scratch]);
  const scopeCut = command.args.indexOf('/usr/bin/prlimit');
  if (command.file !== '/usr/bin/systemd-run' || scopeCut < 0)
    throw new Error('isolated worker omitted its aggregate systemd scope');
  const scopeArgs = command.args.slice(0, scopeCut);
  const cgroup = JSON.parse((await exec(command.file, [
    ...scopeArgs, '/usr/bin/python3', '-c',
    `import json
from pathlib import Path
p=Path('/sys/fs/cgroup') / Path('/proc/self/cgroup').read_text().strip().split(':')[-1].lstrip('/')
print(json.dumps({'memory_max': (p/'memory.max').read_text().strip(), 'swap_max': (p/'memory.swap.max').read_text().strip(), 'tasks_max': (p/'pids.max').read_text().strip(), 'cpu_max': (p/'cpu.max').read_text().strip()}))`,
  ], { timeout: 30_000, maxBuffer: 1024 * 1024 })).stdout);
  const [cpuQuota, cpuPeriod] = cgroup.cpu_max.split(' ').map(Number);
  if (cgroup.memory_max !== '1342177280' || cgroup.swap_max !== '0'
      || cgroup.tasks_max !== '64' || cpuQuota !== 2 * cpuPeriod)
    throw new Error(`aggregate cgroup limits differ: ${JSON.stringify(cgroup)}`);
  const lowMemoryArgs = scopeArgs.map(arg => arg === 'MemoryMax=1342177280'
    ? 'MemoryMax=67108864' : arg);
  let aggregateMemoryRejected = false;
  try {
    await exec(command.file, [...lowMemoryArgs, '/usr/bin/python3', '-c',
      'x=bytearray(128*1024*1024); print(len(x))'],
    { timeout: 30_000, maxBuffer: 1024 * 1024 });
  } catch { aggregateMemoryRejected = true; }
  if (!aggregateMemoryRejected) throw new Error('aggregate memory limit did not reject an oversized worker');
  for (const forbidden of [
    ['import', 'python', '/work/source.py'],
    ['inspect', '--project', '/sandbox/forbidden-home/.ssh'],
    ['artifact', 'import', join(repo, 'README.md'), join(scratch, 'project'),
      '--core', '/app/core/bin/sley'],
  ]) {
    try {
      isolatedCommand(resolve(bundle), scratch, repo, forbidden);
      throw new Error(`isolated worker accepted forbidden arguments: ${forbidden.join(' ')}`);
    } catch (error) {
      if (error.message.startsWith('isolated worker accepted')) throw error;
    }
  }
  const cut = command.args.indexOf('/app/bin/sley-tools');
  if (cut < 0) throw new Error('isolated command omitted the tools binary');
  const started = join(scratch, 'timeout-started');
  const survived = join(scratch, 'timeout-survived');
  const timed = spawn(command.file, [
    ...command.args.slice(0, cut), '/usr/bin/python3', '-c',
    `from pathlib import Path
import time
Path('/work/timeout-started').write_text('started')
time.sleep(2)
Path('/work/timeout-survived').write_text('survived')`,
  ], { stdio: ['ignore', 'pipe', 'pipe'] });
  try {
    let ready = false;
    for (let check = 0; check < 100; check += 1) {
      try { await access(started); ready = true; break; } catch { /* Wait for worker startup. */ }
      await new Promise(done => setTimeout(done, 50));
    }
    if (!ready) throw new Error('timeout worker did not start');
    timed.kill('SIGTERM');
    if (timed.exitCode === null && timed.signalCode === null)
      await new Promise(done => timed.once('exit', done));
    await new Promise(done => setTimeout(done, 2500));
    try { await access(survived); throw new Error('timed-out sandbox worker survived its launcher'); }
    catch (error) { if (error.message.startsWith('timed-out')) throw error; }
  } finally {
    if (timed.exitCode === null) timed.kill('SIGKILL');
  }
  const boundary = JSON.parse((await exec(command.file, [
    ...command.args.slice(0, cut), '/usr/bin/python3', '-c',
    `import errno, json, os, resource, signal, socket
s=socket.socket()
connected=s.connect_ex(('127.0.0.1', 4177)) == 0
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
try:
    with open('/work/oversized', 'wb') as file: file.write(b'x' * (17 * 1024 * 1024))
    file_limit_enforced=False
except OSError as error:
    file_limit_enforced=error.errno == errno.EFBIG
os.unlink('/work/oversized')
print(json.dumps({'home_visible': os.path.exists('/sandbox/forbidden-home'), 'run_visible': os.path.exists('/run'), 'network_connected': connected, 'cpu': resource.getrlimit(resource.RLIMIT_CPU)[0], 'memory': resource.getrlimit(resource.RLIMIT_AS)[0], 'file_size': resource.getrlimit(resource.RLIMIT_FSIZE)[0], 'file_limit_enforced': file_limit_enforced, 'app_visible': os.path.isfile('/app/bin/sley-tools'), 'work_visible': os.path.isdir('/work')}))`,
  ], { timeout: 30_000, maxBuffer: 1024 * 1024 })).stdout);
  if (boundary.home_visible || boundary.run_visible || boundary.network_connected
      || !boundary.app_visible || !boundary.work_visible || !boundary.file_limit_enforced
      || boundary.cpu !== 10 || boundary.memory !== 1073741824 || boundary.file_size !== 16777216)
    throw new Error(`worker boundary failed: ${JSON.stringify(boundary)}`);
  let stderr = '';
  child = spawn(process.execPath, [join(repo, 'apps/playground/server.mjs'),
    '--isolated-bundle', resolve(bundle), '--port', '0'], {
    cwd: repo, stdio: ['ignore', 'pipe', 'pipe'],
  });
  const url = await new Promise((done, fail) => {
    let stdout = '';
    const timer = setTimeout(() => fail(new Error(`isolated Playground startup timed out: ${stderr}`)), 15_000);
    child.stdout.on('data', chunk => {
      stdout += chunk;
      const match = stdout.match(/http:\/\/127\.0\.0\.1:[0-9]+/);
      if (match) { clearTimeout(timer); done(match[0]); }
    });
    child.stderr.on('data', chunk => { stderr += chunk; });
    child.once('exit', code => { clearTimeout(timer); fail(new Error(`isolated Playground exited ${code}: ${stderr}`)); });
  });
  async function http(path, cookie, body) {
    const response = await fetch(url + path, body === undefined
      ? { headers: cookie ? { cookie } : {} }
      : { method: 'POST', headers: { origin: url, 'x-sley-request': 'playground',
        'content-type': 'application/json', ...(cookie ? { cookie } : {}) },
      body: JSON.stringify(body) });
    return { response, value: await response.json(),
      cookie: response.headers.get('set-cookie')?.split(';')[0] };
  }
  async function action(path, cookie, body = {}) {
    const result = await http(path, cookie, body);
    if (!result.response.ok) throw new Error(`${path}: ${JSON.stringify(result.value)}`);
    return result.value;
  }
  const priorSessionDirs = new Set((await readdir(tmpdir())).filter(name => name.startsWith('sley-playground-')));
  const a = await http('/api/sessions', null, { example: 'release-readiness' });
  const newSessionDirs = (await readdir(tmpdir())).filter(name =>
    name.startsWith('sley-playground-') && !priorSessionDirs.has(name));
  if (newSessionDirs.length !== 1) throw new Error('could not identify the isolated probe session workspace');
  firstWorkspace = join(tmpdir(), newSessionDirs[0]);
  const b = await http('/api/sessions', null, { example: 'release-readiness' });
  if (a.response.status !== 201 || b.response.status !== 201 || !a.cookie || !b.cookie
      || a.cookie === b.cookie || a.value.root === b.value.root)
    throw new Error('isolated sessions were not independently created');
  const input = a.value.samples[0].inputs;
  const filesystem = await statfs(firstWorkspace);
  if (filesystem.type !== 0x01021994
      || filesystem.blocks * filesystem.bsize !== MAX_WORKSPACE_BYTES)
    throw new Error('isolated session workspace lacks its hard tmpfs limit');
  const fillPath = join(firstWorkspace, 'hard-quota');
  const fill = await open(fillPath, 'w');
  let diskLimitEnforced = false;
  try {
    const block = Buffer.alloc(1024 * 1024);
    for (let attempt = 0; attempt < 70; attempt += 1) await fill.write(block);
  } catch (error) {
    if (error.code !== 'ENOSPC') throw error;
    diskLimitEnforced = true;
  } finally { await fill.close(); }
  if (!diskLimitEnforced) throw new Error('isolated session exceeded its hard disk limit');
  const unaffected = await action('/api/run', b.cookie, { inputs: input });
  if (unaffected.native.result.allow !== false)
    throw new Error('full first workspace affected a second isolated session');
  await rm(fillPath);
  const oversized = join(firstWorkspace, 'over-budget');
  const workspaceFile = await open(oversized, 'w');
  try { await workspaceFile.truncate(MAX_WORKSPACE_BYTES + 1); }
  finally { await workspaceFile.close(); }
  const budgetDenied = await http('/api/run', a.cookie, { inputs: input });
  if (budgetDenied.response.status !== 507 || budgetDenied.value.kind !== 'runtime_failure')
    throw new Error(`server did not enforce workspace budget: ${JSON.stringify(budgetDenied.value)}`);
  await rm(oversized);
  const before = await action('/api/run', a.cookie, { inputs: input });
  if (before.native.result.allow !== false) throw new Error('native baseline differed');
  await action('/api/propose', a.cookie, { value: '2' });
  await action('/api/validate', a.cookie);
  const committed = await action('/api/commit', a.cookie);
  if (committed.state.root === a.value.root || committed.state.review.after.allow !== true)
    throw new Error('isolated native edit did not commit');
  const reconciliationCommand = isolatedCommand(resolve(bundle), firstWorkspace, repo, [
    'reconcile', '--project', join(firstWorkspace, 'project'),
    '--candidate-id', committed.commit.candidate_id,
    '--expected-parent', a.value.transaction_id,
  ]);
  const reconciliation = JSON.parse((await exec(reconciliationCommand.file,
    reconciliationCommand.args, { timeout: 30_000, maxBuffer: 1024 * 1024 })).stdout);
  if (reconciliation.state !== 'completed'
      || reconciliation.committed_root !== committed.root
      || reconciliation.receipt_id !== committed.commit.receipt_id)
    throw new Error('isolated reconciliation did not verify the native receipt');
  const other = await action('/api/run', b.cookie, { inputs: input });
  if (other.native.result.allow !== false || other.state.root !== b.value.root)
    throw new Error('second session observed the first session edit');
  const comparator = await http('/api/sessions', null, { example: 'benchmark-comparator' });
  if (comparator.response.status !== 201) throw new Error('comparator session failed');
  const correction = comparator.value.samples.find(item => item.label === 'Signed correction').inputs;
  await action('/api/propose', comparator.cookie, { value: false });
  await action('/api/validate', comparator.cookie);
  const comparatorCommit = await action('/api/commit', comparator.cookie);
  const comparatorRun = await action('/api/run', comparator.cookie, { inputs: correction });
  if (comparatorCommit.state.root === comparator.value.root
      || comparatorRun.native.result.invalid_input !== false)
    throw new Error('isolated comparator edit did not change native behavior');
  const range = await http('/api/sessions', null, { example: 'range-predicate' });
  if (range.response.status !== 201) throw new Error('range session failed');
  const rangeRun = await action('/api/run', range.cookie, { inputs: range.value.samples[0].inputs });
  if (rangeRun.native.result.within_range !== true)
    throw new Error('isolated range graph did not run');
  const disconnected = await http('/api/sessions', null, { example: 'release-readiness' });
  if (disconnected.response.status !== 201) throw new Error('disconnection session failed');
  await action('/api/propose', disconnected.cookie, { value: '1' });
  await action('/api/validate', disconnected.cookie);
  let replyObserved = false;
  await new Promise((done, fail) => {
    const request = httpRequest(url + '/api/commit', { method: 'POST', headers: {
      origin: url, 'x-sley-request': 'playground', 'content-type': 'application/json',
      cookie: disconnected.cookie,
    } }, response => { replyObserved = true; response.destroy(); });
    request.once('finish', () => setTimeout(() => request.destroy(), 20));
    request.once('error', error => {
      if (error.code !== 'ECONNRESET') fail(error);
    });
    request.once('close', done);
    request.end('{}');
  });
  if (replyObserved) throw new Error('disconnected client received a commit reply');
  const resumed = await http('/api/session', disconnected.cookie);
  if (resumed.response.status !== 200 || resumed.value.root === disconnected.value.root
      || resumed.value.pending !== null || resumed.value.review?.after?.allow !== true)
    throw new Error('isolated session did not recover a committed root after client disconnect');
  const timedCommit = await http('/api/sessions', null, { example: 'release-readiness' });
  if (timedCommit.response.status !== 201 || !timedCommit.cookie)
    throw new Error('isolated timeout session failed');
  const timedProposal = await action('/api/propose', timedCommit.cookie, { value: '1' });
  await action('/api/validate', timedCommit.cookie);
  await writeFile(armCommitTimeout, 'armed');
  const commitStarted = Date.now();
  const recovered = await http('/api/commit', timedCommit.cookie, {});
  const commitElapsedMs = Date.now() - commitStarted;
  const timedResumed = await http('/api/session', timedCommit.cookie);
  if (recovered.response.status !== 200 || recovered.value.status !== 'committed'
      || recovered.value.recovered_from_verified_receipt !== true
      || recovered.value.root === timedCommit.value.root
      || recovered.value.commit?.candidate_id !== timedProposal.proposal.candidate_id
      || recovered.value.state?.root !== recovered.value.root
      || recovered.value.state?.last_run?.result?.allow !== true
      || timedResumed.response.status !== 200 || timedResumed.value.root !== recovered.value.root
      || timedResumed.value.pending !== null || commitElapsedMs < 28_000)
    throw new Error(`isolated after-commit timeout did not recover: ${JSON.stringify(recovered.value)}`);
  const duplicateReconcile = await http('/api/reconcile', timedCommit.cookie, {});
  if (duplicateReconcile.response.status !== 409)
    throw new Error('recovered isolated commit still appeared uncertain');
  try { await access(armCommitTimeout); throw new Error('isolated commit timeout fault was not triggered'); }
  catch (error) { if (error.message.startsWith('isolated commit timeout fault')) throw error; }
  const test = await action('/api/test', a.cookie);
  if (test.test.result !== 'PASS') throw new Error('isolated native external assertions failed');
  const exported = await fetch(url + '/api/export', { method: 'POST',
    headers: { origin: url, 'x-sley-request': 'playground', cookie: a.cookie,
      'content-type': 'application/json' }, body: '{}' });
  if (!exported.ok) throw new Error(`isolated export failed: ${await exported.text()}`);
  const archive = Buffer.from(await exported.arrayBuffer());
  const archivePath = join(scratch, 'edited.tar.gz');
  await writeFile(archivePath, archive);
  const packageDir = join(scratch, 'exported');
  await mkdir(packageDir);
  await exec('tar', ['-xzf', archivePath, '-C', packageDir]);
  const exportedManifest = JSON.parse(await readFile(join(packageDir, 'program.json'), 'utf8'));
  const imported = join(scratch, 'imported');
  const importedResult = JSON.parse((await exec(join(resolve(bundle), 'bin/sley-tools'), [
    'artifact', 'import', join(packageDir, exportedManifest.artifact.path), imported,
    '--core', join(resolve(bundle), 'core/bin/sley'),
  ], { timeout: 60_000, maxBuffer: 1024 * 1024 })).stdout);
  if (importedResult.program.program_id !== 'release-readiness')
    throw new Error('isolated export could not be reimported');
  const report = {
    contract: 'sley-tools.playground-isolated-probe.v1', result: 'PASS',
    tools_source_commit: (await exec('git', ['rev-parse', 'HEAD'], { cwd: repo })).stdout.trim(),
    tools_source_dirty: Boolean((await exec('git', ['status', '--porcelain', '--untracked-files=all'], { cwd: repo })).stdout.trim()),
    bundle_manifest_sha256: sha(await readFile(join(resolve(bundle), 'BUNDLE.json'))),
    bundle_files: manifest.files,
    server_sha256: sha(await readFile(join(repo, 'apps/playground/server.mjs'))),
    worker_sha256: sha(await readFile(join(repo, 'apps/playground/isolated-worker.mjs'))),
    probe_sha256: sha(await readFile(fileURLToPath(import.meta.url))),
    boundary, cgroup, aggregate_memory_limit_enforced: aggregateMemoryRejected,
    workspace_budget_oversize_and_symlink_rejected: true,
    server_workspace_budget_enforced: true,
    hard_session_disk_limit_enforced: diskLimitEnforced,
    second_session_operated_while_first_full: true,
    unlisted_bundle_file_and_symlink_rejected: true,
    isolated_reconciliation_verified_receipt: true,
    timed_out_worker_terminated: true, forbidden_worker_operations_and_paths_rejected: true,
    first_initial_root: a.value.root, first_committed_root: committed.state.root,
    second_unchanged_root: other.state.root, exported_archive_sha256: sha(archive),
    comparator_initial_root: comparator.value.root,
    comparator_committed_root: comparatorCommit.state.root,
    range_root: range.value.root,
    isolated_disconnected_client_recovered_committed_root: true,
    isolated_timeout_fault_private_mount_namespace: true,
    isolated_timeout_after_native_commit_recovered_root: true,
    isolated_timed_commit_elapsed_ms: commitElapsedMs,
    isolated_timed_commit_initial_root: timedCommit.value.root,
    isolated_timed_commit_recovered_root: recovered.value.root,
    isolated_timed_commit_receipt_id: recovered.value.commit.receipt_id,
    export_reimported: true,
    native_core_test_admission: test.test.native_core_test_admission,
    hosted_ready: false,
  };
  await writeFile(resolve(output), JSON.stringify(report, null, 2) + '\n');
  console.log(`wrote ${resolve(output)}: isolated native edits and timeout recovery passed`);
} finally {
  if (child) {
    child.kill('SIGTERM');
    if (child.exitCode === null) await new Promise(done => child.once('exit', done));
  }
  if (systemdRunWrapped)
    await exec('/usr/bin/umount', ['/usr/bin/systemd-run']);
  if (firstWorkspace) {
    try { await access(firstWorkspace); throw new Error('isolated session mount survived service shutdown'); }
    catch (error) { if (error.message.startsWith('isolated session mount survived')) throw error; }
  }
  await rm(scratch, { recursive: true, force: true });
}
