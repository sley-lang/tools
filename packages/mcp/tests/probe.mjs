/** Reference MCP client: discover resources and complete a native Sley edit. */

import { execFile, spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';

const runFile = promisify(execFile);
const probeEntry = fileURLToPath(import.meta.url);
const args = Object.fromEntries(process.argv.slice(2).map((item) => item.split('=', 2)));
const server = resolve(args.server ?? fileURLToPath(new URL('../src/server.mjs', import.meta.url)));
const serverPrefix = args.serverPrefix ? args.serverPrefix.split(',') : [];
for (const name of ['tools', 'core', 'output']) if (!args[name]) throw new Error(`missing ${name}=PATH`);
const tools = resolve(args.tools);
const core = resolve(args.core);
const examples = fileURLToPath(new URL('../../../examples/graph/', import.meta.url));

function result(response) {
  if (response.content?.length !== 1 || response.content[0].type !== 'text') {
    throw new Error('MCP tool did not return one text result');
  }
  return JSON.parse(response.content[0].text);
}

function requireSuccess(response, label) {
  if (response.isError) throw new Error(`${label} failed: ${JSON.stringify(result(response))}`);
  return result(response);
}

async function connect(project, { allowWrite = false, modern = false, workspace = null,
  review = null } = {}) {
  const client = new Client({ name: 'sley-tools-reference-probe', version: '0.0.1' },
    modern ? { versionNegotiation: { mode: { pin: '2026-07-28' } } } : {});
  const transport = new StdioClientTransport({ command: process.execPath,
    args: [...serverPrefix, server, '--project', project, '--tools', tools, '--core', core,
      ...(allowWrite ? ['--allow-write'] : []), ...(workspace ? ['--workspace', workspace] : []),
      ...(review ? ['--review-before', review.before, '--review-after', review.after,
        '--review-cases', review.cases] : [])] });
  await client.connect(transport);
  return client;
}

async function auditRawStdio(project) {
  const child = spawn(process.execPath,
    [...serverPrefix, server, '--project', project, '--tools', tools, '--core', core],
    { stdio: ['pipe', 'pipe', 'pipe'] });
  child.stdout.setEncoding('utf8');
  child.stderr.setEncoding('utf8');
  let buffer = '';
  let stderr = '';
  let protocolError = null;
  let messageCount = 0;
  const pending = new Map();
  function fail(error) {
    protocolError = error;
    for (const waiter of pending.values()) waiter.reject(error);
    pending.clear();
    child.kill();
  }
  child.stderr.on('data', (chunk) => { stderr += chunk.slice(0, 1000); });
  child.stdout.on('data', (chunk) => {
    buffer += chunk;
    if (buffer.length > 1024 * 1024) return fail(new Error('MCP stdout exceeded audit limit'));
    while (buffer.includes('\n')) {
      const split = buffer.indexOf('\n');
      const line = buffer.slice(0, split);
      buffer = buffer.slice(split + 1);
      let message;
      try { message = JSON.parse(line); }
      catch { return fail(new Error(`non-JSON text on MCP stdout: ${line.slice(0, 120)}`)); }
      if (!message || message.jsonrpc !== '2.0'
        || (!Object.hasOwn(message, 'id') && typeof message.method !== 'string')) {
        return fail(new Error('non-protocol JSON on MCP stdout'));
      }
      messageCount += 1;
      const waiter = pending.get(message.id);
      if (waiter) {
        pending.delete(message.id);
        waiter.resolve(message);
      }
    }
  });
  function request(id, method, params = {}) {
    return new Promise((resolveResponse, rejectResponse) => {
      if (protocolError) return rejectResponse(protocolError);
      const timer = setTimeout(() => {
        pending.delete(id);
        rejectResponse(new Error(`raw MCP request ${method} timed out: ${stderr}`));
        child.kill();
      }, 10000);
      pending.set(id, { resolve: (value) => { clearTimeout(timer); resolveResponse(value); },
        reject: (error) => { clearTimeout(timer); rejectResponse(error); } });
      child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
    });
  }
  try {
    const initialized = await request(1, 'initialize', {
      protocolVersion: '2025-11-25', capabilities: {},
      clientInfo: { name: 'sley-tools-raw-stdio-audit', version: '0.0.1' },
    });
    if (initialized.result?.protocolVersion !== '2025-11-25') {
      throw new Error('raw MCP initialization returned an unexpected version');
    }
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
    const listed = await request(2, 'tools/list');
    if (listed.result?.tools?.length !== 3) throw new Error('raw MCP tool listing is incomplete');
    const inspected = await request(3, 'tools/call', {
      name: 'sley_inspect_program', arguments: {},
    });
    if (inspected.result?.content?.[0]?.type !== 'text') {
      throw new Error('raw MCP inspect did not return protocol content');
    }
    const closed = new Promise((resolveExit, rejectExit) => {
      const timer = setTimeout(() => rejectExit(new Error('raw MCP server did not exit')), 5000);
      child.once('close', (code) => { clearTimeout(timer); resolveExit(code); });
    });
    child.stdin.end();
    const exit = await closed;
    if (exit !== 0 || protocolError || buffer.length || pending.size || messageCount !== 3) {
      throw new Error(`MCP stdout audit failed: exit=${exit} messages=${messageCount} stderr=${stderr}`);
    }
    return messageCount;
  } finally {
    if (child.exitCode === null && child.signalCode === null) child.kill();
  }
}

const temporary = await mkdtemp(join(tmpdir(), 'sley-mcp-probe-'));
try {
  const project = join(temporary, 'release');
  await runFile(tools, ['init', project, '--template', 'release-readiness', '--core', core],
    { timeout: 120000, maxBuffer: 1024 * 1024 });
  const facts = { test_run_completed: true, tests_failed: 0,
    artifact_verified: true, blocking_findings: 1 };
  const beforeManifest = JSON.parse(await readFile(join(project, 'program.json'), 'utf8'));
  const protocolMessages = await auditRawStdio(project);
  const unrelatedWorkspace = join(temporary, 'unrelated');
  await mkdir(unrelatedWorkspace);
  let startupScopeRejected = false;
  try {
    await runFile(process.execPath, [...serverPrefix, server, '--project', project, '--tools', tools,
      '--core', core, '--allow-write', '--workspace', unrelatedWorkspace],
    { timeout: 10000, maxBuffer: 1024 * 1024 });
  } catch (error) {
    const refusal = JSON.parse(error.stderr);
    startupScopeRejected = error.code === 1 && error.stdout === ''
      && refusal.code === 'ST_INPUT_INVALID'
      && refusal.message.includes('direct child');
  }
  if (!startupScopeRejected) throw new Error('MCP accepted a project outside the configured workspace');
  const packageLock = fileURLToPath(new URL('../package-lock.json', import.meta.url));
  const sdkEntry = fileURLToPath(new URL('../../typescript/src/index.js', import.meta.url));
  let rootBefore;
  const readonly = await connect(project);
  try {
    if (readonly.getProtocolEra() !== 'legacy') throw new Error('default MCP client did not negotiate legacy era');
    const names = (await readonly.listTools()).tools.map((tool) => tool.name).sort();
    if (names.join(',') !== ['sley_inspect_program', 'sley_run_program', 'sley_run_tests'].sort().join(',')) {
      throw new Error(`read-only MCP tools differ: ${names}`);
    }
    const resources = (await readonly.listResources()).resources;
    if (resources.length !== 5) throw new Error('MCP resources are incomplete');
    const builder = await readonly.readResource({ uri: 'sley://builder-operations' });
    const guide = JSON.parse(builder.contents[0].text);
    if (guide.creation?.recipe?.contract !== 'sley-tools.graph-authoring.v0'
      || guide.creation?.cases?.length !== 2
      || !guide.operations.includes('int_mul_checked')) {
      throw new Error('MCP graph builder resource omits the native creation guide');
    }
    const capabilities = await readonly.readResource({ uri: 'sley://capabilities' });
    const capability = JSON.parse(capabilities.contents[0].text);
    if (capability.program_edits_enabled !== false || capability.execution_may_persist_reports !== true) {
      throw new Error('MCP capability misstated edit or execution effects');
    }
    const inspected = requireSuccess(await readonly.callTool({ name: 'sley_inspect_program', arguments: {} }), 'inspect');
    rootBefore = inspected.accepted_state_root;
    if (inspected.program.entrypoint.function_id !== beforeManifest.entrypoint.function_id) {
      throw new Error('MCP inspection lost the native function identity');
    }
    const observed = requireSuccess(await readonly.callTool({ name: 'sley_run_program',
      arguments: { inputs: facts } }), 'run');
    if (observed.value.allow !== false || observed.metadata.state_root !== rootBefore) {
      throw new Error('read-only MCP execution differs from accepted Sley graph');
    }
    const rejectedPath = await readonly.callTool({ name: 'sley_inspect_program',
      arguments: { project: temporary } });
    if (!rejectedPath.isError) throw new Error('MCP accepted a model-supplied workspace path');
    let unauthorizedRejected = false;
    try {
      const unauthorized = await readonly.callTool({ name: 'sley_propose_edit',
        arguments: { change: { max_blocking_findings: 2 } } });
      unauthorizedRejected = unauthorized.isError === true;
    } catch {
      unauthorizedRejected = true;
    }
    if (!unauthorizedRejected) throw new Error('read-only MCP connection accepted a write tool');
    const unchanged = requireSuccess(await readonly.callTool({ name: 'sley_inspect_program',
      arguments: {} }), 'inspect after denied write');
    if (unchanged.accepted_state_root !== rootBefore) {
      throw new Error('denied MCP write changed accepted state');
    }
  } finally {
    await readonly.close();
  }

  let rootAfter;
  const review = { before: resolve('evidence/review/fixtures/release-readiness/before/program.json'),
    after: resolve('evidence/review/fixtures/release-readiness/after/program.json'),
    cases: resolve('programs/release-readiness/tests/bridge-cases.json') };
  const writer = await connect(project, { allowWrite: true, workspace: temporary, review });
  try {
    const names = (await writer.listTools()).tools.map((tool) => tool.name);
    if (names.length !== 9) throw new Error(`fully configured MCP tool count differs: ${names}`);
    for (const name of ['sley_create_program', 'sley_create_graph', 'sley_review_change', 'sley_propose_edit',
      'sley_validate_candidate', 'sley_commit_candidate']) {
      if (!names.includes(name)) throw new Error(`write-enabled MCP omitted ${name}`);
    }
    const reviewed = requireSuccess(await writer.callTool({ name: 'sley_review_change',
      arguments: {} }), 'native review');
    if (reviewed.report.result !== 'PASS_BOUNDED_LOCAL'
      || reviewed.report.identity.program_id !== 'release-readiness'
      || reviewed.report.behavioral_evidence.case_count !== 48
      || reviewed.report.behavioral_evidence.changed_case_count < 1
      || reviewed.report.validation.native_core_test_admission
        !== 'NOT_RUN: production native executor unavailable'
      || reviewed.verification.result !== 'PASS') {
      throw new Error('MCP Review did not reproduce verified native differences');
    }
    const invalidName = await writer.callTool({ name: 'sley_create_program',
      arguments: { name: '../escaped', template: 'resource-admission' } });
    if (!invalidName.isError) throw new Error('MCP accepted an out-of-scope project name');
    const created = requireSuccess(await writer.callTool({ name: 'sley_create_program',
      arguments: { name: 'resource', template: 'resource-admission' } }), 'create native program');
    if (created.project_path !== join(temporary, 'resource')
      || created.program_id !== 'resource-admission'
      || !/^[0-9a-f]{64}$/.test(created.accepted_state_root)
      || created.selected_connection_unchanged !== true) {
      throw new Error('MCP creation lost workspace or native root identity');
    }
    const duplicateCreate = await writer.callTool({ name: 'sley_create_program',
      arguments: { name: 'resource', template: 'resource-admission' } });
    if (!duplicateCreate.isError || result(duplicateCreate).code !== 'ST_INPUT_INVALID') {
      throw new Error('MCP creation overwrote an existing project');
    }
    const graphRecipe = JSON.parse(await readFile(join(examples, 'numeric-echo.json'), 'utf8'));
    const graphFixture = JSON.parse(await readFile(join(examples, 'numeric-echo-cases.json'), 'utf8'));
    const graphCases = graphFixture.cases.map((row) => ({ id: row.id,
      inputs: { signed: row.input[0], unsigned: Number(row.input[1]) },
      expected: { signed: row.expected.signed, unsigned: Number(row.expected.unsigned) } }));
    const mismatchedGraph = await writer.callTool({ name: 'sley_create_graph',
      arguments: { name: 'different-name', recipe: graphRecipe, cases: graphCases } });
    if (!mismatchedGraph.isError || result(mismatchedGraph).code !== 'ST_INPUT_INVALID') {
      throw new Error('MCP accepted a graph identity outside its destination name');
    }
    const extraInput = await writer.callTool({ name: 'sley_create_graph',
      arguments: { name: 'numeric-echo', recipe: graphRecipe,
        cases: [{ ...graphCases[0], inputs: { ...graphCases[0].inputs, extra: true } }] } });
    if (!extraInput.isError || result(extraInput).code !== 'ST_INPUT_INVALID') {
      throw new Error('MCP silently dropped an undeclared graph input');
    }
    const graph = requireSuccess(await writer.callTool({ name: 'sley_create_graph',
      arguments: { name: 'numeric-echo', recipe: graphRecipe, cases: graphCases } }), 'create native graph');
    if (graph.project_path !== join(temporary, 'numeric-echo')
      || graph.program_id !== 'numeric-echo'
      || !/^[0-9a-f]{64}$/.test(graph.accepted_state_root)
      || graph.selected_connection_unchanged !== true) {
      throw new Error('MCP graph creation lost native program identity');
    }
    const graphClient = await connect(graph.project_path);
    try {
      const graphResult = requireSuccess(await graphClient.callTool({ name: 'sley_run_program',
        arguments: { inputs: { signed: '-9223372036854775808', unsigned: 4294967295 } } }), 'run newly created graph');
      const graphTests = requireSuccess(await graphClient.callTool({ name: 'sley_run_tests',
        arguments: {} }), 'test newly created graph');
      if (graphResult.value.signed !== '-9223372036854775808'
        || graphResult.value.unsigned !== 4294967295
        || graphResult.metadata.state_root !== graph.accepted_state_root
        || graphTests.external_assertions_against_native_execution !== graphCases.length) {
        throw new Error('MCP-created Sley graph failed its native boundary or declared cases');
      }
    } finally {
      await graphClient.close();
    }
    const duplicateGraph = await writer.callTool({ name: 'sley_create_graph',
      arguments: { name: 'numeric-echo', recipe: graphRecipe, cases: graphCases } });
    if (!duplicateGraph.isError || result(duplicateGraph).code !== 'ST_INPUT_INVALID') {
      throw new Error('MCP graph creation overwrote an existing project');
    }
    const proposal = requireSuccess(await writer.callTool({ name: 'sley_propose_edit',
      arguments: { change: { max_blocking_findings: 2 } } }), 'propose');
    if (proposal.base_root !== rootBefore || !/^[0-9a-f]{64}$/.test(proposal.candidate_id)) {
      throw new Error('MCP proposal lost candidate or base identity');
    }
    const conflictingProposal = await writer.callTool({ name: 'sley_propose_edit',
      arguments: { change: { max_blocking_findings: 3 } } });
    if (!conflictingProposal.isError || result(conflictingProposal).code !== 'ST_INPUT_INVALID'
      || !result(conflictingProposal).message.includes('proposal already exists')) {
      throw new Error('MCP accepted a conflicting change while a candidate was pending');
    }
    const afterRejectedChange = requireSuccess(await writer.callTool({ name: 'sley_inspect_program',
      arguments: {} }), 'inspect after conflicting proposal');
    if (afterRejectedChange.accepted_state_root !== rootBefore
      || afterRejectedChange.threshold !== 0) {
      throw new Error('rejected MCP change advanced the accepted root');
    }
    const validation = requireSuccess(await writer.callTool({ name: 'sley_validate_candidate',
      arguments: { candidate_id: proposal.candidate_id } }), 'validate');
    if (validation.result !== 'PASS' || validation.accepted_root_unchanged !== true) {
      throw new Error('MCP validation changed accepted state');
    }
    const commit = requireSuccess(await writer.callTool({ name: 'sley_commit_candidate',
      arguments: { candidate_id: proposal.candidate_id } }), 'commit');
    rootAfter = commit.new_root;
    if (rootAfter === rootBefore || commit.candidate_id !== proposal.candidate_id) {
      throw new Error('MCP commit omitted the accepted change');
    }
    const after = requireSuccess(await writer.callTool({ name: 'sley_run_program',
      arguments: { inputs: facts } }), 'run after commit');
    if (after.value.allow !== true || after.metadata.state_root !== rootAfter) {
      throw new Error('MCP native boundary did not change');
    }
    const tests = requireSuccess(await writer.callTool({ name: 'sley_run_tests', arguments: {} }), 'tests');
    if (tests.external_assertions_against_native_execution !== 60
      || tests.native_core_test_admission !== 'NOT_RUN: production native executor unavailable') {
      throw new Error('MCP test status conflated external and native tests');
    }
    const duplicate = await writer.callTool({ name: 'sley_propose_edit',
      arguments: { change: { max_blocking_findings: 2 } } });
    if (!duplicate.isError || result(duplicate).code !== 'ST_INPUT_INVALID') {
      throw new Error('MCP did not preserve the typed duplicate-edit error');
    }
  } finally {
    await writer.close();
  }

  const modern = await connect(project, { modern: true });
  try {
    if (modern.getProtocolEra() !== 'modern') throw new Error('MCP did not negotiate the 2026 protocol era');
    const inspected = requireSuccess(await modern.callTool({ name: 'sley_inspect_program', arguments: {} }), 'modern inspect');
    if (inspected.accepted_state_root !== rootAfter) throw new Error('modern MCP observed a stale root');
  } finally {
    await modern.close();
  }

  const resourceProject = join(temporary, 'resource');
  const resourceClient = await connect(resourceProject);
  try {
    const exact = requireSuccess(await resourceClient.callTool({ name: 'sley_run_program', arguments: {
      inputs: { requested_workers: '0', available_workers: '0',
        requested_memory_mib: '9007199254740993', available_memory_mib: '9007199254740992' },
    } }), 'resource exact i64');
    if (exact.value.requested_memory_mib !== '9007199254740993'
      || exact.value.available_memory_mib !== '9007199254740992'
      || exact.value.allow !== false || exact.value.insufficient_memory !== true) {
      throw new Error('MCP changed a signed 64 bit native result');
    }
    const resourceTests = requireSuccess(await resourceClient.callTool({ name: 'sley_run_tests',
      arguments: {} }), 'resource tests');
    if (resourceTests.external_assertions_against_native_execution !== 12) {
      throw new Error(`MCP resource external assertions differ: ${resourceTests.external_assertions_against_native_execution}`);
    }
  } finally {
    await resourceClient.close();
  }

  const fixedProject = join(temporary, 'fixed');
  await runFile(tools, ['init', fixedProject, '--graph', join(examples, 'fixed-limit.json'),
    '--tests', join(examples, 'fixed-limit-cases.json'), '--core', core],
  { timeout: 120000, maxBuffer: 1024 * 1024 });
  const fixedClient = await connect(fixedProject, { allowWrite: true });
  try {
    const before = requireSuccess(await fixedClient.callTool({ name: 'sley_run_program',
      arguments: { inputs: { count: 11 } } }), 'fixed before');
    if (before.value !== false) throw new Error('MCP fixed graph began with wrong native boundary');
    const fixedCases = JSON.parse(await readFile(join(examples, 'fixed-limit-after-cases.json'), 'utf8'));
    const proposal = requireSuccess(await fixedClient.callTool({ name: 'sley_propose_edit', arguments: {
      constant: 'limit', value: 12,
      cases: fixedCases.cases.map((row) => ({ id: row.id, inputs: { count: Number(row.input[0]) },
        expected: row.expected })),
    } }), 'fixed proposal');
    if (proposal.base_root !== before.metadata.state_root) throw new Error('MCP graph proposal lost base root');
    const validation = requireSuccess(await fixedClient.callTool({ name: 'sley_validate_candidate',
      arguments: { candidate_id: proposal.candidate_id } }), 'fixed validation');
    if (validation.result !== 'PASS' || validation.accepted_root_unchanged !== true) {
      throw new Error('MCP graph validation changed accepted state');
    }
    const commit = requireSuccess(await fixedClient.callTool({ name: 'sley_commit_candidate',
      arguments: { candidate_id: proposal.candidate_id } }), 'fixed commit');
    const after = requireSuccess(await fixedClient.callTool({ name: 'sley_run_program',
      arguments: { inputs: { count: 11 } } }), 'fixed after');
    if (after.value !== true || after.metadata.state_root !== commit.new_root) {
      throw new Error('MCP graph native boundary did not change');
    }
    const graphTests = requireSuccess(await fixedClient.callTool({ name: 'sley_run_tests',
      arguments: {} }), 'fixed graph tests');
    if (graphTests.external_assertions_against_native_execution !== 6) {
      throw new Error('MCP graph external assertions differ');
    }
  } finally {
    await fixedClient.close();
  }

  const opcodeProject = join(temporary, 'opcode');
  await runFile(tools, ['init', opcodeProject, '--graph', join(examples, 'fixed-limit.json'),
    '--tests', join(examples, 'fixed-limit-cases.json'), '--core', core],
  { timeout: 120000, maxBuffer: 1024 * 1024 });
  const opcodeClient = await connect(opcodeProject, { allowWrite: true });
  try {
    const names = (await opcodeClient.listTools()).tools.map((tool) => tool.name);
    if (names.length !== 8 || !names.includes('sley_propose_opcode_edit')
      || !names.includes('sley_propose_operand_edit')) {
      throw new Error(`graph opcode MCP tool discovery differs: ${names}`);
    }
    const before = requireSuccess(await opcodeClient.callTool({ name: 'sley_run_program',
      arguments: { inputs: { count: 10 } } }), 'opcode before');
    if (before.value !== true) throw new Error('opcode graph began with wrong native boundary');
    const opcodeCases = JSON.parse(await readFile(join(examples, 'fixed-limit-exclusive-cases.json'), 'utf8'));
    const proposal = requireSuccess(await opcodeClient.callTool({ name: 'sley_propose_opcode_edit', arguments: {
      operation: 'entry.within', opcode: 'less_than',
      cases: opcodeCases.cases.map((row) => ({ id: row.id, inputs: { count: Number(row.input[0]) },
        expected: row.expected })),
    } }), 'opcode proposal');
    if (proposal.base_root !== before.metadata.state_root) throw new Error('MCP opcode proposal lost base root');
    const validation = requireSuccess(await opcodeClient.callTool({ name: 'sley_validate_candidate',
      arguments: { candidate_id: proposal.candidate_id } }), 'opcode validation');
    if (validation.result !== 'PASS' || validation.accepted_root_unchanged !== true) {
      throw new Error('MCP opcode validation changed accepted state');
    }
    const commit = requireSuccess(await opcodeClient.callTool({ name: 'sley_commit_candidate',
      arguments: { candidate_id: proposal.candidate_id } }), 'opcode commit');
    const after = requireSuccess(await opcodeClient.callTool({ name: 'sley_run_program',
      arguments: { inputs: { count: 10 } } }), 'opcode after');
    if (after.value !== false || after.metadata.state_root !== commit.new_root) {
      throw new Error('MCP opcode native boundary did not change');
    }
    const graphTests = requireSuccess(await opcodeClient.callTool({ name: 'sley_run_tests',
      arguments: {} }), 'opcode graph tests');
    if (graphTests.external_assertions_against_native_execution !== 5) {
      throw new Error('MCP opcode graph external assertions differ');
    }
  } finally {
    await opcodeClient.close();
  }

  const operandProject = join(temporary, 'operand');
  await runFile(tools, ['init', operandProject, '--graph', join(examples, 'fixed-limit.json'),
    '--tests', join(examples, 'fixed-limit-cases.json'), '--core', core],
  { timeout: 120000, maxBuffer: 1024 * 1024 });
  const operandClient = await connect(operandProject, { allowWrite: true });
  try {
    const before = requireSuccess(await operandClient.callTool({ name: 'sley_run_program',
      arguments: { inputs: { count: 11 } } }), 'operand before');
    if (before.value !== false) throw new Error('operand graph began with wrong native boundary');
    const rows = [0, 10, 11].map((count) => ({ id: `self-${count}`,
      inputs: { count }, expected: true }));
    const proposed = requireSuccess(await operandClient.callTool({ name: 'sley_propose_operand_edit',
      arguments: { operation: 'entry.within', slot: 'right', reference: 'param.count',
        cases: rows } }), 'operand proposal');
    requireSuccess(await operandClient.callTool({ name: 'sley_validate_candidate',
      arguments: { candidate_id: proposed.candidate_id } }), 'operand validation');
    const committed = requireSuccess(await operandClient.callTool({ name: 'sley_commit_candidate',
      arguments: { candidate_id: proposed.candidate_id } }), 'operand commit');
    const after = requireSuccess(await operandClient.callTool({ name: 'sley_run_program',
      arguments: { inputs: { count: 11 } } }), 'operand after');
    if (after.value !== true || after.metadata.state_root !== committed.new_root
      || committed.external_assertions !== rows.length) {
      throw new Error('MCP operand edit did not change native behavior');
    }
  } finally {
    await operandClient.close();
  }

  const branchProject = join(temporary, 'branch');
  await runFile(tools, ['init', branchProject, '--graph', join(examples, 'range-predicate.json'),
    '--tests', join(examples, 'range-predicate-cases.json'), '--core', core],
  { timeout: 120000, maxBuffer: 1024 * 1024 });
  const branchClient = await connect(branchProject, { allowWrite: true });
  try {
    const names = (await branchClient.listTools()).tools.map((item) => item.name);
    if (!names.includes('sley_propose_branch_edit')) throw new Error('MCP branch tool is absent');
    const initial = JSON.parse(await readFile(join(examples, 'range-predicate-cases.json'), 'utf8'));
    const cases = initial.cases.map((row) => {
      const [value, lower, upper] = row.input.map(BigInt);
      const expected = lower <= upper
        ? { within_range: false, invalid_bounds: true, below_lower: false, above_upper: false }
        : { within_range: !(value < lower || value > upper), invalid_bounds: false,
          below_lower: value < lower, above_upper: value > upper };
      return { id: row.id,
        inputs: { value: row.input[0], lower: row.input[1], upper: row.input[2] }, expected };
    });
    const proposed = requireSuccess(await branchClient.callTool({ name: 'sley_propose_branch_edit',
      arguments: { block: 'entry', targets: { if_true: 'valid_bounds', if_false: 'invalid_bounds' },
        cases } }), 'branch proposal');
    requireSuccess(await branchClient.callTool({ name: 'sley_validate_candidate',
      arguments: { candidate_id: proposed.candidate_id } }), 'branch validation');
    const committed = requireSuccess(await branchClient.callTool({ name: 'sley_commit_candidate',
      arguments: { candidate_id: proposed.candidate_id } }), 'branch commit');
    if (committed.changed_case_count !== cases.length || committed.external_assertions !== cases.length) {
      throw new Error('MCP branch edit did not change and verify all native cases');
    }
    const after = requireSuccess(await branchClient.callTool({ name: 'sley_run_program',
      arguments: { inputs: { value: '0', lower: '-5', upper: '5' } } }), 'branch after');
    if (after.value.invalid_bounds !== true || after.metadata.state_root !== committed.new_root) {
      throw new Error('MCP branch edit did not change native routing');
    }
  } finally {
    await branchClient.close();
  }

  const numericProject = join(temporary, 'numeric-echo');
  const numericClient = await connect(numericProject);
  try {
    const exact = requireSuccess(await numericClient.callTool({ name: 'sley_run_program', arguments: {
      inputs: { signed: '-9223372036854775808', unsigned: 4294967295 },
    } }), 'numeric graph exact i64');
    if (exact.value.signed !== '-9223372036854775808' || exact.value.unsigned !== 4294967295) {
      throw new Error('MCP changed a graph numeric native result');
    }
    const numericTests = requireSuccess(await numericClient.callTool({ name: 'sley_run_tests',
      arguments: {} }), 'numeric graph tests');
    if (numericTests.external_assertions_against_native_execution !== 3) {
      throw new Error('MCP numeric external assertions differ');
    }
  } finally {
    await numericClient.close();
  }

  const comparatorProject = join(temporary, 'comparator');
  const comparatorSpec = join(examples, 'benchmark-comparator.json');
  const comparatorCases = join(examples, 'benchmark-comparator-cases.json');
  await runFile(tools, ['init', comparatorProject, '--graph', comparatorSpec,
    '--tests', comparatorCases, '--core', core],
  { timeout: 120000, maxBuffer: 1024 * 1024 });
  const comparatorClient = await connect(comparatorProject);
  try {
    const compared = requireSuccess(await comparatorClient.callTool({ name: 'sley_run_program', arguments: {
      inputs: { baseline_wall_us: '-1', candidate_wall_us: '9223372036854775807',
        baseline_cpu_us: '200', candidate_cpu_us: '220',
        baseline_archive_bytes: '1000', candidate_archive_bytes: '999' },
    } }), 'checked comparator');
    if (compared.value.wall_delta_us?.error?.code !== 1
      || compared.value.cpu_delta_us?.ok !== '20'
      || compared.value.archive_delta_bytes?.ok !== '-1'
      || compared.value.invalid_input !== true || compared.value.any_regression !== true) {
      throw new Error('MCP changed native checked arithmetic or regression flags');
    }
    const comparatorTests = requireSuccess(await comparatorClient.callTool({ name: 'sley_run_tests',
      arguments: {} }), 'comparator tests');
    if (comparatorTests.external_assertions_against_native_execution !== 9) {
      throw new Error('MCP comparator external assertions differ');
    }
  } finally {
    await comparatorClient.close();
  }

  const report = {
    contract: 'sley-tools.mcp-reference-probe.v1', result: 'PASS',
    runner_runtime: process.versions.deno ? `deno ${process.versions.deno}`
      : process.versions.bun ? `bun ${process.versions.bun}` : `node ${process.version}`,
    server_launch_prefix: serverPrefix,
    sdk_server_version: '2.1.0', sdk_client_version: '2.1.0',
    negotiated_protocol_eras: ['legacy', 'modern'],
    stdout_protocol_only: true, raw_stdio_messages: protocolMessages,
    tools_binary_sha256: createHash('sha256').update(await readFile(tools)).digest('hex'),
    core_binary_sha256: createHash('sha256').update(await readFile(core)).digest('hex'),
    server_entry_sha256: createHash('sha256').update(await readFile(server)).digest('hex'),
    probe_entry_sha256: createHash('sha256').update(await readFile(probeEntry)).digest('hex'),
    tools_sdk_entry_sha256: createHash('sha256').update(await readFile(sdkEntry)).digest('hex'),
    package_lock_sha256: createHash('sha256').update(await readFile(packageLock)).digest('hex'),
    review_before_manifest_sha256: createHash('sha256').update(await readFile(review.before)).digest('hex'),
    review_after_manifest_sha256: createHash('sha256').update(await readFile(review.after)).digest('hex'),
    review_cases_sha256: createHash('sha256').update(await readFile(review.cases)).digest('hex'),
    comparator_spec_sha256: createHash('sha256').update(await readFile(comparatorSpec)).digest('hex'),
    comparator_cases_sha256: createHash('sha256').update(await readFile(comparatorCases)).digest('hex'),
    opcode_cases_sha256: createHash('sha256').update(await readFile(join(examples, 'fixed-limit-exclusive-cases.json'))).digest('hex'),
    before_root: rootBefore, after_root: rootAfter,
    configured_workspace_scope: true, program_edits_opt_in: true,
    out_of_scope_startup_rejected: true,
    unauthorized_write_rejected: true,
    conflicting_change_rejected: true, rejected_change_root_unchanged: true,
    original_candidate_committed_after_rejection: true,
    workspace_creation_scoped: true, created_native_template_project: true,
    created_native_graph_project: true,
    configured_native_review_verified: true,
    execution_may_persist_reports: true,
    resources_discovered: 5, tools_discovered_full: 9, native_boundary_changed: true,
    resource_i64_exact: true, graph_constant_edit: true, graph_opcode_edit: true,
    graph_operand_edit: true,
    graph_branch_edit: true, graph_numeric_exact: true,
    checked_arithmetic_result: true,
    external_assertions: 106, native_core_test_admission: 'NOT_RUN: production native executor unavailable',
    tool_error_code_preserved: true,
  };
  const output = resolve(args.output);
  await mkdir(dirname(output), { recursive: true });
  await writeFile(output, JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(`wrote ${args.output}: reference MCP client completed native Sley edit\n`);
} finally {
  await rm(temporary, { recursive: true, force: true });
}
