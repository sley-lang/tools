/** Linux worker boundary for the Playground's fixed, bundled operations. */

import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { lstat, mkdtemp, opendir, readFile, realpath, rm, statfs } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { isAbsolute, join, relative, sep } from 'node:path';
import { promisify } from 'node:util';

const exec = promisify(execFile);
const TMPFS_MAGIC = 0x01021994;

export const bundledFiles = [
  'bin/sley-tools',
  'bin/sley-tools-native-builder',
  'core/bin/sley',
  'core/MANIFEST.json',
  'programs/release-readiness/artifact/native-program.json',
  'programs/release-readiness/tests/bridge-cases.json',
  'programs/benchmark-comparator/artifact/native-graph.json',
  'programs/benchmark-comparator/tests/cases.json',
  'programs/benchmark-comparator/tests/allow-corrections-cases.json',
  'programs/range-predicate/artifact/native-graph.json',
];

const assets = new Set(bundledFiles.filter(path => path.startsWith('programs/')));
export const MAX_WORKSPACE_BYTES = 64 * 1024 * 1024;
export const MAX_WORKSPACE_ENTRIES = 4096;
const WORKSPACE_INODES = 8192;
const allowed = new Set([
  'artifact import', 'artifact export', 'run', 'inspect', 'propose',
  'propose discard', 'validate', 'commit', 'test', 'reconcile',
]);

function within(base, path) {
  const part = relative(base, path);
  return part === '' || (!part.startsWith(`..${sep}`) && part !== '..' && !isAbsolute(part));
}

export async function verifyIsolatedBundle(bundle) {
  const root = await realpath(bundle);
  const expectedFiles = new Set(['BUNDLE.json', ...bundledFiles]);
  const expectedDirectories = new Set();
  for (const file of expectedFiles) {
    const parts = file.split('/');
    for (let index = 1; index < parts.length; index += 1)
      expectedDirectories.add(parts.slice(0, index).join('/'));
  }
  const foundFiles = new Set();
  const pending = [{ absolute: root, relative: '' }];
  while (pending.length) {
    const directory = pending.pop();
    for await (const entry of await opendir(directory.absolute)) {
      const path = directory.relative ? `${directory.relative}/${entry.name}` : entry.name;
      const absolute = join(directory.absolute, entry.name);
      const metadata = await lstat(absolute);
      if (metadata.isDirectory() && expectedDirectories.has(path))
        pending.push({ absolute, relative: path });
      else if (metadata.isFile() && expectedFiles.has(path)) foundFiles.add(path);
      else throw new Error(`isolated bundle has an unlisted or unsafe entry: ${path}`);
    }
  }
  if (foundFiles.size !== expectedFiles.size)
    throw new Error('isolated bundle is missing a listed file');
  const manifest = JSON.parse(await readFile(join(root, 'BUNDLE.json'), 'utf8'));
  if (manifest.contract !== 'sley-tools.playground-isolated-bundle.v1'
      || Object.keys(manifest.files ?? {}).length !== bundledFiles.length
      || bundledFiles.some(path => !/^[a-f0-9]{64}$/.test(manifest.files?.[path] ?? '')))
    throw new Error('invalid isolated Playground bundle manifest');
  for (const path of bundledFiles) {
    const file = join(root, path);
    if (!(await lstat(file)).isFile() || !within(root, await realpath(file)))
      throw new Error(`isolated bundle file is not confined: ${path}`);
    const digest = createHash('sha256').update(await readFile(file)).digest('hex');
    if (digest !== manifest.files[path]) throw new Error(`isolated bundle digest mismatch: ${path}`);
  }
  return manifest;
}

export async function workspaceBudget(root) {
  let bytes = 0;
  let entries = 0;
  const pending = [root];
  while (pending.length) {
    const directory = await opendir(pending.pop());
    for await (const entry of directory) {
      entries += 1;
      if (entries > MAX_WORKSPACE_ENTRIES) return { bytes, entries, overLimit: true };
      const path = join(directory.path, entry.name);
      const metadata = await lstat(path);
      if (metadata.isDirectory()) pending.push(path);
      else if (metadata.isFile()) bytes += metadata.size;
      else throw new Error('isolated workspace contains a non-file entry');
      if (bytes > MAX_WORKSPACE_BYTES) return { bytes, entries, overLimit: true };
    }
  }
  return { bytes, entries, overLimit: false };
}

