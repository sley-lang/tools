# Generated Payload Validator (N10)

This package validates a device-reading JSON record with required `active` Boolean and `count` integer fields. `count` must be between 0 and 10, and extra fields are rejected. The checked-in [source schema](schema/source.json) belongs to the restricted `json-schema-records-v1` profile. The [mapping](schema/mapping.json) connects schema paths to the typed input envelope, reason flags, and source native entities.

The accepted [native Sley artifact](artifact/native-graph.json) checks requiredness, types, extra properties, integer representability, and bounds. Python parses bounded raw JSON exactly and projects mechanical facts into the envelope. It does not decide schema validity. The native artifact can run with a typed envelope without the raw JSON adapter. Its source authority is rebased when imported into another workspace.

Run the complete raw-input example:

```sh
printf '{"active":true,"count":10}\n' > /tmp/device-reading.json
python3 packages/python/examples/generated_payload_validator.py \
  --tools target/debug/sley-tools \
  --core /path/to/sley-2.1.1-linux-x86_64/bin/sley \
  --package programs/generated-payload-validator \
  --input /tmp/device-reading.json
```

The example imports the artifact into a disposable workspace, rebinds the schema mapping to that workspace's native entity IDs, and asks the native graph for the validity and reason flags. A well-formed but invalid record returns a successful invocation with `valid=false`. Malformed JSON and duplicate keys are input errors. The [raw cases](tests/raw-cases.json) include boundaries, numeric spellings, missing and extra fields, wrong types, and signed-range overflow. The [typed cases](tests/cases.json) are embedded in the artifact and run through `sley-tools test`.

## Worked native bound edit

Import the artifact, then change the native `count` maximum from 10 to 12 through the normal proposal lifecycle:

```sh
target/debug/sley-tools artifact import \
  programs/generated-payload-validator/artifact/native-graph.json my-validator \
  --core /path/to/sley-2.1.1-linux-x86_64/bin/sley
printf '"12"\n' > /tmp/count-maximum.json
target/debug/sley-tools propose --project my-validator \
  --constant p1_maximum --value-file /tmp/count-maximum.json \
  --tests programs/generated-payload-validator/tests/max-12-cases.json
target/debug/sley-tools validate --project my-validator
target/debug/sley-tools commit --project my-validator
target/debug/sley-tools test --project my-validator
```

The edited validator accepts 11 and 12, then rejects 13. Its original source schema remains provenance; raw validation reports `source_schema_alignment=native_edited`. The [catalog probe](../../evidence/catalog/generated-payload-validator-v2.json) compares initial native decisions with an independent exact-number JSON Schema reference, verifies the edit, and imports the edited artifact into another workspace.

This package is included in the [RC7 prerelease](../../docs/INSTALL_RC7.md) under [Apache-2.0](../../LICENSE). Native core test admission and the stable release gate remain open.
