# Local fresh-install acceptance

`scripts/probe_fresh_install.py` builds a local tools bundle, a Python wheel, a TypeScript tarball, and a standalone MCP npm tarball from this checkout. The MCP tarball includes its pinned runtime dependencies and the TypeScript SDK. The bundle carries the official MCP reference client, CLI launcher probe, and their pinned test dependencies. The probe verifies the selected Sley 2.0.1 core archive, then starts a Bubblewrap environment with the development checkout hidden, networking disabled, a private writable `/tmp`, fresh caches, and a new workspace. Inside that environment it extracts the archives and installs the SDK and MCP packages. All program operations use the installed copies.

Run from the repository root after building the two tools binaries:

```sh
cargo build --locked --offline -p sley-tools-backend --bin sley-tools --bin sley-tools-native-builder
python3 scripts/probe_fresh_install.py \
  --tools target/debug/sley-tools \
  --builder target/debug/sley-tools-native-builder \
  --core-archive /path/to/sley-2.0.1-linux-x86_64.tar.gz \
  --output evidence/fresh-install/local-v9.json
```

The local bundle contains the two tools binaries, all ten native catalog packages, examples, documentation, license and notice, the Playground browser adapter, Repair Lab materials, and the MCP reference client. The probe builds the optional `sley_tools` wheel, `@sley-lang/tools` tarball, and `@sley-lang/tools-mcp` tarball separately. These are local test packages, not published releases. Their hashes and the installed binary hashes are recorded in [the current evidence](../evidence/fresh-install/local-v9.json). That run passed with a clean source checkout, the development checkout hidden, and networking disabled. Reproduction needs Cargo, uv, npm, Python 3.14, Node 20 or later, and Bubblewrap on the build/test host; the installed basic CLI itself does not need those build tools.

The isolated scenario checks:

1. Core archive and adjacent binary manifest integrity; installed `doctor` identity.
2. Native Release Readiness creation, execution, testing, behavior edit, manifest-bound export, Review verification, and artifact replay.
3. Installed Python and TypeScript SDK calls, including the exact signed integer `9007199254740993`, plus separate compatible graph opcode edits through both installed SDK packages. The installed CLI also constructs and statically validates a native `TestCase` candidate, refuses an absent case, and keeps execution `NOT_RUN`.
4. The published Data Pipe sample through `sley-tools pipe`, the installed optional Python entrypoint, and three imported native graphs.
5. The installed Playground through `sley-tools playground`: local HTTP run, edit, test, and export for three native programs, with two exported edits replayed in fresh workspaces.
6. Repair Lab RL-01 through RL-08 from new workspaces, including rejected wrong starts, worked native changes, authority and stale-state recovery, portable transfer, and confined resets. The installed bundle runs the same public exercise probe with the development checkout hidden.
7. Import a JSON Schema validator and two bounded Python functions; run raw-input cases, make and test native edits to the validator and tier function, verify changed boundaries, and reject unsupported schema and Python source inputs without publishing projects.
8. Install the standalone MCP tarball outside the checkout with npm offline, then run the official MCP reference client and `sley-tools mcp` launcher from the bundle against that installed server. They check protocol-only stdout and workspace permissions, create and edit native programs, reject a conflicting change without advancing the accepted root, recover by committing the original candidate, verify Review, and complete 106 reference-client assertions across eight fresh projects plus a separate launcher edit.

This is a local package acceptance scenario for those paths. The debug binaries used by this probe may contain build-host paths and are not publication artifacts; the [release-candidate procedure](RELEASE_CANDIDATE.md) separately audits stripped, path-remapped binaries. Neither check covers a live coding-agent task, hosted deployment, public publication, or native core test admission. The production native test executor remains unavailable in the selected core release, so local `sley-tools test` results are external assertions against native execution.
