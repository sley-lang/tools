# Schema Importer: local `json-schema-records-v1` profile

`sley-tools import schema` turns a restricted JSON Schema draft 2020-12 document into an accepted native Sley graph project. The Python package supplies bounded, exact JSON parsing and the graph recipe. The Rust toolkit submits that recipe through the ordinary native candidate lifecycle, tests the accepted graph, and exports a manifest-bound artifact. No reference JSON Schema validator runs in the import or validation path.

From this repository, with the selected core already unpacked:

```sh
cargo build --locked -p sley-tools-backend --bin sley-tools
PYTHONPATH=packages/python target/debug/sley-tools import schema \
  examples/schema/device-reading.schema.json /tmp/sley-device-reading \
  --core /path/to/sley-2.0.1-linux-x86_64/bin/sley
printf '{"active":true,"count":1.0}\n' > /tmp/sley-device-reading-input.json
PYTHONPATH=packages/python python3 -m sley_tools.schema_importer validate \
  --project /tmp/sley-device-reading \
  --input /tmp/sley-device-reading-input.json \
  --tools target/debug/sley-tools
target/debug/sley-tools inspect --project /tmp/sley-device-reading
target/debug/sley-tools test --project /tmp/sley-device-reading
```

The destination must not exist. Installed users need the optional `sley_tools` Python package available to the `python3` interpreter used by `sley-tools`; its installed `sley-schema` command can also validate raw input. The importer does not download dependencies or fetch schema references.

## Accepted subset

- Root `type: "boolean"` or `type: "integer"`, or a flat `type: "object"` with zero to three listed properties.
- Each object property is Boolean or bounded signed 64 bit integer. Every property appears exactly once in `required`; `additionalProperties: false` must be explicit.
- Every integer schema explicitly declares integral `minimum` and `maximum` within signed 64 bit range, with minimum no greater than maximum.
- `$schema`, `$id`, `title`, and `description` are the only accepted annotations. If `$schema` is present it must name draft 2020-12. Property order, `required` order, and annotations do not change the generated graph.
- Literal Boolean schemas (`true` and `false`), optional properties, arrays, nested objects, references, unions, patterns, formats, and every other validation keyword are outside this profile. Import rejects them with a JSON Pointer to the unsupported keyword or shape.

The three-property ceiling comes from the current graph builder's 16 input parameters and 16 result fields. The input parser limits each JSON document to 65,536 bytes, nesting to 16 levels, objects to 64 keys, and arrays to 64 items. Limit failures are resource errors. Malformed JSON and duplicate object keys are input errors; a well-formed value that violates the accepted schema returns `valid: false` from a successful native invocation.

## Host and native responsibilities

The host parser preserves each number token and computes only mechanical facts: root kind, property presence, raw Boolean or mathematically integral numeric kind, representability as signed 64 bit, safe numeric value, and extra-property count. It does not apply the schema's requiredness, type, additional-property, or numeric-bound rules. The generated Sley graph checks those facts and returns `valid` plus stable reason flags. The adapter exposes a typed payload only after the native result is valid.

`1`, `1.0`, `1e0`, and `10e-1` project to the same integer value. `1.5` is nonintegral; `true` and `"1"` are different JSON kinds. An integral number outside signed 64 bit is represented as outside native range with a safe placeholder value, so no truncation can make it valid. A zero with a very large exponent remains zero. The parser rejects duplicate keys rather than choosing one value.

The project stores `schema/source.json` and `schema/mapping.json` beside its normal native graph recipe, cases, manifest, and artifact. The mapping identifies generated parameters, reason flags, and bound constants by schema path. The generated graph is an ordinary graph project: `inspect`, `test`, `propose --constant`, `validate`, `commit`, and `artifact export --bind-manifest` operate on it. After a native edit, raw-input validation reports `source_schema_alignment: "native_edited"`; the original schema is a provenance record, not an authority that silently overwrites the edit. The portable native artifact executes without the original schema or Python parser when supplied with its typed envelope.

The [local probe](../evidence/schema-importer/native-v4.json) checks object, Boolean, integer, and empty-record schemas against `jsonschema` 4.25.1 with `Decimal` JSON decoding and an exact-integer type checker. It covers missing and extra fields, wrong kinds, bounds, decimal and exponent forms, wide values, unsupported keyword locations, parser limits, artifact reimport, and an accepted native bound edit. The [local package install probe](../evidence/fresh-install/local-v6.json) checks an imported validator, a native maximum-bound edit, changed raw-input behavior, and rejection of an unsupported keyword. Broader installed-profile coverage and native core test admission remain open.

The [graph Review probe](../evidence/review/graph-v2.json) also packages the generated validator before and after a native bound edit, verifies the exact changed constant entity and observed case differences, rejects altered package inputs, and replays the edited artifact. The original schema remains source provenance after the edit.
