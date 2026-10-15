# Sley Tools

Build, run, change, and share small programs that execute in [Sley](https://github.com/sley-lang/sley). This repository contains the command-line toolkit, application adapters, ten ready-to-run native programs, and examples that show how to use them.

**Example:** Release Readiness takes facts about a proposed release and returns an `allow` decision with reasons. You can run it, test its behavior, change its threshold, and export the resulting native program. The decision runs in Sley; the toolkit handles files, input, and the connection to the Sley core.

## Try a native program

The current release candidate is for Linux x86_64 and uses the separately installed Sley 2.1.1 core. [Download and verify the RC6 packages](docs/INSTALL_RC6.md), then run:

```sh
CORE="$PWD/sley-2.1.1-linux-x86_64/bin/sley"
TOOLS="$PWD/sley-tools-local/bin/sley-tools"
"$TOOLS" doctor --core "$CORE"
"$TOOLS" init my-first-program --template release-readiness --core "$CORE"
"$TOOLS" run --project my-first-program --input my-first-program/inputs/ready.json
"$TOOLS" test --project my-first-program
```

`init` creates and commits a Sley program in a new local workspace. `run` executes that accepted program. `test` checks the supplied examples against native execution. See [Starter](tools/starter/README.md) for the edit and export steps.

## Choose a tool

| Tool | What it helps you do |
| --- | --- |
| [Starter](tools/starter/README.md) | Create, run, test, edit, and export a first Sley program from the CLI. |
| [Bridge](tools/bridge/README.md) | Call a native Sley program from Python or TypeScript. |
| [Agent Connector](tools/agent-connector/README.md) | Give a local coding agent scoped MCP tools to inspect, run, and edit a Sley project. |
| [Rules](tools/rules/README.md) | Use native decision programs for release and resource checks. |
| [Data Pipe](tools/data-pipe/README.md) | Compare and summarize bounded benchmark data from JSON Lines. |
| [Review](tools/review/README.md) | See what changed between two native program packages and replay the checks. |
| [Playground](tools/playground/README.md) | Explore a few native examples in a local browser interface. |
| [Repair Lab](tools/repair-lab/README.md) | Practice creating and fixing native programs in eight guided exercises. |
| [Schema Importer](tools/schema-importer/README.md) | Turn a supported subset of JSON Schema into an editable Sley validator. |
| [Python Function Importer](tools/python-function-importer/README.md) | Turn a restricted pure Python function into an editable Sley program. |

The [native program catalog](programs/catalog.json) has ten separate examples, each with an artifact, tests, and a README under [`programs/`](programs/). The tool pages above explain the applications and entry points; the program pages explain each native computation.

## What runs where

Sley 2 programs are accepted **SSMC1 semantic graphs**. The JSON graph recipes in this repository are inputs to the builder, not a `.sley` source language. The selected Sley core validates, commits, and executes the graphs. Rust manages authoring, workspaces, artifacts, and local RPC. Python and TypeScript handle application access and external input/output. Data Pipe comparison, selection, and reduction, for example, each run in native Sley graphs.

You can inspect the [graph authoring contract](docs/GRAPH_AUTHORING_V0.md), [local RPC contract](docs/RPC_V0.md), and [artifact format](docs/ARTIFACT_V0.md). The [implementation spec](docs/IMPLEMENTATION_SPEC.md) records the complete scope and release gates.

## Current status

The Linux x86_64 [RC6 prerelease](https://github.com/sley-lang/tools/releases/tag/v0.0.1-rc.6) targets the public Sley 2.1.1 core. Its release assets passed an offline fresh install and the documented native, SDK, MCP, pipeline, lab, and importer examples. It is **not a stable release**. On the selected public Sley 2.1.1 core, native `TestCase` candidates can be validated, but the core cannot yet admit or run them. `sley-tools test` runs external assertions against native program execution and reports that native core tests are pending. A compatible released core and a live coding-agent acceptance run are still needed for the full release gate. See [the precise native-test status](docs/F0_NATIVE_TEST_STATUS_PUBLIC.md).

The Playground is a local optional interface; it is not required for CLI, SDK, or native program use. Hosted Playground qualification remains open.

## Repository layout and license

`crates/` holds the shared Rust backend and CLI, `packages/` the optional Python, TypeScript, and MCP adapters, `programs/` the native examples, `apps/` the local Playground and Repair Lab, and `actions/` the Review GitHub Action. These tools share one backend and release candidate. Each tool has its own README above; each native program has its own package and README.

Licensed under [Apache 2.0](LICENSE). See [NOTICE](NOTICE) for attribution.
