# Live agent acceptance

The reference MCP client checks protocol behavior and native edits without an AI model. A fresh coding-agent session is a separate acceptance gate (AG-06). Do not mark agent onboarding verified until the session completes without developer rescue edits and its tool calls and outcome are recorded.

## Project and connection

Initialize a fresh Release Readiness project with the selected core. Keep the accepted project under an explicit workspace and configure the local MCP adapter with `--project`, `--core`, `--adapter`, `--allow-write`, and `--workspace`. The adapter selects one project per connection; write access is scoped to that project and direct children of the configured workspace for creation. See [MCP_V0.md](MCP_V0.md) for the adapter contract.

From the repository directory, set absolute paths to the built tools binary, selected core, adapter, fresh project, and its parent workspace. The adapter's pinned dependencies must be installed as described in [MCP_V0.md](MCP_V0.md). On 2026-09-27, the installed Codex and Claude Code CLIs accepted these stdio registration forms:

```sh
TOOLS=/absolute/path/to/sley-tools
CORE=/absolute/path/to/sley
ADAPTER=/absolute/path/to/packages/mcp/src/server.mjs
PROJECT=/absolute/path/to/workspace/release-readiness
WORKSPACE=/absolute/path/to/workspace

codex mcp add sley-acceptance -- "$TOOLS" mcp \
  --project "$PROJECT" --core "$CORE" --adapter "$ADAPTER" \
  --allow-write --workspace "$WORKSPACE"
codex mcp get sley-acceptance

claude mcp add -s local sley-acceptance -- "$TOOLS" mcp \
  --project "$PROJECT" --core "$CORE" --adapter "$ADAPTER" \
  --allow-write --workspace "$WORKSPACE"
claude mcp get sley-acceptance
```

The Codex entry is global; the Claude `local` entry is scoped to the current project. `codex mcp get` showed the saved configuration and `claude mcp get` reported a connected stdio server. These checks establish local configuration syntax and a Claude transport handshake, not a completed coding-agent task. Recheck client syntax on another installation. Start the agent session manually. A reference MCP client remains the vendor-independent protocol check.

For the published RC6 package, first follow the [asset installation guide](INSTALL_RC6.md) in a fresh directory, including its checksum and offline MCP package checks. From that directory, prepare a fresh task workspace with the installed paths:

```sh
CORE="$PWD/sley-2.1.1-linux-x86_64/bin/sley"
TOOLS="$PWD/sley-tools-local/bin/sley-tools"
ADAPTER="$PWD/app-mcp/node_modules/@sley-lang/tools-mcp/src/server.mjs"
WORKSPACE="$PWD/sley-agent-acceptance"
PROJECT="$WORKSPACE/release-readiness"
test -x "$CORE" && test -x "$TOOLS" && test -f "$ADAPTER"
mkdir "$WORKSPACE"
"$TOOLS" init "$PROJECT" --template release-readiness --core "$CORE"
"$TOOLS" artifact export --project "$PROJECT" \
  --output "$PROJECT/artifact/native-before.json" --bind-manifest
mkdir -p "$WORKSPACE/before/artifact"
cp "$PROJECT/program.json" "$WORKSPACE/before/program.json"
cp "$PROJECT/artifact/native-before.json" "$WORKSPACE/before/artifact/native-before.json"
```

Register the installed adapter using the `codex mcp add` or `claude mcp add` form above with these absolute paths. The installed CLI help was rechecked on September 28, 2026; both still accept the shown stdio command form. Use T3 Code to start one manual coding-agent session with the task prompt below. This setup prepares a scoped project and a portable baseline package; it does not start an agent or count as AG-06 evidence.

## Task prompt

> Use the configured Sley MCP connection and the fresh Release Readiness project provided by the operator. Change its native rule so one blocking finding is allowed when the test run is complete, no tests failed, and the artifact is verified. Two blocking findings must still reject. Inspect the current program, make and validate a candidate, commit the accepted change, and verify the boundary and at least one independent rejection reason. Use the Sley tools rather than editing project internals. Report the tool calls, candidate identity, accepted roots, test outcome, and any limitation. If a proposed change is rejected, diagnose it and recover without a developer editing the project for you.

The prompt gives the behavior and project, not a candidate or mutation recipe. The agent should discover the exposed tool schema and choose the native edit itself. The [reference MCP probe](../evidence/mcp/reference-v7.json) separately exercises rejected-change recovery: a conflicting proposal is refused, the accepted root stays fixed, and the original candidate is committed. A live agent must demonstrate its own recovery in the manual task; scripted recovery alone does not satisfy AG-06.

## Evidence and pass criteria

Save the exact prompt and supplied context, client and model identity when available, ordered tool calls and responses, and the final report. Record any human intervention. A pass requires a fresh project, native inspection, proposal, validation, commit, and postcommit calls showing a changed accepted root and the requested behavior. Verify the resulting artifact with external assertions and an independent before/after Review over the declared Release Readiness cases. State native core test admission separately; the selected 2.1.1 core currently reports that production native test execution is unavailable.

After the manual agent session, preserve its transcript and run the independent package check from the same RC6 installation directory:

```sh
"$TOOLS" test --project "$PROJECT"
"$TOOLS" artifact export --project "$PROJECT" \
  --output "$PROJECT/artifact/native-after.json" --bind-manifest
mkdir -p "$WORKSPACE/after/artifact"
cp "$PROJECT/program.json" "$WORKSPACE/after/program.json"
cp "$PROJECT/artifact/native-after.json" "$WORKSPACE/after/artifact/native-after.json"
CASES="$PWD/sley-tools-local/programs/release-readiness/tests/bridge-cases.json"
"$TOOLS" review --before "$WORKSPACE/before/program.json" \
  --after "$WORKSPACE/after/program.json" --core "$CORE" \
  --cases "$CASES" --output "$WORKSPACE/review.json"
"$TOOLS" review verify "$WORKSPACE/review.json" \
  --before "$WORKSPACE/before/program.json" \
  --after "$WORKSPACE/after/program.json" --core "$CORE" --cases "$CASES"
```

The before/after directories contain only portable manifests and artifacts. Keep the live workspace and operation records outside those packages. A Review pass checks the declared cases and exact native artifact identities; it cannot substitute for observing the agent's actual tool calls and recovery.

Do not infer agent usability from `mcp get` or from the scripted reference client. The evidence must show what the live agent actually did.
