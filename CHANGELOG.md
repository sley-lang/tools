# Changelog

Sley Tools release candidates share the package version `0.0.1`; each candidate is identified by its `v0.0.1-rc.N` tag.

## 0.0.1-rc.7

Same Sley 2.1.1 core as RC6. Linux x86_64.

### Fixed

- TypeScript SDK: an RPC response frame that is valid JSON but `null` no longer throws inside the stdout handler, which crashed the host process (including the MCP adapter). Any non-object frame now rejects the pending call with `ST_TRANSPORT_FAILED` and stops the RPC process.
- Python SDK: `Runtime.load`, `Runtime.create_graph`, `operation_status`, and `cancel_operation` now raise `SleyToolsError` (`ST_TRANSPORT_FAILED`) when the `sley-tools` binary cannot be started, as `open_project` already did; previously they raised a raw `FileNotFoundError` or `PermissionError`. This also keeps commit recovery on its documented `ST_COMMIT_OUTCOME_UNKNOWN` path when the status query cannot start. An operation report that is not a JSON object is mapped the same way instead of raising `AttributeError`.
- CLI: a command-line argument that is not valid UTF-8 returns an `ST_INPUT_INVALID` JSON failure (exit 2) instead of panicking (exit 101).
- CLI: a reader that closes stdout early, as in `sley-tools inspect --project p | head`, no longer makes the CLI panic; the exit status still reflects the command's own outcome. Other stdout write failures return an `ST_TRANSPORT_FAILED` JSON failure.

### Tests

- `packages/typescript/tests/frame_probe.mjs` (run in CI) feeds non-object response frames to the TypeScript SDK. `tests/test_python_sdk_lifecycle.py` covers every CLI entry point with a missing binary, commit recovery when the status query cannot start, non-object operation reports, and non-object RPC frames. `tests/test_cli_core_checks.py` covers non-UTF-8 arguments and a closed stdout. New Rust unit tests cover argument parsing and the informational flags.

## 0.0.1-rc.6

Now targets Sley 2.1.1. Linux x86_64.

### Changed

- The pinned core is Sley 2.1.1: release commit `af34be33eb1ae913dddd92aa7f4f275aac0e45ee`, `bin/sley` SHA-256 `b2d8ce53051043cd86f0c071b16584053d9a08ec7af5d2d8a91fa3ab68a79e52`, archive `sley-2.1.1-linux-x86_64.tar.gz` SHA-256 `b0898ba7404a1656db2f83f448ea96d2c1175346f35e50b135bedc96e3810bfd`. `doctor`, `init`, every project and RPC command, exported artifacts, the Review Action, CI, and the release and install probes all check this release. The linked `sley-*` crates build from the same commit.
- The pin lives in one Rust module, `crates/backend/src/core_pin.rs`, shared by `sley-tools` and `sley-tools-native-builder`.
- All ten catalog programs were rebuilt from their checked-in sources on 2.1.1 and re-exported; the catalog smoke runs 143 external assertions against native execution. The Review before/after fixtures were regenerated from the new artifacts, and the repository's probes were rerun on 2.1.1 to record new evidence and `compatibility*.json`.

### Added

- `artifact import` accepts native artifacts exported for Sley 2.0.1 as well as 2.1.1. It rebases them into a fresh workspace and runs every declared case on the selected 2.1.1 core before accepting them, so programs from RC5 can be carried forward. Artifacts that name any other core commit are refused.
- `scripts/rebind_catalog_core.py` rebuilds every catalog package on a selected core, keeps curated manifest fields, refuses a rebuild that changes a declared interface, and replays each new artifact.
- `tests/test_legacy_artifact_import.py`, run in CI, imports two RC5 catalog artifacts on 2.1.1 and checks that other commits are refused.

### Notes

- Projects created with RC5 stay bound to the 2.0.1 core, and RC6 reports that core as incompatible. Export a program with RC5 and import the artifact with RC6 to move it; see `docs/INSTALL_RC6.md`.
- The 2.1.1 core validates native `TestCase` candidates, and `v3 tests.affected` still returns `NATIVE_EXECUTOR_UNAVAILABLE`. `sley-tools test` keeps running external assertions against native execution and reports core-owned native tests as pending.

## 0.0.1-rc.5

Faster everyday commands, leaner binaries, and sturdier SDK process handling. Targets the same pinned Sley 2.0.1 core as RC4.

### Faster

- `run`, `test`, `inspect`, edits, and every RPC request now verify the selected core by identity (canonical path, pinned SHA-256, adjacent manifest) without starting a process. Previously each of them ran the full `doctor` probe, which starts the core six times plus `python3 --version` and `node --version`. `run` and `inspect` are 2.4x to 2.5x faster (26 ms to 11 ms, 24 ms to 9.5 ms), and peak memory drops from 17.6 MiB to 4.7 MiB because Node.js is no longer started. `doctor` output is unchanged.
- `test` and graph edit verification evaluate all cases in one long-lived native worker instead of one builder process per case, with the same per-case evaluation. `test` is 2.4x to 2.7x faster (Release Readiness 144 ms to 54 ms).
- SDK cold load (artifact import plus open), from the repository's bridge timing probe: Python 287–367 ms to 114–116 ms, TypeScript 268–365 ms to 122–140 ms (medians over two runs). The 106-assertion MCP reference probe runs in 8.5 s instead of 15.2 s.
- `import sley_tools` takes about half the time (median 46 ms to 22.5 ms): `dataclasses`, `typing`, `tempfile`, `shutil`, and `hashlib` are no longer imported up front.
- The release profile now enables fat LTO, one codegen unit, and symbol stripping: `sley-tools` is 16% smaller (3.15 MB to 2.66 MB) and `sley-tools-native-builder` 21% smaller (4.18 MB to 3.31 MB).

### Added

- `sley-tools --version` and `sley-tools --help` print plain text and exit 0.
- `doctor` on an unpinned core now reports the adjacent manifest's commit and whether it lists that binary, so the selected release can be identified.

### Fixed

- TypeScript SDK: `Runtime.close()` now stops an RPC process that does not exit on end of input once its 5-second grace period ends, and then returns.
- Python SDK: a missing or unstartable `sley-tools` binary now raises `SleyToolsError` (`ST_TRANSPORT_FAILED`), as do non-object CLI failures, RPC errors with `"data": null`, and non-object RPC frames.
- RPC: an oversized response is replaced by an error frame that keeps the request ID, so the client receives `ST_RESOURCE_LIMIT` for that call.

### Notes

- `CallResult` in the Python SDK is now a small immutable class instead of a dataclass. Fields, equality, `repr`, pattern matching, copying, and pickling are unchanged; `dataclasses` helpers such as `dataclasses.replace` no longer apply to it.
- A native failure inside `test` now reports `ST_EXECUTION_FAILED` (or `ST_STALE_BASE` when the accepted root moved), matching the RPC execution path, and each case has a 60-second bound.
- New checks: `tests/test_cli_core_checks.py`, `tests/test_python_sdk_lifecycle.py`, `packages/typescript/tests/close_probe.mjs`, and Rust unit tests for RPC response framing. CI runs all of them.
