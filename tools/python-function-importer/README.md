# Python Function Importer

Python Function Importer converts a restricted pure Python function and an explicit input domain into an accepted Sley program. Use it to move a small, bounded decision or calculation into Sley, then inspect, test, and edit the native result.

```sh
PYTHONPATH=packages/python sley-tools import python \
  examples/python-importer/quota.py examples/python-importer/quota.domain.json \
  /tmp/quota-sley --core "$CORE"
sley-tools test --project /tmp/quota-sley
```

The `python-pure-i64-v1` profile handles a defined subset of typed integer and Boolean expressions and branches. It rejects imports, calls, loops, and arithmetic that may overflow its declared domain. The importer parses source without executing it; after import, the Sley graph performs the computation. See the [exact accepted syntax and limits](../../docs/PYTHON_IMPORTER_V1.md) and [four examples](../../examples/python-importer/). The optional Python package is required for import.
