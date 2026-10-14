# Changelog

Sley Tools release candidates share the package version `0.0.1`; each candidate is identified by its `v0.0.1-rc.N` tag.

## 0.0.1-rc.5 (proposed)

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

- `CallResult` in the Python SDK is now a small immutable class instead of a dataclass. Fields, equality, `repr`, and pattern matching are unchanged.
- A native failure inside `test` now reports `ST_EXECUTION_FAILED` (or `ST_STALE_BASE` when the accepted root moved), matching the RPC execution path, and each case has a 60-second bound.
- New checks: `tests/test_cli_core_checks.py`, `tests/test_python_sdk_lifecycle.py`, `packages/typescript/tests/close_probe.mjs`, and Rust unit tests for RPC response framing. CI runs all of them.
