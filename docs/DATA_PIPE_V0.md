# Data Pipe v0

The first three Data Pipe responsibilities now have local native Sley graph witnesses. The [Benchmark Comparator](../examples/graph/benchmark-comparator.json) computes three checked signed deltas and independent regression flags. The [Benchmark Selector and Projector](../examples/graph/benchmark-selector.json) decides whether a matched row has any regression and returns only the candidate measurements plus selection and invalid-input flags. The [Benchmark Summary Reducer](../examples/graph/benchmark-summary-reducer.json) combines two summaries through four native checked additions: row count, candidate wall time, candidate CPU time, and candidate archive bytes. The JSON recipes are authoring inputs; the accepted SSMC1 graphs are the programs.

All measurement inputs use exact signed 64 bit integers. A negative measurement sets `invalid_input`; the selector refuses to select that row, and callers should reject it. Missing measurements are invalid calls. Equality does not count as a regression. A reducer input may be a partial summary or a one-row summary with count one. The all-zero summary is the identity, including for empty input. Each overflowing total returns `{"error":{"code":1}}`; callers must treat that arm as a failed reduction. A zero baseline is valid. The host pipeline preserves the opaque case identifier and input order without implementing comparison, selection, projection, or summation itself.

The selector has a named native Boolean constant, `include_nonregressions`, initially `false`. Changing it to `true` through `propose --constant`, `validate`, and `commit` broadens the predicate to every valid row. The same six measurement inputs and output interface remain in use; the host parser does not change. The [native probe](../evidence/graph-builder/data-pipe-native-v4.json) checks nine selector cases, ten reducer cases, the exact accepted predicate edit, the empty identity, four independent overflow arms, and the same summary under three batch partitions. It checks all fixture values against an independent Python integer oracle and then executes the accepted Sley graphs through the CLI and Python SDK.

The edited selector can now be exported as a [native graph artifact](ARTIFACT_V0.md#authored-graph-profile-v1) and imported into a fresh workspace. The [transfer probe](../evidence/graph-builder/portable-artifact-v2.json) runs that imported graph through the same Data Pipe command, checks all four selected rows against an independent oracle, then makes a second native edit and re-exports it. The host parser and streaming code are unchanged.

The [N03 comparator](../programs/benchmark-comparator/README.md), [N04 selector](../programs/benchmark-selector/README.md), and [N05 reducer](../programs/benchmark-summary-reducer/README.md) packages include accepted native object artifacts, bound manifests, public cases, and worked native edits. Their package probes check twelve comparator, nine selector, and seventeen reducer cases before and after their edits against independent oracles. The [reducer probe](../evidence/catalog/benchmark-summary-reducer-v2.json) also verifies the empty identity, four positive overflow arms, a negative overflow, and matching Data Pipe output under three batch sizes. The [comparator](../evidence/catalog/benchmark-comparator-v2.json) and [selector](../evidence/catalog/benchmark-selector-v2.json) probes run the imported graphs through the unchanged host command.

The installed `sley-data-pipe` command streams a fixed JSON Lines schema through those three accepted graphs. The [sample input](../examples/data-pipe/benchmark-v0.input.jsonl) and [independent expected output](../examples/data-pipe/benchmark-v0.expected.jsonl) demonstrate the first named pipeline. Each input line has exactly `case_id`, `units`, `baseline`, and `candidate`. `units` is `{"wall":"us","cpu":"us","archive":"bytes"}`. Baseline and candidate each have exact decimal-string `wall_us`, `cpu_us`, and `archive_bytes` values. Case IDs are 1–64 ASCII letters, digits, dots, underscores, or hyphens, starting with a letter or digit. Negative, missing, duplicate, malformed, noncanonical, and unit-mismatched rows fail. A selected output line carries the original case ID, native candidate projection, and native comparator result. A final summary line carries native count and candidate totals for **all** input rows. The host never calculates comparison flags, deltas, selection, projection, or totals.

Create the three native projects once with `sley-tools init PROJECT --graph examples/graph/NAME.json --tests examples/graph/NAME-cases.json --core CORE`, where `NAME` is `benchmark-comparator`, `benchmark-selector`, or `benchmark-summary-reducer`. Then run:

```sh
uv run --no-project --with ./packages/python sley-data-pipe \
  --tools target/debug/sley-tools --core CORE \
  --comparator-project COMPARATOR_PROJECT \
  --selector-project SELECTOR_PROJECT \
  --reducer-project REDUCER_PROJECT \
  --input examples/data-pipe/benchmark-v0.input.jsonl \
  --batch-size 2
```

The unified CLI can launch the installed optional command with the same native projects:

```sh
sley-tools pipe --adapter /path/to/venv/bin/sley-data-pipe --core CORE \
  --comparator-project COMPARATOR_PROJECT \
  --selector-project SELECTOR_PROJECT --reducer-project REDUCER_PROJECT \
  --input examples/data-pipe/benchmark-v0.input.jsonl --batch-size 2
```

The adapter path is selected explicitly by the operator. The launcher passes the running `sley-tools` binary to that adapter and preserves its JSON Lines stdout, machine-report stderr, and exit status. If the optional package is missing, the CLI returns `ST_FEATURE_UNSUPPORTED` before opening a project.

The command writes only JSON Lines data to stdout and writes a final machine report to stderr. `--output FILE` writes beside the destination and replaces it only after the entire pipeline succeeds. A stdout failure can leave earlier selected lines visible; the failure report includes their exact count. A temporary SQLite index detects duplicate case IDs across the whole input without retaining them in process memory. The default limits are 100,000 records, 8,192 bytes per line, 1 GiB of input, a batch of 64, and at most 1,000,000,000,000 per measurement. The default record and measurement maxima keep totals within signed 64 bit; increasing the measurement limit can produce a native checked-overflow failure. Limits are configurable with `--max-records`, `--max-line-bytes`, `--max-input-bytes`, `--max-measurement`, and `--batch-size` (at most 4,096). Empty input emits the native reducer's zero identity.

The [current performance probe](../evidence/data-pipe/performance-v5.json) used the same three native graphs on 64 and 8,192 rows alternating a wall-time regression with an all-metric improvement. The larger input was 1,716,224 bytes and emitted 4,096 selected records into a 1,245,300-byte stream. The probe checked every selected row in order and the exact final native summary. It completed in 118.206 seconds (69.303 rows/second) with 81,252 KiB sampled peak RSS across the pipeline process and live descendants, 172 KiB below the 64-row run despite 128 times as many rows. The output went to a temporary file so the observer did not buffer the stream in memory. RSS was sampled every 50 ms on Linux; brief peaks between samples are not measured. This supports bounded process memory for one mixed fixed-schema workload, not an arbitrary workload or throughput guarantee. The [earlier all-zero probe](../evidence/data-pipe/performance-v2.json) remains historical. Native core test admission remains open.
