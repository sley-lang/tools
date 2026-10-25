# Benchmark Summary Reducer (N05)

This native Sley utility combines two typed partial benchmark summaries. It uses checked signed addition for row count, candidate wall time, candidate CPU time, and candidate archive bytes. An all-zero summary is the empty identity. The [Data Pipe](../../docs/DATA_PIPE_V0.md) calls the reducer while its Python host groups rows, preserves case IDs and stream order, and writes JSON Lines output.

The [manifest](program.json) binds the [accepted native graph artifact](artifact/native-graph.json), [authoring recipe](build/graph.json), selected Sley 2.1.1 core, and [seventeen initial cases](tests/cases.json). The JSON recipe builds Sley objects; the accepted SSMC1 graph is the program. The [package probe](../../evidence/catalog/benchmark-summary-reducer-v2.json) verifies the checked-in artifact after a fresh authority-rebased import and checks it against an [independent integer oracle](../../scripts/probe_data_pipe_native.py). This package is included in the [RC7 prerelease](../../docs/INSTALL_RC7.md) under [Apache-2.0](../../LICENSE); the stable release gate remains open.

## Run it

From the repository root, with `CORE` set to the selected Sley 2.1.1 binary:

```sh
target/debug/sley-tools artifact import \
  programs/benchmark-summary-reducer/artifact/native-graph.json my-reducer --core "$CORE"
target/debug/sley-tools test --project my-reducer
target/debug/sley-tools inspect --project my-reducer
```

`sley-tools init NEW_PROJECT --graph programs/benchmark-summary-reducer/build/graph.json --tests programs/benchmark-summary-reducer/tests/cases.json --core "$CORE"` is the reproducible authoring route. The imported project also works as `--reducer-project` in the [Data Pipe command](../../docs/DATA_PIPE_V0.md), alongside accepted comparator and selector projects.

Eight signed 64 bit inputs form left and right count, wall microseconds, CPU microseconds, and archive bytes. The result has four checked totals and `invalid_input`. With the initial policy, a negative input marks the pair invalid. Missing inputs are invalid calls. Overflow appears as a native arithmetic error for the affected field, never as a wrapped total. The cases distinguish empty input from a zero-valued record, exercise both identity sides, all four positive overflow arms, signed boundaries, negative counts, and negative measurements. The package probe also checks the same Data Pipe output under batch sizes 1, 2, and 4.

## Worked native change

The named native `reject_negative_totals` constant starts as `true`. Set it to `false` to permit signed correction totals in a standalone reducer. Negative counts remain invalid, checked overflow remains explicit, and the empty identity remains zero. The Data Pipe host still rejects negative benchmark rows at its external input boundary.

```sh
printf 'false\n' > /tmp/allow-corrections.json
target/debug/sley-tools propose --project my-reducer \
  --constant reject_negative_totals --value-file /tmp/allow-corrections.json \
  --tests programs/benchmark-summary-reducer/tests/allow-corrections-cases.json
target/debug/sley-tools validate --project my-reducer
target/debug/sley-tools commit --project my-reducer
target/debug/sley-tools test --project my-reducer
```

The [package probe](../../evidence/catalog/benchmark-summary-reducer-v2.json) records the native candidate and receipt, verifies all seventeen cases before and after the edit, exports and reimports the edited graph, and confirms that the unchanged Data Pipe host still produces the independently expected output for valid rows. Native core test admission remains pending on the selected release; these case results are external assertions against native execution.
