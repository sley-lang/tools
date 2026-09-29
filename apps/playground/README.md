# Sley Playground: local and isolated modes

The Playground is a browser interface to the existing `sley-tools` backend and selected Sley core. It creates a disposable local workspace for each browser session. Program decisions and calculations run in accepted Sley graphs through the real VM; Node serves the UI, validates transport inputs, and invokes the tools CLI. No model API key, account, compiler, telemetry, or external network service is needed to use the local mode.

From the repository root, with a built or installed `sley-tools` binary and the selected Sley 2.0.1 binary:

```sh
target/debug/sley-tools playground --adapter apps/playground/server.mjs \
  --core /path/to/sley-2.0.1-linux-x86_64/bin/sley --port 4177
```

Open `http://127.0.0.1:4177`. The server binds only to `127.0.0.1`, checks Host and Origin for browser requests, requires a custom header for mutations, and keeps each browser session in its own temporary workspace. It accepts only the three bundled examples and a fixed set of typed actions. Requests are limited to 16 KiB, CLI operations to 30 seconds, and the service to sixteen live sessions that expire after one hour. Stop the service to remove its temporary workspaces. The CLI binary is the command being run; the core and optional Node adapter paths come from the operator's command line, not from the browser. The adapter must be installed with its bundled `programs/` and `public/` assets.

The initial examples are Release Readiness, the native Benchmark Comparator catalog program, and the [N07 Range Predicate catalog artifact](../../programs/range-predicate/README.md). Run typed inputs or published cases, inspect the accepted root and graph structure, and use the guided Release Readiness or Comparator edit. The UI keeps proposal, Sley validation, and commit separate; a successful commit shows the exact new root, candidate and receipt identities, and before/after output for the same input. After a lost commit reply, the server checks Sley's verified receipt ancestry. It reports a proven commit at its exact root or keeps the outcome uncertain; the **Check commit outcome** control allows another read-only check without retrying the write. The Range Predicate can be run and inspected; it does not yet offer a guided edit.

**Export accepted program** downloads a `.tar.gz` containing the current native artifact, manifest, recipe where applicable, and public cases. It excludes workspace authority, local configuration, session cookies, and repository paths. After extracting it, run the import command in `IMPORT.txt` with a compatible core. The [local probe](../../evidence/playground/local-v1.json) does this with freshly edited Release Readiness and Comparator programs, imports each into a new workspace, verifies the result, and checks that simultaneous session requests cannot exceed the sixteen-session cap. Reproduce the probe with:

```sh
node scripts/probe_playground_local.mjs \
  --core /path/to/sley-2.0.1-linux-x86_64/bin/sley \
  --tools target/debug/sley-tools \
  --output evidence/playground/local-v1.json
```

The local profile uses the selected core's public VM library and external assertions. Native core test admission is still pending. The before/after display is a local behavior comparison, not the full `sley-tools review` report.

The [commit recovery probe](../../evidence/playground/commit-recovery-v1.json) performs a real native commit, holds its reply until the server times out, and verifies the committed root and receipt through an independent reconciliation command. It also simulates a missing reply before commit: the server keeps that outcome unknown and blocks a retry. Reproduce it with:

```sh
node scripts/probe_playground_commit_recovery.mjs \
  --tools target/debug/sley-tools \
  --core /path/to/sley-2.0.1-linux-x86_64/bin/sley \
  --output evidence/playground/commit-recovery-v1.json
```

## Isolated worker candidate

On Linux with unprivileged user/mount namespaces, Bubblewrap, `prlimit`, and a user `systemd-run` that can create cgroup v2 scopes, build a bundle from the exact selected binaries and the three published examples:

```sh
node scripts/build_playground_isolated_bundle.mjs \
  --tools target/debug/sley-tools \
  --core /path/to/sley-2.0.1-linux-x86_64/bin/sley \
  --output /path/to/new/playground-bundle
unshare --user --map-root-user --mount --propagation private \
  node apps/playground/server.mjs \
  --isolated-bundle /path/to/new/playground-bundle --port 4177
```

The service still binds to loopback. It creates a separate 64 MiB tmpfs mount for each session inside its private mount namespace, with a fixed inode limit; writes cannot consume more disk space than that session mount permits. Every CLI operation runs in a new systemd user scope and Bubblewrap mount, user, process, and network namespace. The worker sees a read-only `/usr`, the sealed read-only bundle at `/app`, a 64 MiB temporary `/tmp`, and only its own writable session workspace at `/work`. It does not receive the repository, home directory, `/run`, SSH files, or a host container socket. The server accepts only bundled examples and typed edits, with at most two isolated CLI workers active at once. Each scope has a 1.25 GiB aggregate memory limit, no swap, at most 64 tasks, and a 200% CPU quota. Each command also has a 30-second wall timeout, 1 MiB captured output, a 10-second CPU limit, 1 GiB address-space limit, and 16 MiB per-file write limit. Before each worker call, the server also checks the session workspace for a 64 MiB file-byte budget, at most 4096 entries, and regular files/directories only. A session gets at most 256 worker calls plus 16 read-only inspections for reconciliation. Sessions are cookie-scoped, capped at sixteen, and removed after one idle hour or service shutdown. An invalid bundle or unavailable private mount namespace prevents startup; a missing sandbox dependency prevents worker operations.

The [isolated probe](../../evidence/playground/isolated-v1.json) used the selected 2.0.1 core and verifies the observed mounts, disabled network, cgroup and per-process limits, an oversized worker rejected by its cgroup, launcher termination, hard session disk exhaustion without affecting another session, workspace budget checks, rejection of unlisted bundle files and symlinks, separate Release Readiness sessions, a native edit with verified receipt reconciliation and export/reimport, a client disconnect followed by a verified committed root, a real native commit whose delayed reply times out and is recovered through its verified receipt, a Comparator edit, and a Range Predicate run. The timeout fault uses a temporary executable mount inside the probe's private mount namespace; the sealed bundle and production server are unchanged:

```sh
unshare --user --map-root-user --mount --propagation private \
  node scripts/probe_playground_isolated.mjs \
  --bundle /path/to/new/playground-bundle \
  --output evidence/playground/isolated-v1.json
```

This is an isolated **candidate**, not a hosted-ready or public deployment claim. The Node service runs outside the worker sandbox. A target host still needs its own threat-model review, qualification of the user namespace, tmpfs and cgroup boundaries, and repetition of the after-commit timeout scenario in that host's isolated deployment profile before PG-04/PG-05 and hosted readiness can pass. Public deployment remains separately gated. The service must stay loopback-only meanwhile.

The [current headless Chromium observation](../../evidence/playground/browser-v6.json) covers the actual Release Readiness controls from a produced tools bundle with the development checkout masked: run with one blocking finding, propose a threshold change, validate, commit, and download the accepted artifact. The captured browser state shows the accepted root and result changing from `allow=false` to `allow=true`; the installed CLI imported that exact download into a fresh checkout-masked, network-disabled workspace and passed 48 external assertions. The commit-outcome control was present and disabled for a completed commit; the separate recovery probe exercises uncertain outcomes through the HTTP API. This browser check used local mode, not the isolated candidate. [Earlier observations](../../evidence/playground/browser-v5.json) remain historical.
