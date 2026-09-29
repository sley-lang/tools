# Restricted pure Python function importer

`sley-tools import python` parses one annotated pure Python function and an explicit bounded domain into an accepted native Sley graph. The generated graph enforces integer input ranges and computes the result in the selected Sley VM. Python source is retained for provenance; native execution and later native edits do not load it.

```sh
PYTHONPATH=packages/python sley-tools import python \
  examples/python-importer/quota.py examples/python-importer/quota.domain.json \
  /tmp/quota-sley --core /path/to/sley
sley-tools test --project /tmp/quota-sley
sley-tools run --project /tmp/quota-sley --input /tmp/quota-sley/inputs/example.json
```

The import module also accepts named input objects and reports `in_domain`, the decoded value, the accepted state root, and whether the graph has since been edited:

```sh
printf '{"capacity":"100","used":"37"}\n' > /tmp/quota-input.json
PYTHONPATH=packages/python python3 -m sley_tools.python_importer run \
  --project /tmp/quota-sley --input /tmp/quota-input.json --tools target/debug/sley-tools
```

The `python-pure-i64-v1` profile pins CPython 3.14 AST parsing and accepts an ordinary synchronous function with matching `int`/`bool` annotations, Boolean and signed 64 bit literals, parameter references, single-name local bindings without reassignment, `if`/`else`, conditional expressions, returns, comparisons, Boolean `and`/`or`/`not`, and statically safe `+`, `-`, `*`, and unary negation. Boolean `and` and `or` lower to short-circuit branches. Every reachable path must return the annotated type. An optional leading docstring and comments are metadata.

Arithmetic uses conservative interval analysis over the declared domain. Potentially overflowing intermediates are rejected even if a branch might make them unreachable; the [unsafe quota domain](../examples/python-importer/quota-unsafe.domain.json) illustrates this. The current native authoring profile returns checked arithmetic results but cannot extract one for another operation. As a result, nested arithmetic and comparisons of computed arithmetic are explicitly rejected even when their intervals are safe. Imports, calls, attributes, subscripts, containers, comprehensions, loops, exceptions, classes, decorators, defaults, reflection, arbitrary truthiness, division, modulo, floats, and Python's unbounded integer semantics are outside this profile.

The domain JSON names one through eight ordered parameters with exact types; integer bounds and values are canonical decimal strings. It supplies one through 64 valid-domain cases whose expected outputs are checked against the accepted graph before import succeeds. The importer also generates representable below/above-bound cases and tests their native rejection during initialization. These are test inputs, not facts inferred by executing source. Input outside a declared integer bound returns `{"in_domain":false,...}` from the native graph; the named-input adapter reports no value. Inputs outside signed 64 bit representation are rejected at transport. Import caps source at 32 KiB, domain JSON at 64 KiB, AST nodes at 256, and AST depth at 16; generated graphs also obey the builder's block, operation, and constant limits.

The [four examples](../examples/python-importer/) cover a bounded range predicate, clamp, quota remaining, and tier classifier. The [probe](../evidence/python-importer/native-v3.json) compares their native artifacts to separately executed references in a networkless Bubblewrap process, checks native out-of-domain behavior, rejects a top-level side effect without executing it, rejects unsafe arithmetic before publication, and edits a generated tier threshold through the standard native proposal/validate/commit/test/export path. This evidence supports the tested examples and static interval property; it is not a proof of all possible Python source translations. Production native core test admission remains unavailable in the selected release.

The project retains `python-source/source.py`, `domain.json`, and `mapping.json`. Mapping entries bind source spans to native operation and constant entity IDs, and include the initial recipe digest. The accepted graph is authoritative after a native edit. Reimport requires a new project path; the importer does not overwrite an existing project.

The normal native constant edit can be inspected, tested, reviewed, and exported. The [graph Review probe](../evidence/review/graph-v2.json) reviews a threshold edit to the imported tier function with exact native object identities, baseline cases, report recomputation, and edited artifact replay. This completes the tested tier example's PI-06 path; it does not imply that every accepted Python source shape has been independently reviewed.
