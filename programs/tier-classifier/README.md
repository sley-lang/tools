# Tier Classifier (N06)

This native Sley utility maps every signed 64 bit value into exactly one ordered tier: `0` bronze through `bronze_max`, `1` silver through `silver_max`, and `2` gold above it. The initial thresholds are 10 and 20. A separate middle-tier gate is initially enabled. The graph checks `bronze_max < silver_max` before classifying; if the order is invalid, it returns `tier=-1` and `invalid_configuration=true` instead of a category. This is a native domain rejection result. The generic graph editor can still commit an invalid threshold value, so callers must inspect the result; it is not a pre-commit configuration guard.

The [manifest](program.json) binds the [accepted native objects](artifact/native-graph.json), [authoring recipe](build/graph.json), and [eleven public cases](tests/cases.json). The [catalog probe](../../evidence/catalog/tier-classifier-v2.json) uses the selected Sley 2.1.1 core, an independent integer oracle, fresh import, native threshold and branch edits, invalid-order results, and a second artifact replay. The [Repair Lab RL-03](../../apps/repair-lab/exercises/rl03/TASK.md) starts from this artifact with the middle branch deliberately disabled. This package is included in the [RC7 prerelease](../../docs/INSTALL_RC7.md) under [Apache-2.0](../../LICENSE); the stable release gate remains open.

## Run it

From the repository root, with `CORE` set to the selected Sley binary:

```sh
target/debug/sley-tools artifact import \
  programs/tier-classifier/artifact/native-graph.json my-tier --core "$CORE"
target/debug/sley-tools test --project my-tier
target/debug/sley-tools inspect --project my-tier
```

Supply one canonical signed decimal string in a JSON array: `["10"]` returns bronze; `["11"]` returns silver; `["21"]` returns gold. A missing or wrong-typed value is an input error. The native result is `{ "tier": "0", "invalid_configuration": false }` for the first example.

## Worked threshold edit

Change only `bronze_max` from 10 to 12. Values 11 and 12 move from silver to bronze; values below 11 and above 12 remain in their previous tiers. The alternate [public cases](tests/bronze-12-cases.json) bind that policy:

```sh
printf '"12"\n' > /tmp/bronze-max.json
target/debug/sley-tools propose --project my-tier \
  --constant bronze_max --value-file /tmp/bronze-max.json \
  --tests programs/tier-classifier/tests/bronze-12-cases.json
target/debug/sley-tools validate --project my-tier
target/debug/sley-tools commit --project my-tier
target/debug/sley-tools test --project my-tier
```

The [probe](../../scripts/probe_tier_catalog.py) also changes the lower threshold to equal the upper threshold and observes the explicit invalid-configuration result for all checked inputs. It restores the valid threshold, disables and restores the middle branch, and verifies 14 independently chosen native inputs in each policy phase. The selected core's native test executor is unavailable; these are external assertions against accepted native execution.
