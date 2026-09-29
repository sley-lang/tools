/** Thin async client for the local Sley tools RPC process. */

import { spawn, execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { promisify } from 'node:util';

const runFile = promisify(execFile);
const LIMITS = { u32: [0n, 4294967295n], i64: [-(2n ** 63n), 2n ** 63n - 1n] };

export class SleyToolsError extends Error {
  constructor(code, category, message, { coreSymbol = null, coreNumeric = null } = {}) {
    super(message);
    this.name = 'SleyToolsError';
    this.code = code;
    this.category = category;
    this.coreSymbol = coreSymbol;
    this.coreNumeric = coreNumeric;
  }
}

function inputError(message) {
  return new SleyToolsError('ST_INPUT_INVALID', 'input_shape', message);
}

export function encodeInteger(value, type) {
  if (!(type in LIMITS)) throw inputError(`unsupported integer type alias: ${type}`);
  let exact;
  if (typeof value === 'bigint') exact = value;
  else if (typeof value === 'number' && Number.isSafeInteger(value)) exact = BigInt(value);
  else throw inputError('integer input must be bigint or a safe integral number');
  const [minimum, maximum] = LIMITS[type];
  if (exact < minimum || exact > maximum) throw inputError(`${type} input is outside its declared domain`);
  return { kind: 'integer', type, value: exact.toString() };
}

function taggedInput(inputs) {
  const expected = ['test_run_completed', 'tests_failed', 'artifact_verified', 'blocking_findings'];
  if (inputs === null || typeof inputs !== 'object' || Array.isArray(inputs)
    || Object.keys(inputs).length !== expected.length || expected.some((name) => !Object.hasOwn(inputs, name))) {
    throw inputError('Release Readiness requires exactly four named inputs');
  }
  if (typeof inputs.test_run_completed !== 'boolean' || typeof inputs.artifact_verified !== 'boolean') {
    throw inputError('test_run_completed and artifact_verified must be Boolean');
  }
  return {
    kind: 'record',
    fields: {
      test_run_completed: { kind: 'boolean', value: inputs.test_run_completed },
      tests_failed: encodeInteger(inputs.tests_failed, 'u32'),
      artifact_verified: { kind: 'boolean', value: inputs.artifact_verified },
      blocking_findings: encodeInteger(inputs.blocking_findings, 'u32'),
    },
  };
}

function taggedResource(inputs) {
  const names = ['requested_workers', 'available_workers', 'requested_memory_mib', 'available_memory_mib'];
  if (inputs === null || typeof inputs !== 'object' || Array.isArray(inputs)
    || Object.keys(inputs).length !== names.length || names.some((name) => !Object.hasOwn(inputs, name))) {
    throw inputError('Resource Admission requires exactly four named inputs');
  }
  const fields = {};
  for (const name of names) {
    const value = encodeInteger(inputs[name], 'i64');
    if (BigInt(value.value) < 0n) throw inputError(`${name} is outside the declared nonnegative domain`);
    fields[name] = value;
  }
  return { kind: 'record', fields };
}

function taggedGraph(inputs, parameters) {
  const names = parameters.map((parameter) => parameter.name);
  if (inputs === null || typeof inputs !== 'object' || Array.isArray(inputs)
    || Object.keys(inputs).length !== names.length || names.some((name) => !Object.hasOwn(inputs, name))) {
    throw inputError('graph inputs must match the named parameters exactly');
  }
  const values = parameters.map(({ name, value_type: kind }) => {
    const value = inputs[name];
    if (kind === 'Bool') {
      if (typeof value !== 'boolean') throw inputError(`graph parameter ${name} must be Boolean`);
      return { kind: 'boolean', value };
    }
    if (kind === 'UInt32' || kind === 'SInt64') return encodeInteger(value, kind === 'UInt32' ? 'u32' : 'i64');
    throw inputError(`unsupported graph parameter type: ${kind}`);
  });
  return { kind: 'arguments', values };
}

function graphRaw(value, kind) {
  if (kind === 'CheckedSInt64') {
    if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).length !== 1) throw inputError('checked graph value needs one arm');
    if (Object.hasOwn(value, 'ok')) return { ok: graphRaw(value.ok, 'SInt64') };
    if (value.error && typeof value.error === 'object' && !Array.isArray(value.error)
      && Object.keys(value.error).length === 1 && value.error.code === 1) {
      return { error: { code: 1 } };
    }
    throw inputError('checked graph value has an unavailable arithmetic failure');
  }
  if (kind === 'Bool') {
    if (typeof value !== 'boolean') throw inputError('graph Boolean value must be true or false');
    return value;
  }
  if (kind === 'UInt32' || kind === 'SInt64') return encodeInteger(value, kind === 'UInt32' ? 'u32' : 'i64').value;
  throw inputError(`unsupported graph value type: ${kind}`);
}

