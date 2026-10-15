# Local MCP adapter v0

The optional connector in `packages/mcp/` exposes one explicitly selected local Sley project to an MCP client. It uses the official `@modelcontextprotocol/server` 2.1.0 SDK over stdio, with `zod` 4.6.5 for typed tool inputs. The lockfile pins the dependency graph. The SDK's `serveStdio` entry serves both the 2025 legacy protocol era and the 2026-07-28 era; the [reference client evidence](../evidence/mcp/reference-v7.json) negotiated each against the same connector. See the [official SDK package guide](https://github.com/modelcontextprotocol/typescript-sdk/blob/main/docs/get-started/packages.md), [stdio guide](https://github.com/modelcontextprotocol/typescript-sdk/blob/main/docs/serving/stdio.md), and [protocol version guide](https://github.com/modelcontextprotocol/typescript-sdk/blob/main/docs/protocol-versions.md).

For development, install the optional package and start it with explicit paths:

```sh
cd packages/mcp
npm ci --ignore-scripts
node src/server.mjs --project /absolute/path/to/project \
  --tools /absolute/path/to/sley-tools \
  --core /absolute/path/to/sley
```

To build and install a standalone local npm tarball:

```sh
python3 scripts/package_mcp.py --output-dir /tmp/sley-mcp-dist
mkdir -p /tmp/sley-mcp-client
npm install --offline --ignore-scripts --no-package-lock \
  --prefix /tmp/sley-mcp-client /tmp/sley-mcp-dist/sley-lang-tools-mcp-0.0.1.tgz
/tmp/sley-mcp-client/node_modules/.bin/sley-tools-mcp --project /absolute/path/to/project \
  --tools /absolute/path/to/sley-tools \
  --core /absolute/path/to/sley
```

The tarball bundles its pinned MCP runtime dependencies and the Sley TypeScript SDK. Build it from the repository root. This local artifact has not been published to npm.

The public CLI can launch an explicitly selected installed adapter. It replaces itself with Node, so stdout remains MCP protocol only:

```sh
sley-tools mcp --project /absolute/path/to/project \
  --core /absolute/path/to/sley \
  --adapter /tmp/sley-mcp-client/node_modules/@sley-lang/tools-mcp/src/server.mjs
```

The launcher passes its own executable as `--tools`. Add the same `--allow-write`, `--workspace`, and three Review path options shown below when needed. The adapter path is an explicit local executable-code choice; the CLI never downloads or discovers one from project metadata. Without `--adapter`, the CLI returns a setup diagnostic. Node 20 or later is required for this optional command. The [launcher probe](../evidence/mcp/launcher-v4.json) verifies a real native behavior edit through the CLI and MCP client, plus missing-adapter and malformed-option refusals.

Add `--allow-write` only when the owner wants that MCP connection to propose and commit program changes. The server resolves the selected project, tools binary, and core binary before accepting MCP requests. Tool arguments contain no project path. With authoring disabled, the connector exposes inspect, run, and test tools; execution and tests may persist local reports, and their annotations reflect that. With authoring enabled, it also exposes propose, validate, and commit. The backend and core still enforce candidate and root bindings. MCP annotations are descriptive, not an authorization check.

To permit creation, add `--allow-write --workspace /absolute/path/to/existing/workspace`. The selected project must be a direct child of that workspace. Creation accepts a simple project name and creates another direct child without switching the active connection. `sley_create_program` offers two fixed native templates. `sley_create_graph` accepts a bounded `sley-tools.graph-authoring.v0` recipe and 1–256 host-valued cases; its `program_id` must match the destination name. The `sley://builder-operations` resource gives a complete working recipe and case example. Signed 64 bit values use exact decimal strings. The connector passes these to the TypeScript SDK and the Rust native builder, which asks the selected Sley core to validate and commit the graph. Reconnect with the new `--project` path to use it. Existing destinations are rejected. A failed native construction can leave a project marked `INCOMPLETE`; inspect that directory before retrying.

To expose Review, configure the exact native package and fixture paths at startup:

```sh
node src/server.mjs --project /absolute/path/to/project \
  --tools /absolute/path/to/sley-tools --core /absolute/path/to/sley \
  --review-before /absolute/path/to/before/program.json \
  --review-after /absolute/path/to/after/program.json \
  --review-cases /absolute/path/to/cases.json
```

All three Review paths are required together. The model cannot substitute other paths in a tool call. Review writes a temporary report, verifies it against the same configured inputs, returns the report and verification, and removes the temporary report. Its tool annotation reflects that work.

