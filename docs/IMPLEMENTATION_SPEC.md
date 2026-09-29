# SLEY TOOLS
## Unified adoption toolkit and first-program corpus

**Document:** Implementation specification 1.0  
**Date:** September 26, 2026  
**Target repository:** `sley-lang/tools`  
**Status:** Ready for implementation planning and local development. No implementation or runtime verification is claimed by this document.  
**Audience:** The implementing coding agent and Sley maintainers  
**Product objective:** Make it straightforward for a new human or agent to create a useful Sley program, call it from an existing application, change it, test it, review it, and share it.

---

## 0. Implementation mandate

Build the ten adoption projects specified here in **one tools monorepo**, sharing their protocol, authoring, execution, validation, packaging, and documentation foundations. Do not create ten repositories, ten incompatible clients, or a new agent orchestration platform.

The flagship acceptance story is:

> A fresh user installs the toolkit, creates a release-readiness program through supported Sley interfaces, runs it from Python and TypeScript, asks an ordinary coding agent to change its behavior, reviews the exact change, and reproduces the tested artifact in another workspace.

All ten projects are in scope. Start their independent design and test work immediately, but integrate them in dependency order. A partially implemented frontend or a mock passing a test does not satisfy a native integration milestone.

**First implementation action:** establish the compatibility and authoring probe in Section 4, then deliver the smallest real Starter + Rules vertical slice. Avoid spending the first wave constructing empty package scaffolds for all ten products.

### 0.1 Decisions made by this specification

| Area | Decision |
|---|---|
| Repository | One repository: `sley-lang/tools`. Reuse it if it already exists and is the appropriate repository. |
| Core language ownership | SSMC1, SCB1, SMP1, validation, VM semantics, and durable transactions remain owned by `sley-lang/sley`. |
| Main executable | `sley-tools`. Do not shadow or replace the existing `sley` executable. |
| Shared implementation | One tools backend and one set of public toolkit contracts. Python, TypeScript, MCP, and the browser are adapters to it. |
| Initial execution target | Linux x86_64. Other platforms are separate support claims, not assumed from SDK portability. |
| Initial computational scope | Pure, deterministic, bounded programs. Host adapters perform external input/output. |
| Core compatibility | Begin with the verified public 2.0.1 release. Add narrowly scoped upstream support only where the actual public path requires it. |
| Agent integration | Local MCP and documented CLI/SDK usage. No model subscription or model API is required to use the toolkit. |
| Distribution | A versioned tools release, with optional SDK/MCP/browser components from the same source revision. Registry publication is not required for development. |
| Program counting | Supporting tools are not native Sley programs. Maintain a separate catalog of genuine executable programs. |
| Documentation | One main implementation specification, one concise agent guide, runnable examples, and generated reference material. No duplicative governance-document hierarchy. |
| Deployment | Local operation first. Public hosting and package publication have explicit release gates. |

### 0.2 Scope of implementation authority

This document is a specification, not a record that any repository was created, any package was published, or any code was executed.

The implementing agent should create or use a local tools checkout and build the work. Use the owner's established authorization for remote repository creation and pushes. When remote creation is authorized, create only `sley-lang/tools`; do not create sibling product repositories or modify organization-wide settings. Preserve existing work and inspect an existing repository before changing its layout.

Core changes, when necessary, belong in the existing Sley repository as a small, attributable patch with regression coverage. They do not justify a permanent tools-side fork of the kernel. Public releases, production deployment, licensing decisions, and merging core changes remain owner-controlled actions unless explicitly delegated.

---

## Contents

