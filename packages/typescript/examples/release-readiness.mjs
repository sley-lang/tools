/** Run the checked-in native Release Readiness artifact from Node. */

import { openRuntime } from '@sley-lang/tools';

const args = Object.fromEntries(process.argv.slice(2).map((item) => {
  const [key, value] = item.split('=', 2);
  return [key, value];
}));
if (!args.tools || !args.core || !args.artifact) {
  throw new Error('usage: node release-readiness.mjs tools=PATH core=PATH artifact=PATH');
}
const runtime = openRuntime({ tools: args.tools, core: args.core });
try {
  const program = await runtime.load({ artifact: args.artifact });
  const result = await program.call('evaluate', {
    inputs: { test_run_completed: true, tests_failed: 0n, artifact_verified: true, blocking_findings: 1n },
  });
  process.stdout.write(JSON.stringify({ result: result.value, observation_id: result.metadata.observation_id }) + '\n');
} finally {
  await runtime.close();
}
