#!/usr/bin/env node
/** Local, loopback-only browser adapter for accepted Sley programs. */

import { createServer } from 'node:http';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomBytes } from 'node:crypto';
import { mkdtemp, mkdir, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';
import { isolatedCommand, mountIsolatedWorkspace, unmountIsolatedWorkspace,
  verifyIsolatedBundle, verifyIsolatedMountNamespace, workspaceBudget } from './isolated-worker.mjs';

const exec = promisify(execFile);
const appDir = dirname(fileURLToPath(import.meta.url));
const repo = resolve(appDir, '../..');
const publicDir = join(appDir, 'public');
const MAX_BODY = 16 * 1024;
const SESSION_MS = 60 * 60 * 1000;
const MAX_SESSIONS = 16;
const MAX_SESSION_WORKER_CALLS = 256;
const MAX_ACTIVE_ISOLATED_WORKERS = 2;
const I64_MIN = -(1n << 63n);
const I64_MAX = (1n << 63n) - 1n;
const examples = {
  'release-readiness': {
    title: 'Release readiness',
    description: 'Check whether supplied release facts permit a release.',
    artifact: 'programs/release-readiness/artifact/native-program.json',
    fields: [
      ['test_run_completed', 'Bool', 'Test run completed'],
      ['tests_failed', 'UInt32', 'Failed tests'],
      ['artifact_verified', 'Bool', 'Artifact verified'],
      ['blocking_findings', 'UInt32', 'Blocking findings'],
    ],
    samples: [{ label: 'One blocker', inputs: {
      test_run_completed: true, tests_failed: '0', artifact_verified: true,
      blocking_findings: '1',
    } }],
    edit: { label: 'Maximum blocking findings', type: 'UInt32', initial: '0', suggested: '2' },
  },
  'benchmark-comparator': {
    title: 'Benchmark comparator',
    description: 'Compare wall time, CPU time, and archive size with checked deltas.',
    artifact: 'programs/benchmark-comparator/artifact/native-graph.json',
    fields: [
      ['baseline_wall_us', 'SInt64', 'Baseline wall time (µs)'],
      ['candidate_wall_us', 'SInt64', 'Candidate wall time (µs)'],
      ['baseline_cpu_us', 'SInt64', 'Baseline CPU time (µs)'],
      ['candidate_cpu_us', 'SInt64', 'Candidate CPU time (µs)'],
      ['baseline_archive_bytes', 'SInt64', 'Baseline archive (bytes)'],
      ['candidate_archive_bytes', 'SInt64', 'Candidate archive (bytes)'],
    ],
    samples: [
      { label: 'CPU regression', inputs: {
        baseline_wall_us: '100', candidate_wall_us: '90', baseline_cpu_us: '200',
        candidate_cpu_us: '220', baseline_archive_bytes: '1000', candidate_archive_bytes: '999',
      } },
      { label: 'Signed correction', inputs: {
        baseline_wall_us: '-1', candidate_wall_us: '0', baseline_cpu_us: '0',
        candidate_cpu_us: '0', baseline_archive_bytes: '0', candidate_archive_bytes: '0',
      } },
    ],
    edit: { label: 'Reject negative measurements', type: 'Bool',
      constant: 'reject_negative_inputs', initial: true, suggested: false,
      cases: { true: 'programs/benchmark-comparator/tests/cases.json',
        false: 'programs/benchmark-comparator/tests/allow-corrections-cases.json' } },
  },
  'range-predicate': {
    title: 'Range predicate',
    description: 'Inspect inclusive bounds and run a branching native graph.',
    artifact: 'programs/range-predicate/artifact/native-graph.json',
    fields: [
      ['value', 'SInt64', 'Value'], ['lower', 'SInt64', 'Lower bound'],
      ['upper', 'SInt64', 'Upper bound'],
    ],
    samples: [
      { label: 'Upper endpoint', inputs: { value: '5', lower: '-5', upper: '5' } },
      { label: 'Reversed bounds', inputs: { value: '0', lower: '5', upper: '-5' } },
    ],
    edit: null,
  },
};

class ApiError extends Error {
  constructor(status, kind, message, detail = null) {
    super(message);
    this.status = status;
    this.kind = kind;
    this.detail = detail;
  }
}

function options() {
  const args = process.argv.slice(2);
  let tools = join(repo, 'target/debug/sley-tools');
  let core = null;
  let port = 4177;
  let isolatedBundle = null;
  while (args.length) {
    const flag = args.shift();
    const value = args.shift();
    if (!value) throw new Error(`${flag} needs a value`);
    if (flag === '--core') core = resolve(value);
    else if (flag === '--tools') tools = resolve(value);
    else if (flag === '--isolated-bundle') isolatedBundle = resolve(value);
    else if (flag === '--port' && /^(0|[1-9][0-9]*)$/.test(value)) port = Number(value);
    else throw new Error(`unknown option ${flag}`);
  }
  if ((!core && !isolatedBundle) || port > 65535)
    throw new Error('usage: node apps/playground/server.mjs (--core CORE | --isolated-bundle BUNDLE) [--tools TOOLS] [--port PORT]');
  if (isolatedBundle && core) throw new Error('the isolated bundle selects its own verified core');
  return { tools, core: isolatedBundle ? '/app/core/bin/sley' : core, port, isolatedBundle };
}

const config = options();
if (config.isolatedBundle) {
  config.isolatedBundle = await realpath(config.isolatedBundle);
  await verifyIsolatedBundle(config.isolatedBundle);
  await verifyIsolatedMountNamespace();
}
const sessions = new Map();
let creatingSessions = 0;
let activeIsolatedWorkers = 0;
let boundPort;

function sendJson(res, status, body, headers = {}) {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store', 'x-content-type-options': 'nosniff', ...headers });
  res.end(JSON.stringify(body));
}

function requireHost(req) {
  const host = req.headers.host;
  if (host !== `127.0.0.1:${boundPort}` && host !== `localhost:${boundPort}`)
    throw new ApiError(403, 'input_problem', 'Open the Playground on its loopback address.');
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    if (req.headers.origin !== `http://${host}` || req.headers['x-sley-request'] !== 'playground')
      throw new ApiError(403, 'input_problem', 'This request did not come from the Playground page.');
  }
}