1. [Goals, non-goals, and completion principles](#1-goals-non-goals-and-completion-principles)
2. [Verified baseline and source authority](#2-verified-baseline-and-source-authority)
3. [Repository, packaging, and architecture](#3-repository-packaging-and-architecture)
4. [Foundation: compatibility and the public authoring path](#4-foundation-compatibility-and-the-public-authoring-path)
5. [Shared toolkit contracts](#5-shared-toolkit-contracts)
6. [Project 01: Sley Starter](#6-project-01-sley-starter)
7. [Project 02: Sley Bridge](#7-project-02-sley-bridge)
8. [Project 03: Sley Agent Connector](#8-project-03-sley-agent-connector)
9. [Project 04: Sley Rules](#9-project-04-sley-rules)
10. [Project 05: Sley Data Pipe](#10-project-05-sley-data-pipe)
11. [Project 06: Sley Review](#11-project-06-sley-review)
12. [Project 07: Sley Playground](#12-project-07-sley-playground)
13. [Project 08: Sley Repair Lab](#13-project-08-sley-repair-lab)
14. [Project 09: Schema Importer](#14-project-09-schema-importer)
15. [Project 10: Python Function Importer](#15-project-10-python-function-importer)
16. [Native program corpus and the path to 25](#16-native-program-corpus-and-the-path-to-25)
17. [Verification, performance, and evidence](#17-verification-performance-and-evidence)
18. [Implementation order and parallel work](#18-implementation-order-and-parallel-work)
19. [Distribution, documentation, and public release](#19-distribution-documentation-and-public-release)
20. [Risks, blocker handling, and final acceptance](#20-risks-blocker-handling-and-final-acceptance)
21. [Agent handoff and source notes](#21-agent-handoff-and-source-notes)

---

## 1. Goals, non-goals, and completion principles

### 1.1 Goals

**G1 — First successful authoring session.** A newcomer can create a new native program and make a verified behavior change without writing binary encodings, copying fixture identities, or using undocumented maintainer utilities.

**G2 — Incremental adoption.** An existing application can call a small Sley function without migrating its application framework or data storage.

**G3 — Familiar agent access.** An agent using its existing CLI or MCP-capable client can inspect and modify Sley programs through typed operations and actionable diagnostics.

**G4 — Useful programs.** Publish executable examples with real responsibilities, independent expected results, boundary cases, and worked modifications. Build toward a 25-program corpus without inflating it with wrappers or trivial duplicates.

**G5 — Review and contribution.** A contributor can submit an artifact change through an ordinary Git workflow; a reviewer can identify the exact artifact, understand the change, and reproduce the tests.

**G6 — Low operational cost.** Basic functionality is local, usable offline after installation, and does not require a paid service, always-running cloud machine, mandatory account, or model API key.

**G7 — Honest capability claims.** Distinguish a proposed operation from an authorized commit, a kernel-valid program from a behaviorally correct program, and a local demonstration from a released integration.

### 1.2 Non-goals for this program

This effort does not introduce a new canonical Sley text language, replace the core VM, implement self-hosting, finish every outstanding Sley acceptance criterion, build a general-purpose package registry, or create a universal Python/Rust/Go translator.

It does not require native networking, file access, arbitrary shell execution, a new agent framework, distributed job scheduling, a cloud account system, or a marketplace. It does not automatically post marketing material or submit data to model providers.

The toolkit may consume host-language source or structured authoring requests at its boundary. Those inputs are not a second canonical form of Sley. The accepted graph and its core identities remain authoritative.

### 1.3 What counts as done

A project is complete for its stated version only when its public interface works against the declared real core profile, its required negative cases behave correctly, its instructions work from a fresh environment, and its evidence names the exact tools revision and core artifact.

A mock, unchecked transcript, hardcoded fixture replay, or host-language fallback is not a native Sley implementation. All are allowed as explicitly labeled development aids; none can satisfy native execution acceptance.

A gate that was not run is `NOT_RUN`. A dependency failure is `BLOCKED`. A smaller delivered scope is described as that scope, not silently promoted to the full project.

---

## 2. Verified baseline and source authority

### 2.1 Observations at specification time

The following are documentation/repository observations, not results of executing Sley in this session:

| Item | Observed value |
|---|---|
| Public repository | `sley-lang/sley` |
| Latest public release returned by GitHub | `v2.0.1` |
| Release build commit | `c748dda18c1276687cc3b0d1ef9b7f257eb09b24` |
| Default-branch snapshot inspected | `da0c3759fce0b6a9d1dc27562a97b0b6a9849d96` |
| Release artifact | `sley-2.0.1-linux-x86_64.tar.gz` |
| Published artifact SHA-256 | `34b3b56957a9ddf9a4edc35c2a961c89928d8e78dc74c76b23f1f5378e975e44` |
| Published artifact size | 2,559,049 bytes |
| Platform stated by release | Linux x86_64, static musl build |
| Release qualification | Released; not a GA claim |

These values come from the public release and repository metadata. Verify them again when implementation begins; distinguish the release commit from subsequent documentation/evidence commits. A newer core must be an explicit compatibility update, not an unrecorded substitution. [S1, S2]

A read of `sley-lang/tools` returned HTTP 404 during preparation. This does not establish that the name is globally available or that an inaccessible repository does not exist. The implementing agent must resolve the repository under its own current authorization before creating it.

### 2.2 Important baseline distinctions

The Quickstart's demo imports a prebuilt program. It explicitly does not cover public candidate construction, commit, test selection, or merge, and its JSON transport still carries hex-encoded canonical request bodies. [S3]

Separately, the Candidate Record specification states that proposal construction is implemented and independently conformant, and that constructing a candidate grants no authority. Validation and durable commit are separate responsibilities. **The demo gap must not be misreported as proof that candidate construction or transactions are absent.** [S4]

Capability summary digests bind authenticated context; they are not credentials. The documented fixed empty-summary vector has specific fixture bindings and must not be copied into arbitrary workspaces as authorization. [S5]

The release notes document a two-commit route for new functions and tests that target them, and the exclusion of E7 opcodes from supported execution. These constraints motivate explicit authoring probes, pure initial programs, and honest multi-step workflows. [S6]

### 2.3 Source authority

For implementation, use this order:

1. The selected core commit's normative contracts and generated descriptors, reconciled with executable behavior and tests.
2. A reproducible observation against the exact selected release artifact.
3. Current public guides and release notes.
4. This toolkit specification for new toolkit behavior, not for redefining core semantics.

A discrepancy is a recorded compatibility issue to resolve. Do not silently choose whichever interpretation makes a demo pass.

Do not assume an old local checkout path, an older private Sley lineage, or a self-hosting branch is the current public core. Inspect the actual checkout before writing.

---

## 3. Repository, packaging, and architecture

### 3.1 One repository, several modules

Use the following as the target layout. Internal module boundaries may be adjusted for implementation quality without splitting the repository or duplicating authority.

```text
tools/
  README.md
  AGENTS.md
  Cargo.toml
  Cargo.lock
  pyproject.toml
  package.json
  <JavaScript and Python dependency lockfiles>
  crates/
    backend/                  # Core integration, authoring, execution, artifacts
    cli/                      # sley-tools executable
  packages/
    python/                   # sley_tools SDK and Python import frontend
    typescript/               # TypeScript SDK and shared browser-facing types
    mcp/                      # MCP adapter using the shared backend
  apps/
    playground/               # Browser UI and local/isolated service adapter
  modules/
    starter/                  # Templates and onboarding flows
    rules/                    # Rules application manifests and adapters
    data-pipe/                # Stream orchestration and pipeline definitions
    review/                   # Human and machine review presentation
    repair-lab/               # Exercise runner and curriculum
    schema-import/            # Supported-schema frontend and mappings
    python-import/            # Profile, mappings, and test corpus
  programs/
    catalog.json
    <program-id>/
      program.json
      build/                  # Reproducible authoring recipe, not canonical source
      artifact/               # Portable accepted native artifact
      tests/                  # Small published behavior fixtures
      README.md
  actions/
    check/action.yml          # Monorepo-contained GitHub Action wrapper
  schemas/                    # Toolkit contracts, not copies of core specs
  tests/
    unit/
    integration/
    compatibility/
    release/
  docs/
    IMPLEMENTATION_SPEC.md
    quickstart.md
    reference/                # Generated where practical
  scripts/                    # Small build/test/release entry points
  .github/
    workflows/
    ISSUE_TEMPLATE/
  compatibility.json
```

Do not create empty directories solely to resemble this tree. Create modules when work begins. Shared implementation belongs in the backend rather than being copied into each module.

Use repository labels such as `area:starter`, `area:bridge`, `area:mcp`, `area:rules`, `area:pipe`, `area:review`, `area:playground`, `area:lab`, `area:schema`, and `area:python-import`. One issue board or the repository's existing planning system is sufficient. Do not maintain parallel status prose in numerous files.

### 3.2 Separation of responsibility

```text
CLI | Python SDK | TypeScript SDK | MCP | Playground
                         |
                 Shared tools backend
             / authoring | execution | artifacts \
                         |
             Supported Sley core interfaces
                         |
             Validation / transactions / VM
```

This is a responsibility diagram, not a requirement to use network services.

The default backend should be a Rust library plus a local executable, reusing publicly available core codecs and types when appropriate. Python and TypeScript should be thin clients, not independently maintained Sley codecs. The agent may choose simpler internal packaging if it preserves one semantic implementation and the public contracts.

Prefer existing public protocol interfaces for stateful operations. Reusing public Rust codecs to construct a request is acceptable. Directly editing core storage or invoking a hidden trusted test helper as a production authorization path is not.

### 3.3 Delivery boundaries

The standalone CLI must not require Python, Node, or a compiler for basic Starter, Rules, and execution use when installed from its binary release. Optional Python import, Python SDK, TypeScript SDK, MCP, and browser tooling may have their documented runtimes.

Build-time dependencies are not runtime dependencies. Missing optional runtimes must produce a precise diagnostic and must not prevent unrelated commands from working.

Use one tools release version initially. Package metadata, generated references, and artifacts identify the same source revision. Do not promise that the tools major version equals the Sley major version.

Proposed registry identifiers are `sley-tools` / `sley_tools` for Python and `@sley-lang/tools` for JavaScript. Verify availability and account ownership before publishing; use local wheels/tarballs and checkout installation during development.

### 3.4 Licensing and external publication

Do not infer a license for new tools code merely from the core's license or from earlier licensing brainstorming. Preserve all upstream notices for reused material. Record an owner-approved license for the new tools before public publication.

An adoption-friendly permissive license is the recommendation for the interoperability layer and worked examples, but this document does not finalize that decision. Licensing status may block public release; it must not stop local implementation and testing. Keep any future separately licensed commercial material out of this repository unless the owner deliberately establishes that boundary.

---

## 4. Foundation: compatibility and the public authoring path

### 4.1 F0 — Compatibility and construction probe

Before downstream features rely on unverified assumptions, produce a small executable probe using the actual selected public core.

The probe must discover and record:

- Executable identity, artifact checksum, platform, protocol version, schema epoch, supported methods, and actual execution profiles.
- How an ordinary user creates a workspace and supplies legitimate local authority for its allowed operations.
- How to construct and submit a candidate using public, documented codecs/interfaces.
- How to bind the live workspace, root, transaction, policy, principal, expiry, and candidate nonce correctly.
- How to validate, commit, execute with caller-supplied inputs, obtain reports, and exercise tests through supported routes.
- Which relevant limits come from protocol negotiation, core validation, execution, or the tools layer.

Probe a real creation, a behavior edit, a rejection, and a portable export/import. A hello response or an import of the old release demo alone does not pass F0.

The output is a compact machine-readable capability matrix with actual outcomes and one brief explanation of gaps. Put it in the compatibility/evidence system; do not create a new governance subsystem.

### 4.2 Reuse before repair

If the required route exists, expose it through the toolkit. If the route exists only as a codec or a low-level public operation, build a usable facade around it.

If a genuinely missing core interface prevents ordinary authorized use, implement the smallest necessary upstream addition in the existing core repository, subject to normal owner review. Examples might include an explicit public workspace-initialization path or a supported facade for an existing operation. These are possible remedies, not established missing features.

The core patch must preserve the separation between proposal, validation, authorization, and durable commit. It must not weaken existing checks or make candidate contents authoritative.

### 4.3 Three execution statuses

Each capability in `compatibility.json` has one of:

| Status | Meaning |
|---|---|
| `supported` | Verified through the declared publicly reproducible route. |
| `requires_core_change` | A precise missing interface or defect has an identified upstream dependency. |
| `unsupported` | Outside the current tools/core profile. |

A local unreleased core patch can be tested, but its results are labeled `development-core`. They are not evidence of compatibility with the unmodified 2.0.1 release.

A release may require a later core version once that dependency is available. It must not advertise 2.0.1 for features that only work with the patch.

### 4.4 Authority and initialization

A workspace created by a local user must have a supported way to establish scoped development authority. The agent must trace and test the real policy model rather than manufacture a passing context from fixture bytes.

Local development authority can be convenient without being a global permission bypass. Scope it to the workspace, allowed operations, and configured budgets. An imported public artifact must not contain reusable secrets or cause the importing user to inherit another user's permissions.

Do not reuse a documented empty-summary digest outside its exact bindings. Do not treat an empty capability list as evidence that every mutation class is authorized. Do not conflate application-level rules with kernel policy.

### 4.5 F0 acceptance

F0 passes when a fresh environment can:

1. Initialize a disposable workspace with declared authority.
2. Construct and commit a new small native function.
3. Execute it on at least two distinct inputs using the real VM.
4. Modify its behavior through a newly constructed candidate.
5. Demonstrate one invalid or unauthorized change being rejected with the accepted program root unchanged.
6. Add and run tests through the declared supported test path.
7. Export the accepted artifact, import it into a fresh workspace, and reproduce the behavior.
8. Record the exact path used and any required core change.

This is the first substantive gate. Other lanes may design and test frontends concurrently, but none may claim native integration before their dependency passes.

---

## 5. Shared toolkit contracts

### 5.1 Contract ownership

The toolkit owns the user-facing authoring facade, local control protocol, SDK models, program manifests, catalog, review format, and evidence format.

The toolkit does **not** own the interpretation of canonical Sley bytes, core entity identities, policy, validation rules, or transaction receipts. Reuse selected-core definitions and generated descriptors instead of copying numeric tags into adapters.

Freeze the first small public toolkit contract after F0. Generate language types and reference documentation where practical. A change that alters public meaning requires a contract version update and migration tests.

All interface names below are **new toolkit interfaces to implement**, not claims that these commands or methods already exist in Sley.

### 5.2 Local control protocol

Provide `sley-tools rpc` as a bounded, local standard-input/standard-output service. Use newline-delimited JSON-RPC 2.0 for control messages, following the base request/response and error model. [S14] This tools protocol is separate from SMP1 and from MCP; adapters translate it rather than making one protocol impersonate another.

Keep stdout protocol-only and diagnostics on stderr. Correlate responses by request ID. Bound input length, output length, nesting, inflight work, and stderr collection. Events and progress messages must not be mistaken for final responses.

Minimum logical operations:

| Operation | Responsibility |
|---|---|
| `sleytools.hello` | Tools/core identity, contract versions, capability matrix, limits. |
| `sleytools.workspace.open` | Open or explicitly initialize a permitted workspace; return an opaque tools handle. |
| `sleytools.program.inspect` | Read a bounded, root-pinned view with useful labels and exact identities. |
| `sleytools.program.propose` | Build a proposal from typed authoring operations, without committing it. |
| `sleytools.candidate.validate` | Run real core validation and return structured diagnostics. |
| `sleytools.candidate.commit` | Request an authorized commit bound to the validated candidate and current base. |
| `sleytools.program.execute` | Execute an exact artifact/root entrypoint with typed values and limits. |
| `sleytools.tests.run` | Execute declared behavioral cases and distinguish native tests from external assertions. |
| `sleytools.artifact.export` / `import` | Create or load a verified portable package with explicit workspace scope. |
| `sleytools.review.compare` | Produce an exact-identity comparison and its declared level of interpretation. |
| `sleytools.operation.status` / `cancel` | Inspect or cancel tools work without silently replaying mutations. |

Not every operation must become a separate MCP tool. The CLI and SDK may provide higher-level convenience flows over these primitives.

Workspace handles are opaque references to configured local workspaces, not user-supplied unrestricted paths in every request. Paths permitted at CLI initialization must be validated and resolved before the handle is issued.

### 5.3 Typed values and wide integers

Begin with booleans, signed 64-bit integers, unit, records, and only the additional container types actually verified in F0. Public application fields should use explicit domains within those types.

Use a lossless tagged representation on the tools wire. For example:

```json
{"kind":"integer","type":"i64","value":"42"}
```

Here, `i64` is a tools alias mapped to the exact core type in the selected profile, not a new core type definition. All integer values on this wire use canonical decimal strings. Do not mix number and string encoding according to magnitude.

SDK convenience layers accept Python `int` and JavaScript `bigint`; a JavaScript `number` is accepted only when it is a safe integral value inside the declared type/domain. Python booleans must not be accepted as integers merely because of host-language inheritance. [S13] No silent float rounding, string-to-number coercion, integer wrapping, or loss of precision is permitted.

Records carry schema-aware field mappings. Readable names are a user interface, not canonical core identity. Resolve them to exact IDs/fields using the manifest and pinned root.

The separate JSON Schema importer must honor JSON Schema's numerical meaning, including integral decimal representations. That input format is not identical to the tagged tools wire. Section 14 defines the distinction.

### 5.4 Authoring facade

Provide typed builders for the minimum graph constructs needed by the corpus: function signatures, primitive types, constants, parameters, pure operations, branches/returns, records, entrypoints, and tests or test bindings as supported.

The builder must lower to real canonical structures using the core's descriptors. It must resolve logical labels locally, track exact entity references, and return construction diagnostics before submitting impossible payloads.

Support both initial construction and targeted modification. An edit may use a readable function/field selector, but the tools backend must bind it to the exact base root and entity version before constructing a candidate.

Do not replace every program wholesale to avoid implementing modification. Do not bypass mutation preconditions by exporting, editing bytes, and reimporting a different repository.

The facade is not a natural-language compiler. Agents submit structured operations. Python source import is a separate, explicitly restricted frontend.

### 5.5 State and transaction lifecycle

A proposal has an exact base, immutable candidate identity, and separate validation/commit outcomes. A successful validation is not a promise that a later commit will succeed against changed state.

Serialize writes per workspace by default. A stale base is a conflict, not permission to silently rebase or regenerate the candidate. Requerying and proposing again must be an explicit higher-level operation visible in the transcript.

For multi-step flows, especially adding functions and tests under the documented two-commit limitation, report every committed step. Never label the entire flow atomic. Use disposable or isolated working branches where appropriate and expose final readiness separately from intermediate commits.

A rejected candidate must not advance the accepted semantic root. Temporary objects or diagnostic reports may exist if permitted by the core; do not claim the entire filesystem is unchanged.

### 5.6 Retries, cancellation, and uncertain commit outcomes

Reads may be retried when safe. A write whose outcome is unknown must not be recreated with a fresh nonce and retried blindly.

Record a tools operation ID and the candidate/core identities before attempting a commit. After a connection loss, resolve status through supported receipts/state queries. If the result cannot be established, return `ST_COMMIT_OUTCOME_UNKNOWN` with reconciliation instructions and preserve the evidence.

Cancellation distinguishes `cancel_requested`, `cancelled_before_commit`, `completed`, and `outcome_unknown`. Do not report cancellation as rollback of a commit that may already have completed.

Candidate/session identities must remain scoped to their real lifetimes. Never replay an old session handle after restarting the endpoint.

### 5.7 Error contract

Every failure must include a stable tools code, category, useful message, relevant field/entity location when known, and original core code/details when one exists. Redact secrets.

Required categories are usage, input shape, unsupported feature, compatibility, permission, stale state/conflict, semantic validation, execution/domain, resource limit, transport, and internal error.

Use a distinct `ST_` namespace, including at least:

```text
ST_INPUT_INVALID
ST_FEATURE_UNSUPPORTED
ST_CORE_INCOMPATIBLE
ST_PERMISSION_DENIED
ST_STALE_BASE
ST_VALIDATION_FAILED
ST_EXECUTION_FAILED
ST_RESOURCE_LIMIT
ST_IMPORT_UNSUPPORTED
ST_COMMIT_OUTCOME_UNKNOWN
ST_TRANSPORT_FAILED
ST_INTERNAL_ERROR
```

Do not replace core failures with generic prose or invent a core code for a tools-only error. SDK exception classes and MCP results must preserve the same underlying meaning.

CLI exit behavior must be documented and tested. At minimum, distinguish successful execution, a requested check evaluating false, invalid invocation/input, and failed operation. A Rules decision of `allow=false` is a successful computation unless the caller requested check-style exit behavior.

### 5.8 Program manifest and artifacts

Each program has a versioned `program.json` describing:

- Unique program ID, title, purpose, and program version.
- Required tools contract and exact tested core profile.
- Native entrypoint identities and readable names.
- Input/output schemas and accepted domains.
- Artifact paths, checksums, state/program identities where applicable.
- Native logic versus host integration responsibilities.
- Test cases, independent oracle provenance, and worked-change scenario.
- Source/recipe provenance and license status.

Publish a JSON Schema for this manifest. Examples in documentation must validate against it.

Keep the portable core artifact authoritative. A build recipe reconstructs a program through supported APIs; it does not replace an accepted artifact with an alternative textual authority.

Portable packages must be path-safe, bounded, and verified before import. Do not embed local workspace secrets, absolute maintainer paths, private reports, or live credentials. Keep provenance and explanatory material distinct from core authority.

### 5.9 Reproducibility claims

Require the same pinned artifact and inputs to produce the same declared semantic result. Record the core's observation identifiers where provided.

Do not require unrelated fresh authoring runs to have identical workspace IDs, nonces, sessions, transactions, or roots. Their identity inputs may legitimately differ. Byte-reproducible rebuilding can be an additional, explicitly defined profile; it is not established by stripping fields from evidence after the fact.

For export/reimport, compare the exact identities and invariants promised by the core's exchange profile, plus behavior. Do not conflate identical program behavior with identical whole-repository state.

### 5.10 Host/native boundary and operational defaults

Host adapters may read files, parse external formats, frame messages, render results, and operate the UI. Sley must perform the computation advertised as Sley: rule decisions, filtering predicates, calculations, reductions, and generated validation checks.

Start local execution with a small worker limit, preferably two active native jobs, configurable by the user. Do not saturate every CPU by default. Set finite per-request budgets and allow callers to lower them.

No arbitrary shell endpoint, automatic privilege escalation, implicit network calls, or hidden host execution fallback belongs in the shared backend. This is a local developer toolkit, not a remote administrative agent.

### 5.11 Unified CLI surface

The following command families belong to the single `sley-tools` executable. Implement and document a consistent argument grammar; the names below are the target public surface.

| Command family | Purpose |
|---|---|
| `doctor` | Diagnose installed versions, profiles, and optional dependencies. |
| `init` | Create a local project from a supported constructor/template. |
| `inspect` | Read a bounded, exact-version program view. |
| `propose`, `validate`, `commit` | Expose the explicit authoring lifecycle for scripts and advanced users. |
| `run`, `test` | Execute a native entrypoint or declared tests. |
| `artifact export`, `artifact import` | Transfer verified portable artifacts. |
| `pipe` | Run a named or compatible native data pipeline over bounded input. |
| `review` | Compare artifacts and write human/machine reports. |
| `mcp` | Start the local MCP adapter, or explain the missing optional component. |
| `playground` | Start the permitted local browser service; not a public deployment command. |
| `lab list`, `lab start`, `lab check`, `lab reset` | Operate the executable learning exercises. |
| `import schema`, `import python` | Run an explicitly selected restricted importer. |

Common options include an explicit project selection, core selection, limits, and machine output where applicable. Command help must identify which options affect which subsystem. Logs never corrupt the data/protocol stream: `pipe`, `rpc`, and `mcp` have their documented streaming formats and must reject incompatible output-mode combinations.

All documentation command blocks must be executable tests or clearly labeled illustrative templates. Avoid a second naming scheme in MCP docs and a third in SDK docs for the same concept.

### 5.12 Configuration and package selection

Use explicit invocation options over trusted user configuration over documented defaults. Project metadata supplies artifact requirements and project-relative data locations; it must not silently select arbitrary executable paths, inject command arguments, source shell scripts, or widen authority.

An explicit user-level `--core` selection or equivalent trusted configuration chooses the binary. Verify its actual profile. A manifest may request a compatible version, but it does not authorize downloading or executing a different binary.

Keep optional module discovery local and deterministic. An unavailable MCP/SDK/import component produces a setup diagnostic rather than an automatic network install. A user can install that component intentionally from the versioned tools release.

Project-local caches and workspace storage are documented, bounded where relevant, and excluded from version control by default. Commands that remove them require explicit scope; the toolkit never clears unrelated user caches or workspaces.

---

## 6. Project 01: Sley Starter

**Priority:** 1  
**Purpose:** Turn installation into a first successful create–execute–test–modify session.  
**Primary dependency:** F0 authoring and compatibility support.  
**Native demonstration:** Release Readiness, with a smaller range predicate available for diagnostics.

### 6.1 User experience

The intended interface is:

```sh
sley-tools doctor
sley-tools init my-first-program --template release-readiness
sley-tools run --project my-first-program --input my-first-program/inputs/ready.json
sley-tools test --project my-first-program
sley-tools inspect --project my-first-program
```

These commands are implementation targets. The shipped quickstart must be tested against their final syntax, rather than leaving illustrative commands that cannot run.

`doctor` reports what is installed, its identity, whether the selected core is compatible, optional runtimes, and supported profiles. It must not claim a feature works merely because a method name appears in a hello response.

`init` constructs the template through the public authoring path. It may create a project directory and scoped workspace, but refuses to overwrite existing user files by default. It records which native commits occurred.

An offline local template must not trigger a network request. Installation and fetching a core release are separate, visible actions.

### 6.2 Minimum deliverable

Provide an installation guide, a reproducible template constructor, a typed builder interface, a readable inspector, a run command, a behavioral test command, and an executable worked modification.

The worked modification must change real behavior. For Release Readiness, start with a permitted blocking-finding limit of zero and change the limit to a declared nonnegative threshold. Add boundary tests for the new threshold. A later exercise may add a new input, but the first public workflow must remain small.

Show at least one failed proposal and why the accepted version remains usable. Do not use a fabricated invalid byte sequence when a realistic type, precondition, or permission error can demonstrate the boundary.

The builder must handle live context and core codecs internally. A user should not need to copy digests, manufacture candidate envelopes, or understand descriptor tag numbers to finish the quickstart.

### 6.3 Test semantics

The toolkit's `test` command must clearly distinguish:

- Native core test execution and resulting native evidence, where supported.
- External tools assertions that execute a real Sley entrypoint and compare actual outputs to expected values.

Both can be useful. They are not interchangeable, and external assertions must not be described as native test admission. The Starter must demonstrate the supported native test path identified in F0 in addition to its application assertions.

Where native code and newly targeting tests require two commits, the project is not ready until the second step and its checks succeed. Report an incomplete setup honestly if interrupted.

### 6.4 Acceptance

**ST-01:** A clean Linux x86_64 environment completes the quickstart from released tools/core artifacts without the Sley source tree or private assets.

**ST-02:** Template initialization constructs a new program; the transcript proves it did not merely import the existing release-demo fixture.

**ST-03:** Distinct inputs produce the documented distinct results from real execution.

**ST-04:** The worked edit changes the correct boundary behavior, preserves unrelated cases, and produces exact candidate/commit evidence.

**ST-05:** A rejected edit leaves the previously accepted semantic root executable.

**ST-06:** Export/import reproduces the accepted program in a fresh workspace; missing optional runtimes do not break basic commands.

### 6.5 Out of scope

A template marketplace, a universal natural-language compiler, package discovery, and a new canonical source syntax are not required.

---

## 7. Project 02: Sley Bridge

**Priority:** 2  
**Purpose:** Embed Sley computations inside existing applications.  
**Dependencies:** Shared execution/artifact contracts; Starter for constructing the examples.  
**Interfaces:** Python, TypeScript/Node, and the general CLI.

### 7.1 Minimum application interface

Each SDK provides a small application API with these capabilities:

- Open a compatible runtime/backend with explicit lifetime management.
- Load a verified program artifact or open an allowed workspace.
- Resolve a named entrypoint to an exact artifact/root binding.
- Call the entrypoint with typed, domain-checked values and a deadline.
- Obtain a typed result and execution metadata.
- Close the process/session cleanly.

Provide synchronous Python use and an idiomatic asynchronous TypeScript interface. Additional concurrency models are optional until needed by real users.

A representative API shape is:

```text
runtime = open_runtime(core=..., limits=...)
program = runtime.load(artifact=...)
result = program.call("evaluate_release", inputs=..., deadline=...)
```

The exact public names may be refined during the shared-contract freeze. Both SDKs must express equivalent behavior and use the same backend.

### 7.2 Runtime behavior

Keep the backend and core process alive across calls when appropriate. Do not spawn and initialize an entire toolchain for every scalar operation. Record cold start separately from warm execution in benchmarks.

Avoid automatic background daemons and hidden machine-wide caches. A process-scoped persistent connection is sufficient. A user-managed shared service can be considered later.

On a process crash, surface a useful transport error. Reopening a session does not authorize replaying a previous write. Cancellation and timeout behavior follows Section 5.

### 7.3 Value mapping

Implement the lossless integer contract, records, booleans, and the required container subset consistently. Python and JavaScript must not disagree at the safe-integer boundary, boolean/int boundary, signed limits, empty records, or absent fields.

The user-visible native return value is separate from execution metadata. An optional debug result can carry receipt/report identifiers without forcing ordinary application code to understand core internals.

A read-only artifact invocation may still cause the core to persist an execution report. Document actual effects instead of declaring the entire operation storage-free.

### 7.4 Demonstration applications

Ship a minimal Python application and a minimal Node application that call the same Release Readiness artifact. Include at least one wide-integer case using another suitable native program, because a small boolean-only example will not test the integer boundary.

Keep the applications small enough to copy into an existing project. The only business logic in the wrappers should be ordinary presentation and request handling. The release decision must remain in Sley.

### 7.5 Acceptance

**BR-01:** Python and TypeScript call the same native artifact and match its independent expected results.

**BR-02:** Signed-limit and large-integer values remain exact; coercions and out-of-domain inputs fail consistently.

**BR-03:** Repeated calls reuse the intended process/session and do not leak workers or file descriptors in a bounded soak test.

**BR-04:** A deadline, cancellation, endpoint failure, and unsupported artifact produce structured errors, not hangs or false success.

**BR-05:** The application runs without a Sley source checkout, network access, or model credentials after installation.

**BR-06:** Measurement reports distinguish cold startup, encoding, core execution, decoding, and end-to-end time where instrumentation permits. Missing subdivisions are stated, not fabricated.

### 7.6 Out of scope

A stable C ABI, in-process FFI for every language, remote cluster execution, ORM integrations, and arbitrary native effects are not required. Rust and Go bindings can be added after the shared contract proves useful; they are not additional repositories.

---

## 8. Project 03: Sley Agent Connector

**Priority:** 3  
**Purpose:** Let an ordinary coding agent operate Sley through useful typed tools.  
**Dependencies:** Shared authoring/execution backend and version-matched documentation.  
**Default transport:** Local MCP over stdio.

### 8.1 Protocol implementation

Use an official MCP SDK appropriate to the selected implementation language. Pin the SDK and protocol versions in the toolchain lock and test their negotiated behavior. Do not implement a large bespoke MCP stack or assume every client supports every extension.

MCP exposes tools and resource descriptions, and its stdio transport requires protocol-only stdout with logs on stderr. Those contracts are separate from the Sley machine protocol. [S7, S8]

Use the November 25, 2025 MCP specification as a documented initial reference, not an assertion that it is the newest version forever. At implementation, select the version actually supported by the chosen SDK and target clients and record it. Later versions are a compatibility decision.

### 8.2 Tool surface

Begin with approximately eight coherent tools rather than mirroring every low-level core method:

| Tool | Result |
|---|---|
| Inspect program | Root-pinned, bounded structure and relevant contracts. |
| Create program | Typed construction proposal for a permitted workspace/template. |
| Propose edit | Exact candidate built from typed changes. |
| Validate candidate | Real validation outcome and actionable diagnostics. |
| Commit candidate | Authorized commit bound to candidate and base state. |
| Run program | Typed execution result with declared limits. |
| Run tests | Native/external test outcomes, clearly classified. |
| Review change | Exact old/new identities, changes, and evidence links. |

Create can share a schema or implementation with propose where that is cleaner. Keep discoverability and usability ahead of rigid tool count.

Resources include a short Sley mental model, supported builder operations, an example manifest, current capabilities, and a compact error guide. They are generated or drawn from the selected tools/core version, not a stale global prompt.

### 8.3 Permissions and context

Default to explicitly configured workspaces. Enable writes through a startup configuration or host approval mechanism that is visible to the owner; do not give a model unrestricted filesystem scope.

MCP tool annotations describe behavior but do not enforce authorization. Enforcement belongs in the backend and the core. A model cannot widen its privileges by supplying a different role, root, or capability digest in tool arguments.

Native execution may persist reports. Tool annotations and descriptions must reflect that actual effect rather than marking every run operation as purely read-only.

Return concise, relevant state. Do not send an entire specification or repository on every call. If a bounded inspection cannot provide the required information, offer a continuation or a narrower query; do not silently truncate material needed to perform the operation correctly.

### 8.4 Initial client targets

Provide tested setup instructions for the owner's current Claude Code and Codex workflows using the clients' supported MCP configuration. Recheck client syntax during implementation rather than copying unverified configuration snippets.

The toolkit must not depend on either vendor. A reference MCP client must be able to exercise the same tools. Optional live-model acceptance uses existing authorized accounts; no automated purchase or API spending is part of setup.

### 8.5 Agent task

Supply a task prompt that states the requested behavior change and points to the project. It must not include a prebuilt candidate or the final mutation sequence.

The agent must inspect, propose, validate, commit, and verify a change to Release Readiness or the benchmark comparator. Include a stale-base or rejected-change recovery task in the integration suite. A scripted MCP client proves protocol operation; a fresh live agent separately demonstrates onboarding usability.

### 8.6 Acceptance

**AG-01:** A reference MCP client discovers the tools/resources and completes a real native change.

**AG-02:** Stdout contains only valid MCP messages; errors retain tools and core meaning.

**AG-03:** Out-of-scope workspace access and unauthorized writes fail without changing an accepted program.

**AG-04:** The agent receives useful typed inputs and errors rather than needing to manufacture canonical hex.

**AG-05:** The two declared client integrations are actually tested before they are labeled supported. An unavailable client is reported as unverified, not passed.

**AG-06:** At least one fresh live-agent task is completed without developer rescue edits before the public claim “agent onboarding verified.” Record the prompt, supplied context, model/client identity when available, tool calls, and actual outcome.

### 8.7 Out of scope

Agent scheduling, model routing, autonomous marketing, cloud agent hosting, paid APIs, and a new replacement for the user's coding environment are excluded.

---

## 9. Project 04: Sley Rules

**Priority:** 4, implemented with Starter as the first vertical slice  
**Purpose:** Provide useful, embeddable decisions with inspectable behavior and tested changes.  
**Dependencies:** Native authoring and execution; Bridge for embedding examples.

### 9.1 First application: Release Readiness

Implement a deterministic application-level release check. It does not certify a real release or replace the owner's release process; it computes a decision from the supplied facts.

Initial input:

| Field | Domain |
|---|---|
| `test_run_completed` | Boolean |
| `tests_failed` | Integer, 0 through a declared finite maximum |
| `artifact_verified` | Boolean |
| `blocking_findings` | Integer, 0 through a declared finite maximum |

The initial program embeds `max_blocking_findings = 0` as a native constant.

Return `allow` plus stable reason flags or codes for incomplete test run, failed tests, unverified artifact, and excessive blocking findings. Multiple reasons may apply; order them deterministically. A fixed-size record of flags is acceptable if variable-length collections would add unnecessary complexity.

The decision is:

```text
allow = test_run_completed
        AND tests_failed == 0
        AND artifact_verified
        AND blocking_findings <= max_blocking_findings
```

A negative count is invalid input, not a favorable decision. The native contract and/or explicit native checks must enforce the declared domain, with bridge validation providing an additional ergonomic boundary.

The host renders reason descriptions from stable codes. It must not independently recompute `allow` and substitute that result.

### 9.2 Worked changes

The first change alters the embedded blocking threshold and adds below/at/above-boundary tests. The agent must modify the native program rather than alter the input record to fake the new policy.

A second, later change can require an additional verified fact. That exercise must handle input-schema evolution explicitly and show the incompatible old-input result or an intentional migration adapter. Do not silently supply a missing fact as true.

### 9.3 Second application: Resource Admission

Provide a separate pure rule that decides whether a requested bounded job fits an available resource budget. Inputs can include requested/available worker count and memory units. Output is allow/reject with distinct insufficient-resource reasons.

The program consumes supplied measurements; it does not inspect the host or launch work. Host collection and scheduling are outside the native computation.

This gives users a second application with different behavior without turning Rules into a general policy language or workflow engine.

### 9.4 Required properties

Define exhaustive truth-table cases for Release Readiness's boolean dimensions and boundary cases for count fields. Verify that increasing blocking findings cannot turn a rejection into an approval for an unchanged nonnegative threshold.

For Resource Admission, verify exact-fit, insufficient capacity, invalid negative values, and arithmetic boundaries. Avoid summing or multiplying resources unless needed by the defined contract.

### 9.5 Acceptance

**RU-01:** Both applications execute their actual decision logic in Sley.

**RU-02:** Independent expected results cover combinations, boundaries, invalid domains, and multiple simultaneous reasons.

**RU-03:** The threshold change produces a real changed native artifact and the intended behavioral difference only.

**RU-04:** At least one application is embedded through both SDKs, and its semantic results agree.

**RU-05:** Machine output, human explanation, and check-style exit codes remain consistent without moving business logic into wrappers.

**RU-06:** The README explicitly separates this application engine from Sley's kernel authorization policy.

---

## 10. Project 05: Sley Data Pipe

**Priority:** 5  
**Purpose:** Run a useful, bounded structured-data pipeline from the terminal.  
**Dependencies:** Shared typed execution; corpus programs for comparison, transformation, and reduction.

### 10.1 Initial use case

Process benchmark records into exact comparisons and an aggregate summary. This is a generic utility; it must not hardcode private Greyforge benchmark files or assert a ZJX performance result.

The first supported input is JSON Lines with a documented fixed schema. A comparison row contains a case identifier and nonnegative integer measurements for baseline and candidate wall time, total CPU time, and archive size, in fixed documented units.

Default suggested units are microseconds and bytes. Set explicit maxima that leave room for any required arithmetic. Missing records, duplicate case identifiers, unit mismatches, invalid numeric domains, and malformed input must be diagnosed.

### 10.2 Native responsibilities

Provide native programs for:

1. Comparing a baseline/candidate row, returning exact deltas and per-metric regression flags.
2. Selecting and projecting records according to an explicit predicate, such as any regression.
3. Reducing counts and bounded integer totals across a batch, and combining partial summaries through a native reducer.

The host may decode JSON, match explicitly identified records, divide input into bounded batches, pass records to native code, and serialize outputs. It must not perform the advertised filtering, comparison, or reduction in Python/JavaScript/Rust and describe the result as native.

Default comparison is exact. Optional tolerances require an explicit versioned configuration and native decision logic; do not quietly introduce floating-point epsilon or treat aggregate improvement as proof that every row improved.

### 10.3 Pipeline scope

Start with named shipped pipelines rather than a custom query language. Users can supply a compatible native artifact as an advanced extension once the artifact contract is ready.

Support filter, projection, and bounded reduction. Joins, arbitrary expressions, remote connectors, infinite stream processing, and a replacement for `jq` are outside the first version.

Do not require native strings just to carry an opaque case identifier. The host can preserve a row identifier while native logic processes the declared typed measurement fields, provided this mapping is exact and visible.

### 10.4 Streaming and failure behavior

Set configurable record, line-size, input-byte, batch-size, and memory limits. The implementation should process a large file without loading it all into memory. Document which reductions maintain bounded state and which requested operations are unsupported.

Preserve input order unless the user explicitly selects an ordering operation. Empty input returns a documented empty summary rather than crashing or inventing records.

For an output file, write to a temporary file and publish the final file only on success. For stdout, partial output is possible; signal failure with a nonzero exit and an explicit diagnostic including the number of emitted records. Do not represent a partially emitted stream as a successful complete result.

The pipe command uses stdout for data. Diagnostics and a final optional machine report go to stderr or an explicit report file. The general CLI JSON envelope must not be interleaved into the data stream.

### 10.5 Numerical behavior

Use signed/unsigned domains only as verified by the chosen core profile. The initial toolkit wire uses explicit integer types and never floats.

For signed deltas, ensure both operands and the subtraction fit the native type or detect overflow. A zero baseline is a valid exact-comparison case; any optional percentage calculation must define its zero-denominator behavior and rounding separately.

Prefer exact deltas and flags in the first version over implementing unnecessary ratio arithmetic. If totals can overflow, return a structured failure rather than wrapping or switching to host arithmetic unnoticed.

### 10.6 Acceptance

**DP-01:** A published sample input produces the independently specified output through real native comparison, selection, and reduction.

**DP-02:** Different batch partitions produce the same defined summary and output order.

**DP-03:** Empty input, duplicates, missing fields, malformed lines, zero baselines, signed boundaries, and overflow have documented outcomes.

**DP-04:** A bounded large-input test shows memory does not grow in proportion to the whole file for the shipped streaming pipeline.

**DP-05:** File-output failure does not replace a valid existing output; stdout failure reports partial emission correctly.

**DP-06:** A user can change the selection predicate in Sley, rerun the tests, and use the new artifact without changing the host parsing wrapper.

## 11. Project 06: Sley Review

**Priority:** 6  
**Purpose:** Make native artifact changes reviewable and testable through ordinary Git/GitHub workflows.  
**Dependencies:** Artifact and manifest contracts; shared inspection and core comparison support.

### 11.1 Git representation

Track the portable native artifact, its manifest, small behavioral fixtures, and a readable generated review view. Choose a compact, diff-friendly export representation where the core permits it, but do not create an alternative canonical text source.

Git stores the artifacts and contribution history. Sley identities identify the native program state. A Git commit SHA and a Sley state root answer different questions and must both be recorded where relevant.

Do not install global Git filters or modify the user's global Git configuration automatically. A repository-local optional text conversion/view helper may be offered explicitly, but the normal review path must work without it.

### 11.2 Review report

A report contains:

| Section | Required content |
|---|---|
| Identity | Old/new artifact checksums, core profile, relevant roots/entrypoints, tools revision. |
| Structural change | Added, removed, and changed entities or supported semantic units. |
| Interface change | Input/output type and domain differences, renamed labels, entrypoint changes. |
| Behavioral evidence | Executed before/after cases and observed differences; not an inferred proof of equivalence. |
| Validation/test status | Core validation result, native tests, external assertions, and any skipped work. |
| Integrity | Whether the tracked artifact matches its manifest and the displayed report. |
| Limitations | Unsupported comparison regions, incomplete inspection, or unexecuted tests. |

Use readable names while retaining exact identities. When the core cannot establish a semantic match between entities, show that uncertainty rather than confidently describing a deletion/addition pair as a rename.

Core semantic comparison can inform the report, but a structural diff alone does not establish behavioral equivalence. A finite test suite does not prove equivalence for all inputs.

### 11.3 CI integration

Keep the reusable Action inside this repository, with a usage form such as:

```yaml
# Replace the placeholder with a reviewed immutable tools commit.
uses: sley-lang/tools/actions/check@<full-commit-sha>
```

This is a documentation template, not runnable YAML until the placeholder is replaced. The release process must generate tested examples using the real pinned commit.

The action validates the package, checks manifest/artifact consistency, executes the required tests on the selected core, and uploads a readable report. It can write a job summary without needing permission to post PR comments.

Default to minimal read permissions and no secrets for untrusted pull-request validation. Do not execute untrusted changes in a privileged release context. GitHub's current guidance warns about privileged triggers combined with untrusted checkout and recommends least privilege and immutable action references. [S9]

A PR-comment feature is optional and must use a separately authorized, narrow path. It is not necessary for the initial product.

### 11.4 Trust boundaries

Program artifacts and contributed fixtures are untrusted input to validation. Authoring recipes are host programs and therefore also need an appropriate unprivileged CI environment when run; do not assume that a Sley artifact's validation makes an accompanying Python build script safe to execute.

Recompute the report from the submitted artifact. Do not accept a contributor's `PASS` field or prewritten Markdown as validation evidence.

Bound inspection and rendering. The on-screen summary can be abbreviated, but the full report and an explicit completeness state must be available. An incomplete required validation cannot be marked successful because the visible summary looks reasonable.

### 11.5 Acceptance

**RV-01:** A normal artifact-change pull request can be checked without secrets, organization administration, or installing a custom Git client.

**RV-02:** The report names exact old/new artifacts and displays the known behavior change from the Rules exercise.

**RV-03:** A stale manifest, altered artifact, falsified result file, or mismatched report fails integrity verification.

**RV-04:** Unsupported comparison information is clearly labeled; no inferred rename or equivalence is falsely presented as established fact.

**RV-05:** A second clean environment reproduces the selected artifact's tests using the report's pinned inputs.

**RV-06:** CI failure preserves the useful report and diagnostics without publishing secrets or granting write privileges to untrusted code.

---

## 12. Project 07: Sley Playground

**Priority:** 7  
**Purpose:** Let a visitor run, inspect, and change a real Sley program before installing it.  
**Dependencies:** Shared backend, working examples, authoring, execution, and review contracts.

### 12.1 Initial delivery modes

Implement two clearly distinguished modes:

**Local mode:** A user starts a loopback-only service and opens the browser UI. It uses permitted local workspaces and requires no external account or paid service.

**Hosted-ready mode:** The same UI uses isolated disposable server-side workspaces. This mode must pass its isolation and resource tests before any public deployment. Building it does not authorize deployment or operational spending.

Do not require WebAssembly or browser-native Sley execution to deliver the first version. Do not claim computation ran in the browser if it actually ran on a server.

### 12.2 Minimum interface

Provide an example selector, short explanation, typed input editor, run result, relevant function/graph inspector, guided change interface, test results, before/after review, and export control.

Start with Release Readiness, the benchmark comparator, and a small range/clamp example. Users must be able to complete a meaningful change without a model API key.

The default experience is not a blank source editor. Expose functions, fields, constants, conditions, tests, and explicit changes. An advanced structured-request panel is acceptable, but opaque hex should not be the normal workflow.

Results must distinguish an input problem, rejected change, runtime failure, incomplete operation, and successful computation returning false.

### 12.3 Change and export flow

A guided edit creates a proposal and shows its validation result before commit. A successful commit updates the displayed root. The user can compare it with the previous accepted version and export the actual artifact and manifest.

A fresh local tools installation must import that export and reproduce the behavior. Exported packages must not contain hosted workspace credentials, user tokens, or unnecessary server paths.

For sharing, initially support links to immutable published examples identified by program version or artifact identity. Arbitrary user-created shared workspaces, accounts, and persistent cloud storage are outside the first version.

### 12.4 Resource isolation

Local service defaults to loopback binding and validates browser origin/host expectations. It must not become an unauthenticated LAN administration endpoint merely because a web UI exists.

Hosted workers have per-session workspace isolation, bounded input/output, finite CPU/wall/memory budgets, cleanup, and a fixed operation allowlist. Do not mount maintainers' repositories, SSH material, or a privileged container socket into workers. Network access from computation workers is disabled unless a future explicit feature requires it.

The chosen sandbox must be evaluated for the deployment threat model. Sley validation alone is not evidence that the host process, parsers, or web service are secure. A container is an implementation option, not a security proof.

Default hosted features are restricted to the bundled examples and typed mutations. Arbitrary Python imports and arbitrary package uploads are disabled in the first public profile.

### 12.5 Usability and privacy

The UI must support keyboard navigation, readable errors, copyable machine results, and a useful narrow-screen layout. Do not build accounts, analytics infrastructure, or a chat product to finish the first playground.

No third-party telemetry or model call occurs by default. Any optional telemetry is disclosed and aggregate; program contents and credentials are not collected silently.

The browser must not claim success until the backend returns the actual outcome. A timeout after commit uses the same reconciliation state as the SDK rather than optimistically resetting the form.

### 12.6 Acceptance

**PG-01:** A browser user can run and modify a bundled native program without a model, account, or local compiler.

**PG-02:** The UI displays the exact executed/committed version and the real error category.

**PG-03:** An exported changed artifact runs identically in a fresh local tools environment.

**PG-04:** Two sessions cannot access or alter each other's workspaces; resource exhaustion is contained to the configured worker scope.

**PG-05:** Disconnection and timeout do not produce a false “unchanged” or “rolled back” claim after a possible commit.

**PG-06:** Local operation is fully usable. Public-hosting readiness and actual public deployment are recorded as separate statuses.

---

## 13. Project 08: Sley Repair Lab

**Priority:** 8  
**Purpose:** Teach humans and agents how to create and repair programs through executable tasks.  
**Dependencies:** Starter, real execution/testing, and concise version-matched help.

### 13.1 Initial curriculum

Ship eight small exercises with distinct lessons:

| Exercise | Task | Required evidence |
|---|---|---|
| RL-01 Create | Construct a simple range predicate from a stated contract. | New artifact and passing behavior tests. |
| RL-02 Boundary | Repair an inclusive/exclusive threshold error. | Below/at/above cases distinguish old and new behavior. |
| RL-03 Branch | Fix a classification branch without changing unrelated categories. | Multiple branches remain correct. |
| RL-04 Tests | Add a missing boundary test using the supported native/external paths. | The relevant flawed version fails the new case. |
| RL-05 Type | Diagnose a structured type mismatch. | Corrected proposal plus preserved failure diagnostic. |
| RL-06 Stale state | Recover from an edit whose base is no longer current. | Explicit reinspection and a new proposal, not silent overwrite. |
| RL-07 Authority | Explain and resolve an insufficient local grant through the legitimate setup path. | No invented credential or permission bypass. |
| RL-08 Portability | Export, import, and verify a changed program in another workspace. | Manifest and execution evidence from both sides. |

The authority exercise uses disposable owner-controlled fixtures and established policy APIs. It is an onboarding lesson, not a mechanism for bypassing another workspace's policy.

### 13.2 Exercise structure

Each exercise contains a starting artifact or construction task, a short behavioral goal, public examples, a deterministic judge, versioned expected properties, optional hints, and a worked resolution.

Use the same native programs and builder where appropriate. Do not build a second exercise-only compiler or grant special runtime privileges just to make the lab easier.

A judge must check actual outputs and required change properties. Kernel validity alone does not satisfy the exercise. Avoid judges that accept hardcoded answers to only the visible examples.

### 13.3 Public learning versus evaluation

Worked solutions and public fixtures belong in the public corpus. Their purpose is learning.

Keep genuinely held-out adoption-evaluation tasks in a separate, owner-controlled evaluation location or local private artifact set. Do not commit them to this public repository and then describe them as uncontaminated hidden tests. Private hidden checks must not be required for outside contributors to run ordinary public CI.

A public judge may include cases not shown in the first tutorial screen; once the source is public, those cases are not secret. Describe them accurately.

### 13.4 Model conversation intake

A useful Grok or other model interaction can become an exercise only after the failure and correction are reproduced locally. Preserve the task, the relevant mistaken assumption, the actual diagnostic, and the verified resolution.

Do not publish private prompts, account information, or copied third-party content without appropriate permission. Do not label a model's unexecuted proposal as a working solution.

The toolkit should make publication of a clean lesson straightforward, but it does not automate posting or claim that the material will enter future model training.

### 13.5 Acceptance

**RL-AC-01:** Every exercise runs from a clean install against its declared core profile.

**RL-AC-02:** Each intended wrong solution is rejected by behavioral or required-process checks, not merely by prose review.

**RL-AC-03:** Every worked solution has a saved real execution result and produces the specified final behavior.

**RL-AC-04:** Hints and references are version-matched and sufficient for a fresh learner/agent without a maintainer-only document.

**RL-AC-05:** Public learning materials and private evaluation are clearly separated.

**RL-AC-06:** Resetting an exercise is confined to its disposable workspace and does not delete unrelated user work.

---

## 14. Project 09: Schema Importer

**Priority:** 9  
**Purpose:** Convert a useful, precisely defined subset of existing JSON Schema contracts into real Sley validation programs.  
**Dependencies:** Typed builder, exact data decoding, native checks, artifact packaging, and independent reference validation.

### 14.1 Supported profile: `json-schema-records-v1`

Target JSON Schema draft 2020-12 with an explicitly declared supported subset. Do not claim general JSON Schema support.

The required initial subset consists of:

- Root schemas with `type: "boolean"`, or bounded integer schemas.
- Flat object schemas with explicitly listed properties.
- Property types restricted to booleans and bounded integers.
- Every property explicitly required through `required`.
- `additionalProperties: false` explicitly present for object schemas.
- Integer minimum and maximum bounds explicitly present, integral, ordered, and representable in the selected native integer type.
- A small declared annotation allowlist, such as `title`, `description`, `$id`, and the selected `$schema` identifier.

The special literal schemas `true` and `false` are not required in v1; a schema object declaring `type: "boolean"` is supported. State this distinction in the CLI help.

Reject optional properties, arrays, nested objects, references, unions, conditionals, patterns, formats, unevaluated-property rules, open objects, and other validation keywords in v1. The importer may support additional features later only through a versioned profile and tests.

Requiring closed/all-required records is a limit on the accepted **schema subset**, not permission to reinterpret the defaults of arbitrary JSON Schema. A schema that omits `required` must be rejected as outside the profile, not silently treated as requiring every field.

### 14.2 Compilation result

Produce a typed native payload representation, a native validator entrypoint, reason codes, a manifest, public boundary tests, and a mapping from schema paths to generated entities/checks.

The host parser may decode external JSON and mechanically project it into a typed validation envelope. The Sley program must decide the advertised validation rules: requiredness, allowed property types, unknown-property rejection, and declared numeric bounds.

Do not call a host JSON Schema library, return its result through a Sley stub, and call that a native validator. The reference library belongs in tests, not the implementation of the advertised native decision.

### 14.3 Raw-input validation without impossible typed inputs

Malformed or wrong-typed input cannot always be represented as an ordinary valid typed payload. Therefore distinguish:

**Parsed JSON envelope:** Mechanical facts from an exact parser, including the root kind, property presence, each supported property's raw kind, extra-property count, whether a numeric value is mathematically integral, and whether an integral value fits the native representation.

**Native validation result:** The generated Sley program applies the accepted schema's rules to those facts and available values. It returns valid/invalid and stable reason codes. A native typed payload may be exposed only after validation succeeds.

The parser adapter must be schema-mechanical rather than an independent schema-rule evaluator. Its classification and projection logic is small, shared, separately tested, and disclosed in the manifest's host/native responsibility statement.

Input outside a parser resource limit is a transport/resource failure, not an invented schema-invalid result. Malformed JSON is a parse failure. A well-formed value violating the supported schema is a successful validator invocation returning invalid.

### 14.4 Exact numerical semantics

JSON Schema considers `1` and `1.0` the same integral value for the integer type. It also permits numerical representations beyond a particular host's floating-point precision. Do not decide integrality by a binary float parse or by whether the token contains a decimal point. [S10]

Use exact decimal/rational token handling sufficient to classify integral values and range membership under explicit resource bounds. Inputs such as `1.0` and `1e0` must be handled consistently with the selected reference semantics. A nonintegral value such as `1.5` must not be rounded into acceptance.

A mathematically integral value outside native range is represented in the validation envelope as outside the native range and rejected by the native validator, since accepted schemas have bounds within that range. Do not overflow while decoding it.

JSON booleans are not numeric values. Duplicate object keys are rejected by the external input profile before schema evaluation because the importer does not promise an interpretation for ambiguous member occurrences.

### 14.5 Schema diagnostics

Unknown validation keywords fail closed with a schema JSON Pointer and a precise unsupported-feature explanation. Recognized non-validation annotations may be preserved without affecting validation, but the allowlist is explicit.

Property order in a schema object must not alter validation meaning. Create a stable property/entity mapping for generated artifacts. Do not claim independently generated roots must match unless the selected reproducible-build profile actually ensures the identity inputs match.

JSON Schema's `properties`, `required`, and `additionalProperties` have separate meanings; their mappings must preserve that separation. [S11]

### 14.6 Reference verification

Test the generated native validator against an independently selected draft-2020-12 validator configured with exact numeric handling. A reference that already lost precision during JSON decoding is not a valid oracle for wide-number tests.

Required cases include correct payloads, missing fields, extra fields, wrong root type, boolean-as-integer rejection, fractional numbers, integral decimal/exponent forms, both numeric bounds, values outside representation range, empty records, and annotation-only schema changes.

Keep implementation and oracle code separate enough that one mapping bug does not define both answers. Record the reference validator version and decoder configuration.

### 14.7 Acceptance

**SI-01:** An accepted schema generates a real native artifact and a readable mapping report.

**SI-02:** Native validation agrees with the independent reference on the supported subset's required cases.

**SI-03:** Unsupported schemas fail at import with exact locations; no constraint is silently ignored or strengthened.

**SI-04:** `1`, `1.0`, and `1e0` are equivalent for an integer constraint; `1.5`, `true`, and `"1"` are not silently coerced.

**SI-05:** A wide or malformed input cannot crash the decoder or be accepted through truncation/rounding.

**SI-06:** A user can inspect and modify the generated native validator through the normal tools workflow without requiring reimport from the original schema.

### 14.8 Out of scope

OpenAPI generation, complete JSON Schema, arbitrary regular expressions, network reference fetching, nested recursive models, and schema registries are excluded from v1.

---

## 15. Project 10: Python Function Importer

**Priority:** 10  
**Purpose:** Convert a restricted pure Python function into an independently executable, editable Sley artifact.  
**Dependencies:** Typed builder, numeric-domain checking, pure control flow, tests, and manifests.

### 15.1 Profile name and claim

Name the initial profile `python-pure-i64-v1`. User-facing descriptions must say “restricted pure Python functions,” not “Python compatibility” or “convert any Python application.”

Input is Python source plus an explicit typed domain manifest. Output is a real native artifact, generated tests, diagnostics, and a source-span-to-entity mapping.

The imported artifact must execute without Python. Python can be required for importing or running a reference test, but not for the imported computation itself.

### 15.2 Supported syntax

The required initial subset is:

- One ordinary synchronous function with explicitly named parameters.
- Simple `int` and `bool` annotations, interpreted by this profile without evaluating annotation expressions.
- Integer and boolean literals.
- Parameters and local single-name bindings with statically inferable supported types.
- Addition, subtraction, multiplication, and unary negation only when all relevant intermediate results are proven to fit the profile's integer type over the accepted domain.
- Integer/boolean equality and appropriate ordered integer comparisons.
- `if`/`else`, conditional returns, and conditional expressions.
- Boolean `and`, `or`, and `not` only when operands are statically boolean and short-circuit meaning is preserved.
- A return on every reachable path with one compatible result type.

Comments and a function docstring are allowed as non-executable metadata. The agent may initially reject local reassignment or other awkward constructs conservatively, provided the profile and tests identify that restriction. Required representative functions must still be expressible.

Reject imports, function calls, attributes, subscripts, arbitrary containers, comprehensions, loops, recursion, classes, decorators, defaults with executable expressions, generators, async constructs, exceptions, reflection, `eval`, `exec`, and side effects. Reject all additional top-level executable statements.

Division, floor division, modulo, exponentiation, floating point, arbitrary truthiness, implicit numeric coercion, and unbounded Python integers are outside v1. Add them only with explicit semantic work, not by mapping similarly named native opcodes.

### 15.3 Parsing without executing

Parse the source into an AST using a pinned parser. Python's `ast` API provides parsing and source-location facilities; it is not a mandate to compile or execute the input. [S12]

The import path must not import the submitted module, execute annotations, evaluate decorators, or execute arbitrary source to discover behavior. AST processing has finite source-size, node-count, and nesting limits, and runs with bounded resources.

Reference execution in tests is a separate, explicit operation in an isolated environment. It must not happen implicitly when a user imports untrusted source.

### 15.4 Numeric and boolean semantics

Python integers are not inherently limited to 64 bits; this importer deliberately accepts a bounded domain. [S13] The domain manifest declares each integer parameter's range. Reject missing, inconsistent, or out-of-representation domains.

Use conservative range analysis or another justified static method to establish that every executed arithmetic intermediate fits. When the importer cannot establish that property, reject the function as unsupported rather than accepting a program that wraps or diverges from Python outside the sampled tests.

For example, a subtraction whose inputs each fit i64 can still overflow i64. Checking input types alone is insufficient. Branch-sensitive reasoning is permitted, but not required to accept every actually safe function; a conservative refusal is valid and should be actionable.

The exported native program must enforce its declared input domain, not rely solely on the original Python caller behaving correctly. Out-of-domain invocation is an explicit error, not evidence of full Python equivalence.

Boolean `and` and `or` return operands in unrestricted Python. [S13] Restricting this profile to statically boolean operands must be deliberate. Preserve short-circuit control flow so an unselected branch is not executed accidentally.

### 15.5 Lowering and subsequent editing

Lower accepted source into native types, operations, blocks, and returns through the shared authoring builder. Bind source spans for review and diagnostics, not as canonical identity.

The resulting artifact is now a Sley program. Native edits need not preserve a perfect Python round trip. Do not overwrite those edits during a later import without an explicit new version or merge workflow.

Avoid embedding a Python interpreter, a host callback, or the source text as the actual computation. The runtime dependency must be the declared Sley artifact/core profile.

### 15.6 Required example functions

Translate and publish at least these representative behaviors:

- A bounded range predicate, including below/at/above limits.
- A clamp function expressed through conditional returns.
- A quota-remaining function with nonnegative bounded inputs and explicit exhausted behavior.
- A small tier classifier with multiple ordered conditions.

At least one example uses an arithmetic operation whose intermediate safety requires more than checking the final output type. Include a near-identical out-of-profile example that is correctly rejected.

These examples may populate the native program catalog. Do not count the Python original and the Sley translation as two native programs.

### 15.7 Verification and claims

For each accepted example, compare the native artifact with an independently executed Python reference on all boundaries and representative interior cases. Exhaustively test small finite domains when practical. Use fixed seeds for generated tests and record them.

Finite differential tests demonstrate agreement on tested inputs. The bounded arithmetic analysis establishes its stated arithmetic property only. Neither should be described as a proof that the importer correctly translates all possible Python programs.

Report the supported syntax, input domains, and any conservative rejections prominently. A successfully translated sample is not a reason to broaden the advertised profile automatically.

### 15.8 Acceptance

**PI-01:** All required representative functions import and execute as native Sley artifacts with no Python runtime involved in their later execution.

**PI-02:** Required boundary/interior tests agree with the Python reference over the declared input domains.

**PI-03:** Unsupported syntax, missing domains, potential intermediate overflow, and nonboolean truthiness fail before publication of an accepted artifact.

**PI-04:** Import never executes submitted source; explicit reference tests are isolated and separately recorded.

**PI-05:** Short-circuiting, ordered branches, and return types match the accepted profile's meaning.

**PI-06:** A subsequent native edit can be made, tested, reviewed, and exported through the normal tools without rerunning the importer.

## 16. Native program corpus and the path to 25

### 16.1 Separate tool count from program count

The ten projects are adoption tools and applications. Their implementation does not automatically produce ten independent native programs, and a Python wrapper plus a TypeScript wrapper does not count as two Sley programs.

Maintain `programs/catalog.json` as the single program inventory. Each entry identifies a distinct executable responsibility, its artifact, its entrypoint, its integration examples, its verified behavior, and its worked modification.

A small pure function can count as a native utility if it has a useful specified responsibility and is independently runnable. Label it as a utility, not a complete end-user application. A hidden helper, a copied threshold with a different name, or the same program exposed through another adapter does not add to the count.

### 16.2 Initial native seed set

The following ten seeds are required across the complete project, not before the first useful release:

| ID | Program | Distinct responsibility | Initial route |
|---|---|---|---|
| N01 | Release Readiness | Evaluate multiple release facts and produce an allow result with reasons. | Starter + Rules |
| N02 | Resource Admission | Decide whether a requested job fits supplied capacity constraints. | Rules |
| N03 | Benchmark Comparator | Compare one matched measurement row and report exact deltas/regression flags. | Data Pipe |
| N04 | Record Selector/Projector | Select and project bounded typed records using a native predicate. | Data Pipe |
| N05 | Benchmark Summary Reducer | Combine typed partial summaries with defined identity and overflow behavior. | Data Pipe |
| N06 | Tier Classifier | Map an integer domain into ordered categories with explicit boundaries. | Repair Lab / Python importer |
| N07 | Range Predicate | Decide membership in an explicitly bounded interval. | Starter / Repair Lab / Python importer |
| N08 | Bounded Clamp | Clamp a value into a valid interval with defined invalid-bound behavior. | Python importer |
| N09 | Quota Remaining | Compute nonnegative remaining capacity and exhausted state without arithmetic ambiguity. | Python importer |
| N10 | Generated Payload Validator | Validate an external record under the declared JSON Schema subset. | Schema importer |

N04 and N05 must be independently callable and tested to count separately. If the implementation combines them into one artifact with one inseparable responsibility, record the honest lower count and add a genuinely distinct utility later. Do not split modules solely to inflate the catalog.

### 16.3 Minimum behavioral contracts

**N03:** Compare candidate and baseline values in the same declared units. Equality is not a regression. A regression in one metric remains visible even when other metrics improve. Define invalid negative or missing measurements explicitly.

**N04:** Preserve relative order of selected rows. Verify no selected row is duplicated or omitted, and projection fields match their source values. Changing a native predicate must change selection without modifying host parsing.

**N05:** Define an empty identity summary. Combining valid summaries must match directly summarizing their concatenated records within the supported arithmetic domain. Out-of-domain totals must be an error, not a wrapped result. Tests distinguish an empty input from an input containing zero-valued measurements.

**N06:** Every permitted input maps to exactly one category. Adjacent thresholds are ordered; invalid configuration is rejected. Editing one threshold must leave unrelated categories unchanged.

**N07:** Specify inclusion/exclusion of each endpoint and invalid reversed bounds. The worked correction must exercise the relevant boundary.

**N08:** Valid bounds satisfy `lower <= upper`. Within-domain outputs satisfy the interval and leave already-in-range inputs unchanged. A reversed interval is invalid rather than silently swapped.

**N09:** For declared nonnegative `limit` and `used`, return zero/exhausted when use meets or exceeds the limit; otherwise return the exact difference. Do not subtract first and then depend on unsigned underflow behavior.

N01, N02, and N10 follow their project-specific contracts. Domains and result types are explicit in each manifest before code is considered complete.

### 16.4 Per-program publication package

Each catalog entry must include:

1. A short task description and why the program is useful.
2. The accepted native artifact and exact tested core/tools profile.
3. A reproducible authoring or import recipe through supported interfaces.
4. A runnable command and at least one integration example where appropriate.
5. Public positive, negative, and boundary tests with independently justified expected values.
6. A verified worked modification, including the before/after behavior and relevant receipts/reports.
7. A host/native responsibility statement and known limits.
8. A license/provenance record suitable for the owner's publication decision.

These can live in one program README plus structured files. Do not create separate narrative documents for every field.

### 16.5 Admission of programs 11–25

Do not commission the remaining fifteen merely to meet a number. Admit the next program when it addresses a distinct useful task or teaches a materially new capability, fits the available native execution profile, and has an independent correctness oracle.

Prefer requests discovered while using the tools: configuration validation, scheduling decisions, structured transformations, version comparison, deterministic scoring, or additional bounded algorithms. These are candidate directions, not requirements that outrank the ten defined projects.

Every addition follows the same package and truthfulness rules. Public corpus quality and user success take precedence over posting frequency or raw artifact count.

---

## 17. Verification, performance, and evidence

### 17.1 Verification layers

Use a small dependency-aware suite:

| Layer | Scope | When |
|---|---|---|
| Unit | Pure tools logic, manifests, mappings, codecs at adapter boundaries. | Every relevant change. |
| Core compatibility | Real selected-core probes and required operations. | Backend/core profile changes. |
| Program behavior | Independent oracles and boundary/property tests. | Relevant program changes. |
| Adapter integration | CLI, both SDKs, MCP, and browser routes to real artifacts. | Relevant adapter changes. |
| Fresh-install acceptance | Packaged tools/core without source-tree assumptions. | Release candidates. |
| Live-agent onboarding | A small fresh task with no rescue edits. | Before supported onboarding claims; not every commit. |
| Deployment isolation | Local browser exposure and hosted-worker boundaries. | Before enabling the relevant deployment profile. |

Use existing core tests for core changes. Do not rerun the entire core release/reproducibility campaign for a documentation-only tools change. Run relevant focused tests during development, then a complete tools integration check before release.

No compulsory multi-council review process, enormous new benchmark gauntlet, or repeated ceremonial evidence generation is required. The evidence must be sufficient to substantiate the claim, not a substitute for delivering a working program.

### 17.2 Independent oracles

For simple programs, expected results can be derived directly from the stated contract and encoded in an independent test implementation. For importers, use a reference validator/interpreter path separate from the lowering implementation.

Do not generate expected values by running the same potentially wrong Sley program once and calling the outputs golden truth. Golden vectors can preserve behavior only after their expected meaning has been independently established.

Test domains include ordinary values, every declared boundary, wrong types, absent values, and meaningful multi-condition combinations. Use exhaustive testing for small domains and fixed-seed generated cases for larger ones when useful. There is no universal minimum test count that proves correctness.

### 17.3 Cross-adapter equivalence

Use one shared set of input/result fixtures for CLI, Python, TypeScript, MCP, and browser adapters. Compare typed semantic values and relevant error categories, not presentation whitespace or volatile session IDs.

The same artifact/input combination must not change meaning because one client treats a large integer as a floating-point number or one UI supplies an omitted field by default.

Test lossless conversion separately from native execution, then test their integration.

### 17.4 Required cross-cutting failures

The integration suite must exercise:

- Incompatible core/protocol/schema profile.
- Wrong typed input, large integers, unsupported import constructs, and malformed external data.
- Stale base, insufficient authority, expired or unusable local context, and mismatched candidate bindings.
- Invalid proposal and refusal without advancing the accepted program root.
- Timeout, cancelled work, process death, and uncertain commit reconciliation.
- Corrupt artifact or mismatched manifest.
- Bounded input/output exhaustion and incomplete response handling.
- Interrupted multi-step construction/test workflow.

These are local defensive regression cases. Use disposable workspaces and synthetic inputs; no third-party target or intrusive external testing is involved.

### 17.5 Evidence format

Generate an evidence report for each integration/release run. At minimum it identifies:

```text
report_contract
run_id
started_at / finished_at
status: PASS | FAIL | BLOCKED | NOT_RUN
source: tools_commit, dirty_state, dependency_lock_digests
core: release, commit_if_known, executable_sha256, protocol, schema_epoch
environment: platform, architecture, runtime_versions, configured_limits
artifacts: program_ids, artifact_digests, relevant_roots
checks: test_id, status, command_or_operation, observed_result, evidence_path
dependencies: required_core_changes, missing_optional_runtimes
claims: verified_features, unverified_features, known_limitations
measurements: actual timings/token counts when available
```

Use an implemented JSON Schema, not free-form assertions. Missing measurements are null/unavailable with a reason, not zero. Do not initialize planning files with `PASS`.

Record the actual command/operation and outputs needed to reproduce a finding. Do not include secrets, unnecessary host identity, private prompts, or huge redundant logs.

Generated development artifacts live in an ignored local output directory. Check in concise stable fixtures and milestone summaries; attach larger sanitized evidence to release artifacts as appropriate. Avoid turning routine evidence churn into repository noise.

### 17.6 Performance measurements

Measure cost before optimizing it. For Bridge and Data Pipe, report cold startup, warm end-to-end latency, throughput for a declared batch size, resource limits, and observed peak memory where available.

Compare performance only against equivalent work, inputs, correctness checks, and timing boundaries. Keep native execution time separate from toolkit startup/serialization when instrumentation supports that distinction.

Use repeated runs and report sample counts. Report medians and tails only with enough observations to make the calculation meaningful; avoid confident speed claims from one run. Do not claim that a local result establishes general superiority over Python, Rust, or Go.

A correctness gate is mandatory. Speed is initially observational except for basic operational requirements: no unbounded hangs, no full-file memory growth in the specified streaming mode, no one-process-per-scalar-call implementation, and no uncontrolled worker proliferation.

After recording a baseline, prevent meaningful regressions in the same environment with a documented tolerance and explanation. Do not invent a universal latency promise before measurements exist.

### 17.7 Onboarding measurements

Track time to first successful program after dependencies are available, number of manual rescue steps, number of failed proposals, documentation consulted, and successful completion of the requested behavior.

For agent tasks, record model/client versions where available, exact task text, supplied context, tool calls, and actual token/cost data exposed by the client. Do not estimate token counts from transcript characters and present them as measured usage.

Separate three conditions when evaluating learning:

1. No Sley documentation supplied and browsing disabled.
2. The standard versioned tools guide/examples supplied.
3. Browsing or retrieval allowed and recorded.

Do not mix them into one score. Do not infer that improved performance proves a particular public conversation entered a training set.

### 17.8 Fresh-environment acceptance

The release test must install from the actual produced packages and verified core artifact into a new environment. It must not rely on a developer's checkout, untracked files, environment variables, old sessions, cached authorization, or private paths.

It must run the flagship story, one large-integer SDK case, one Data Pipe input, one repair exercise, and both supported import profiles. Optional hosted deployment testing is separate from the local install check.

A tools binary that passes tests only when started from its build directory is not release-ready.

---

## 18. Implementation order and parallel work

### 18.1 Priority versus dependency

Keep the ten-project priority order, but recognize that Rules is the acceptance example for Starter and Review can begin before its marketing priority suggests.

| Work package | Required outputs | Real integration dependency |
|---|---|---|
| F0 | Core identity, capability matrix, real construction/edit/test probe. | Selected core artifact. |
| F1 | Shared backend, typed values, manifests, error/lifecycle contracts. | F0 findings. |
| P01 Starter | Installed create/run/test/modify flow. | F1 and supported authoring. |
| P04 Rules | N01 and N02 with independent oracles. | F1 native construction/execution. |
| P02 Bridge | Python/TypeScript clients and application examples. | F1 and N01. |
| P03 Agent Connector | MCP adapter, resources, client setups. | F1 and native change flow. |
| P05 Data Pipe | N03–N05 and bounded host orchestration. | F1 execution/containers needed by profile. |
| P06 Review | Artifact comparisons, reports, CI Action. | F1 artifact/inspection contract. |
| P07 Playground | Local UI and hosted-ready isolated profile. | F1, P01, P04, and review rendering. |
| P08 Repair Lab | Eight exercises and judges. | P01 and suitable native seed programs. |
| P09 Schema Importer | Declared schema subset, N10, reference tests. | F1 builder/value profile. |
| P10 Python Importer | Declared AST profile, N06–N09, reference tests. | F1 builder/control-flow profile. |

No package waits for an unrelated model benchmark or a blanket Sley GA designation. It waits only for the specific capability and tests it actually requires.

### 18.2 Work that can begin immediately

Before native integration is ready, independent work can establish SDK models, UI layout, interpreter/schema reference tests, parser diagnostics, program contracts, golden input cases, manifest schemas, and review presentation.

These outputs should be merged only when useful and tested. They remain frontend/unit work until connected to the real core.

Do not create a fake “Sley runtime” as a substitute for resolving the authoring path. A mock adapter is acceptable only in clearly identified unit tests.

### 18.3 Suggested lanes

Use a small number of lanes if multiple agents are available:

**Lane A — Foundation and first utility:** F0/F1, Starter, Rules, narrowly scoped core integration. This lane owns shared-contract changes.

**Lane B — Application and agent access:** Python/TypeScript SDKs, MCP, client setup, cross-adapter fixtures.

**Lane C — Programs and review:** Data Pipe, Review, corpus packaging, Repair Lab.

**Lane D — Importers and browser:** Schema/Python frontends and Playground, with their independent oracles and isolated execution integration.

One capable agent can execute the same work sequentially. Do not build scheduling software to manage these lanes. Use ordinary branches/worktrees and a shared compatibility contract.

Before changing a shared schema, coordinate with the lane owning it and update all generated clients together. Avoid several agents inventing incompatible fields concurrently.

### 18.4 Integration milestones

**M1 — Real authoring:** F0/F1 and Starter + N01. A new program and a real change execute correctly from the public path.

**M2 — Useful embedding:** Bridge, Agent Connector, N02, Data Pipe, and Review. The flagship story works through SDK and agent tools, and a contributor can review the result.

**M3 — Accessible learning:** Playground local mode, Repair Lab, and expanded seed programs. Hosted-ready status is separately qualified.

**M4 — Migration and complete initial scope:** Both restricted importers, all ten projects, and the honest initial native catalog count pass their required acceptance.

Milestones permit useful incremental releases. They do not justify advertising unfinished later projects. The owner can publish M1 while M2–M4 continue, once M1's actual release requirements are satisfied.

### 18.5 Blocker handling

When blocked, record the smallest reproducible failure, affected feature, exact core/tools revision, proposed bounded remedy, and other work that remains unblocked.

Then continue the next meaningful unblocked task. Do not halt the whole program because an importer needs a core primitive, a client account is unavailable for a live test, or public hosting is not authorized.

Conversely, do not bypass the blocked requirement with host fallback, fixed fixture credentials, or a fake success response. The dependency remains visible until resolved.

### 18.6 Change discipline

Use small coherent commits with implementation, tests, and the relevant documentation. Preserve user changes. Avoid unrelated cleanup in the core repository.

Agent discretion covers internal data structures, algorithms, test organization, frontend framework, and dependency choice when compatible with this document. Record only decisions that affect public contracts, compatibility, substantial cost, or trust boundaries.

Do not expand this document into dozens of micro-specifications before coding. Implement, test, revise the relevant section when needed, and keep the actual public interfaces accurate.

---

## 19. Distribution, documentation, and public release

### 19.1 Release contents

A tools release includes the supported platform binary, an identity/checksum manifest, the tested core compatibility range or exact profile, licenses/notices as approved, concise installation instructions, native seed artifacts, and small verification commands.

Python/TypeScript/MCP components can be distributed as separate package artifacts from the same repository/release. Document their exact dependency on the backend version. Registry releases are optional until the owner authorizes them and ownership is confirmed.

Do not download an unpinned “latest” core silently at runtime. Installation can offer a documented verified core acquisition step; normal execution must use the selected compatible binary and report its identity.

No automatic self-update, package publication, account creation, or telemetry is required.

### 19.2 Platform support

Linux x86_64 is the initial end-to-end target. macOS, native Windows, Linux ARM, and browser-native execution are unsupported until tested and explicitly added.

A TypeScript SDK building on Windows is not evidence that native Sley execution works on Windows. WSL may be a separately documented tested environment; do not label it native Windows support.

Browser UI portability is distinct from the backend execution platform. State where execution actually occurs.

### 19.3 Documentation hierarchy

The top-level README should answer, in this order:

1. What useful result can I get?
2. How do I install and run the first example?
3. How do I call it from my application or agent?
4. How do I change and test it?
5. What is supported, what is not, and how do I contribute?

`AGENTS.md` should be short and practical: repository entry points, setup, relevant tests, authority boundaries, and current known blockers. Link to this specification rather than duplicating it.

Each program README explains the task, input/output meaning, native/host split, one runnable command, and one verified change. Generated API references and machine-readable schemas supply detail when needed.

Avoid forcing every newcomer to read the complete kernel specification before running a program. Preserve access to exact contracts for the agent implementing deeper changes.

### 19.4 Website and discoverability

Keep source and documentation in the tools repository. Integrate generated documentation or links into the existing Sley website only through its established owner-authorized process; do not create a new website repository solely for this toolkit.

Use stable program/version URLs where the existing site supports them, readable text, clear Sley 2 labels, and directly downloadable example artifacts. A compact machine-readable documentation entry can link to the supported API, current capabilities, and worked examples.

Do not promise crawler access, future training inclusion, or search visibility. The deliverable is useful public material that humans and tools can read and reproduce.

### 19.5 Public demonstrations

Prepare one reproducible demonstration per completed milestone and selected native program. The demonstration should show the task, actual output, a meaningful edit, a check, and how to reproduce it.

Generate reports from real runs. A promotional statement must describe the tested scope. “This MCP client changed this program successfully” is a narrower and supportable claim; “all models now understand Sley” is not.

Publishing posts or sending model-provider data is a separate action, not an automatic release step.

### 19.6 Release gate

Before publishing a milestone:

- Its required project tests and fresh-install scenario pass against the named compatible core.
- Every claimed supported platform/client is tested; omissions are disclosed.
- Packages contain no secrets, private paths, accidentally bundled local work, or unresolved placeholder configuration.
- Manifests, artifacts, generated docs, and package versions agree.
- The new-code license and reused-code notices are owner-approved and complete.
- Required upstream core changes are available through the advertised route; development-core requirements are not hidden.
- The owner has authorized publication and any hosting/cost implications.

A licensing or publication gate may leave a complete local implementation unpublished. That is different from an incomplete implementation and should be reported separately.

---

## 20. Risks, blocker handling, and final acceptance

### 20.1 Main risks and chosen responses

| Risk | Required response |
|---|---|
| Public authoring is less usable than core tests suggest. | F0 real probe and smallest upstream facade/repair where necessary. |
| Tooling reimplements core semantics inconsistently. | Shared backend, public codecs/descriptors, one compatibility contract. |
| Adapters hide host-language business logic. | Explicit native/host boundaries and native execution tests. |
| Apparent translation changes language semantics. | Restricted profiles, fail-closed import, exact domains, independent references. |
| JSON/JavaScript integers lose precision. | Lossless wire representation and cross-client boundary fixtures. |
| “Atomic” onboarding is actually several commits. | Explicit step journal, incomplete-state reporting, final readiness check. |
| A disconnected commit is retried and duplicated. | Candidate-bound status reconciliation and unknown-outcome state. |
| UI hides failed or incomplete validation. | Structured outcomes and no optimistic success claim. |
| Public playground exposes maintainers' environment. | Disposable workers, resource limits, isolation verification, separate deployment gate. |
| Repository fills with duplicate wrappers and records. | One backend, one catalog, ignored run artifacts, concise milestone evidence. |
| Core progress silently breaks examples. | Pinned tested profiles and explicit compatibility upgrade checks. |
| Project stalls on a peripheral integration. | Continue unblocked lanes while retaining the real blocker. |
| Scope grows into a platform before users can run anything. | First deliver Starter + N01, then add integrations around that working artifact. |

### 20.2 End-to-end acceptance scenario

Run the following as an integrated release test, from produced packages rather than a privileged development checkout:

**E2E-01 — Install and identify.** Install tools and the declared core into a clean Linux x86_64 environment. Verify identity and checksums. `doctor` reports the actual supported profile.

**E2E-02 — Construct.** Initialize Release Readiness through the supported authoring interface, with new workspace/candidate bindings and legitimate scoped local authority.

**E2E-03 — Execute and test.** Run passing and failing application inputs. Execute the declared native test path and external assertions, recording which is which.

**E2E-04 — Embed.** Call the same accepted artifact from Python and TypeScript and verify equivalent typed outputs. Include the separate wide-integer fixture.

**E2E-05 — Agent edit.** Through MCP, change a requirement, add an appropriate test, validate, commit, and verify. A scripted protocol run is always available; the live-agent result is separately recorded for the onboarding claim.

**E2E-06 — Refusal.** Submit a realistic invalid or unauthorized change and show refusal without advancing the accepted semantic root.

**E2E-07 — Review and transfer.** Generate an exact artifact review, export the changed program, and reproduce it in a second clean workspace.

**E2E-08 — Data utility.** Run the published benchmark pipeline and match the independent results across at least two batch partitions.

**E2E-09 — Learning and browser.** Complete one Repair Lab task and one local Playground change/export scenario.

**E2E-10 — Import.** Import one accepted schema and one accepted Python function, run their independent cases, then make and test a native modification to each. Reject representative unsupported inputs.

These ten scenarios complement the project-specific acceptance criteria. They do not replace independent semantic tests or authorize untested platforms.

### 20.3 Completion report

The implementing agent's final report must state:

- Repository and exact tools commit; any necessary core commit/PR dependency.
- Implemented public commands, SDK packages, and supported profiles.
- Status of each of the ten projects and each required acceptance group.
- Exact commands used for the full test and fresh-install checks.
- Actual evidence/artifact locations and hashes.
- Native program catalog count, clearly excluding wrappers and duplicate variants.
- Known failures, blocked/untested clients, and unsupported input features.
- Whether the result is local-only, publication-ready, or actually published under explicit authorization.

Do not report “complete” merely because every module directory exists or every planned interface has a stub. Do not claim a public release if only local artifacts were built.

### 20.4 Definition of full initial-scope completion

All ten projects implement their required bounded versions; F0 and the real authoring path work; the shared clients agree on typed semantics; the initial native corpus is honestly accounted for; public instructions are reproducible; required integration and fresh-install checks pass; and remaining limitations are explicit.

Actual public deployment, owner licensing approval, and live-client tests unavailable in the implementation environment may remain separately pending. They must prevent the corresponding public claims, not be disguised as passing results.

---

## 21. Agent handoff and source notes

### 21.1 Pasteable implementation handoff

> Implement `SLEY_TOOLS_IMPLEMENTATION_SPEC.md` in a single `sley-lang/tools` monorepo. All ten bounded projects are in scope. Start by inspecting the actual checkout and verifying the selected public core with F0; do not assume the demo gap means the core mechanisms are absent. Build the real Starter + Release Readiness slice first, then integrate the SDKs, MCP, Data Pipe, Review, Playground, Repair Lab, and restricted importers around the shared backend. Begin independent frontend/oracle work in parallel where useful, but never count mocks as native integration. Keep core semantics and durable authority in `sley-lang/sley`; use a narrow upstream patch only if a real public-path gap requires it. Preserve existing work, use ordinary branches/worktrees, keep documentation consolidated, and do not create satellite repositories. Run focused tests during development and the complete fresh-install acceptance before declaring the relevant scope ready. Continue unblocked work when a dependency is blocked. Report actual commits, commands, artifacts, evidence, supported profiles, and remaining limitations. Do not publish packages, deploy publicly, choose a new-code license, or spend money without the owner's applicable authorization.

### 21.2 Sources and verification boundary

The following primary sources were consulted on September 26, 2026. Repository documents were read at the inspected commit where specified. The baseline is documentary; this specification's author did not execute the Sley binary or test the proposed tools.

New commands, method names, manifest formats, profiles, project layout, and acceptance criteria in this document are design requirements, not features claimed to already exist.

**[S1] Sley 2.0.1 public release.** Release build identity, artifact hash/size, platform, and known-limit summary.  
`https://github.com/sley-lang/sley/releases/tag/v2.0.1`

**[S2] Public Sley default-branch snapshot inspected.** Documentation/evidence snapshot distinct from the release build commit.  
`https://github.com/sley-lang/sley/commit/da0c3759fce0b6a9d1dc27562a97b0b6a9849d96`

**[S3] Sley Quickstart.** Public demo scope, JSON bridge body encoding, session and transport behavior.  
`https://github.com/sley-lang/sley/blob/da0c3759fce0b6a9d1dc27562a97b0b6a9849d96/docs/QUICKSTART.md`

**[S4] Candidate Record v1.** Proposal construction, immutable bindings, descriptor-driven payloads, and separation from authority/commit.  
`https://github.com/sley-lang/sley/blob/da0c3759fce0b6a9d1dc27562a97b0b6a9849d96/docs/spec/CANDIDATE_RECORD_V1.md`

**[S5] Capability Summary v1.** Context projection and the distinction between summary binding and authority.  
`https://github.com/sley-lang/sley/blob/da0c3759fce0b6a9d1dc27562a97b0b6a9849d96/docs/spec/CAPABILITY_SUMMARY_V1.md`

**[S6] Sley 2.0.0 release notes and retained limits.** Native code/test two-commit limitation, excluded E7 opcodes, and release versus GA status; the 2.0.1 release retains these limits.  
`https://github.com/sley-lang/sley/blob/da0c3759fce0b6a9d1dc27562a97b0b6a9849d96/docs/release/SLEY-2.0.0.md`

**[S7] Model Context Protocol tools specification.** Tools, schemas, and client interaction responsibilities.  
`https://modelcontextprotocol.io/specification/2025-11-25/server/tools`

**[S8] Model Context Protocol transport specification.** Stdio framing and stdout/stderr requirements; local HTTP considerations if later used.  
`https://modelcontextprotocol.io/specification/2025-11-25/basic/transports`

**[S9] GitHub Actions secure use reference.** Least privilege, immutable action references, and untrusted pull-request execution boundaries.  
`https://docs.github.com/en/actions/reference/security/secure-use`

**[S10] JSON Schema numeric types.** Integral numeric representations, numeric ranges, and numerical semantics.  
`https://json-schema.org/understanding-json-schema/reference/numeric`

**[S11] JSON Schema object reference.** Separate meanings of properties, required fields, and additional properties.  
`https://json-schema.org/understanding-json-schema/reference/object`

**[S12] Python AST documentation.** Parsing, syntax-tree structure, and source locations.  
`https://docs.python.org/3/library/ast.html`

**[S13] Python built-in types.** Unlimited-precision integers, boolean subtype behavior, operand-returning boolean operations, and short-circuit semantics.  
`https://docs.python.org/3/library/stdtypes.html`

**[S14] JSON-RPC 2.0 specification.** Base request/response, IDs, and errors for the toolkit's local control interface.  
`https://www.jsonrpc.org/specification`

### 21.3 Final implementation principle

**Build one useful native program and a reliable path around it. Then expand that same path across all ten tools and the growing corpus. The objective is reproducible adoption, not ten impressive-looking wrappers around an unverified demo.**