function graphCases(cases, parameters, result) {
  if (!Array.isArray(cases) || cases.length < 1 || cases.length > 256) {
    throw inputError('graph proposal needs 1 through 256 cases');
  }
  if (!Array.isArray(parameters) || parameters.some((parameter) => !parameter
    || typeof parameter.name !== 'string' || typeof parameter.value_type !== 'string')) {
    throw inputError('graph parameters need name and value_type');
  }
  if (!result || typeof result !== 'object' || Array.isArray(result)) {
    throw inputError('graph result shape is missing');
  }
  if (result.kind === 'primitive' && typeof result.value_type !== 'string') {
    throw inputError('graph primitive result needs value_type');
  }
  if (result.kind === 'record' && (!Array.isArray(result.fields)
    || result.fields.some((field) => !field || typeof field.name !== 'string'
      || typeof field.value_type !== 'string'))) {
    throw inputError('graph record result needs typed fields');
  }
  const names = parameters.map((parameter) => parameter.name);
  const rows = cases.map((testCase) => {
    if (!testCase || typeof testCase !== 'object' || Array.isArray(testCase)
      || Object.keys(testCase).length !== 3 || !['id', 'inputs', 'expected'].every((name) => Object.hasOwn(testCase, name))) {
      throw inputError('graph case needs id, inputs, and expected');
    }
    if (typeof testCase.id !== 'string' || testCase.id.length < 1 || testCase.id.length > 64) {
      throw inputError('graph case id must be 1 through 64 characters');
    }
    const inputs = testCase.inputs;
    if (!inputs || typeof inputs !== 'object' || Array.isArray(inputs)
      || Object.keys(inputs).length !== names.length || names.some((name) => !Object.hasOwn(inputs, name))) {
      throw inputError('graph case inputs must match named parameters');
    }
    const encodedInput = parameters.map((parameter) => graphRaw(inputs[parameter.name], parameter.value_type));
    let expected;
    if (result.kind === 'primitive') expected = graphRaw(testCase.expected, result.value_type);
    else if (result.kind === 'record') {
      const values = testCase.expected;
      if (!values || typeof values !== 'object' || Array.isArray(values)
        || Object.keys(values).length !== result.fields.length
        || result.fields.some((field) => !Object.hasOwn(values, field.name))) {
        throw inputError('graph case expected record has the wrong fields');
      }
      expected = Object.fromEntries(result.fields.map((field) => [field.name, graphRaw(values[field.name], field.value_type)]));
    } else throw inputError('unsupported graph result shape');
    return { id: testCase.id, input: encodedInput, expected };
  });
  return { contract: 'sley-tools.graph-cases.v0', cases: rows };
}

const GRAPH_LABEL = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;
const GRAPH_PROGRAM_ID = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;
const GRAPH_INPUT_TYPES = new Set(['Bool', 'UInt32', 'SInt64']);
const GRAPH_RESULT_TYPES = new Set([...GRAPH_INPUT_TYPES, 'CheckedSInt64']);
const GRAPH_UNARY = new Set(['bool_not', 'int_neg_checked', 'result_ok']);
const GRAPH_BINARY = new Set(['bool_and', 'bool_or', 'equal', 'not_equal', 'less_than', 'less_equal',
  'greater_than', 'greater_equal', 'int_add_checked', 'int_sub_checked', 'int_mul_checked']);
const GRAPH_EDITABLE_OPCODES = new Set(['bool_and', 'bool_or', 'equal', 'not_equal', 'less_than',
  'less_equal', 'greater_than', 'greater_equal', 'int_add_checked', 'int_sub_checked',
  'int_mul_checked', 'int_neg_checked', 'result_ok']);

function graphLabel(name) {
  if (typeof name !== 'string' || !GRAPH_LABEL.test(name)) {
    throw inputError('graph label must start with a letter and contain at most 64 ASCII letters, digits, or underscores');
  }
  return name;
}

function graphReference(value) {
  if (typeof value !== 'string') throw inputError('graph value reference must be a string');
  const parts = value.split('.');
  if (parts.length !== 2 || !['param', 'op'].includes(parts[0])) {
    throw inputError('graph value reference must be param.NAME or op.NAME');
  }
  graphLabel(parts[1]);
  return value;
}

/** Assemble a host recipe; the Rust builder and Sley core own native semantics. */
export class GraphBuilder {
  constructor({ programId, title, purpose }) {
    if (typeof programId !== 'string' || programId.length > 64 || !GRAPH_PROGRAM_ID.test(programId)) {
      throw inputError('graph program ID must be lowercase words separated by single hyphens');
    }
    if (typeof title !== 'string' || !title.trim() || title.length > 120) {
      throw inputError('graph title must be 1 through 120 characters');
    }
    if (typeof purpose !== 'string' || !purpose.trim() || purpose.length > 500) {
      throw inputError('graph purpose must be 1 through 500 characters');
    }
    this.programId = programId;
    this.title = title;
    this.purpose = purpose;
    this.parameters = [];
    this.constants = [];
    this.blocks = [];
    this.names = new Set();
    this.operations = new Set();
    this.result = null;
    this.entryBlock = null;
  }

  static param(name) { return `param.${graphLabel(name)}`; }
  static op(name) { return `op.${graphLabel(name)}`; }

  named(name) {
    graphLabel(name);
    if (this.names.has(name)) throw inputError(`duplicate graph label: ${name}`);
    this.names.add(name);
  }

  parameter(name, valueType) {
    if (!GRAPH_INPUT_TYPES.has(valueType)) throw inputError('unsupported graph parameter type');
    this.named(name);
    this.parameters.push({ name, value_type: valueType });
    return this;
  }

