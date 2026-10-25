# Benchmark Comparator (N03)

This native Sley utility compares one matched benchmark row. It returns exact checked signed deltas for wall time, CPU time, and archive size, three independent regression flags, an `any_regression` flag, and an invalid-input flag. The [Data Pipe](../../docs/DATA_PIPE_V0.md) calls it for each row while its Python host preserves case IDs, order, and JSON Lines input and output.

The [manifest](program.json) binds the [accepted native graph artifact](artifact/native-graph.json), [authoring recipe](build/graph.json), selected Sley 2.1.1 core, and [twelve initial cases](tests/cases.json). The JSON recipe builds Sley objects; the accepted SSMC1 graph is the program. The [package probe](../../evidence/catalog/benchmark-comparator-v2.json) verifies the checked-in artifact after a fresh authority-rebased import, then checks the program and a native edit against an [independent integer oracle](../../scripts/probe_checked_graph.py). This package is included in the [RC7 prerelease](../../docs/INSTALL_RC7.md) under [Apache-2.0](../../LICENSE); the stable release gate remains open.

## Run it

From the repository root, with `CORE` set to the selected Sley 2.1.1 binary:

```sh
target/debug/sley-tools artifact import \
  programs/benchmark-comparator/artifact/native-graph.json my-comparator --core "$CORE"
target/debug/sley-tools test --project my-comparator
target/debug/sley-tools inspect --project my-comparator
```

`sley-tools init NEW_PROJECT --graph programs/benchmark-comparator/build/graph.json --tests programs/benchmark-comparator/tests/cases.json --core "$CORE"` is the reproducible authoring route. The imported project also works as `--comparator-project` in the [Data Pipe command](../../docs/DATA_PIPE_V0.md), alongside accepted selector and reducer projects.

Six signed 64 bit inputs are supplied in baseline/candidate pairs, in wall microseconds, CPU microseconds, and archive bytes. Equal values are not regressions. A regression in one metric remains visible when other metrics improve. Delta overflow returns a native checked arithmetic error for that metric. A negative measurement sets `invalid_input`; a missing measurement is an invalid call. The initial and edited case files cover zero, equality, mixed results, signed boundaries, six negative cases, and overflow.

## Worked native change

The named native `reject_negative_inputs` constant starts as `true`. Set it to `false` to let the standalone comparator represent signed correction measurements without marking them invalid. Deltas and regression flags retain their meanings. The Data Pipe host still rejects negative benchmark rows at its external input boundary.

```sh
printf 'false\n' > /tmp/allow-corrections.json
target/debug/sley-tools propose --project my-comparator \
  --constant reject_negative_inputs --value-file /tmp/allow-corrections.json \
  --tests programs/benchmark-comparator/tests/allow-corrections-cases.json
target/debug/sley-tools validate --project my-comparator
target/debug/sley-tools commit --project my-comparator
target/debug/sley-tools test --project my-comparator
```

The [package probe](../../evidence/catalog/benchmark-comparator-v2.json) records the native candidate and receipt, verifies all twelve cases before and after the change, exports and reimports the edited graph, and confirms that the unchanged Data Pipe host still produces the independently expected output for valid rows. Native core test admission remains pending on the selected release; these case results are external assertions against native execution.
