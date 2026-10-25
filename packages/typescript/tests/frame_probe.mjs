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
const project = await mkdtemp(join(tmpdir(), 'sley-ts-frame-'));
const frames = ['null', '[]', '7', '"text"'];
try {
  for (const frame of frames) {
    process.env.FAKE_RPC_NON_OBJECT_FRAME = frame;
    const runtime = openRuntime({ tools: fake, core: fake });
    let failure;
    try { await runtime.openProject({ project }); }
    catch (error) { failure = error; }
    if (failure?.code !== 'ST_TRANSPORT_FAILED') {
      throw new Error(`frame ${frame}: expected ST_TRANSPORT_FAILED, got ${failure?.code ?? 'success'}: ${failure?.message}`);
    }
    if (runtime.process !== null) throw new Error(`frame ${frame}: runtime kept its RPC process`);
  }
  console.log(`frame probe: ${frames.length} non-object response frames rejected without crashing`);
} finally {
  delete process.env.FAKE_RPC_NON_OBJECT_FRAME;
  clearTimeout(watchdog);
  await rm(project, { recursive: true, force: true });
}
