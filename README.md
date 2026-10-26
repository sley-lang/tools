# Sley Tools

[![Prerelease](https://img.shields.io/github/v/release/sley-lang/tools?include_prereleases&label=prerelease)](https://github.com/sley-lang/tools/releases/tag/v0.0.1-rc.7)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-3b82f6)](LICENSE)
[![sleylang.org](https://img.shields.io/badge/sleylang.org-website-22d3ee)](https://sleylang.org)
[![Follow @SleyLanguage](https://img.shields.io/badge/@SleyLanguage-000000?logo=x&logoColor=white)](https://x.com/SleyLanguage)

Build, run, change, and share small programs that execute in [Sley](https://github.com/sley-lang/sley). This repository contains the command-line toolkit, application adapters, ten ready-to-run native programs, and examples that show how to use them.

**Example:** Release Readiness takes facts about a proposed release and returns an `allow` decision with reasons. You can run it, test its behavior, change its threshold, and export the resulting native program. The decision runs in Sley; the toolkit handles files, input, and the connection to the Sley core.

## Try a native program

RC7 is for Linux x86_64 and runs on the separately installed Sley 2.1.1 core (the current Sley core is [2.1.6](https://github.com/sley-lang/sley/releases/tag/v2.1.6); `doctor` accepts only the 2.1.1 core RC7 was built for). [Download and verify the RC7 packages](docs/INSTALL_RC7.md), then run:

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

The Linux x86_64 [RC7 prerelease](https://github.com/sley-lang/tools/releases/tag/v0.0.1-rc.7) targets the public Sley 2.1.1 core, and its release assets passed an offline fresh install and the documented native, SDK, MCP, pipeline, lab and importer examples. `sley-tools test` checks each program with external assertions against native execution. The 2.1.1 core validates native `TestCase` candidates but does not yet admit or run them, so `test` reports native core tests as pending ([details](docs/F0_NATIVE_TEST_STATUS_PUBLIC.md)).

The Playground is a local optional interface; it is not required for CLI, SDK, or native program use.

## Repository layout and license

`crates/` holds the shared Rust backend and CLI, `packages/` the optional Python, TypeScript, and MCP adapters, `programs/` the native examples, `apps/` the local Playground and Repair Lab, and `actions/` the Review GitHub Action. These tools share one backend and release candidate. Each tool has its own README above; each native program has its own package and README.

Licensed under [Apache 2.0](LICENSE). See [NOTICE](NOTICE) for attribution.

## More Sley

- [sley-lang/sley](https://github.com/sley-lang/sley): the Sley language, core and `sley-agent` workbench.
- [sley-lang/examples](https://github.com/sley-lang/examples): thirty small native programs with their authoring records.
- [Sley Arena](https://sleylang.org/arena): write a bot in Sley and climb a public ladder.

---

<p align="center">
  <b>Building with Sley?</b> <a href="https://github.com/sley-lang/tools">Star Sley Tools on GitHub</a> · <a href="https://x.com/SleyLanguage">Follow @SleyLanguage</a> · <a href="https://sleylang.org">sleylang.org</a>
</p>

<p align="center"><sub>Built by <a href="https://greyforge.tech">Greyforge Labs</a></sub></p>