  constant(name, valueType, value) {
    if (!GRAPH_INPUT_TYPES.has(valueType)) throw inputError('unsupported graph constant type');
    graphRaw(value, valueType);
    this.named(name);
    this.constants.push({ name, value_type: valueType, value });
    return this;
  }

  primitiveResult(valueType) {
    if (!GRAPH_RESULT_TYPES.has(valueType)) throw inputError('unsupported graph primitive result type');
    this.result = { kind: 'primitive', value_type: valueType };
    return this;
  }

  recordResult(fields) {
    if (!fields || typeof fields !== 'object' || Array.isArray(fields)
      || Object.getPrototypeOf(fields) !== Object.prototype
      || Object.keys(fields).length < 1 || Object.keys(fields).length > 16) {
      throw inputError('graph record result needs 1 through 16 named fields');
    }
    const entries = Object.entries(fields).map(([name, valueType]) => {
      graphLabel(name);
      if (!GRAPH_RESULT_TYPES.has(valueType)) throw inputError('unsupported graph result field type');
      return { name, value_type: valueType };
    });
    this.result = { kind: 'record', fields: entries };
    return this;
  }

  block(name) {
    this.named(name);
    const block = { name, operations: [] };
    this.blocks.push(block);
    if (this.entryBlock === null) this.entryBlock = name;
    return new GraphBlock(this, block);
  }

  entry(name) {
    this.entryBlock = graphLabel(name);
    return this;
  }

  build() {
    if (this.result === null || this.blocks.length === 0
      || !this.blocks.some((block) => block.name === this.entryBlock)) {
      throw inputError('graph needs a result and an existing entry block');
    }
    if (this.parameters.length > 16 || this.constants.length > 64 || this.blocks.length > 16
      || this.blocks.reduce((count, block) => count + block.operations.length, 0) > 128) {
      throw inputError('graph exceeds bounded authoring limits');
    }
    if (this.blocks.some((block) => !block.terminator)) throw inputError('every graph block needs a terminator');
    return structuredClone({ contract: 'sley-tools.graph-authoring.v0', program_id: this.programId,
      title: this.title, purpose: this.purpose, parameters: this.parameters,
      constants: this.constants, result: this.result, entry_block: this.entryBlock, blocks: this.blocks });
  }
}

export class GraphBlock {
  constructor(builder, block) {
    this.builder = builder;
    this.block = block;
  }

  operation(item) {
    if (this.block.terminator) throw inputError('cannot add an operation after a block terminator');
    graphLabel(item.name);
    if (this.builder.operations.has(item.name)) throw inputError(`duplicate graph operation label: ${item.name}`);
    this.builder.operations.add(item.name);
    this.block.operations.push(item);
    return this;
  }

  constantRef(name, constant) {
    if (!this.builder.constants.some((item) => item.name === constant)) {
      throw inputError(`unknown graph constant: ${constant}`);
    }
    return this.operation({ op: 'constant_ref', name, constant });
  }

  unary(opcode, name, operand) {
    if (!GRAPH_UNARY.has(opcode)) throw inputError('unsupported unary graph operation');
    return this.operation({ op: opcode, name, operand: graphReference(operand) });
  }

  binary(opcode, name, left, right) {
    if (!GRAPH_BINARY.has(opcode)) throw inputError('unsupported binary graph operation');
    return this.operation({ op: opcode, name, left: graphReference(left), right: graphReference(right) });
  }

  record(name, fields) {
    if (!Array.isArray(fields) || fields.length < 1 || fields.length > 16) {
      throw inputError('graph record operation needs 1 through 16 fields');
    }
    return this.operation({ op: 'record_new', name, fields: fields.map(graphReference) });
  }

  terminate(value) {
    if (this.block.terminator) throw inputError('graph block already has a terminator');
    this.block.terminator = value;
    return this;
  }

  returnValue(value) { return this.terminate({ kind: 'return', value: graphReference(value) }); }
  jump(target) { return this.terminate({ kind: 'branch', target: graphLabel(target) }); }
  conditional(condition, ifTrue, ifFalse) {
    return this.terminate({ kind: 'cond_branch', condition: graphReference(condition),
      if_true: graphLabel(ifTrue), if_false: graphLabel(ifFalse) });
  }
  end() { return this.builder; }
}

function decodeGraphValue(value, kind) {
  if (kind === 'CheckedSInt64') {
    if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).length !== 3 || value.kind !== 'result') {
      throw new SleyToolsError('ST_EXECUTION_FAILED', 'execution', 'native checked result has the wrong tag');
    }
    if (value.arm === 'ok') return { ok: decodeGraphValue(value.value, 'SInt64') };
    if (value.arm === 'error' && value.value?.kind === 'builtin_failure'
      && value.value.failure === 'arithmetic' && value.value.code === 1
      && Object.keys(value.value).length === 3) return { error: { code: 1 } };
    throw new SleyToolsError('ST_EXECUTION_FAILED', 'execution', 'native checked result has an unavailable arithmetic failure');
  }
  if (kind === 'Bool') {
    if (!value || Object.keys(value).length !== 2 || value.kind !== 'boolean' || typeof value.value !== 'boolean') {
      throw new SleyToolsError('ST_EXECUTION_FAILED', 'execution', 'native graph result is not tagged Boolean');
    }
    return value.value;
  }
  const alias = { UInt32: 'u32', SInt64: 'i64' }[kind];
  if (!alias || !value || Object.keys(value).length !== 3 || value.kind !== 'integer'
    || value.type !== alias || typeof value.value !== 'string') {
    throw new SleyToolsError('ST_EXECUTION_FAILED', 'execution', 'native graph result has the wrong integer tag');
  }
  let number;
  try { number = BigInt(value.value); }
  catch { throw new SleyToolsError('ST_EXECUTION_FAILED', 'execution', 'native graph integer is not decimal'); }
  let canonical;
  try { canonical = encodeInteger(number, alias).value === value.value; }
  catch { canonical = false; }
  if (!canonical) throw new SleyToolsError('ST_EXECUTION_FAILED', 'execution', 'native graph integer is not canonical');
  return alias === 'u32' ? Number(number) : number;
}

