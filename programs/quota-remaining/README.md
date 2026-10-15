# Quota Remaining (N09)

This native Sley utility reports nonnegative quota remaining, whether the quota is exhausted, whether an input is invalid, and whether usage reached a configurable notification level. Inputs are signed 64 bit values, but the declared application domain requires nonnegative `limit` and `used`. Negative inputs return `invalid_input=true` with a sentinel remaining value of zero. Callers must check that flag before using the remaining value.

The graph checks the nonnegative domain first, then checks `used >= limit`. Exhausted inputs return zero. Only the branch with `0 <= used < limit` performs checked `limit - used`, so its result fits signed 64 bit. The accepted Sley graph makes each decision and performs the arithmetic; Python or the CLI handles typed input and output transport.

The [manifest](program.json) binds the [native artifact](artifact/native-graph.json), [authoring recipe](build/graph.json), public [initial cases](tests/cases.json), and selected core profile. Recreate the native program through the supported authoring path:

```sh
target/debug/sley-tools init my-quota \
  --graph programs/quota-remaining/build/graph.json \
  --tests programs/quota-remaining/tests/cases.json --core "$CORE"
target/debug/sley-tools test --project my-quota
```

Or import the portable artifact and call it from Python:

```sh
target/debug/sley-tools artifact import \
  programs/quota-remaining/artifact/native-graph.json my-quota --core "$CORE"
PYTHONPATH=packages/python python3 packages/python/examples/quota_remaining.py \
  --tools target/debug/sley-tools --core "$CORE" \
  --artifact programs/quota-remaining/artifact/native-graph.json \
  --limit 20 --used 7
```

`limit=20, used=7` returns checked success `remaining=13`, `exhausted=false`, and `invalid_input=false`. The initial `notify_at_used=8` also returns `at_notification_level=false`. CLI integer values use canonical decimal strings; the Python SDK returns native checked success with a Python integer.

## Worked native notification edit

Change only the notification threshold from `8` to `6`. The [updated cases](tests/notify-6-cases.json) leave remaining and exhaustion unchanged while inputs with `used=6` or `7` reach the notification level:

```sh
printf '"6"\n' > /tmp/quota-notify.json
target/debug/sley-tools propose --project my-quota \
  --constant notify_at_used --value-file /tmp/quota-notify.json \
  --tests programs/quota-remaining/tests/notify-6-cases.json
target/debug/sley-tools validate --project my-quota
target/debug/sley-tools commit --project my-quota
target/debug/sley-tools test --project my-quota
```

The [catalog probe](../../evidence/catalog/quota-remaining-v2.json) checks the authored and imported programs, signed extremes and negative inputs, this edit and its reversal, and the restored artifact in another workspace. Production native core test admission remains unavailable; these are external assertions against native execution.

This package is included in the [RC6 prerelease](../../docs/INSTALL_RC6.md) under [Apache-2.0](../../LICENSE). The stable release gate remains open.
