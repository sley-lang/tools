# Schema Importer

Schema Importer turns a supported JSON Schema into an accepted, editable Sley validation program. Use it when you have a simple data contract and want to run its checks as a native Sley graph.

The `json-schema-records-v1` profile accepts a root Boolean or bounded integer, or a closed flat object with up to three required Boolean or bounded integer properties. Unsupported schema features are rejected explicitly. Python parses bounded JSON and projects input facts; the generated native graph decides validity and reason flags.

```sh
PYTHONPATH=packages/python sley-tools import schema \
  examples/schema/device-reading.schema.json /tmp/sley-device-reading \
  --core "$CORE"
sley-tools test --project /tmp/sley-device-reading
```

See the [supported schema, validation command, and limits](../../docs/SCHEMA_IMPORTER_V1.md) and the [generated validator example](../../programs/generated-payload-validator/README.md). An accepted validator can be edited through the normal native authoring path. The optional Python package is required for importing and raw JSON validation.