function taggedForProgram(name, inputs) {
  if (name === 'release-readiness') return taggedInput(inputs);
  if (name === 'resource-admission') return taggedResource(inputs);
  throw inputError(`unsupported program profile: ${name}`);
}

function taggedChange(name, change) {
  if (change === null || typeof change !== 'object' || Array.isArray(change) || Object.keys(change).length !== 1) {
    throw inputError('authoring change must contain exactly one supported field');
  }
  if (name === 'release-readiness' && Object.hasOwn(change, 'max_blocking_findings')) {
    return { field: 'max_blocking_findings', value: encodeInteger(change.max_blocking_findings, 'u32') };
  }
  if (name === 'resource-admission' && Object.hasOwn(change, 'require_memory_spare')) {
    if (typeof change.require_memory_spare !== 'boolean') throw inputError('require_memory_spare must be Boolean');
    return { field: 'require_memory_spare', value: { kind: 'boolean', value: change.require_memory_spare } };
  }
  throw inputError('authoring field is unavailable for this program profile');
}

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

function cliError(error, action) {
  if (error instanceof SleyToolsError) return error;
  const details = typeof error?.stderr === 'string' ? error.stderr : '';
  try {
    const parsed = JSON.parse(details);
    if (!parsed || typeof parsed !== 'object' || typeof parsed.code !== 'string'
      || typeof parsed.message !== 'string') throw new Error('invalid CLI error');
    return new SleyToolsError(parsed.code, parsed.category ?? 'transport', parsed.message,
      { coreSymbol: parsed.core_symbol, coreNumeric: parsed.core_numeric });
  } catch {
    return new SleyToolsError('ST_TRANSPORT_FAILED', 'transport', `${action} failed: ${String(error).slice(0, 300)}`);
  }
}

export class Runtime {
  constructor({ tools, core }) {
    this.tools = resolve(tools);
    this.core = resolve(core);
    this.process = null;
    this.temporary = null;
    this.projectPath = null;
    this.preserveTemporary = false;
    this.pending = new Map();
    this.nextId = 1;
    this.stdoutBuffer = '';
    this.stderrExcerpt = '';
    this.handle = null;
    this.hello = null;
    this.program = null;
    this.exited = false;
    this.queue = Promise.resolve();
  }

  async load({ artifact }) {
    if (this.process !== null) throw inputError('runtime already has a loaded program');
    this.temporary = await mkdtemp(join(tmpdir(), 'sley-typescript-sdk-'));
    const project = join(this.temporary, 'project');
    try {
      await runFile(this.tools, ['artifact', 'import', resolve(artifact), project, '--core', this.core], {
        timeout: 30000, maxBuffer: 1024 * 1024,
      });
      return await this.openProject({ project });
    } catch (error) {
      await this.close();
      throw cliError(error, 'artifact import');
    }
  }

  async createGraph({ project, recipe, cases }) {
    if (this.process !== null) throw inputError('runtime already has a loaded program');
    if (typeof project !== 'string' || project.length === 0) throw inputError('graph project path is required');
    if (!recipe || typeof recipe !== 'object' || Array.isArray(recipe)
      || recipe.contract !== 'sley-tools.graph-authoring.v0') {
      throw inputError('graph recipe has the wrong contract');
    }
    if (!Array.isArray(recipe.parameters) || !Array.isArray(recipe.constants)) {
      throw inputError('graph recipe needs parameters and constants');
    }
    const constants = recipe.constants.map((constant) => {
      if (!constant || typeof constant !== 'object' || Array.isArray(constant)
        || Object.keys(constant).length !== 3
        || !['name', 'value_type', 'value'].every((name) => Object.hasOwn(constant, name))) {
        throw inputError('graph constant needs name, value_type, and value');
      }
      return { ...constant, value: graphRaw(constant.value, constant.value_type) };
    });
    const normalized = { ...recipe, constants };
    const fixture = graphCases(cases, recipe.parameters, recipe.result);
    let specText, casesText;
    try {
      specText = JSON.stringify(normalized) + '\n';
      casesText = JSON.stringify(fixture) + '\n';
    } catch (error) {
      throw inputError(`graph authoring data is not JSON: ${error}`);
    }
    if (Buffer.byteLength(specText) > 65536 || Buffer.byteLength(casesText) > 1048576) {
      throw new SleyToolsError('ST_RESOURCE_LIMIT', 'resource_limit', 'graph recipe or cases exceed the profile limit');
    }
    const destination = resolve(project);
    const temporary = await mkdtemp(join(tmpdir(), 'sley-typescript-graph-'));
    try {
      const specPath = join(temporary, 'graph.json');
      const casesPath = join(temporary, 'cases.json');
      await writeFile(specPath, specText);
      await writeFile(casesPath, casesText);
      try {
        await runFile(this.tools, ['init', destination, '--graph', specPath, '--tests', casesPath, '--core', this.core], {
          timeout: 120000, maxBuffer: 1024 * 1024,
        });
      } catch (error) {
        if (error?.killed) {
          throw new SleyToolsError('ST_TRANSPORT_FAILED', 'transport',
            `graph initialization timed out at ${destination}; inspect its status before retrying`);
        }
        throw cliError(error, 'graph initialization');
      }
    } finally {
      await rm(temporary, { recursive: true, force: true });
    }
    return this.openProject({ project: destination });
  }