async function bodyJson(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY) throw new ApiError(413, 'input_problem', 'The request is too large.');
    chunks.push(chunk);
  }
  try {
    const value = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('object required');
    return value;
  } catch {
    throw new ApiError(400, 'input_problem', 'Send one JSON object.');
  }
}

function cookieSession(req) {
  const item = (req.headers.cookie ?? '').split(';').map(value => value.trim())
    .find(value => value.startsWith('sley_playground='));
  const token = item?.slice('sley_playground='.length);
  const session = token && sessions.get(token);
  if (!session || session.expiring)
    throw new ApiError(404, 'input_problem', 'Start a program to open a session.');
  session.touched = Date.now();
  return session;
}

function serial(session, action) {
  const task = session.chain.then(action, action);
  session.chain = task.catch(() => {});
  return task;
}

function toolFailure(error) {
  if (error.killed || error.code === 'ETIMEDOUT')
    return new ApiError(504, 'incomplete_operation', 'The operation timed out. Inspect the accepted root before continuing.');
  let detail;
  try { detail = JSON.parse(error.stderr); } catch { detail = null; }
  if (detail?.code === 'ST_COMMIT_OUTCOME_UNKNOWN' || detail?.code === 'ST_TRANSPORT_FAILED')
    return new ApiError(504, 'incomplete_operation', detail.message ?? 'The operation outcome is uncertain.', detail);
  const category = detail?.category;
  const kind = category === 'input_shape' ? 'input_problem'
    : category === 'execution' || !detail ? 'runtime_failure' : 'rejected_change';
  return new ApiError(kind === 'input_problem' ? 400 : kind === 'runtime_failure' ? 502 : 409, kind,
    detail?.message ?? 'The Sley tools worker failed.', detail);
}

