# Range Predicate (N07)

This native Sley utility decides whether a signed 64 bit value belongs to an interval. It branches explicitly when `lower > upper`, includes both endpoints by default, and returns five Boolean fields: `within_range`, `invalid_bounds`, `below_lower`, `above_upper`, and `excluded_upper`. The accepted graph makes these decisions; the host only decodes typed inputs and manages the workspace.

The [manifest](program.json) binds the [stored native objects](artifact/native-graph.json), [authoring recipe](build/graph.json), and [eleven public cases](tests/cases.json). The recipe is an input to the native builder, not a Sley source syntax. The [catalog probe](../../evidence/catalog/range-predicate-v2.json) checks the package against the selected core and an independent interval oracle, then verifies fresh import, two native boundary edits, and export replay. This package is included in the [RC7 prerelease](../../docs/INSTALL_RC7.md) under [Apache-2.0](../../LICENSE); the stable release gate remains open.

The [Python SDK example](examples/python.py) imports the same native artifact into a fresh local workspace and calls its Sley entrypoint. Python supplies typed inputs and prints the result; it does not decide interval membership. From the repository root with the local SDK installed and `CORE` set to the selected Sley 2.1.1 binary:

```sh
python3 programs/range-predicate/examples/python.py \
  --tools target/debug/sley-tools --core "$CORE" \
  --artifact programs/range-predicate/artifact/native-graph.json \
  --value 9007199254740993 --lower 9007199254740993 --upper 9007199254740995
```

## Run it

From the repository root, with `CORE` set to the selected Sley 2.1.1 binary:

```sh
target/debug/sley-tools artifact import \
  programs/range-predicate/artifact/native-graph.json my-range --core "$CORE"
target/debug/sley-tools test --project my-range
target/debug/sley-tools inspect --project my-range
target/debug/sley-tools run --project my-range \
  --input programs/range-predicate/inputs/upper-endpoint.json
target/debug/sley-tools run --project my-range \
  --input programs/range-predicate/inputs/reversed-bounds.json
```

For example, input `["5", "-5", "5"]` returns `within_range=true`, while `["0", "5", "-5"]` returns `invalid_bounds=true`. Signed integer inputs use canonical decimal strings. A missing or wrong-typed input is a rejected call, not an interval result.

## Boundary lesson and native correction

The named `exclude_upper_endpoint` constant is initially `false`. Changing it to `true` makes a value exactly at `upper` fail membership and sets `excluded_upper=true`; strict `above_upper` remains false. The separate [excluded-endpoint cases](tests/excluded-upper-cases.json) cover that policy. Change the constant back to `false` with the initial cases to correct the endpoint regression:

```sh
printf 'true\n' > /tmp/exclude-upper.json
target/debug/sley-tools propose --project my-range \
  --constant exclude_upper_endpoint --value-file /tmp/exclude-upper.json \
  --tests programs/range-predicate/tests/excluded-upper-cases.json
target/debug/sley-tools validate --project my-range
target/debug/sley-tools commit --project my-range

printf 'false\n' > /tmp/include-upper.json
target/debug/sley-tools propose --project my-range \
  --constant exclude_upper_endpoint --value-file /tmp/include-upper.json \
  --tests programs/range-predicate/tests/cases.json
target/debug/sley-tools validate --project my-range
target/debug/sley-tools commit --project my-range
target/debug/sley-tools test --project my-range
```

The [probe](../../scripts/probe_range_catalog.py) makes 35 native input checks in each phase. It verifies the accepted root stays unchanged through proposal and validation, changes on commit, then returns to its initial content root after the correction. It exports the corrected native objects, imports them into another workspace, and repeats the checks. The selected core's native test admission is unavailable; these case results are external assertions against native execution.
