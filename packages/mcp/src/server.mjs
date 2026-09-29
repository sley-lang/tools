#!/usr/bin/env node
/** Local MCP adapter for one explicitly selected Sley project. */

import { execFile } from 'node:child_process';
import { mkdtemp, readFile, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { promisify } from 'node:util';

import { McpServer } from '@modelcontextprotocol/server';
import { serveStdio } from '@modelcontextprotocol/server/stdio';
import * as z from 'zod/v4';

import { SleyToolsError, encodeInteger, openRuntime } from '../../typescript/src/index.js';

const VERSION = '0.0.1';
const runFile = promisify(execFile);
const DECIMAL = /^(0|-?[1-9][0-9]*)$/;
const HEX_ID = /^[0-9a-f]{64}$/;
const GRAPH_NAME = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;
const U32 = z.number().int().min(0).max(4294967295);
const I64 = z.string().regex(DECIMAL).describe('Exact signed 64 bit decimal string');
const GRAPH_INPUT_TYPE = z.enum(['Bool', 'UInt32', 'SInt64']);
const GRAPH_RESULT_TYPE = z.enum(['Bool', 'UInt32', 'SInt64', 'CheckedSInt64']);
const GRAPH_RECIPE = z.object({
  contract: z.literal('sley-tools.graph-authoring.v0'),
  program_id: z.string().regex(GRAPH_NAME).max(64),
  title: z.string().min(1).max(120), purpose: z.string().min(1).max(500),
  parameters: z.array(z.object({ name: z.string(), value_type: GRAPH_INPUT_TYPE }).strict()).max(16),
  constants: z.array(z.object({ name: z.string(), value_type: GRAPH_INPUT_TYPE,
    value: z.union([z.boolean(), U32, I64]) }).strict()).max(64),
  result: z.union([
    z.object({ kind: z.literal('primitive'), value_type: GRAPH_RESULT_TYPE }).strict(),
    z.object({ kind: z.literal('record'), fields: z.array(z.object({
      name: z.string(), value_type: GRAPH_RESULT_TYPE,
    }).strict()).min(1).max(16) }).strict(),
  ]),
  entry_block: z.string(), blocks: z.array(z.unknown()).min(1).max(16),
}).strict();
const GRAPH_CASES = z.array(z.object({ id: z.string().min(1).max(64),
  inputs: z.record(z.string(), z.union([z.boolean(), U32, I64])),
  expected: z.unknown(),
}).strict()).min(1).max(256);

function options(argv) {
  const selected = {};
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === '--allow-write' && selected.allowWrite === undefined) {
      selected.allowWrite = true;
    } else if (['--project', '--tools', '--core', '--workspace', '--review-before',
      '--review-after', '--review-cases'].includes(flag) && selected[flag] === undefined) {
      selected[flag] = argv[++index];
    } else {
      throw new Error(`unsupported or duplicate MCP option: ${flag}`);
    }
  }
  for (const flag of ['--project', '--tools', '--core']) {
    if (!selected[flag]) throw new Error(`MCP requires ${flag} PATH`);
  }
  const reviewFlags = ['--review-before', '--review-after', '--review-cases'];
  if (reviewFlags.some((flag) => Boolean(selected[flag]))
    && reviewFlags.some((flag) => !selected[flag])) {
    throw new Error('MCP review needs --review-before, --review-after, and --review-cases together');
  }
  return selected;
}

function kindSchema(kind) {
  if (kind === 'Bool') return z.boolean();
  if (kind === 'UInt32') return U32;
  if (kind === 'SInt64') return I64;
  if (kind === 'CheckedSInt64') return z.union([
    z.object({ ok: I64 }).strict(),
    z.object({ error: z.object({ code: z.literal(1) }).strict() }).strict(),
  ]);
  throw new Error(`unsupported graph type: ${kind}`);
}

function inputShape(program) {
  if (program.graphProfile) {
    return Object.fromEntries(program.graphParameters.map((parameter) => [parameter.name, kindSchema(parameter.value_type)]));
  }
  if (program.name === 'release-readiness') return {
    test_run_completed: z.boolean(), tests_failed: U32,
    artifact_verified: z.boolean(), blocking_findings: U32,
  };
  if (program.name === 'resource-admission') return {
    requested_workers: I64, available_workers: I64,
    requested_memory_mib: I64, available_memory_mib: I64,
  };
  throw new Error(`unsupported MCP program profile: ${program.name}`);
}