async function tool(session, ...args) {
  let admitted = false;
  if (config.isolatedBundle) {
    const readOnlyInspect = args[0] === 'inspect';
    if (session.workerCalls >= MAX_SESSION_WORKER_CALLS + (readOnlyInspect ? 16 : 0))
      throw new ApiError(429, 'runtime_failure', 'This isolated session has reached its operation limit.');
    const budget = await workspaceBudget(session.root);
    if (budget.overLimit && !readOnlyInspect)
      throw new ApiError(507, 'runtime_failure', 'This isolated session has reached its workspace limit.');
    if (activeIsolatedWorkers >= MAX_ACTIVE_ISOLATED_WORKERS)
      throw new ApiError(429, 'runtime_failure', 'The isolated worker pool is busy. Try again shortly.');
    activeIsolatedWorkers += 1;
    admitted = true;
    session.workerCalls += 1;
  }
  try {
    const command = config.isolatedBundle
      ? isolatedCommand(config.isolatedBundle, session.root, repo, args.map(String))
      : { file: config.tools, args: args.map(String) };
    const { stdout } = await exec(command.file, command.args, {
      timeout: 30_000, maxBuffer: 1024 * 1024, windowsHide: true,
    });
    return JSON.parse(stdout);
  } catch (error) {
    const failure = toolFailure(error);
    if (['propose', 'validate', 'commit'].includes(args[0]) && failure.kind === 'input_problem') {
      failure.kind = 'rejected_change';
      failure.status = 409;
    }
    throw failure;
  } finally {
    if (admitted) activeIsolatedWorkers -= 1;
  }
}

function typedInputs(example, raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw))
    throw new ApiError(400, 'input_problem', 'Enter all typed inputs.');
  const names = example.fields.map(([name]) => name);
  if (Object.keys(raw).length !== names.length || Object.keys(raw).some(name => !names.includes(name)))
    throw new ApiError(400, 'input_problem', 'Input names must match this program.');
  const values = {};
  for (const [name, type] of example.fields) {
    const value = raw[name];
    if (type === 'Bool') {
      if (typeof value !== 'boolean') throw new ApiError(400, 'input_problem', `${name} must be true or false.`);
      values[name] = value;
    } else if (type === 'UInt32') {
      if (typeof value !== 'string' || !/^(0|[1-9][0-9]*)$/.test(value)
          || BigInt(value) > 4294967295n)
        throw new ApiError(400, 'input_problem', `${name} must be an unsigned 32 bit integer.`);
      values[name] = Number(value);
    } else {
      if (typeof value !== 'string' || !/^(0|-?[1-9][0-9]*)$/.test(value))
        throw new ApiError(400, 'input_problem', `${name} must be a canonical signed integer.`);
      const number = BigInt(value);
      if (number < I64_MIN || number > I64_MAX)
        throw new ApiError(400, 'input_problem', `${name} is outside signed 64 bit range.`);
      values[name] = value;
    }
  }
  return example.artifact && example.fields.some(([, type]) => type === 'UInt32')
    ? values : names.map(name => values[name]);
}

async function runInput(session, raw) {
  const input = typedInputs(examples[session.example], raw);
  const path = join(session.root, `input-${randomBytes(8).toString('hex')}.json`);
  await writeFile(path, JSON.stringify(input), { mode: 0o600 });
  try { return await tool(session, 'run', '--project', session.project, '--input', path); }
  finally { await rm(path, { force: true }); }
}

async function state(session) {
  const inspected = await tool(session, 'inspect', '--project', session.project);
  const example = examples[session.example];
  const graph = inspected.authoring?.profile === 'graph_v0'
    ? JSON.parse(await readFile(join(session.project, 'build/graph.json'), 'utf8')) : null;
  const snapshot = {
    example: session.example, title: example.title, description: example.description,
    fields: example.fields, samples: example.samples, edit: example.edit,
    root: inspected.accepted_state_root, transaction_id: inspected.accepted_transaction_id,
    program_id: inspected.program.program_id,
    result_fields: inspected.program.result_fields,
    authoring: inspected.authoring,
    graph,
    threshold: inspected.threshold,
    pending: session.pending ?? null, review: session.review ?? null,
    last_run: session.lastRun ?? null,
  };
  session.lastState = snapshot;
  return snapshot;
}

