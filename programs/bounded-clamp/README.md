# Bounded Clamp (N08)

This native Sley utility clamps a signed 64 bit value to configured inclusive bounds. The accepted graph compares `lower_bound` and `upper_bound` before using either bound. If the configuration is reversed, it returns `invalid_bounds=true` with a sentinel `clamped_value=0`; callers must check the flag before using that value. For valid bounds, a value already inside the interval passes through unchanged, and values outside become the nearest endpoint. The result also reports `below_lower` and `above_upper`.

The [manifest](program.json) binds the [native artifact](artifact/native-graph.json), [authoring recipe](build/graph.json), and selected Sley core. The recipe and [public cases](tests/cases.json) reproduce the program through the supported graph builder:

```sh
target/debug/sley-tools init my-clamp \
  --graph programs/bounded-clamp/build/graph.json \
  --tests programs/bounded-clamp/tests/cases.json --core "$CORE"
target/debug/sley-tools test --project my-clamp
```

To use the checked-in portable artifact from another workspace:

```sh
target/debug/sley-tools artifact import \
  programs/bounded-clamp/artifact/native-graph.json my-clamp --core "$CORE"
target/debug/sley-tools run --project my-clamp --input my-clamp/inputs/example.json
PYTHONPATH=packages/python python3 packages/python/examples/bounded_clamp.py \
  --tools target/debug/sley-tools --core "$CORE" \
  --artifact programs/bounded-clamp/artifact/native-graph.json --value 6
```

For the initial `[-5, 5]` configuration, input `6` returns `clamped_value=5` and `above_upper=true`. The host handles typed input and workspace transport; comparison, branch selection, and output construction run in the Sley VM. Integers in CLI input JSON use canonical decimal strings.

## Worked native bound edit

The initial upper bound is `5`. The [updated cases](tests/upper-6-cases.json) specify behavior with upper bound `6`. Propose and validate the change before committing it:

```sh
printf '"6"\n' > /tmp/clamp-upper.json
target/debug/sley-tools propose --project my-clamp --constant upper_bound \
  --value-file /tmp/clamp-upper.json \
  --tests programs/bounded-clamp/tests/upper-6-cases.json
target/debug/sley-tools validate --project my-clamp
target/debug/sley-tools commit --project my-clamp
target/debug/sley-tools test --project my-clamp
```

The [catalog probe](../../evidence/catalog/bounded-clamp-v2.json) verifies the edit, then changes `lower_bound` to `7` to exercise reversed bounds, restores the original configuration, exports it, and repeats the checks after importing into another workspace. It compares 38 independently selected values per phase, including signed extremes, and tests three wrong-typed inputs. The selected core's production native test admission remains unavailable, so these cases are external assertions against native execution.

This package is included in the [RC7 prerelease](../../docs/INSTALL_RC7.md) under [Apache-2.0](../../LICENSE). The stable release gate remains open.
