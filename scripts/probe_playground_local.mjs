#!/usr/bin/env node
/** Exercise local Playground HTTP flow against real Sley tools and artifact replay. */

import { spawn, spawnSync, execFile } from 'node:child_process';
import { request as httpRequest } from 'node:http';
import { promisify } from 'node:util';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const exec = promisify(execFile);
const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
function option(name, fallback = null) {
  const index = args.indexOf(name);
  return index < 0 ? fallback : args[index + 1];
}
const core = option('--core');
const tools = resolve(option('--tools', join(repo, 'target/debug/sley-tools')));
const adapter = join(repo, 'apps/playground/server.mjs');
const output = resolve(option('--output', join(repo, 'evidence/playground/local-v1.json')));
if (!core) throw new Error('usage: node scripts/probe_playground_local.mjs --core CORE [--tools TOOLS] [--output FILE]');
const sha = value => createHash('sha256').update(value).digest('hex');
const command = async (...params) => JSON.parse((await exec(tools, params.map(String), {
  timeout: 60_000, maxBuffer: 1024 * 1024,
})).stdout);

async function service() {
  let stderr = '';
  const child = spawn(tools, ['playground', '--adapter', adapter,
    '--core', resolve(core), '--port', '0'], {
    cwd: repo, stdio: ['ignore', 'pipe', 'pipe'],
  });
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
  return { child, url, logs: () => stderr };
}

