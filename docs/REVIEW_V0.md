# Local native artifact review v0

`sley-tools review` compares two manifest-bound Release Readiness, Resource Admission, or `graph_v0` packages. The two artifacts must be from the same native program and authoring profile. A worked edit in one workspace retains entity identities, so the report can identify the exact entity version replaced. Artifacts from unrelated workspaces may have no matching entity IDs; the report lists exact additions and removals and does not infer renames.

The fixed-program report retains `sley-tools.review.v0`; the graph report uses `sley-tools.review.v1` and corresponding v1 result and verification contracts. The fixed-program report meaning and fields remain unchanged.

```sh
sley-tools review \
  --before BEFORE/program.json --after AFTER/program.json \
  --core CORE --cases programs/release-readiness/tests/bridge-cases.json \
  --output review.json
sley-tools review verify review.json \
  --before BEFORE/program.json --after AFTER/program.json \
  --core CORE --cases programs/release-readiness/tests/bridge-cases.json
```

Use the Resource Admission case file for that program. Graph packages use a `sley-tools.graph-cases.v0` file with typed positional inputs and baseline expected results; the Review command accepts up to 128 cases. Each package directory contains `program.json`, the artifact named by its `artifact.path`, and any `imported_source_artifact` referenced by the manifest. A graph package also contains the bound `build/graph.json` recipe. `artifact export --bind-manifest` creates the current artifact binding after a native edit. Keep before and after package snapshots as ordinary Git files; workspace authority and local configuration stay out of the package.

The command rejects duplicate JSON fields, symlinked files or artifact directories, oversized files, a missing or stale artifact or graph recipe checksum, mismatched core and native identities, false input/output declarations, and altered native objects. Manifests, case files, and reports are limited to 1 MiB; graph artifacts use their declared 8 MiB transfer limit. It imports each artifact into a fresh scoped workspace through core candidate validation and commit, runs the independent external assertions, and executes each declared case before and after. Baseline outputs must match the case file's expected values. The report records artifact, manifest, fixture, core binary, and tools executable checksums; exact added, removed, and changed object IDs; declared interface differences; observed case changes; and the available validation status. For graph packages it also shows native named constants from the recipe verified during import. `review verify` recomputes the report from those same inputs and rejects a changed or falsified report. Output files are created only after all checks pass and are never overwritten.

This is a bounded local review profile. The released core still lacks production native test admission; those tests are reported as `NOT_RUN`. The command checks the selected manifest fields against the imported native profile, while full JSON Schema validation remains a separate locked development command. Source roots in v0 artifacts are exporter claims without signed receipts. Finite cases show observed behavior, not general equivalence. The report identifies the tools executable by SHA-256; its source commit is not embedded. The [local probe](../evidence/review/local-v4.json) covers both worked edits, exact one-object changes, report replay, stale manifest and false interface rejection, altered artifact rejection, and falsified report rejection.

## Reusable Action

The repository includes [`actions/check/action.yml`](../actions/check/action.yml), a composite Action for Linux x86_64 pull requests. It installs Rust 1.93.0, builds the tools from the selected immutable Action checkout, installs the hash-locked manifest validator, verifies the selected Sley 2.1.1 release archive and binary, validates both manifests against the JSON Schema, runs `review` and `review verify`, writes a bounded job summary, and uploads the JSON report and diagnostics. Inputs are workspace-relative file paths; the Action refuses symlinked paths and files outside the caller workspace. The optional `core-archive` input can name a workspace-relative copy of the exact pinned archive; otherwise the Action downloads the official release asset and verifies its SHA-256 before extraction.

The repository's [smoke workflow](../.github/workflows/native-review-smoke.yml) runs this Action on the checked-in Release Readiness Review fixture. Its [GitHub-hosted manual run at commit `5c60a14`](../evidence/review/action-hosted-v1.json) passed both manifest schema checks, 48 native execution cases with 24 changed, one changed entity, report recomputation, and artifact upload. A fresh native artifact pair in [draft PR #1](https://github.com/sley-lang/tools/pull/1) then passed the read-only `pull_request` path using that immutable Action commit; the [hosted PR evidence](../evidence/review/action-pr-hosted-v1.json) pins its exact inputs, run, and uploaded report. Native core test admission remains unavailable.

```yaml
# Template: replace the tools placeholder with a reviewed, published full commit SHA.
name: Sley artifact review
on: pull_request
permissions:
  contents: read
jobs:
  review:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        with:
          persist-credentials: false
      - uses: sley-lang/tools/actions/check@<full-commit-sha>
        with:
          before: review/before/program.json
          after: review/after/program.json
          cases: review/cases.json
```

Keep the Action reference immutable and run it on an unprivileged `pull_request` job without secrets or write permission. It reads submitted native packages as data and never executes a submitted authoring script. The uploaded status is `PASS_BOUNDED_REVIEW` when all implemented checks pass; native core tests remain explicitly `NOT_RUN`, so this is not the complete F0 or RV acceptance gate. On failure, the Action retains any partial report for inspection but writes a failure summary without treating that report as validated. [Draft PR #2](https://github.com/sley-lang/tools/pull/2) changed an artifact's bytes without updating its manifest; the [hosted rejection evidence](../evidence/review/action-pr-rejection-hosted-v1.json) records a failed job and uploaded status, log, and summary. Validation stopped before a report existed. Native core test admission and public release of a tested Action commit remain open. The [local Action probe](../evidence/review/action-local-v5.json) exercises the same runner with fixed and graph packages and a tampered manifest in an external caller workspace.

The [graph Review probe](../evidence/review/graph-v4.json) covers a schema-generated validator, a Python-imported tier function, and a primitive Boolean graph. Both importer examples make a normal native constant edit, export the edited artifact, produce and verify a report, and replay the artifact. It rejects a changed graph recipe, stale manifest, altered artifact, false interface, false baseline, wrong typed case shape, and falsified report. It does not treat the original JSON Schema or Python source as the edited graph's authority.

The checked-in [review fixtures](../evidence/review/fixtures/manifest.json) preserve before and after packages from one workspace for each program. Build an independent pair in a new directory with `scripts/build_review_fixtures.py`; this script refuses to replace the checked-in pair. `scripts/probe_review_isolated.py` copies only the tools binaries, the packaged core, fixtures, and case files into a Bubblewrap mount namespace with network isolation and no source checkout. Its [replay evidence](../evidence/review/isolated-v4.json) records 48 Release Readiness and 49 Resource Admission cases, one changed native object per program, and successful report recomputation. This is an isolated environment on the same host, not evidence from a second machine or a released tools package.

An earlier two-host replay reproduced selected Review reports on another Linux x86_64 machine. That historical check does not establish current-binary compatibility, network isolation, or native core test admission.

```sh
uv run --no-project --python 3.14 --with-requirements requirements-dev.lock python3 scripts/build_review_fixtures.py \
  --tools target/debug/sley-tools \
  --core /path/to/sley-2.1.1-linux-x86_64/bin/sley \
  --release-artifact programs/release-readiness/artifact/native-program.json \
  --resource-artifact programs/resource-admission/artifact/native-program.json \
  --schema schemas/program-v0.schema.json \
  --output-dir /path/to/new-review-fixtures
```