function resultSchema(program) {
  if (program.graphResult?.kind === 'primitive') return kindSchema(program.graphResult.value_type);
  if (program.graphResult?.kind === 'record') {
    return z.object(Object.fromEntries(program.graphResult.fields.map((field) =>
      [field.name, kindSchema(field.value_type)]))).strict();
  }
  throw new Error('graph result shape is unavailable');
}

function hostValue(value, kind) {
  if (kind === 'SInt64') {
    const exact = BigInt(value);
    if (encodeInteger(exact, 'i64').value !== value) {
      throw new SleyToolsError('ST_INPUT_INVALID', 'input_shape', 'signed integer is not canonical');
    }
    return exact;
  }
  if (kind === 'UInt32' && typeof value === 'string') {
    const exact = BigInt(value);
    if (encodeInteger(exact, 'u32').value !== value) {
      throw new SleyToolsError('ST_INPUT_INVALID', 'input_shape', 'unsigned integer is not canonical');
    }
    return Number(exact);
  }
  return value;
}

function hostInputs(program, values) {
  if (program.graphProfile) return Object.fromEntries(program.graphParameters.map((parameter) =>
    [parameter.name, hostValue(values[parameter.name], parameter.value_type)]));
  if (program.name === 'resource-admission') return Object.fromEntries(Object.entries(values).map(([key, value]) =>
    [key, hostValue(value, 'SInt64')]));
  return values;
}

function hostExpected(program, expected) {
  if (program.graphResult.kind === 'primitive') return hostExpectedValue(expected, program.graphResult.value_type);
  return Object.fromEntries(program.graphResult.fields.map((field) =>
    [field.name, hostExpectedValue(expected[field.name], field.value_type)]));
}

function hostExpectedValue(value, kind) {
  if (kind === 'CheckedSInt64') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new SleyToolsError('ST_INPUT_INVALID', 'input_shape', 'checked result needs one arm');
    }
    return Object.hasOwn(value, 'ok') ? { ok: hostValue(value.ok, 'SInt64') } : value;
  }
  return hostValue(value, kind);
}

function encoded(value) {
  return JSON.stringify(value, (_name, item) => typeof item === 'bigint' ? item.toString() : item);
}

function success(value) {
  const text = encoded(value);
  if (Buffer.byteLength(text) > 131072) {
    return failure(new SleyToolsError('ST_RESOURCE_LIMIT', 'resource_limit', 'MCP result exceeds 128 KiB'));
  }
  return { content: [{ type: 'text', text }] };
}

function failure(error) {
  const details = error instanceof SleyToolsError
    ? { code: error.code, category: error.category, message: error.message,
        core_symbol: error.coreSymbol, core_numeric: error.coreNumeric }
    : { code: 'ST_TRANSPORT_FAILED', category: 'transport', message: String(error).slice(0, 500) };
  return { isError: true, content: [{ type: 'text', text: encoded(details) }] };
}

function tool(server, name, config, handler) {
  server.registerTool(name, config, async (args) => {
    try { return success(await handler(args)); }
    catch (error) { return failure(error); }
  });
}

async function cliJson(tools, args, timeout = 120000) {
  try {
    const { stdout } = await runFile(tools, args, {
      timeout, maxBuffer: 1024 * 1024,
    });
    return JSON.parse(stdout);
  } catch (error) {
    if (error instanceof SleyToolsError) throw error;
    try {
      const details = JSON.parse(error.stderr);
      if (typeof details.code === 'string' && typeof details.message === 'string') {
        throw new SleyToolsError(details.code, details.category ?? 'transport', details.message,
          { coreSymbol: details.core_symbol, coreNumeric: details.core_numeric });
      }
    } catch (parsed) {
      if (parsed instanceof SleyToolsError) throw parsed;
    }
    throw new SleyToolsError('ST_TRANSPORT_FAILED', 'transport',
      `Sley tools command failed: ${String(error).slice(0, 300)}`);
  }
}

