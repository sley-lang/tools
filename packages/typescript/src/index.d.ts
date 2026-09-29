export type IntegerType = 'u32' | 'i64';
export type TaggedInteger = { kind: 'integer'; type: IntegerType; value: string };
export type ReleaseReadinessInputs = {
  test_run_completed: boolean;
  tests_failed: number | bigint;
  artifact_verified: boolean;
  blocking_findings: number | bigint;
};
export type ReleaseReadinessResult = {
  allow: boolean;
  test_run_incomplete: boolean;
  tests_failed: boolean;
  artifact_unverified: boolean;
  blocking_findings_exceeded: boolean;
};
export type ResourceAdmissionInputs = {
  requested_workers: number | bigint;
  available_workers: number | bigint;
  requested_memory_mib: number | bigint;
  available_memory_mib: number | bigint;
};
export type ResourceAdmissionResult = {
  allow: boolean;
  invalid_request: boolean;
  invalid_capacity: boolean;
  insufficient_workers: boolean;
  insufficient_memory: boolean;
  requested_memory_mib: bigint;
  available_memory_mib: bigint;
};
export type CallResult = { value: ReleaseReadinessResult | ResourceAdmissionResult; metadata: Record<string, unknown> };
export type GraphInputs = Record<string, boolean | number | bigint>;
export type GraphValue = boolean | number | bigint;
export type GraphCheckedValue = { ok: number | bigint } | { error: { code: 1 } };
export type GraphCallResult = { value: GraphValue | GraphCheckedValue | Record<string, GraphValue | GraphCheckedValue>; metadata: Record<string, unknown> };
export type GraphCase = { id: string; inputs: GraphInputs; expected: GraphValue | GraphCheckedValue | Record<string, GraphValue | GraphCheckedValue> };
export type GraphPrimitiveType = 'Bool' | 'UInt32' | 'SInt64';
export type GraphResultType = GraphPrimitiveType | 'CheckedSInt64';
export type GraphNamedType = { name: string; value_type: GraphResultType };
export type GraphParameter = { name: string; value_type: GraphPrimitiveType };
export type GraphConstant =
  | { name: string; value_type: 'Bool'; value: boolean }
  | { name: string; value_type: 'UInt32' | 'SInt64'; value: number | bigint };
export type GraphOperation =
  | { op: 'constant_ref'; name: string; constant: string }
  | { op: 'bool_not' | 'int_neg_checked' | 'result_ok'; name: string; operand: string }
  | { op: 'bool_and' | 'bool_or' | 'equal' | 'not_equal' | 'less_than' | 'less_equal' | 'greater_than' | 'greater_equal' | 'int_add_checked' | 'int_sub_checked' | 'int_mul_checked'; name: string; left: string; right: string }
  | { op: 'record_new'; name: string; fields: string[] };
export type GraphTerminator =
  | { kind: 'return'; value: string }
  | { kind: 'branch'; target: string }
  | { kind: 'cond_branch'; condition: string; if_true: string; if_false: string };
export type GraphRecipe = {
  contract: 'sley-tools.graph-authoring.v0';
  program_id: string;
  title: string;
  purpose: string;
  parameters: GraphParameter[];
  constants: GraphConstant[];
  result: { kind: 'primitive'; value_type: GraphResultType } | { kind: 'record'; fields: GraphNamedType[] };
  entry_block: string;
  blocks: Array<{ name: string; operations: GraphOperation[]; terminator: GraphTerminator }>;
};
export class GraphBuilder {
  constructor(options: { programId: string; title: string; purpose: string });
  static param(name: string): string;
  static op(name: string): string;
  parameter(name: string, valueType: GraphPrimitiveType): this;
  constant(name: string, valueType: 'Bool', value: boolean): this;
  constant(name: string, valueType: 'UInt32' | 'SInt64', value: number | bigint): this;
  primitiveResult(valueType: GraphResultType): this;
  recordResult(fields: Record<string, GraphResultType>): this;
  block(name: string): GraphBlock;
  entry(name: string): this;
  build(): GraphRecipe;
}
export class GraphBlock {
  constantRef(name: string, constant: string): this;
  unary(opcode: 'bool_not' | 'int_neg_checked' | 'result_ok', name: string, operand: string): this;
  binary(opcode: 'bool_and' | 'bool_or' | 'equal' | 'not_equal' | 'less_than' | 'less_equal' | 'greater_than' | 'greater_equal' | 'int_add_checked' | 'int_sub_checked' | 'int_mul_checked', name: string, left: string, right: string): this;
  record(name: string, fields: string[]): this;
  returnValue(value: string): this;
  jump(target: string): this;
  conditional(condition: string, ifTrue: string, ifFalse: string): this;
  end(): GraphBuilder;
}
export type AuthoringChange = { max_blocking_findings: number | bigint } | { require_memory_spare: boolean };

