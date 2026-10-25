# Resource Admission (N02)

This pure Sley program decides whether a supplied job request fits supplied worker and memory capacity. It does not inspect a host, reserve capacity, or launch a job. The Rust builder creates its typed native SSMC1 graph; Sley validates and commits that graph, and the public Sley VM library executes the accepted root using `extended_v1`.

The four inputs are `requested_workers`, `available_workers`, `requested_memory_mib`, and `available_memory_mib`. The public application domain is nonnegative `SInt64` through `9223372036854775807`. Zero is allowed. The native graph also detects negative values if called directly. It returns `allow`, four independent reason flags (`invalid_request`, `invalid_capacity`, `insufficient_workers`, `insufficient_memory`), and the exact requested and available memory values as `SInt64`. It performs comparisons without summing or multiplying resources, so the decision has no arithmetic overflow path. The host checks input shape/domain and renders the native result; it does not recompute the decision.

Build a fresh local project with the selected Sley 2.1.1 release:

```sh
target/debug/sley-tools init resource-project --template resource-admission --core /path/to/sley
target/debug/sley-tools run --project resource-project --input resource-project/inputs/ready.json
target/debug/sley-tools test --project resource-project
target/debug/sley-tools inspect --project resource-project
```

The [portable native artifact](artifact/native-program.json) contains 22 verified program objects and no workspace authority. `sley-tools artifact import programs/resource-admission/artifact/native-program.json PROJECT --core CORE` rebases it into a fresh workspace, validates and commits it through the core, and runs 12 independent native execution assertions. Both SDK examples load this same artifact. The public [49-case exact-integer fixture](tests/bridge-cases.json) covers equality, spare capacity, shortages, `2^53+1`, and signed maximum values. `scripts/probe_resource_admission.py` checks fresh authoring; `scripts/probe_resource_artifact.py` checks transfer, two fresh imports, both SDKs, second-generation export/import, and tamper rejection. Evidence is in `evidence/resource-admission/`. The CLI test also invokes the native graph with negative values and independently checks the rejection flags. Computed `allow=false` is a successful execution; `run --check` returns exit 1 for it.

The worked edit is `sley-tools edit --project PROJECT --require-memory-spare true`. It changes the accepted native comparison from `requested_memory > available_memory` to `requested_memory >= available_memory`; exact memory fit is then rejected while one unit of spare capacity remains allowed. The [edit probe](../../evidence/resource-admission/edit-v2.json) records the candidate, receipt, old/new roots, four boundary cases, a core `STALE_ROOT` rejection that leaves the accepted copy executable, and reimport of the edited native artifact. Native core test admission is pending the production executor. The selected release and exact tool/core profile are recorded in `compatibility.json`; this package is included in the [RC7 prerelease](../../docs/INSTALL_RC7.md) under [Apache-2.0](../../LICENSE). The stable release gate remains open. Resource Admission is an application rule, separate from Sley's kernel authorization policy.