  async openProject({ project }) {
    if (this.process !== null) throw inputError('runtime already has a loaded program');
    this.projectPath = resolve(project);
    const child = spawn(this.tools, ['rpc', '--project', this.projectPath], { stdio: ['pipe', 'pipe', 'pipe'], detached: true });
    this.process = child;
    this.exited = false;
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk) => this.#onStdout(chunk));
    child.stderr.on('data', (chunk) => {
      if (this.stderrExcerpt.length < 8192) this.stderrExcerpt += chunk.slice(0, 8192 - this.stderrExcerpt.length);
    });
    child.on('error', (error) => this.#failAll(new SleyToolsError('ST_TRANSPORT_FAILED', 'transport', `RPC process failed: ${error}`)));
    child.on('exit', () => {
      if (this.process === child && !this.exited) this.#stopProcessGroup();
      this.exited = true;
      this.#failAll(new SleyToolsError('ST_TRANSPORT_FAILED', 'transport', `RPC process closed: ${this.stderrExcerpt.slice(0, 300)}`));
    });
    try {
      this.hello = await this.#request('sleytools.hello', {}, 5000);
      if (this.hello.contract !== 'sley-tools.rpc.v0') {
        throw new SleyToolsError('ST_CORE_INCOMPATIBLE', 'compatibility', 'RPC contract differs from TypeScript SDK');
      }
      if (this.hello.core?.binary_sha256 !== sha256(await readFile(this.core))) {
        throw new SleyToolsError('ST_CORE_INCOMPATIBLE', 'compatibility', 'RPC project uses a different selected core binary');
      }
      const opened = await this.#request('sleytools.workspace.open', {}, 5000);
      this.handle = opened.handle;
      const inspected = await this.#request('sleytools.program.inspect', { handle: this.handle }, 5000);
      this.program = new Program(this, inspected);
      return this.program;
    } catch (error) {
      await this.close();
      throw error;
    }
  }

