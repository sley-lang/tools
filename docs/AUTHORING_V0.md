# Explicit native edit lifecycle v0

For the two worked Rules programs, `sley-tools` now exposes the same native edit as three separate commands:

```sh
sley-tools propose --project PROJECT --max-blocking-findings 2
sley-tools validate --project PROJECT
sley-tools commit --project PROJECT
```

For Resource Admission, propose `--require-memory-spare true` or `false` instead. `sley-tools propose discard --project PROJECT` removes a local uncommitted proposal. The existing `edit` command remains a one-step convenience path.

Graph projects also accept `sley-tools propose --project PROJECT --constant NAME --value-file JSON --tests CASES`. The value file contains one typed JSON value, and the case file supplies the expected results after the change. This route replaces exactly one accepted native constant object. Core validation remains separate from the external behavioral assertions, which run after commit; a false case expectation is reported against the accepted new root. See the [graph authoring guide](GRAPH_AUTHORING_V0.md) and [native edit probe](../evidence/graph-builder/fixed-limit-edit-v4.json).

The Python and TypeScript SDKs accept the same targeted graph edit with `program.propose_graph_constant("limit", 12, cases)` and `await program.proposeGraphConstant('limit', 12, cases)`. Each case supplies an `id`, named `inputs`, and an `expected` host value. Validate the returned candidate ID and commit it as above. The SDKs refresh the accepted root after commit. The commit result carries `postcommit_assertions_status`; when expected cases are false, it reports `FAIL` and the new accepted root so callers can correct the cases against the committed graph. The [SDK edit probe](../evidence/bridge/graph-sdk-authoring-v2.json) covers both clients and this false expectation path.

The Python SDK uses the same candidate identity and refreshes the program after commit:

```python
with open_runtime(tools=TOOLS, core=CORE) as runtime:
    program = runtime.open_project(PROJECT)
    candidate_id = program.propose({"max_blocking_findings": 2})["candidate_id"]
    program.validate(candidate_id)
    program.commit(candidate_id)
    result = program.call("evaluate", inputs=FACTS)
```

The TypeScript SDK exposes the same sequence with `await program.propose({ max_blocking_findings: 2 })`, `await program.validate(candidateId)`, and `await program.commit(candidateId)`. For Resource Admission, use `{ require_memory_spare: true }`. A caller can use `discard(candidateId)` before commit to abandon a proposal.

`propose` asks the Rust builder for a typed one-object replacement bound to the live workspace, principal, accepted root, parent transaction, policy, schema epoch, and exact old object. It checks the canonical candidate bytes and replacement body, then stores the proposal in the ignored `.sley-tools/proposal.json` file with mode `0600`. The candidate has a ten-minute expiry. The accepted root stays unchanged.

`validate` submits those exact bytes through the selected core's `candidate.create` and `candidate.validate` methods. The latter returns a candidate-result record even when the candidate is denied. The CLI decodes its decision and diagnostic, reports denial as a structured error, and writes a validation record only for `Valid` with the expected candidate identity and unchanged accepted root. `commit` checks the proposal and validation identities against the current project, creates and validates the same candidate again in a new core session, then requests the authorized commit. A prior validation does not guarantee a later commit. A stale base, expired proposal, changed object, or altered candidate is rejected; no new nonce or candidate is silently substituted. If a transport error leaves the commit outcome uncertain, the existing `ST_COMMIT_OUTCOME_UNKNOWN` path preserves the pending marker for reconciliation.

For local authority lessons, `sley-tools init PROJECT --template release-readiness --core CORE --grant-profile create-only` establishes a separate disposable workspace whose accepted policy grants creation, entrypoint, and test mutations but not `ReplaceEntityVersion`. `inspect` reports the current principal's allowed mutation classes from that accepted policy. Construction and execution work; an edit proposal can be formed but `validate` returns `ST_PERMISSION_DENIED` with the core's `CANDIDATE_VALIDATION_CAPABILITY_DENIED` decision. The accepted root stays unchanged. An owner who needs a normal development grant can export the verified program and import it into a new workspace through the ordinary setup path. Artifact import creates independent workspace and policy identities; it does not carry the source grant. This profile is restricted to the Release Readiness template in the current CLI.

The CLI holds an advisory lock in `.sley-tools/write.lock` around these commands, the one-step `edit`, and artifact export. Another cooperating CLI writer gets `ST_RESOURCE_LIMIT` while the project is busy. Direct core clients can still change the accepted head; exact-root and old-object checks reject their stale proposals.

The [CLI probe](../evidence/starter/authoring-lifecycle-v2.json) covers both worked Rules edits, separate validation, exact candidate identity at commit, one changed native entity version per edit, changed native boundary behavior, existing external assertions, a busy writer, commit before validation, duplicate proposal, proposal tampering, and a stale proposal after an independent accepted edit. The [RPC probe](../evidence/bridge/rpc-authoring-v2.json) checks those edits through a process-scoped handle, including handle rotation after commit and stale-session rejection. The [Python](../evidence/bridge/python-authoring-v2.json) and [TypeScript](../evidence/bridge/typescript-authoring-v2.json) probes verify typed SDK calls and automatic reopening after commit. The named graph constant edit also works through local RPC and both SDKs; broader graph operations remain open. None claims native core test admission.
