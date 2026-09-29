/** Verify TypeScript SDK reconciliation after a simulated lost commit reply. */

import { readFile, rm } from 'node:fs/promises';
import { join, resolve } from 'node:path';

import { SleyToolsError, openRuntime } from '../packages/typescript/src/index.js';

const args = Object.fromEntries(process.argv.slice(2).map((item) => item.split('=', 2)));
for (const name of ['tools', 'core', 'artifact']) if (!args[name]) throw new Error(`missing ${name}=PATH`);

async function scenario(afterCommit) {
  const runtime = openRuntime({ tools: resolve(args.tools), core: resolve(args.core) });
  let project;
  let parent;
  let operationId;
  try {
    const program = await runtime.load({ artifact: resolve(args.artifact) });
    project = runtime.projectPath;
    parent = runtime.temporary;
    const proposal = await program.propose({ max_blocking_findings: 1 });
    operationId = (await program.validate(proposal.candidate_id)).operation_id;
    const original = runtime.request.bind(runtime);
    runtime.request = async (method, params, timeout, signal) => {
      if (method !== 'sleytools.candidate.commit') return original(method, params, timeout, signal);
      if (afterCommit) await original(method, params, timeout, signal);
      throw new SleyToolsError('ST_TRANSPORT_FAILED', 'transport', 'simulated lost commit reply');
    };
    try {
      await program.commit(proposal.candidate_id);
      throw new Error('simulated lost reply returned success');
    } catch (error) {
      const expected = afterCommit ? 'ST_STALE_BASE' : 'ST_COMMIT_OUTCOME_UNKNOWN';
      if (!(error instanceof SleyToolsError) || error.code !== expected || !error.message.includes(operationId)) throw error;
    }
    const status = await runtime.operationStatus(operationId);
    if (status.state !== (afterCommit ? 'completed' : 'validated')) {
      throw new Error('SDK status did not distinguish completed and unattempted commits');
    }
    const cancelled = await runtime.cancelOperation(operationId);
    if (cancelled.state !== (afterCommit ? 'completed' : 'cancelled_before_commit')) {
      throw new Error('SDK cancellation misreported commit outcome');
    }
    if (!runtime.preserveTemporary) throw new Error('SDK did not preserve uncertain workspace');
  } finally {
    await runtime.close();
  }
  try {
    await readFile(join(project, '.sley-tools/operations', `${operationId}.json`));
  } finally {
    if (parent) await rm(parent, { recursive: true, force: true });
  }
}

await scenario(false);
await scenario(true);
process.stdout.write(`${JSON.stringify({ predispatch_unknown_preserved: true,
  completed_commit_identified_and_preserved: true, cancel_respected_commit_boundary: true })}\n`);