async function reconcilePending(session) {
  const pending = session.pending;
  if (!pending?.uncertain || !pending.expected_parent)
    throw new ApiError(409, 'rejected_change', 'There is no uncertain commit to reconcile.');
  const result = await tool(session, 'reconcile', '--project', session.project,
    '--candidate-id', pending.candidate_id, '--expected-parent', pending.expected_parent);
  if (result.contract !== 'sley-tools.candidate-reconciliation.v0'
      || result.candidate_id !== pending.candidate_id
      || result.expected_parent !== pending.expected_parent
      || !['completed', 'outcome_unknown'].includes(result.state))
    throw new ApiError(502, 'runtime_failure', 'Sley returned an invalid reconciliation result.');
  return result;
}

async function finishCommit(session, committed, recovered = false) {
  const { beforeRoot, inputs, before } = session.commitContext;
  session.pending = null;
  session.commitContext = null;
  session.review = { before_root: beforeRoot, after_root: committed.new_root,
    candidate_id: committed.candidate_id, receipt_id: committed.receipt_id,
    inputs, before, after: null };
  let postcommit_error = null;
  try {
    const after = await runInput(session, inputs);
    session.review.after = after.result;
    session.lastRun = { inputs, result: after.result, root: after.state_root };
  } catch (error) {
    postcommit_error = error.message;
  }
  let inspected = null;
  try { inspected = await state(session); }
  catch (error) { postcommit_error ??= error.message; }
  if (!inspected && session.lastState)
    session.lastState = { ...session.lastState, root: committed.new_root,
      pending: null, review: session.review, state_unavailable: true };
  return { status: 'committed', commit: committed, root: committed.new_root,
    recovered_from_verified_receipt: recovered, postcommit_error,
    review: session.review, state: inspected };
}

function recoveredCommit(result) {
  if (!/^[a-f0-9]{64}$/.test(result.committed_root ?? '')
      || !/^[a-f0-9]{64}$/.test(result.receipt_id ?? ''))
    throw new ApiError(502, 'runtime_failure', 'The verified receipt omitted the committed identity.');
  return { candidate_id: result.candidate_id, receipt_id: result.receipt_id,
    new_root: result.committed_root, reconciliation: result };
}

async function createSession(id) {
  const example = examples[id];
  if (!example) throw new ApiError(400, 'input_problem', 'Choose a bundled example.');
  if (sessions.size + creatingSessions >= MAX_SESSIONS)
    throw new ApiError(429, 'runtime_failure', 'The local session limit is full. Sessions expire after one hour.');
  creatingSessions += 1;
  let root;
  let session;
  try {
    root = await mkdtemp(join(tmpdir(), 'sley-playground-'));
    const project = join(root, 'project');
    session = { root, project, example: id, touched: Date.now(),
      workerCalls: 0, chain: Promise.resolve() };
    if (config.isolatedBundle) {
      await mountIsolatedWorkspace(root);
      session.mounted = true;
    }
    if (example.artifact) {
      await tool(session, 'artifact', 'import', join(repo, example.artifact), project, '--core', config.core);
    } else {
      await tool(session, 'init', project, '--graph', join(repo, example.graph),
        '--tests', join(repo, example.cases), '--core', config.core);
    }
    const token = randomBytes(32).toString('hex');
    sessions.set(token, session);
    return { token, session };
  } catch (error) {
    if (session) await removeSessionWorkspace(session);
    else if (root) await rm(root, { recursive: true, force: true });
    throw error;
  } finally {
    creatingSessions -= 1;
  }
}