export async function mountIsolatedWorkspace(root, bytes = MAX_WORKSPACE_BYTES,
                                             inodes = WORKSPACE_INODES) {
  if (!(await lstat(root)).isDirectory() || await realpath(root) !== root)
    throw new Error('isolated workspace mount point is not a real directory');
  await exec('/usr/bin/mount', ['-t', 'tmpfs', '-o',
    `size=${bytes},nr_inodes=${inodes},mode=0700,nosuid,nodev,noexec`, 'tmpfs', root],
  { timeout: 5000, maxBuffer: 1024 * 1024 });
  try {
    const filesystem = await statfs(root);
    if (filesystem.type !== TMPFS_MAGIC || filesystem.blocks * filesystem.bsize !== bytes
        || filesystem.files > inodes)
      throw new Error('isolated workspace mount has unexpected limits');
  } catch (error) {
    await exec('/usr/bin/umount', [root], { timeout: 5000, maxBuffer: 1024 * 1024 });
    throw error;
  }
}

export async function unmountIsolatedWorkspace(root) {
  await exec('/usr/bin/umount', [root], { timeout: 5000, maxBuffer: 1024 * 1024 });
}

export async function verifyIsolatedMountNamespace() {
  const map = (await readFile('/proc/self/uid_map', 'utf8')).trim().split(/\s+/);
  if (process.getuid() !== 0 || map[0] !== '0' || map[1] === '0')
    throw new Error('isolated Playground requires an unprivileged user and private mount namespace');
  const root = await mkdtemp(join(tmpdir(), 'sley-playground-mount-check-'));
  let mounted = false;
  try {
    await mountIsolatedWorkspace(root, 1024 * 1024, 128);
    mounted = true;
  } finally {
    if (mounted) await unmountIsolatedWorkspace(root);
    await rm(root, { recursive: true, force: true });
  }
}

export function isolatedCommand(bundle, workspace, repo, args) {
  const operation = args[0] === 'artifact' || (args[0] === 'propose' && args[1] === 'discard')
    ? `${args[0]} ${args[1]}` : args[0];
  if (!allowed.has(operation)) throw new Error(`operation unavailable in isolated Playground: ${operation}`);
  const mapped = args.map(arg => {
    if (arg === '/app/core/bin/sley') return arg;
    if (!isAbsolute(arg)) return arg;
    if (within(workspace, arg)) return `/work${relative(workspace, arg) ? `/${relative(workspace, arg)}` : ''}`;
    if (within(repo, arg)) {
      const path = relative(repo, arg).split(sep).join('/');
      if (assets.has(path)) return `/app/${path}`;
    }
    throw new Error(`path unavailable in isolated Playground: ${arg}`);
  });
  return {
    file: '/usr/bin/systemd-run',
    args: [
      '--user', '--scope', '--quiet', '--collect',
      '-p', 'MemoryMax=1342177280', '-p', 'MemorySwapMax=0',
      '-p', 'TasksMax=64', '-p', 'CPUQuota=200%', '--',
      '/usr/bin/prlimit', '--cpu=10', '--as=1073741824', '--fsize=16777216', '--',
      '/usr/bin/bwrap', '--unshare-all', '--die-with-parent',
      '--tmpfs', '/', '--ro-bind', '/usr', '/usr',
      '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib',
      '--symlink', 'usr/lib', '/lib64', '--proc', '/proc', '--dev', '/dev',
      '--size', '67108864', '--tmpfs', '/tmp',
      '--ro-bind', bundle, '/app', '--bind', workspace, '/work',
      '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin',
      '--setenv', 'HOME', '/tmp', '--setenv', 'TMPDIR', '/tmp',
      '--chdir', '/work', '--', '/app/bin/sley-tools', ...mapped,
    ],
  };
}