  #failAll(error) {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.signal?.removeEventListener('abort', pending.onAbort);
      pending.reject(error);
    }
    this.pending.clear();
  }

  #onStdout(chunk) {
    this.stdoutBuffer += chunk;
    if (this.stdoutBuffer.length > 262145 && !this.stdoutBuffer.includes('\n')) {
      this.#failAll(new SleyToolsError('ST_RESOURCE_LIMIT', 'resource_limit', 'RPC response exceeded 256 KiB'));
      this.#stopProcessGroup();
      return;
    }
    while (this.stdoutBuffer.includes('\n')) {
      const index = this.stdoutBuffer.indexOf('\n');
      const line = this.stdoutBuffer.slice(0, index);
      this.stdoutBuffer = this.stdoutBuffer.slice(index + 1);
      if (line.length > 262144) {
        this.#failAll(new SleyToolsError('ST_RESOURCE_LIMIT', 'resource_limit', 'RPC response exceeded 256 KiB'));
        this.#stopProcessGroup();
        return;
      }
      let response;
      try { response = JSON.parse(line); }
      catch (error) {
        this.#failAll(new SleyToolsError('ST_TRANSPORT_FAILED', 'transport', `RPC response is invalid JSON: ${error}`));
        this.#stopProcessGroup();
        return;
      }
      const pending = this.pending.get(response.id);
      if (response.jsonrpc !== '2.0' || !pending) {
        this.#failAll(new SleyToolsError('ST_TRANSPORT_FAILED', 'transport', 'RPC response ID or version differs from request'));
        this.#stopProcessGroup();
        return;
      }
      this.pending.delete(response.id);
      clearTimeout(pending.timer);
      pending.signal?.removeEventListener('abort', pending.onAbort);
      if (response.error) {
        const data = response.error.data ?? {};
        pending.reject(new SleyToolsError(data.tools_code ?? 'ST_TRANSPORT_FAILED', data.category ?? 'transport',
          response.error.message ?? 'RPC operation failed', { coreSymbol: data.core_symbol, coreNumeric: data.core_numeric }));
      } else if (response.result && typeof response.result === 'object' && !Array.isArray(response.result)) {
        pending.resolve(response.result);
      } else pending.reject(new SleyToolsError('ST_TRANSPORT_FAILED', 'transport', 'RPC result is not an object'));
    }
  }

  #stopProcessGroup() {
    const child = this.process;
    if (child && !this.exited && child.pid) {
      try { process.kill(-child.pid, 'SIGKILL'); }
      catch (error) { if (error.code !== 'ESRCH') child.kill('SIGKILL'); }
    }
    this.exited = true;
  }

  #request(method, params, timeoutMs, signal = undefined) {
    if (!this.process || this.exited || !this.process.stdin.writable) {
      return Promise.reject(new SleyToolsError('ST_TRANSPORT_FAILED', 'transport', 'RPC process is closed'));
    }
    if (signal?.aborted) {
      return Promise.reject(new SleyToolsError('ST_CANCELLED', 'execution', 'read-only call cancelled before dispatch'));
    }
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        signal?.removeEventListener('abort', onAbort);
        this.#stopProcessGroup();
        reject(new SleyToolsError('ST_TRANSPORT_FAILED', 'transport', 'RPC response timed out; runtime stopped'));
      }, timeoutMs);
      const onAbort = () => {
        if (!this.pending.has(id)) return;
        this.pending.delete(id);
        clearTimeout(timer);
        signal.removeEventListener('abort', onAbort);
        this.#stopProcessGroup();
        reject(new SleyToolsError('ST_CANCELLED', 'execution', 'read-only call cancelled; runtime stopped'));
      };
      this.pending.set(id, { resolve, reject, timer, signal, onAbort });
      signal?.addEventListener('abort', onAbort, { once: true });
      if (signal?.aborted) {
        onAbort();
        return;
      }
      const frame = JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n';
      this.process.stdin.write(frame, (error) => {
        if (error && this.pending.has(id)) {
          const pending = this.pending.get(id);
          this.pending.delete(id);
          clearTimeout(pending.timer);
          signal?.removeEventListener('abort', onAbort);
          this.#stopProcessGroup();
          reject(new SleyToolsError('ST_TRANSPORT_FAILED', 'transport', `cannot send RPC request: ${error}`));
        }
      });
    });
  }

  request(method, params, timeoutMs, signal = undefined) {
    if (signal !== undefined && method !== 'sleytools.program.execute') {
      return Promise.reject(inputError('cancellation is available only for read-only program execution'));
    }
    const result = this.queue.then(() => this.#request(method, params, timeoutMs, signal));
    this.queue = result.catch(() => {});
    return result;
  }

  async #operationCommand(action, operationId) {
    if (!this.projectPath || typeof operationId !== 'string' || !/^[0-9a-f]{64}$/.test(operationId)) {
      throw inputError('operation command needs a loaded project and exact operation ID');
    }
    try {
      const { stdout } = await runFile(this.tools,
        ['operation', action, '--project', this.projectPath, '--id', operationId],
        { timeout: 30000, maxBuffer: 1024 * 1024 });
      const result = JSON.parse(stdout);
      if (['outcome_unknown', 'cancel_requested'].includes(result.state)) this.preserveTemporary = true;
      return result;
    } catch (error) {
      throw cliError(error, `operation ${action}`);
    }
  }

  operationStatus(operationId) { return this.#operationCommand('status', operationId); }
  cancelOperation(operationId) { return this.#operationCommand('cancel', operationId); }

  async close() {
    const child = this.process;
    this.process = null;
    if (child && child.exitCode === null && child.signalCode === null) {
      const closed = new Promise((resolveClosed) => child.once('exit', resolveClosed));
      if (child.stdin.writable) child.stdin.end();
      const timer = setTimeout(() => this.#stopProcessGroup(), 5000);
      await closed;
      clearTimeout(timer);
    }
    this.#failAll(new SleyToolsError('ST_TRANSPORT_FAILED', 'transport', 'runtime closed'));
    if (this.temporary && !this.preserveTemporary) {
      await rm(this.temporary, { recursive: true, force: true });
    }
    this.temporary = null;
  }
}

export class Program {
  constructor(runtime, inspection) {
    this.runtime = runtime;
    this.operations = new Map();
    this.name = inspection.program.program_id;
    this.entrypoint = inspection.program.entrypoint.name;
    this.functionId = inspection.program.entrypoint.function_id;
    this.stateRoot = inspection.accepted_state_root;
    this.resultTypeId = inspection.program.result_type_id;
    this.resultFields = new Map(inspection.program.result_fields.map((field) => [field.name, field.type ?? 'Bool']));
    this.graphProfile = inspection.program.authoring_profile === 'graph_v0';
    this.graphParameters = this.graphProfile ? inspection.authoring.parameters : [];
    this.graphConstants = this.graphProfile ? inspection.authoring.constants : [];
    this.graphOperations = this.graphProfile && inspection.authoring.supported_edits?.includes('operation_opcode')
      ? inspection.authoring.operations : [];
    this.graphBranches = this.graphProfile ? inspection.authoring.branches ?? [] : [];
    this.graphResult = this.graphProfile ? inspection.authoring.result : null;
  }

  async call(entrypoint, { inputs, deadlineMs = 10000, signal = undefined }) {
    if (entrypoint !== this.entrypoint) throw inputError(`unknown entrypoint: ${entrypoint}`);
    if (!Number.isSafeInteger(deadlineMs) || deadlineMs < 1 || deadlineMs > 60000) {
      throw inputError('deadlineMs must be an integer from 1 through 60000');
    }
    if (signal !== undefined && !(signal instanceof AbortSignal)) throw inputError('signal must be an AbortSignal');
    const input = this.graphProfile ? taggedGraph(inputs, this.graphParameters) : taggedForProgram(this.name, inputs);
    const response = await this.runtime.request('sleytools.program.execute',
      { handle: this.runtime.handle, entrypoint, input, deadline_ms: deadlineMs }, deadlineMs + 2000, signal);
    const { value, metadata } = response;
    if (!value || !metadata || metadata.state_root !== this.stateRoot || metadata.function_id !== this.functionId) {
      throw new SleyToolsError('ST_EXECUTION_FAILED', 'execution', 'native result differs from pinned entrypoint identity');
    }
    if (this.graphProfile) {
      if (this.graphResult.kind === 'primitive') {
        return { value: decodeGraphValue(value, this.graphResult.value_type), metadata };
      }
      if (this.graphResult.kind !== 'record' || value.kind !== 'record' || value.type_id !== this.resultTypeId) {
        throw new SleyToolsError('ST_EXECUTION_FAILED', 'execution', 'native graph record identity differs from entrypoint');
      }
      const fields = value.fields;
      const definitions = this.graphResult.fields;
      if (!fields || Object.keys(fields).length !== definitions.length
        || definitions.some((field) => !Object.hasOwn(fields, field.name))) {
        throw new SleyToolsError('ST_EXECUTION_FAILED', 'execution', 'native graph record has unexpected fields');
      }
      const decoded = {};
      for (const field of definitions) decoded[field.name] = decodeGraphValue(fields[field.name], field.value_type);
      return { value: decoded, metadata };
    }
    if (value.kind !== 'record' || value.type_id !== this.resultTypeId) {
      throw new SleyToolsError('ST_EXECUTION_FAILED', 'execution', 'native result differs from pinned entrypoint identity');
    }
    if (!value.fields || Object.keys(value.fields).length !== this.resultFields.size
      || [...this.resultFields.keys()].some((name) => !Object.hasOwn(value.fields, name))) {
      throw new SleyToolsError('ST_EXECUTION_FAILED', 'execution', 'native result record has unexpected fields');
    }
    const decoded = {};
    for (const [name, fieldType] of this.resultFields) {
      const field = value.fields[name];
      if (fieldType === 'SInt64') {
        if (!field || Object.keys(field).length !== 3 || field.kind !== 'integer' || field.type !== 'i64' || typeof field.value !== 'string') {
          throw new SleyToolsError('ST_EXECUTION_FAILED', 'execution', `native field ${name} is not tagged i64`);
        }
        let number;
        try { number = BigInt(field.value); }
        catch { throw new SleyToolsError('ST_EXECUTION_FAILED', 'execution', `native field ${name} is not decimal i64`); }
        let canonical;
        try { canonical = encodeInteger(number, 'i64').value === field.value; }
        catch { canonical = false; }
        if (!canonical) {
          throw new SleyToolsError('ST_EXECUTION_FAILED', 'execution', `native field ${name} is not canonical i64`);
        }
        decoded[name] = number;
      } else {
        if (!field || Object.keys(field).length !== 2 || field.kind !== 'boolean' || typeof field.value !== 'boolean') {
          throw new SleyToolsError('ST_EXECUTION_FAILED', 'execution', `native field ${name} is not Boolean`);
        }
        decoded[name] = field.value;
      }
    }
    return { value: decoded, metadata };
  }

  callGraph(entrypoint, options) {
    if (!this.graphProfile) throw inputError('program is not a graph_v0 project');
    return this.call(entrypoint, options);
  }

  inspect() { return this.runtime.request('sleytools.program.inspect', { handle: this.runtime.handle }, 5000); }

  test() { return this.runtime.request('sleytools.tests.run', { handle: this.runtime.handle }, 30000); }

  propose(change) {
    return this.runtime.request('sleytools.program.propose',
      { handle: this.runtime.handle, change: taggedChange(this.name, change) }, 20000);
  }

  proposeGraphConstant(name, value, cases) {
    if (!this.graphProfile) throw inputError('program is not a graph_v0 project');
    const selected = this.graphConstants.find((constant) => constant.name === name);
    if (!selected) throw inputError(`unknown graph constant: ${name}`);
    const kind = selected.value_type;
    const raw = graphRaw(value, kind);
    const tagged = kind === 'Bool' ? { kind: 'boolean', value: raw }
      : encodeInteger(value, kind === 'UInt32' ? 'u32' : 'i64');
    return this.runtime.request('sleytools.program.propose', {
      handle: this.runtime.handle,
      change: { field: `constant.${name}`, value: tagged,
        cases: graphCases(cases, this.graphParameters, this.graphResult) },
    }, 20000);
  }

  proposeGraphOpcode(block, name, opcode, cases) {
    if (!this.graphProfile) throw inputError('program is not a graph_v0 project');
    graphLabel(block);
    graphLabel(name);
    if (!GRAPH_EDITABLE_OPCODES.has(opcode)) throw inputError('unsupported graph opcode edit');
    return this.runtime.request('sleytools.program.propose', {
      handle: this.runtime.handle,
      change: { field: `operation.${block}.${name}.opcode`, value: opcode,
        cases: graphCases(cases, this.graphParameters, this.graphResult) },
    }, 20000);
  }

  proposeGraphOperand(block, name, slot, reference, cases) {
    if (!this.graphProfile) throw inputError('program is not a graph_v0 project');
    graphLabel(block);
    graphLabel(name);
    const selected = this.graphOperations.find((operation) => operation.block === block && operation.name === name);
    if (!selected || !selected.operands || !Object.hasOwn(selected.operands, slot)) {
      throw inputError('graph operation has no editable operand at that slot');
    }
    if (typeof reference !== 'string' || !/^(param|op)\.[A-Za-z][A-Za-z0-9_]{0,63}$/.test(reference)) {
      throw inputError('graph operand reference must be param.NAME or op.NAME');
    }
    return this.runtime.request('sleytools.program.propose', {
      handle: this.runtime.handle,
      change: { field: `operation.${block}.${name}.${slot}`, value: reference,
        cases: graphCases(cases, this.graphParameters, this.graphResult) },
    }, 20000);
  }

  proposeGraphBranch(block, targets, cases) {
    if (!this.graphProfile) throw inputError('program is not a graph_v0 project');
    graphLabel(block);
    const selected = this.graphBranches.find((branch) => branch.block === block);
    if (!selected) throw inputError(`unknown graph branch: ${block}`);
    if (!targets || typeof targets !== 'object' || Array.isArray(targets)
      || Object.keys(targets).length !== Object.keys(selected.targets).length
      || Object.keys(selected.targets).some((key) => typeof targets[key] !== 'string')) {
      throw inputError('graph branch targets have the wrong fields');
    }
    for (const key of Object.keys(targets)) {
      if (!Object.hasOwn(selected.targets, key)) throw inputError('graph branch targets have the wrong fields');
      graphLabel(targets[key]);
    }
    return this.runtime.request('sleytools.program.propose', {
      handle: this.runtime.handle,
      change: { field: `branch.${block}.targets`, value: targets,
        cases: graphCases(cases, this.graphParameters, this.graphResult) },
    }, 20000);
  }

  async validate(candidateId) {
    const response = await this.runtime.request('sleytools.candidate.validate',
      { handle: this.runtime.handle, candidate_id: candidateId }, 30000);
    if (typeof response.operation_id !== 'string' || !/^[0-9a-f]{64}$/.test(response.operation_id)) {
      throw new SleyToolsError('ST_CORE_INCOMPATIBLE', 'compatibility', 'validation omitted durable operation ID');
    }
    this.operations.set(candidateId, response.operation_id);
    return response;
  }

  async commit(candidateId) {
    let response;
    try {
      response = await this.runtime.request('sleytools.candidate.commit',
        { handle: this.runtime.handle, candidate_id: candidateId }, 90000);
    } catch (error) {
      if (error instanceof SleyToolsError && ['ST_TRANSPORT_FAILED', 'ST_COMMIT_OUTCOME_UNKNOWN'].includes(error.code)) {
        this.runtime.handle = null;
        this.runtime.preserveTemporary = true;
        const operationId = this.operations.get(candidateId);
        let status;
        if (operationId) {
          try { status = await this.runtime.operationStatus(operationId); }
          catch { /* Preserve the project and report the unresolved operation. */ }
        }
        if (status?.state === 'completed') {
          throw new SleyToolsError('ST_STALE_BASE', 'stale_state',
            `candidate ${candidateId} committed with receipt ${status.core_reconciliation.receipt_id}; reopen or repair project ${this.runtime.projectPath}; operation ${operationId}`);
        }
        throw new SleyToolsError('ST_COMMIT_OUTCOME_UNKNOWN', 'transport',
          `candidate ${candidateId} outcome is unknown; query operation ${operationId} in preserved project ${this.runtime.projectPath} before retrying`);
      }
      throw error;
    }
    this.runtime.handle = null;
    if (!response || response.contract !== 'sley-tools.rpc-commit.v0' || response.commit?.candidate_id !== candidateId
      || response.commit?.operation_id !== this.operations.get(candidateId)
      || typeof response.new_state_root !== 'string' || !/^[0-9a-f]{64}$/.test(response.new_state_root)) {
      this.runtime.preserveTemporary = true;
      throw new SleyToolsError('ST_COMMIT_OUTCOME_UNKNOWN', 'transport',
        `commit response for candidate ${candidateId} was inconsistent; inspect operation ${this.operations.get(candidateId)} in project ${this.runtime.projectPath}`);
    }
    this.stateRoot = response.new_state_root;
    try {
      const opened = await this.runtime.request('sleytools.workspace.open', {}, 5000);
      this.runtime.handle = opened.handle;
      const inspection = await this.runtime.request('sleytools.program.inspect', { handle: opened.handle }, 5000);
      if (inspection.accepted_state_root !== response.new_state_root) {
        throw new SleyToolsError('ST_STALE_BASE', 'stale_state', 'accepted root advanced again after commit');
      }
      Object.assign(this, new Program(this.runtime, inspection));
    } catch (error) {
      this.runtime.preserveTemporary = true;
      throw new SleyToolsError('ST_COMMIT_OUTCOME_UNKNOWN', 'transport',
        `candidate ${candidateId} committed at ${response.new_state_root}, but the SDK could not reopen project ${this.runtime.projectPath}; operation ${this.operations.get(candidateId)}`);
    }
    return response.commit;
  }

  discard(candidateId) {
    return this.runtime.request('sleytools.candidate.discard',
      { handle: this.runtime.handle, candidate_id: candidateId }, 10000);
  }
}

export function openRuntime({ tools, core }) { return new Runtime({ tools, core }); }