async function removeSessionWorkspace(session) {
  if (session.mounted) {
    await unmountIsolatedWorkspace(session.root);
    session.mounted = false;
  }
  await rm(session.root, { recursive: true, force: true });
}

function tarEntry(name, content) {
  const data = Buffer.from(content);
  const header = Buffer.alloc(512);
  const put = (start, length, value) => header.write(String(value), start, length, 'ascii');
  const oct = (start, length, value) => put(start, length, value.toString(8).padStart(length - 1, '0') + '\0');
  if (Buffer.byteLength(name) > 100) throw new Error('export path too long');
  put(0, 100, name);
  oct(100, 8, 0o644); oct(108, 8, 0); oct(116, 8, 0);
  oct(124, 12, data.length); oct(136, 12, 0);
  header.fill(0x20, 148, 156); header[156] = '0'.charCodeAt(0);
  put(257, 6, 'ustar\0'); put(263, 2, '00');
  const sum = header.reduce((total, byte) => total + byte, 0);
  oct(148, 8, sum);
  return Buffer.concat([header, data, Buffer.alloc((512 - (data.length % 512)) % 512)]);
}

async function exportPackage(session) {
  const output = join(session.project, 'artifact', `playground-${randomBytes(6).toString('hex')}.json`);
  await mkdir(dirname(output), { recursive: true });
  await tool(session, 'artifact', 'export', '--project', session.project, '--output', output, '--bind-manifest');
  const manifest = JSON.parse(await readFile(join(session.project, 'program.json'), 'utf8'));
  const files = [['program.json', JSON.stringify(manifest, null, 2) + '\n']];
  for (const relative of [manifest.artifact.path, manifest.imported_source_artifact?.path,
                          manifest.graph_recipe?.path].filter(Boolean)) {
    if (!/^[a-zA-Z0-9_.-]+(?:\/[a-zA-Z0-9_.-]+)*$/.test(relative)
        || relative.split('/').includes('..')) throw new Error('invalid export path');
    files.push([relative, await readFile(join(session.project, relative))]);
  }
  if (manifest.program_id === 'release-readiness') {
    files.push(['tests/bridge-cases.json',
      await readFile(join(config.isolatedBundle ?? repo,
        'programs/release-readiness/tests/bridge-cases.json'))]);
  } else {
    files.push(['tests/cases.json', await readFile(join(session.project, 'tests/cases.json'))]);
  }
  files.push(['IMPORT.txt', `Import the native artifact with:\n\nsley-tools artifact import ${manifest.artifact.path} NEW_PROJECT --core CORE\n\nThe accepted core is ${manifest.core.binary_sha256}.\n`]);
  return gzipSync(Buffer.concat([...files.map(([name, data]) => tarEntry(name, data)), Buffer.alloc(1024)]));
}