| Tool | Current behavior |
|---|---|
| `sley_inspect_program` | Bounded accepted-root view and program manifest. |
| `sley_create_program` | New Release Readiness or Resource Admission native project under the configured workspace. Requires `--allow-write --workspace`. |
| `sley_create_graph` | New native Sley graph from a bounded recipe and declared cases under the configured workspace. Requires `--allow-write --workspace`; creation uses the SDK and selected core. |
| `sley_run_program` | Typed `evaluate` inputs, exact native result, and execution metadata. Signed 64 bit inputs and outputs use decimal strings on the MCP wire. |
| `sley_run_tests` | Declared external assertions against native execution, with native core test admission stated separately. |
| `sley_propose_edit` | One worked field edit or named graph constant edit, with expected cases for the graph profile. Requires `--allow-write`. |
| `sley_propose_opcode_edit` | One listed native graph operation opcode edit, with expected postchange cases. Available on editable graph projects with `--allow-write`. |
| `sley_propose_operand_edit` | Replace one listed native graph operation's `left`, `right`, or unary `operand` reference with a parameter or earlier operation, with expected postchange cases. Available on editable graph projects with `--allow-write`. |
| `sley_propose_branch_edit` | One listed native graph branch target edit, with expected postchange cases. Available on branching graph projects with `--allow-write`. |
| `sley_validate_candidate` | Separate selected-core validation of the exact candidate. Requires `--allow-write`. |
| `sley_commit_candidate` | Revalidation and commit of that candidate, then SDK reopening at the accepted root. Requires `--allow-write`. |
| `sley_review_change` | Exact configured before/after native package comparison and report verification. Requires the three Review paths. |

The five resources are `sley://mental-model`, `sley://builder-operations`, `sley://program-manifest`, `sley://capabilities`, and `sley://error-guide`. They return short context from this tools version and the selected project. Tool failures preserve Sley tools error codes and available core symbols in an MCP `isError` result. The server writes protocol messages only to stdout.

Reproduce the reference-client probe with:

```sh
node packages/mcp/tests/probe.mjs \
  tools=target/debug/sley-tools \
  core=/absolute/path/to/sley \
  output=evidence/mcp/reference-v7.json
node packages/mcp/tests/launcher_probe.mjs \
  tools=target/debug/sley-tools \
  core=/absolute/path/to/sley \
  server=packages/mcp/src/server.mjs \
  output=evidence/mcp/launcher-v4.json
```

The Node reference run is required for the supported local adapter claim. The current [Node 26.8.1 probe](../evidence/mcp/reference-v7.json) passed 106 external assertions across both negotiated MCP protocol eras, and the [CLI launcher probe](../evidence/mcp/launcher-v4.json) passed a native edit. The same current sources passed again from installed packages inside the [isolated fresh-install scenario](../evidence/fresh-install/local-v10.json). Earlier September 28 attempts in a restricted agent sandbox produced no nested-child Node pipe output and timed out during MCP initialization; a later unrestricted run passed. This supports an environment-specific failure, although it does not identify the exact restricted-runtime mechanism. An additional [Deno 2.9.6 compatibility run](../evidence/mcp/reference-deno-v1.json) had already exercised the same source and assertions. The fresh-install report records a clean source checkout.

The current Node reference probe starts eight isolated projects. A direct stdio audit parses initialization, tool listing, and inspection as three JSON-RPC stdout messages; an out-of-scope startup fails with no stdout. The official reference client verifies discovery and workspace scope, rejects an unauthorized write without changing the accepted root, creates a native Resource Admission project within that scope, and rejects a duplicate creation. It reviews configured Release Readiness package snapshots across 48 cases and verifies the report. It rejects a second edit while a candidate is pending, confirms the accepted root is unchanged, then validates and commits the original candidate. It also edits a graph constant, an opcode, an operand, and a branch through native proposals, checks exact signed 64 bit values in Resource Admission and a numeric graph, returns the Benchmark Comparator's native checked overflow result, runs 106 external assertions, preserves a typed error, and negotiates both MCP protocol eras. The installed scenario repeats that probe and the CLI launcher against the standalone tarball outside the checkout with networking disabled. These probes do not run an AI model. On 2026-09-27, the installed Codex CLI accepted and returned a stdio MCP registration, and the installed Claude Code CLI accepted one and reported a connected server. The [live-agent runbook](LIVE_AGENT_ACCEPTANCE.md) records the separate task and evidence required before claiming agent onboarding. No other AI harness was launched. Npm publication and a dynamic project switch remain open.