async function initTemplate(tools, core, workspace, name, template) {
  const destination = join(workspace, name);
  await cliJson(tools, ['init', destination, '--template', template, '--core', core]);
  let selected;
  try { selected = await cliJson(tools, ['inspect', '--project', destination], 30000); }
  catch (error) {
    throw new SleyToolsError('ST_TRANSPORT_FAILED', 'transport',
      `project was created at ${destination}, but inspection failed: ${error.message}`);
  }
  return { project_path: destination, program_id: selected.program.program_id,
    accepted_state_root: selected.accepted_state_root,
    native_function_id: selected.program.entrypoint.function_id,
    selected_connection_unchanged: true };
}

async function initGraph(tools, core, workspace, name, recipe, cases) {
  if (recipe.program_id !== name) {
    throw new SleyToolsError('ST_INPUT_INVALID', 'input_shape',
      'graph program_id must match the new project name');
  }
  if (Buffer.byteLength(encoded(recipe)) > 65536 || Buffer.byteLength(encoded(cases)) > 1048576) {
    throw new SleyToolsError('ST_RESOURCE_LIMIT', 'resource_limit',
      'graph recipe or cases exceed the bounded authoring profile');
  }
  const parameters = recipe.parameters;
  const result = recipe.result;
  const convertedCases = cases.map((row) => {
    if (Object.keys(row.inputs).length !== parameters.length
      || parameters.some(({ name: parameter }) => !Object.hasOwn(row.inputs, parameter))) {
      throw new SleyToolsError('ST_INPUT_INVALID', 'input_shape',
        `graph case ${row.id} inputs must match the declared parameters exactly`);
    }
    if (result.kind === 'record' && (!row.expected || typeof row.expected !== 'object'
      || Array.isArray(row.expected) || Object.keys(row.expected).length !== result.fields.length
      || result.fields.some(({ name: field }) => !Object.hasOwn(row.expected, field)))) {
      throw new SleyToolsError('ST_INPUT_INVALID', 'input_shape',
        `graph case ${row.id} expected fields must match the declared result exactly`);
    }
    return { id: row.id,
      inputs: Object.fromEntries(parameters.map(({ name: parameter, value_type: kind }) =>
        [parameter, hostValue(row.inputs[parameter], kind)])),
      expected: result.kind === 'primitive'
        ? hostExpectedValue(row.expected, result.value_type)
        : Object.fromEntries(result.fields.map(({ name: field, value_type: kind }) =>
          [field, hostExpectedValue(row.expected[field], kind)])) };
  });
  const convertedRecipe = { ...recipe, constants: recipe.constants.map((constant) =>
    ({ ...constant, value: hostValue(constant.value, constant.value_type) })) };
  const destination = join(workspace, name);
  const creationRuntime = openRuntime({ tools, core });
  try {
    const created = await creationRuntime.createGraph({ project: destination,
      recipe: convertedRecipe, cases: convertedCases });
    const selected = await created.inspect();
    return { project_path: destination, program_id: selected.program.program_id,
      accepted_state_root: selected.accepted_state_root,
      native_function_id: selected.program.entrypoint.function_id,
      selected_connection_unchanged: true };
  } finally {
    await creationRuntime.close();
  }
}

async function reviewChange(tools, core, configured) {
  const scratch = await mkdtemp(join(tmpdir(), 'sley-mcp-review-'));
  const reportPath = join(scratch, 'report.json');
  const inputs = ['--before', configured.before, '--after', configured.after,
    '--core', core, '--cases', configured.cases];
  try {
    await cliJson(tools, ['review', ...inputs, '--output', reportPath], 180000);
    const report = JSON.parse(await readFile(reportPath, 'utf8'));
    const verification = await cliJson(tools,
      ['review', 'verify', reportPath, ...inputs], 180000);
    return { report, verification };
  } finally {
    await rm(scratch, { recursive: true, force: true });
  }
}

function resource(server, name, uri, title, producer) {
  server.registerResource(name, uri, { title, mimeType: 'text/plain' }, async (requested) => ({
    contents: [{ uri: requested.href, text: encoded(await producer()) }],
  }));
}