async function api(req, res, pathname) {
  if (req.method === 'GET' && pathname === '/api/examples') {
    return sendJson(res, 200, { examples: Object.entries(examples).map(([id, value]) => ({
      id, title: value.title, description: value.description,
    })) });
  }
  if (req.method === 'POST' && pathname === '/api/sessions') {
    const body = req.playgroundBody;
    const { token, session } = await createSession(body.example);
    return sendJson(res, 201, await state(session), {
      'set-cookie': `sley_playground=${token}; HttpOnly; SameSite=Strict; Path=/`,
    });
  }
  if (req.method === 'GET' && pathname === '/api/session') {
    let session;
    try { session = cookieSession(req); }
    catch (error) {
      if (error instanceof ApiError && error.status === 404)
        return sendJson(res, 200, { active: false });
      throw error;
    }
    return sendJson(res, 200, await serial(session, async () => {
      try { return await state(session); }
      catch (error) {
        if (session.lastState && (session.pending?.uncertain || session.lastState.state_unavailable))
          return { ...session.lastState, pending: session.pending ?? null,
            state_unavailable: true };
        throw error;
      }
    }));
  }
  const session = cookieSession(req);
  if (req.method !== 'POST') throw new ApiError(405, 'input_problem', 'Unsupported request.');
  if (pathname === '/api/run') {
    const body = req.playgroundBody;
    return sendJson(res, 200, await serial(session, async () => {
      const result = await runInput(session, body.inputs);
      session.lastRun = { inputs: body.inputs, result: result.result, root: result.state_root };
      return { status: 'computed', native: result, state: await state(session) };
    }));
  }
  if (pathname === '/api/propose') {
    const body = req.playgroundBody;
    return sendJson(res, 200, await serial(session, async () => {
      if (session.pending) throw new ApiError(409, 'rejected_change', 'Finish or discard the current proposal.');
      const example = examples[session.example];
      if (!example.edit) throw new ApiError(400, 'rejected_change', 'This example has no guided edit yet.');
      let proposal;
      if (session.example === 'release-readiness') {
        const parsed = typedInputs({ fields: [['value', 'UInt32']], artifact: true }, { value: body.value });
        proposal = await tool(session, 'propose', '--project', session.project,
          '--max-blocking-findings', parsed.value);
      } else {
        if (typeof body.value !== 'boolean') throw new ApiError(400, 'input_problem', 'Choose true or false.');
        const valuePath = join(session.root, `value-${randomBytes(8).toString('hex')}.json`);
        await writeFile(valuePath, JSON.stringify(body.value), { mode: 0o600 });
        try {
          proposal = await tool(session, 'propose', '--project', session.project,
            '--constant', example.edit.constant, '--value-file', valuePath,
            '--tests', join(repo, example.edit.cases[String(body.value)]));
        } finally { await rm(valuePath, { force: true }); }
      }
      session.pending = { candidate_id: proposal.candidate_id, base_root: proposal.base_root,
        value: body.value, validated: false };
      const inspected = await state(session);
      if (inspected.root !== proposal.base_root
          || !/^[a-f0-9]{64}$/.test(inspected.transaction_id ?? ''))
        throw new ApiError(409, 'rejected_change', 'The accepted head changed during the proposal.');
      session.pending.expected_parent = inspected.transaction_id;
      return { status: 'proposed', proposal, state: inspected };
    }));
  }
  if (pathname === '/api/validate') {
    return sendJson(res, 200, await serial(session, async () => {
      if (!session.pending) throw new ApiError(409, 'rejected_change', 'Create a proposal first.');
      const validation = await tool(session, 'validate', '--project', session.project);
      if (validation.candidate_id !== session.pending.candidate_id)
        throw new ApiError(409, 'rejected_change', 'The validated candidate differs from the proposal.');
      session.pending.validated = true;
      return { status: 'validated', validation, state: await state(session) };
    }));
  }
  if (pathname === '/api/commit') {
    return sendJson(res, 200, await serial(session, async () => {
      if (!session.pending?.validated)
        throw new ApiError(409, 'rejected_change', 'Validate the proposal before committing.');
      const beforeRoot = session.pending.base_root;
      const inputs = session.lastRun?.inputs ?? examples[session.example].samples[0].inputs;
      const before = session.lastRun?.root === beforeRoot
        ? session.lastRun.result : (await runInput(session, inputs)).result;
      session.commitContext = { beforeRoot, inputs, before };
      let committed;
      try { committed = await tool(session, 'commit', '--project', session.project); }
      catch (error) {
        if (error.kind === 'incomplete_operation') {
          session.pending.uncertain = true;
          session.pending.validated = false;
          let reconciliation = null;
          try { reconciliation = await reconcilePending(session); }
          catch { /* Preserve the uncertain outcome for a later check. */ }
          if (reconciliation?.state === 'completed')
            return finishCommit(session, recoveredCommit(reconciliation), true);
          error.detail = { observed_root: reconciliation?.observed_root ?? null,
            prior_root: beforeRoot, reconciliation_state: reconciliation?.state ?? 'unavailable' };
        } else {
          session.commitContext = null;
        }
        throw error;
      }
      return finishCommit(session, committed);
    }));
  }
  if (pathname === '/api/reconcile') {
    return sendJson(res, 200, await serial(session, async () => {
      const result = await reconcilePending(session);
      if (result.state === 'completed')
        return finishCommit(session, recoveredCommit(result), true);
      let inspected = null;
      try { inspected = await state(session); }
      catch { /* The verified ancestry result still describes the uncertain outcome. */ }
      return { status: 'outcome_unknown', reconciliation: result, state: inspected };
    }));
  }
  if (pathname === '/api/discard') {
    return sendJson(res, 200, await serial(session, async () => {
      await tool(session, 'propose', 'discard', '--project', session.project);
      session.pending = null;
      return { status: 'discarded', state: await state(session) };
    }));
  }
  if (pathname === '/api/test') {
    return sendJson(res, 200, await serial(session, async () => ({
      status: 'tested', test: await tool(session, 'test', '--project', session.project),
      state: await state(session),
    })));
  }
  if (pathname === '/api/export') {
    const archive = await serial(session, () => exportPackage(session));
    res.writeHead(200, { 'content-type': 'application/gzip',
      'content-disposition': `attachment; filename="sley-${session.example}.tar.gz"`,
      'cache-control': 'no-store', 'x-content-type-options': 'nosniff' });
    return res.end(archive);
  }
  throw new ApiError(404, 'input_problem', 'Unknown Playground action.');
}

