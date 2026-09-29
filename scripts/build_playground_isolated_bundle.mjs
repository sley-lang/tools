#!/usr/bin/env node
/** Assemble the exact binaries and bundled examples used by isolated Playground workers. */

import { createHash } from 'node:crypto';
import { chmod, copyFile, mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { bundledFiles } from '../apps/playground/isolated-worker.mjs';

const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
function option(name) {
  const index = args.indexOf(name);
  return index < 0 ? null : args[index + 1];
}
const tools = option('--tools');
const core = option('--core');
const output = option('--output');
if (!tools || !core || !output || args.length !== 6)
  throw new Error('usage: node scripts/build_playground_isolated_bundle.mjs --tools TOOLS --core CORE --output NEW_DIRECTORY');
const source = new Map([
  ['bin/sley-tools', resolve(tools)],
  ['bin/sley-tools-native-builder', join(dirname(resolve(tools)), 'sley-tools-native-builder')],
  ['core/bin/sley', resolve(core)],
  ['core/MANIFEST.json', resolve(dirname(resolve(core)), '..', 'MANIFEST.json')],
]);
const files = {};
await mkdir(resolve(output));
for (const path of bundledFiles) {
  const from = source.get(path) ?? join(repo, path);
  const to = join(resolve(output), path);
  await mkdir(dirname(to), { recursive: true });
  await copyFile(from, to);
  if (path.startsWith('bin/') || path === 'core/bin/sley') await chmod(to, 0o755);
  files[path] = createHash('sha256').update(await readFile(to)).digest('hex');
}
await writeFile(join(resolve(output), 'BUNDLE.json'), JSON.stringify({
  contract: 'sley-tools.playground-isolated-bundle.v1', files,
}, null, 2) + '\n');
console.log(`built isolated Playground bundle at ${resolve(output)}`);