function buildServer(runtime, program, allowWrite, workspace, review, tools, core) {
  const server = new McpServer({ name: 'sley-tools', version: VERSION }, {
    instructions: 'Inspect the pinned program before editing. Propose, validate, and commit a candidate in order. Run the program and tests after commit. Signed 64 bit values use exact decimal strings.',
  });
  const inputs = z.object(inputShape(program)).strict();
  tool(server, 'sley_inspect_program', {
    title: 'Inspect Sley program',
    description: 'Return the selected program manifest, accepted root, native identities, and supported graph edits.',
    inputSchema: z.object({}).strict(),
    annotations: { readOnlyHint: true, openWorldHint: false },
  }, () => program.inspect());
  tool(server, 'sley_run_program', {
    title: 'Run Sley program',
    description: 'Execute evaluate in the native Sley VM at the selected accepted root. Execution may persist a local report.',
    inputSchema: z.object({ inputs, deadline_ms: z.number().int().min(1).max(60000).optional() }).strict(),
    annotations: { readOnlyHint: false, openWorldHint: false },
  }, ({ inputs: values, deadline_ms }) => program.graphProfile
    ? program.callGraph('evaluate', { inputs: hostInputs(program, values), deadlineMs: deadline_ms })
    : program.call('evaluate', { inputs: hostInputs(program, values), deadlineMs: deadline_ms }));
  tool(server, 'sley_run_tests', {
    title: 'Run Sley project cases',
    description: 'Run declared external assertions against native execution; native core test admission is separately reported and currently pending.',
    inputSchema: z.object({}).strict(),
    annotations: { readOnlyHint: false, openWorldHint: false },
  }, () => program.test());
  if (review) {
    tool(server, 'sley_review_change', {
      title: 'Review native Sley change',
      description: 'Compare and verify the two manifest-bound native packages and case file explicitly configured for this connection. Creates a temporary report outside the selected project.',
      inputSchema: z.object({}).strict(),
      annotations: { readOnlyHint: false, openWorldHint: false },
    }, () => reviewChange(tools, core, review));
  }

  if (allowWrite) {
    if (workspace) {
      tool(server, 'sley_create_program', {
        title: 'Create native Sley program',
        description: 'Create a new native template program as a direct child of the configured workspace. The current MCP connection remains on its selected program.',
        inputSchema: z.object({ name: z.string().regex(/^[a-z][a-z0-9-]{0,63}$/),
          template: z.enum(['release-readiness', 'resource-admission']) }).strict(),
        annotations: { readOnlyHint: false, openWorldHint: false },
      }, ({ name, template }) => initTemplate(tools, core, workspace, name, template));
      tool(server, 'sley_create_graph', {
        title: 'Create native Sley graph',
        description: 'Create a bounded native Sley graph under the configured workspace. Read sley://builder-operations for a complete recipe and host-valued case example. Signed 64 bit values use exact decimal strings. The current connection remains on its selected program.',
        inputSchema: z.object({ name: z.string().regex(GRAPH_NAME).max(64),
          recipe: GRAPH_RECIPE, cases: GRAPH_CASES }).strict(),
        annotations: { readOnlyHint: false, openWorldHint: false },
      }, ({ name, recipe, cases }) => initGraph(tools, core, workspace, name, recipe, cases));
    }
    if (program.graphProfile && program.graphConstants.length) {
      const cases = z.array(z.object({ id: z.string().min(1).max(64), inputs,
        expected: resultSchema(program) }).strict()).min(1).max(256);
      tool(server, 'sley_propose_edit', {
        title: 'Propose Sley graph constant edit',
        description: 'Propose one named graph constant replacement against the accepted root with expected postchange cases. Does not commit.',
        inputSchema: z.object({ constant: z.enum(program.graphConstants.map((item) => item.name)),
          value: z.union([z.boolean(), U32, I64]), cases }).strict(),
        annotations: { readOnlyHint: false, openWorldHint: false },
      }, ({ constant, value, cases: rows }) => {
        const selected = program.graphConstants.find((item) => item.name === constant);
        const typed = hostValue(value, selected.value_type);
        const hostCases = rows.map((row) => ({ id: row.id, inputs: hostInputs(program, row.inputs),
          expected: hostExpected(program, row.expected) }));
        return program.proposeGraphConstant(constant, typed, hostCases);
      });
    } else if (!program.graphProfile) {
      const change = program.name === 'release-readiness'
        ? z.object({ max_blocking_findings: U32 }).strict()
        : z.object({ require_memory_spare: z.boolean() }).strict();
      tool(server, 'sley_propose_edit', {
        title: 'Propose Sley program edit',
        description: 'Propose one typed behavior change against the accepted root. Does not commit.',
        inputSchema: z.object({ change }).strict(),
        annotations: { readOnlyHint: false, openWorldHint: false },
      }, ({ change: selected }) => program.propose(selected));
    }
    if (program.graphProfile && program.graphOperations.length) {
      const cases = z.array(z.object({ id: z.string().min(1).max(64), inputs,
        expected: resultSchema(program) }).strict()).min(1).max(256);
      const operations = program.graphOperations.map(({ block, name }) => `${block}.${name}`);
      tool(server, 'sley_propose_opcode_edit', {
        title: 'Propose Sley graph opcode edit',
        description: 'Replace one selected native graph operation opcode with a type-compatible opcode and expected postchange cases. Does not commit.',
        inputSchema: z.object({ operation: z.enum(operations), opcode: z.string().min(1).max(64),
          cases }).strict(),
        annotations: { readOnlyHint: false, openWorldHint: false },
      }, ({ operation, opcode, cases: rows }) => {
        const selected = program.graphOperations.find(({ block, name }) => `${block}.${name}` === operation);
        const hostCases = rows.map((row) => ({ id: row.id, inputs: hostInputs(program, row.inputs),
          expected: hostExpected(program, row.expected) }));
        return program.proposeGraphOpcode(selected.block, selected.name, opcode, hostCases);
      });
      tool(server, 'sley_propose_operand_edit', {
        title: 'Propose Sley graph operand edit',
        description: 'Replace one operand reference in an accepted native graph operation, with expected postchange cases. Does not commit.',
        inputSchema: z.object({ operation: z.enum(operations), slot: z.enum(['left', 'right', 'operand']),
          reference: z.string().regex(/^(param|op)\.[A-Za-z][A-Za-z0-9_]{0,63}$/), cases }).strict(),
        annotations: { readOnlyHint: false, openWorldHint: false },
      }, ({ operation, slot, reference, cases: rows }) => {
        const selected = program.graphOperations.find(({ block, name }) => `${block}.${name}` === operation);
        const hostCases = rows.map((row) => ({ id: row.id, inputs: hostInputs(program, row.inputs),
          expected: hostExpected(program, row.expected) }));
        return program.proposeGraphOperand(selected.block, selected.name, slot, reference, hostCases);
      });
    }
    if (program.graphProfile && program.graphBranches.length) {
      const cases = z.array(z.object({ id: z.string().min(1).max(64), inputs,
        expected: resultSchema(program) }).strict()).min(1).max(256);
      const label = z.string().regex(/^[A-Za-z][A-Za-z0-9_]{0,63}$/);
      tool(server, 'sley_propose_branch_edit', {
        title: 'Propose Sley graph branch edit',
        description: 'Replace the target labels of one native graph branch with expected postchange cases. Does not commit.',
        inputSchema: z.object({ block: z.enum(program.graphBranches.map((item) => item.block)),
          targets: z.union([z.object({ target: label }).strict(),
            z.object({ if_true: label, if_false: label }).strict()]), cases }).strict(),
        annotations: { readOnlyHint: false, openWorldHint: false },
      }, ({ block, targets, cases: rows }) => {
        const hostCases = rows.map((row) => ({ id: row.id, inputs: hostInputs(program, row.inputs),
          expected: hostExpected(program, row.expected) }));
        return program.proposeGraphBranch(block, targets, hostCases);
      });
    }
    const candidate = z.object({ candidate_id: z.string().regex(HEX_ID) }).strict();
    tool(server, 'sley_validate_candidate', {
      title: 'Validate Sley candidate',
      description: 'Ask the selected core to validate the proposed candidate without changing accepted state.',
      inputSchema: candidate,
      annotations: { readOnlyHint: false, openWorldHint: false },
    }, ({ candidate_id }) => program.validate(candidate_id));
    tool(server, 'sley_commit_candidate', {
      title: 'Commit Sley candidate',
      description: 'Revalidate and commit the exact candidate if its base is current. The SDK refreshes the accepted root; external assertions are reported separately.',
      inputSchema: candidate,
      annotations: { readOnlyHint: false, openWorldHint: false },
    }, ({ candidate_id }) => program.commit(candidate_id));
  }

  resource(server, 'mental-model', 'sley://mental-model', 'Sley mental model', () => ({
    tools_version: VERSION, program: program.name,
    model: 'The accepted Sley graph is the program. The Rust toolkit constructs candidates through public core APIs. This adapter translates MCP calls to one pinned local project.',
    authoring: 'Propose, validate, commit, then verify at the new root. Expected cases are external assertions, not native core test admission.',
  }));
  resource(server, 'builder-operations', 'sley://builder-operations', 'Supported graph operations', () => ({
    graph_contract: 'sley-tools.graph-authoring.v0',
    input_types: ['Bool', 'UInt32', 'SInt64'],
    result_types: ['Bool', 'UInt32', 'SInt64', 'CheckedSInt64'],
    operations: ['constant_ref', 'bool_not', 'bool_and', 'bool_or', 'equal', 'not_equal',
      'less_than', 'less_equal', 'greater_than', 'greater_equal',
      'int_add_checked', 'int_sub_checked', 'int_mul_checked', 'int_neg_checked',
      'result_ok', 'record_new'],
    terminators: ['return', 'branch', 'cond_branch'],
    creation: { name: 'fixed-limit',
      recipe: { contract: 'sley-tools.graph-authoring.v0', program_id: 'fixed-limit',
        title: 'Fixed Limit', purpose: 'Decide whether a count is within a configured limit',
        parameters: [{ name: 'count', value_type: 'UInt32' }],
        constants: [{ name: 'limit', value_type: 'UInt32', value: '10' }],
        result: { kind: 'primitive', value_type: 'Bool' }, entry_block: 'entry',
        blocks: [{ name: 'entry', operations: [
          { op: 'constant_ref', name: 'accepted_limit', constant: 'limit' },
          { op: 'less_equal', name: 'within', left: 'param.count', right: 'op.accepted_limit' },
        ], terminator: { kind: 'return', value: 'op.within' } }] },
      cases: [{ id: 'at-limit', inputs: { count: 10 }, expected: true },
        { id: 'above-limit', inputs: { count: 11 }, expected: false }] },
  }));
  resource(server, 'program-manifest', 'sley://program-manifest', 'Selected program manifest', async () =>
    (await program.inspect()).program);
  resource(server, 'capabilities', 'sley://capabilities', 'Selected Sley capabilities', () => ({
    rpc: runtime.hello, mcp_sdk: '@modelcontextprotocol/server@2.1.0',
    configured_project: program.name, program_edits_enabled: allowWrite,
    workspace_creation_enabled: Boolean(allowWrite && workspace),
    graph_creation_enabled: Boolean(allowWrite && workspace),
    review_enabled: Boolean(review),
    execution_may_persist_reports: true,
  }));
  resource(server, 'error-guide', 'sley://error-guide', 'Sley tools error guide', () => ({
    ST_INPUT_INVALID: 'Correct typed fields or values.',
    ST_STALE_BASE: 'Reopen and inspect the accepted root, then propose again.',
    ST_VALIDATION_FAILED: 'Read core diagnostics and change the proposal.',
    ST_COMMIT_OUTCOME_UNKNOWN: 'Inspect accepted state before retrying any mutation.',
    ST_EXECUTION_FAILED: 'Native execution or declared external assertions failed.',
  }));
  server.server.onclose = () => { void runtime.close(); };
  return server;
}