async function main() {
  let sourceCommit = null;
  let sourceDirty = null;
  try {
    sourceCommit = (await exec('git', ['rev-parse', 'HEAD'], { cwd: repo })).stdout.trim();
    sourceDirty = Boolean((await exec('git', ['status', '--porcelain', '--untracked-files=all'],
      { cwd: repo })).stdout.trim());
  } catch { /* Installed bundles have no Git checkout. */ }
  const temporary = await mkdtemp(join(tmpdir(), 'sley-playground-probe-'));
  let running;
  try {
    const missing = spawnSync(tools, ['playground', '--adapter', join(repo, 'missing-playground.mjs'),
      '--core', resolve(core)], { encoding: 'utf8' });
    if (missing.status !== 4 || missing.stdout
        || JSON.parse(missing.stderr).code !== 'ST_FEATURE_UNSUPPORTED')
      throw new Error('missing optional Playground adapter was not diagnosed');
    const duplicate = spawnSync(tools, ['playground', '--adapter', adapter,
      '--core', resolve(core), '--port', '0', '--port', '0'], { encoding: 'utf8' });
    if (duplicate.status !== 2 || duplicate.stdout
        || JSON.parse(duplicate.stderr).code !== 'ST_INPUT_INVALID')
      throw new Error('duplicate Playground port was accepted');
    running = await service();
    const { url } = running;
    async function http(path, { body, cookie } = {}) {
      const response = await fetch(url + path, body === undefined
        ? { headers: cookie ? { cookie } : {} } : {
        method: 'POST', headers: { origin: url, 'x-sley-request': 'playground',
          'content-type': 'application/json', ...(cookie ? { cookie } : {}) },
        body: JSON.stringify(body),
      });
      const value = await response.json();
      return { response, value, cookie: response.headers.get('set-cookie')?.split(';')[0] };
    }
    async function session(id) {
      const created = await http('/api/sessions', { body: { example: id } });
      if (created.response.status !== 201 || !created.cookie || created.value.example !== id)
        throw new Error(`cannot create ${id} session: ${JSON.stringify(created.value)}`);
      return created;
    }
    async function act(path, cookie, body = {}) {
      const result = await http(path, { cookie, body });
      if (!result.response.ok) throw new Error(`${path}: ${JSON.stringify(result.value)}`);
      return result.value;
    }
    const listing = await http('/api/examples');
    if (listing.value.examples.length !== 3) throw new Error('Playground omitted a bundled program');
    const source = await fetch(url + '/');
    if (!source.ok || !(await source.text()).includes('Sley Playground'))
      throw new Error('Playground UI did not load');
    const noSession = await http('/api/session');
    if (noSession.value.active !== false) throw new Error('fresh browser inherited a workspace');
    const crossOrigin = await fetch(url + '/api/sessions', {
      method: 'POST', headers: { origin: 'https://elsewhere.invalid',
        'x-sley-request': 'playground', 'content-type': 'application/json' },
      body: JSON.stringify({ example: 'release-readiness' }),
    });
    if (crossOrigin.status !== 403) throw new Error('cross-origin session creation was accepted');
    const oversized = await fetch(url + '/api/sessions', {
      method: 'POST', headers: { origin: url, 'x-sley-request': 'playground',
        'content-type': 'application/json' },
      body: JSON.stringify({ example: 'release-readiness', padding: 'x'.repeat(17_000) }),
    });
    if (oversized.status !== 413) throw new Error('oversized Playground request was accepted');
    const release = await session('release-readiness');
    const releaseCookie = release.cookie;
    const releaseInput = release.value.samples[0].inputs;
    const badInput = await http('/api/run', { cookie: releaseCookie,
      body: { inputs: { ...releaseInput, tests_failed: 'not-an-integer' } } });
    if (badInput.response.status !== 400 || badInput.value.kind !== 'input_problem')
      throw new Error('typed browser input was not classified as an input problem');
    const unchangedEdit = await http('/api/propose', { cookie: releaseCookie,
      body: { value: '0' } });
    if (unchangedEdit.response.status !== 409 || unchangedEdit.value.kind !== 'rejected_change')
      throw new Error('unchanged native edit was not classified as a rejected change');
    const beforeRelease = await act('/api/run', releaseCookie, { inputs: releaseInput });
    if (beforeRelease.native.result.allow !== false)
      throw new Error('Release Readiness should compute false before its edit');
    const proposedRelease = await act('/api/propose', releaseCookie, { value: '2' });
    if (proposedRelease.state.root !== release.value.root) throw new Error('proposal changed accepted root');
    const validatedRelease = await act('/api/validate', releaseCookie);
    if (validatedRelease.validation.candidate_id !== proposedRelease.proposal.candidate_id)
      throw new Error('Release Readiness validated a different candidate');
    const committedRelease = await act('/api/commit', releaseCookie);
    if (committedRelease.state.root === release.value.root
        || committedRelease.state.review.before.allow !== false
        || committedRelease.state.review.after.allow !== true)
      throw new Error('Release Readiness browser edit did not change native behavior');
    const releaseTest = await act('/api/test', releaseCookie);
    if (releaseTest.test.result !== 'PASS' || releaseTest.test.external_assertions_against_native_execution < 48)
      throw new Error('Release Readiness browser tests did not pass');
    const comparator = await session('benchmark-comparator');
    const comparatorCookie = comparator.cookie;
    const isolated = await http('/api/session', { cookie: releaseCookie });
    if (isolated.value.example !== 'release-readiness' || isolated.value.root !== committedRelease.state.root)
      throw new Error('second session could access or replace the first session');
    const correction = comparator.value.samples.find(item => item.label === 'Signed correction').inputs;
    const beforeComparator = await act('/api/run', comparatorCookie, { inputs: correction });
    if (beforeComparator.native.result.invalid_input !== true)
      throw new Error('native comparator did not mark initial signed correction invalid');
    const proposedComparator = await act('/api/propose', comparatorCookie, { value: false });
    await act('/api/validate', comparatorCookie);
    const committedComparator = await act('/api/commit', comparatorCookie);
    if (committedComparator.state.root === comparator.value.root
        || committedComparator.state.review.before.invalid_input !== true
        || committedComparator.state.review.after.invalid_input !== false
        || committedComparator.commit.candidate_id !== proposedComparator.proposal.candidate_id)
      throw new Error('native comparator policy edit did not change behavior');
    const crossSessionInput = await http('/api/run', { cookie: comparatorCookie,
      body: { inputs: releaseInput } });
    if (crossSessionInput.response.status !== 400 || crossSessionInput.value.kind !== 'input_problem')
      throw new Error('a session accepted another program’s input shape');
    const range = await session('range-predicate');
    const rangeRun = await act('/api/run', range.cookie, { inputs: range.value.samples[0].inputs });
    if (rangeRun.native.result.within_range !== true
        || rangeRun.native.result.excluded_upper !== false
        || rangeRun.state.result_fields.length !== 5
        || rangeRun.state.graph.blocks.length !== 3)
      throw new Error('branching range graph did not run or inspect');
    const wrongHostStatus = await new Promise((done, fail) => {
      const request = httpRequest(url + '/api/examples', { headers: { host: 'attacker.invalid' } },
        response => { response.resume(); response.once('end', () => done(response.statusCode)); });
      request.once('error', fail);
      request.end();
    });
    if (wrongHostStatus !== 403) throw new Error('non-loopback Host was accepted');

    async function replayExport(cookie, name, inputs, expected) {
      const response = await fetch(url + '/api/export', { method: 'POST',
        headers: { origin: url, 'x-sley-request': 'playground', cookie,
          'content-type': 'application/json' }, body: '{}' });
      if (!response.ok) throw new Error(`export failed: ${await response.text()}`);
      const archive = Buffer.from(await response.arrayBuffer());
      const archivePath = join(temporary, `${name}.tar.gz`);
      const packageDir = join(temporary, `${name}-package`);
      await writeFile(archivePath, archive);
      await mkdir(packageDir);
      await exec('tar', ['-xzf', archivePath, '-C', packageDir]);
      const manifest = JSON.parse(await readFile(join(packageDir, 'program.json'), 'utf8'));
      const imported = join(temporary, `${name}-imported`);
      const replay = await command('artifact', 'import',
        join(packageDir, manifest.artifact.path), imported, '--core', resolve(core));
      if (replay.program.program_id !== name) throw new Error(`${name} export imported a different program`);
      const inputFile = join(temporary, `${name}-input.json`);
      const order = name === 'release-readiness'
        ? { ...inputs, tests_failed: Number(inputs.tests_failed),
          blocking_findings: Number(inputs.blocking_findings) }
        : Object.values(inputs);
      await writeFile(inputFile, JSON.stringify(order));
      const observed = await command('run', '--project', imported, '--input', inputFile);
      if (JSON.stringify(observed.result) !== JSON.stringify(expected))
        throw new Error(`${name} exported program changed its behavior`);
      return { sha256: sha(archive), imported_root: replay.program.entrypoint.state_root };
    }
    const releaseExport = await replayExport(releaseCookie, 'release-readiness', releaseInput,
      committedRelease.state.review.after);
    const comparatorExport = await replayExport(comparatorCookie, 'benchmark-comparator', correction,
      committedComparator.state.review.after);
    const concurrent = await Promise.all(Array.from({ length: 14 }, () =>
      http('/api/sessions', { body: { example: 'range-predicate' } })));
    const admitted = concurrent.filter(result => result.response.status === 201);
    const refused = concurrent.filter(result => result.response.status === 429);
    if (admitted.length !== 13 || refused.length !== 1
        || new Set(admitted.map(result => result.cookie)).size !== 13
        || refused[0].value.kind !== 'runtime_failure')
      throw new Error('concurrent session creation exceeded the sixteen-session limit');
    const stillFull = await http('/api/sessions', { body: { example: 'range-predicate' } });
    if (stillFull.response.status !== 429)
      throw new Error('completed session creation did not retain the admission limit');
    const report = {
      contract: 'sley-tools.playground-local-probe.v1', result: 'PASS',
      tools_source_commit: sourceCommit, tools_source_dirty: sourceDirty,
      tools_binary_sha256: sha(await readFile(tools)),
      core_binary_sha256: sha(await readFile(resolve(core))),
      server_sha256: sha(await readFile(join(repo, 'apps/playground/server.mjs'))),
      html_sha256: sha(await readFile(join(repo, 'apps/playground/public/index.html'))),
      client_sha256: sha(await readFile(join(repo, 'apps/playground/public/app.js'))),
      style_sha256: sha(await readFile(join(repo, 'apps/playground/public/style.css'))),
      probe_script_sha256: sha(await readFile(fileURLToPath(import.meta.url))),
      examples: ['release-readiness', 'benchmark-comparator', 'range-predicate'],
      release: { before_root: release.value.root, after_root: committedRelease.state.root,
        candidate_id: committedRelease.commit.candidate_id,
        receipt_id: committedRelease.commit.receipt_id, export: releaseExport },
      comparator: { before_root: comparator.value.root, after_root: committedComparator.state.root,
        candidate_id: committedComparator.commit.candidate_id,
        receipt_id: committedComparator.commit.receipt_id, export: comparatorExport },
      range_root: range.value.root,
      ui_served: true, portable_export_replayed: true,
      unified_cli_launcher_verified: true, missing_adapter_rejected: true,
      duplicate_port_rejected: true,
      origin_and_host_rejection: true, bounded_request_rejection: true,
      input_and_change_errors_distinguished: true,
      separate_session_cookies: true,
      concurrent_session_limit_enforced: true,
      native_core_test_admission: releaseTest.test.native_core_test_admission,
      hosted_ready: false,
    };
    await mkdir(dirname(output), { recursive: true });
    await writeFile(output, JSON.stringify(report, null, 2) + '\n');
    console.log(`wrote ${output}: local Playground native run/edit/export passed`);
  } catch (error) {
    if (running?.logs()) console.error(running.logs());
    throw error;
  } finally {
    if (running) {
      running.child.kill('SIGTERM');
      if (running.child.exitCode === null)
        await new Promise(done => running.child.once('exit', done));
    }
    await rm(temporary, { recursive: true, force: true });
  }
}

await main();
