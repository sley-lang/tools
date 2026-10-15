# Benchmark Selector and Projector (N04)

This native Sley utility decides whether a matched benchmark row contains any regression and projects the candidate's three measurements. It is useful in the [Data Pipe](../../docs/DATA_PIPE_V0.md), where the host keeps record IDs and stream order while the accepted graph makes the selection and projection decisions.

The [manifest](program.json) binds the [native graph artifact](artifact/native-graph.json), the [authoring recipe](build/graph.json), and the [nine initial cases](tests/cases.json). The artifact contains exact stored Sley objects without workspace authority. The recipe is an authoring input; the accepted SSMC1 graph is the program. The manifest and [catalog probe](../../evidence/catalog/benchmark-selector-v2.json) record the selected core, tools binary, object digest, fresh import, and behavior checks. This package is included in the [RC6 prerelease](../../docs/INSTALL_RC6.md) under [Apache-2.0](../../LICENSE); the stable release gate remains open.

## Run it

From the repository root, with `CORE` set to the selected Sley 2.1.1 binary:

```sh
target/debug/sley-tools artifact import \
  programs/benchmark-selector/artifact/native-graph.json my-selector --core "$CORE"
target/debug/sley-tools test --project my-selector
target/debug/sley-tools inspect --project my-selector
```

`sley-tools init NEW_PROJECT --graph programs/benchmark-selector/build/graph.json --tests programs/benchmark-selector/tests/cases.json --core "$CORE"` is the reproducible authoring route. To use the imported program in Data Pipe, pass `--selector-project my-selector` alongside compatible native comparator and reducer projects to the [sample command](../../docs/DATA_PIPE_V0.md). The Python host decodes bounded JSON Lines, preserves IDs and order, and serializes results; it does not decide selection or perform projection.

Six exact signed 64 bit inputs are supplied in baseline/candidate pairs for wall microseconds, CPU microseconds, and archive bytes. The result contains `selected`, `invalid_input`, and the three candidate measurements. A negative measurement sets `invalid_input` and refuses selection. A missing measurement is an invalid call. Equality is not a regression, and a zero baseline is valid. The initial native `include_nonregressions=false` constant selects a valid row when any candidate measurement exceeds its baseline. The [independent oracle](../../scripts/probe_data_pipe_native.py) checks every public case, including zero, equality, mixed improvement/regression, signed boundaries, and negative input. The Data Pipe host preserves the relative order of selected records.

## Worked native change

Set the named constant to `true`, validate its exact Sley candidate, commit it, and rerun the [changed cases](tests/include-all-cases.json):

```sh
printf 'true\n' > /tmp/include-all.json
target/debug/sley-tools propose --project my-selector \
  --constant include_nonregressions --value-file /tmp/include-all.json \
  --tests programs/benchmark-selector/tests/include-all-cases.json
target/debug/sley-tools validate --project my-selector
target/debug/sley-tools commit --project my-selector
target/debug/sley-tools test --project my-selector
```

The [package probe](../../evidence/catalog/benchmark-selector-v2.json) records the candidate and receipt, exports this changed predicate as native objects, imports it into a fresh workspace, and verifies that the unchanged Data Pipe command selects all four valid sample rows instead of two. An imported graph can be edited and re-exported again. Native core test admission remains pending on the selected release; the nine case results and Data Pipe integration are external assertions against native execution.
