# Release candidate build

RC7 targets Linux x86_64 with the separately verified Sley 2.1.1 core. It is a prerelease because core-owned native `TestCase` admission and the live coding-agent acceptance run remain open. [Install the published assets](INSTALL_RC7.md) to use it without a checkout.

Build from a clean source checkout with Rust 1.93.0. Put release output and temporary files on a filesystem with enough space:

```sh
export TMPDIR=/absolute/path/to/writable-tmp
export CARGO_TARGET_DIR=/absolute/path/to/release-target
export RUSTFLAGS="--remap-path-prefix=${HOME}=/build"
cargo +1.93.0 build --release --locked --offline \
  -p sley-tools-backend --bin sley-tools --bin sley-tools-native-builder
python3 scripts/probe_release_package.py \
  --tools "$CARGO_TARGET_DIR/release/sley-tools" \
  --builder "$CARGO_TARGET_DIR/release/sley-tools-native-builder" \
  --core-archive /absolute/path/to/sley-2.1.1-linux-x86_64.tar.gz \
  --archive-output /absolute/path/to/dist/sley-tools-release-candidate.tar.gz \
  --report /absolute/path/to/report.json
```

The workspace release profile enables fat LTO, one codegen unit, and symbol stripping, so no profile overrides are needed. The package probe checks a clean checkout, audits the files, builds the native program bundle, verifies its checksums, and runs a first native project against the pinned core. The Python wheel, TypeScript tarball, and standalone MCP tarball are separate assets; `scripts/probe_adapter_archives.py` audits their package contents. The [fresh install procedure](FRESH_INSTALL_V1.md) checks the installed toolkit with the source checkout hidden and networking disabled. Publication must use the exact verified assets and keep their digests in `SHA256SUMS.txt`.
