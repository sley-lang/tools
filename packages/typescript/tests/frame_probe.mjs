// A response frame that is valid JSON but not an object must reject the
// pending call with ST_TRANSPORT_FAILED instead of throwing in the stdout
// handler, which would crash the host process (for example the MCP adapter).
// Usage: node packages/typescript/tests/frame_probe.mjs
import { chmod, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { openRuntime } from '../src/index.js';

const here = dirname(fileURLToPath(import.meta.url));
const fake = resolve(here, '../../../tests/fixtures/fake_rpc_tools.py');
await chmod(fake, 0o755);
const watchdog = setTimeout(() => {
  console.error('FAIL: frame probe did not finish within 30 s');
  process.exit(1);
}, 30000);
let unhandled = 0;
process.on('unhandledRejection', (error) => {
  unhandled += 1;
  console.error(`unhandled rejection: ${error}`);
});
const project = await mkdtemp(join(tmpdir(), 'sley-ts-frame-'));
const frames = ['null', '[]', '7', '"text"'];
try {
  for (const frame of frames) {
    process.env.FAKE_RPC_NON_OBJECT_FRAME = frame;
    const runtime = openRuntime({ tools: fake, core: fake });
    const started = Date.now();
    const opening = runtime.openProject({ project });
    const pid = runtime.process.pid;
    let failure;
    try { await opening; }
    catch (error) { failure = error; }
    const elapsed = Date.now() - started;
    if (failure?.code !== 'ST_TRANSPORT_FAILED') {
      throw new Error(`frame ${frame}: expected ST_TRANSPORT_FAILED, got ${failure?.code ?? 'success'}: ${failure?.message}`);
    }
    if (runtime.process !== null) throw new Error(`frame ${frame}: runtime kept its RPC process`);
    // The fake ignores stdin EOF and SIGTERM, so close() alone would only stop
    // it after its 5 s grace period. The bad frame must stop it immediately.
    let alive = true;
    try { process.kill(pid, 0); } catch (error) { alive = error.code !== 'ESRCH'; }
    if (alive) throw new Error(`frame ${frame}: RPC process ${pid} survived the rejected frame`);
    if (elapsed >= 4000) throw new Error(`frame ${frame}: rejection took ${elapsed} ms; the RPC process was not stopped on the bad frame`);
  }
  await new Promise((resolveTick) => setImmediate(resolveTick));
  if (unhandled) throw new Error(`${unhandled} unhandled rejection(s)`);
  console.log(`frame probe: ${frames.length} non-object response frames rejected without crashing`);
} finally {
  delete process.env.FAKE_RPC_NON_OBJECT_FRAME;
  clearTimeout(watchdog);
  await rm(project, { recursive: true, force: true });
}
