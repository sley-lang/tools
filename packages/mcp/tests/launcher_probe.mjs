/** Verify the public CLI launcher against the installed MCP adapter. */

import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';

const runFile = promisify(execFile);
const args = Object.fromEntries(process.argv.slice(2).map((item) => item.split('=', 2)));
for (const name of ['tools', 'core', 'server', 'output']) {
  if (!args[name]) throw new Error(`missing ${name}=PATH`);
}
const tools = resolve(args.tools);
const core = resolve(args.core);
const server = resolve(args.server);
const output = resolve(args.output);
const hash = async (path) => createHash('sha256').update(await readFile(path)).digest('hex');

function content(response) {
  if (response.isError || response.content?.length !== 1 || response.content[0].type !== 'text') {
    throw new Error(`MCP tool failed: ${JSON.stringify(response)}`);
  }
  return JSON.parse(response.content[0].text);
}

async function rejected(argumentsList, code, status) {
  try {
    await runFile(tools, argumentsList, { timeout: 10000 });
  } catch (error) {
    const diagnostic = JSON.parse(error.stderr);
    if (error.code === status && error.stdout === '' && diagnostic.code === code) return;
    throw new Error(`wrong launcher refusal: ${JSON.stringify(diagnostic)}`);
  }
  throw new Error('launcher accepted an invalid setup');
}

const temporary = await mkdtemp(join(tmpdir(), 'sley-mcp-launcher-'));
try {
  const project = join(temporary, 'release');
  await runFile(tools, ['init', project, '--template', 'release-readiness', '--core', core],
    { timeout: 120000, maxBuffer: 1024 * 1024 });
  const base = ['mcp', '--project', project, '--core', core];
  await rejected(base, 'ST_FEATURE_UNSUPPORTED', 4);
  await rejected([...base, '--adapter', join(temporary, 'missing.mjs')],
    'ST_FEATURE_UNSUPPORTED', 4);
  await rejected([...base, '--adapter', server, '--review-before', project],
    'ST_INPUT_INVALID', 2);
  await rejected([...base, '--adapter', server, '--tools', tools],
    'ST_INPUT_INVALID', 2);

  const client = new Client({ name: 'sley-tools-launcher-probe', version: '0.0.1' });
  const transport = new StdioClientTransport({ command: tools,
    args: [...base, '--adapter', server, '--allow-write'] });
  await client.connect(transport);
  let oldRoot;
  let newRoot;
  try {
    const names = (await client.listTools()).tools.map((tool) => tool.name);
    for (const name of ['sley_inspect_program', 'sley_run_program', 'sley_propose_edit',
      'sley_validate_candidate', 'sley_commit_candidate']) {
      if (!names.includes(name)) throw new Error(`launcher omitted ${name}`);
    }
    oldRoot = content(await client.callTool({ name: 'sley_inspect_program', arguments: {} }))
      .accepted_state_root;
    const facts = { test_run_completed: true, tests_failed: 0,
      artifact_verified: true, blocking_findings: 1 };
    const before = content(await client.callTool({ name: 'sley_run_program',
      arguments: { inputs: facts } }));
    if (before.value.allow !== false || before.metadata.state_root !== oldRoot) {
      throw new Error('launcher did not execute the accepted native rule');
    }
    const proposed = content(await client.callTool({ name: 'sley_propose_edit',
      arguments: { change: { max_blocking_findings: 2 } } }));
    const validated = content(await client.callTool({ name: 'sley_validate_candidate',
      arguments: { candidate_id: proposed.candidate_id } }));
    if (validated.result !== 'PASS' || validated.accepted_root_unchanged !== true) {
      throw new Error('launcher did not preserve separate candidate validation');
    }
    const committed = content(await client.callTool({ name: 'sley_commit_candidate',
      arguments: { candidate_id: proposed.candidate_id } }));
    newRoot = committed.new_root;
    const after = content(await client.callTool({ name: 'sley_run_program',
      arguments: { inputs: facts } }));
    if (newRoot === oldRoot || committed.candidate_id !== proposed.candidate_id
      || after.value.allow !== true || after.metadata.state_root !== newRoot) {
      throw new Error('launcher did not carry the native behavior edit through commit');
    }
  } finally {
    await client.close();
  }
  const report = { contract: 'sley-tools.mcp-launcher-probe.v1', result: 'PASS',
    tools_binary_sha256: await hash(tools), core_binary_sha256: await hash(core),
    adapter_sha256: await hash(server), probe_entry_sha256: await hash(fileURLToPath(import.meta.url)),
    missing_adapter_rejected: true, missing_adapter_file_rejected: true,
    incomplete_review_rejected: true, tools_override_rejected: true,
    old_root: oldRoot, new_root: newRoot,
    native_boundary_changed: true, protocol_client_connected: true };
  await writeFile(output, `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify({ result: 'PASS', evidence: output })}\n`);
} finally {
  await rm(temporary, { recursive: true, force: true });
}
