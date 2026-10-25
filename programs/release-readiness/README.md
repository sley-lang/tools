# Release Readiness: native program prototype

This program computes an application-level release decision from supplied facts. It does not inspect a real test run or certify a release.

The local catalog includes a [versioned manifest](program.json) and [accepted native object artifact](artifact/native-program.json). The artifact contains the program graph without source workspace authority. `sley-tools artifact import programs/release-readiness/artifact/native-program.json PROJECT_DIR --core CORE` commits a rebased copy into a fresh workspace. The manifest validates against [program schema v0](../../schemas/program-v0.schema.json); the build and validation records are in `evidence/catalog/`. This package is included in the [RC7 prerelease](../../docs/INSTALL_RC7.md) under [Apache-2.0](../../LICENSE). Native test admission and the stable release gate remain open.

To construct another catalog copy without changing this one, run `python3 scripts/build_release_readiness_catalog.py --tools target/debug/sley-tools --core CORE --catalog NEW_DIR --evidence NEW_EVIDENCE.json` from the tools repository. The script refuses to overwrite a manifest, artifact, or evidence file. Fresh workspace nonces make the new object IDs and bytes differ; import and native execution verify the semantic behavior.

The Rust builder in `crates/backend` creates and edits a typed SSMC1 graph through the public Sley 2.1.1 candidate path. Sley validates and commits the graph. The Rust backend then loads the verified accepted root and invokes the public core VM with `extended_v1`; the decision is computed by native Sley operations.

## Contract

Input order:

1. `test_run_completed: Bool`
2. `tests_failed: UInt32` (0 through 4,294,967,295)
3. `artifact_verified: Bool`
4. `blocking_findings: UInt32` (same range)

Output is a native named Sley record with five `Bool` fields in definition order:

1. `allow`
2. `test_run_incomplete`
3. `tests_failed`
4. `artifact_unverified`
5. `blocking_findings_exceeded`

The four reason flags can be true simultaneously. Each field has a stable native `MemberId`, recorded in the evidence alongside its readable name. `allow` is the native negation of the disjunction of those reasons. A `UInt32` input cannot represent a negative count. The initial graph contains the native constant `max_blocking_findings = 0`; the worked edit replaces that constant with `2`, using an exact old object precondition. The native graph uses `ConstantRef`, `GreaterThan`, `BoolNot`, `BoolOr`, and `RecordNew`.

## Verified scope

`scripts/probe_release_readiness.py` constructs a fresh workspace through the released CLI, commits the program, runs 18 initial cases, commits the threshold edit, and runs 40 edited cases. The independent Python oracle checks the full Boolean truth table at zero and positive counts, count boundaries, multiple reasons, the changed threshold boundary, and monotonicity as blocking findings increase. It also checks that negative and out-of-range counts are refused by the tools boundary. The evidence is in `evidence/release-readiness/n01-probe-v2.json`.

The CLI's offered hello omits the `extended_execute` feature, so its restricted `execute` cannot run this graph. The backend therefore invokes the pinned public VM library after loading the accepted repository head. The current `sley-tools` CLI can initialize, run, inspect, externally test, edit, and transfer a local Release Readiness program. The public edit changes the threshold from zero to two, preserves the accepted root after a rejected stale proposal, and passes 60 edited assertions. The artifact probe exports 20 verified native objects, imports them with fresh workspace authority, compares 60 source/import outputs, rejects a tampered object, and replays a second-generation export. Native core test admission remains open.
