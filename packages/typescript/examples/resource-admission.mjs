/** Call a locally created Resource Admission Sley program. */

import { openRuntime } from '@sley-lang/tools';

const args = Object.fromEntries(process.argv.slice(2).map((item) => item.split('=', 2)));
for (const name of ['tools', 'core', 'artifact']) {
  if (!args[name]) throw new Error(`missing ${name}=PATH`);
}
const runtime = openRuntime({ tools: args.tools, core: args.core });
try {
  const program = await runtime.load({ artifact: args.artifact });
  const result = await program.call('evaluate', { inputs: {
    requested_workers: 2n,
    available_workers: 4n,
    requested_memory_mib: 2n ** 53n + 1n,
    available_memory_mib: 2n ** 53n + 1n,
  } });
  console.log(result.value);
} finally {
  await runtime.close();
}