try {
  const selected = options(process.argv.slice(2));
  const [project, tools, core, workspace, reviewBefore, reviewAfter, reviewCases] = await Promise.all([
    realpath(selected['--project']), realpath(selected['--tools']), realpath(selected['--core']),
    selected['--workspace'] ? realpath(selected['--workspace']) : Promise.resolve(null),
    selected['--review-before'] ? realpath(selected['--review-before']) : Promise.resolve(null),
    selected['--review-after'] ? realpath(selected['--review-after']) : Promise.resolve(null),
    selected['--review-cases'] ? realpath(selected['--review-cases']) : Promise.resolve(null),
  ]);
  if (workspace && dirname(project) !== workspace) {
    throw new Error('selected project must be a direct child of the configured workspace');
  }
  const runtime = openRuntime({ tools, core });
  const program = await runtime.openProject({ project });
  const review = reviewBefore ? { before: reviewBefore, after: reviewAfter, cases: reviewCases } : null;
  const handle = serveStdio(() => buildServer(runtime, program,
    selected.allowWrite === true, workspace, review, tools, core));
  process.on('SIGINT', () => { void handle.close(); });
  process.on('SIGTERM', () => { void handle.close(); });
} catch (error) {
  console.error(encoded({ code: error instanceof SleyToolsError ? error.code : 'ST_INPUT_INVALID',
    message: String(error).slice(0, 500) }));
  process.exitCode = 1;
}
