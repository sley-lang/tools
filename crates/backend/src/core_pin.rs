// The one Sley core release these tools are built, tested, and pinned against.
// Both binaries include this file, so the CLI identity check, exported
// artifacts, and artifact import all agree on the same release.

/// Public Sley release tag, without the leading `v`.
#[allow(dead_code)]
pub const TESTED_CORE_RELEASE: &str = "2.1.1";
/// Source commit recorded in the release `MANIFEST.json`.
pub const TESTED_CORE_COMMIT: &str = "af34be33eb1ae913dddd92aa7f4f275aac0e45ee";
/// SHA-256 of `bin/sley` in `sley-2.1.1-linux-x86_64.tar.gz`.
#[allow(dead_code)]
pub const TESTED_CORE_BINARY_SHA256: &str =
    "b2d8ce53051043cd86f0c071b16584053d9a08ec7af5d2d8a91fa3ab68a79e52";