async function handle(req, res) {
  try {
    requireHost(req);
    if (req.method === 'POST') {
      if (!req.headers['content-type']?.startsWith('application/json'))
        throw new ApiError(415, 'input_problem', 'Send JSON for Playground actions.');
      req.playgroundBody = await bodyJson(req);
    }
    const pathname = new URL(req.url, `http://${req.headers.host}`).pathname;
    if (pathname.startsWith('/api/')) return await api(req, res, pathname);
    const staticFiles = { '/': ['index.html', 'text/html; charset=utf-8'],
      '/app.js': ['app.js', 'text/javascript; charset=utf-8'],
      '/style.css': ['style.css', 'text/css; charset=utf-8'] };
    const file = staticFiles[pathname];
    if (req.method !== 'GET' || !file) throw new ApiError(404, 'input_problem', 'Page not found.');
    const content = await readFile(join(publicDir, file[0]));
    res.writeHead(200, { 'content-type': file[1], 'cache-control': 'no-store',
      'x-content-type-options': 'nosniff',
      'content-security-policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
      'referrer-policy': 'no-referrer' });
    res.end(content);
  } catch (error) {
    if (res.headersSent) return res.end();
    const known = error instanceof ApiError;
    if (!known) console.error(error);
    sendJson(res, known ? error.status : 500, { kind: known ? error.kind : 'runtime_failure',
      message: known ? error.message : 'The Playground could not finish this operation.',
      detail: known ? error.detail : null });
  }
}

const server = createServer((req, res) => { void handle(req, res); });
server.listen(config.port, '127.0.0.1', () => {
  boundPort = server.address().port;
  console.log(`Sley Playground ${config.isolatedBundle ? 'isolated' : 'local'} mode: http://127.0.0.1:${boundPort}`);
});
const cleanup = setInterval(() => {
  for (const [token, session] of sessions) {
    if (Date.now() - session.touched > SESSION_MS && !session.expiring) {
      session.expiring = true;
      session.cleanupPromise = serial(session, () => removeSessionWorkspace(session))
        .then(() => sessions.delete(token))
        .catch(error => { session.expiring = false; session.cleanupPromise = null;
          console.error(error); });
    }
  }
}, 60_000);
cleanup.unref();
async function shutdown() {
  server.close();
  await Promise.all([...sessions.entries()].map(async ([token, session]) => {
    if (session.cleanupPromise) await session.cleanupPromise;
    if (!sessions.has(token)) return;
    await session.chain;
    await removeSessionWorkspace(session);
  }));
  process.exit(0);
}
process.once('SIGINT', () => { void shutdown(); });
process.once('SIGTERM', () => { void shutdown(); });
