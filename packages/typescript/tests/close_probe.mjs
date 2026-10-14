// Runtime.close() must stop an RPC process that ignores stdin EOF.
// Usage: node packages/typescript/tests/close_probe.mjs
import { chmod, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { openRuntime } from '../src/index.js';

const here = dirname(fileURLToPath(import.meta.url));
const fake = resolve(here, '../../../tests/fixtures/fake_rpc_tools.py');
await chmod(fake, 0o755);
const watchdog = setTimeout(() => {
  console.error('FAIL: Runtime.close() did not return within 15 s');
  process.exit(1);
}, 15000);
const project = await mkdtemp(join(tmpdir(), 'sley-ts-close-'));
try {
  const runtime = openRuntime({ tools: fake, core: fake });
  const program = await runtime.openProject({ project });
  if (program.name !== 'release-readiness') throw new Error('fake inspection was not loaded');
  const pid = runtime.process.pid;
  const started = Date.now();
  await runtime.close();
  const elapsed = Date.now() - started;
  let alive = true;
  try { process.kill(pid, 0); } catch (error) { alive = error.code !== 'ESRCH'; }
  if (alive) throw new Error(`RPC process ${pid} survived close()`);
  if (elapsed < 4000) throw new Error(`close() returned after ${elapsed} ms, before the 5 s grace period`);
  console.log(`close probe: stuck RPC process stopped after ${elapsed} ms`);
} finally {
  clearTimeout(watchdog);
  await rm(project, { recursive: true, force: true });
}