export class SleyToolsError extends Error {
  readonly code: string;
  readonly category: string;
  readonly coreSymbol: string | null;
  readonly coreNumeric: number | null;
}

export function encodeInteger(value: number | bigint, type: IntegerType): TaggedInteger;

export class Runtime {
  constructor(options: { tools: string; core: string });
  readonly hello: Record<string, unknown> | null;
  readonly handle: string | null;
  load(options: { artifact: string }): Promise<Program>;
  createGraph(options: { project: string; recipe: GraphRecipe; cases: GraphCase[] }): Promise<Program>;
  openProject(options: { project: string }): Promise<Program>;
  operationStatus(operationId: string): Promise<Record<string, unknown>>;
  cancelOperation(operationId: string): Promise<Record<string, unknown>>;
  close(): Promise<void>;
}

export class Program {
  readonly name: string;
  readonly entrypoint: string;
  readonly functionId: string;
  readonly stateRoot: string;
  readonly resultTypeId: string | null;
  readonly graphProfile: boolean;
  readonly graphParameters: Array<{ name: string; value_type: 'Bool' | 'UInt32' | 'SInt64' }>;
  readonly graphConstants: Array<{ name: string; value_type: 'Bool' | 'UInt32' | 'SInt64'; value: boolean | string }>;
  readonly graphBranches: Array<{ block: string; targets: { target: string } | { if_true: string; if_false: string } }>;
  call(entrypoint: string, options: { inputs: ReleaseReadinessInputs | ResourceAdmissionInputs; deadlineMs?: number; signal?: AbortSignal }): Promise<CallResult>;
  callGraph(entrypoint: string, options: { inputs: GraphInputs; deadlineMs?: number; signal?: AbortSignal }): Promise<GraphCallResult>;
  inspect(): Promise<Record<string, unknown>>;
  test(): Promise<Record<string, unknown>>;
  propose(change: AuthoringChange): Promise<Record<string, unknown>>;
  proposeGraphConstant(name: string, value: GraphValue, cases: GraphCase[]): Promise<Record<string, unknown>>;
  proposeGraphOpcode(block: string, name: string, opcode: 'bool_and' | 'bool_or' | 'equal' | 'not_equal' | 'less_than' | 'less_equal' | 'greater_than' | 'greater_equal' | 'int_add_checked' | 'int_sub_checked' | 'int_mul_checked' | 'int_neg_checked' | 'result_ok', cases: GraphCase[]): Promise<Record<string, unknown>>;
  proposeGraphOperand(block: string, name: string, slot: 'left' | 'right' | 'operand', reference: string, cases: GraphCase[]): Promise<Record<string, unknown>>;
  proposeGraphBranch(block: string, targets: { target: string } | { if_true: string; if_false: string }, cases: GraphCase[]): Promise<Record<string, unknown>>;
  validate(candidateId: string): Promise<Record<string, unknown>>;
  commit(candidateId: string): Promise<Record<string, unknown>>;
  discard(candidateId: string): Promise<Record<string, unknown>>;
}

export function openRuntime(options: { tools: string; core: string }): Runtime;
