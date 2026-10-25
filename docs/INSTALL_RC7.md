# Install Sley Tools 0.0.1 RC7

RC7 is a Linux x86_64 prerelease. It runs native programs with the separately released Sley 2.1.1 core. No source checkout or model API key is required.

Download the core and Tools assets into a new empty directory:

```sh
gh release download v2.1.1 -R sley-lang/sley -p sley-2.1.1-linux-x86_64.tar.gz
gh release download v0.0.1-rc.7 -R sley-lang/tools
printf '%s  %s\n' \
  b0898ba7404a1656db2f83f448ea96d2c1175346f35e50b135bedc96e3810bfd \
  sley-2.1.1-linux-x86_64.tar.gz | sha256sum -c -
sha256sum -c SHA256SUMS.txt
```

Stop if a checksum fails. Unpack the two archives and run a native program:

```sh
tar -xzf sley-2.1.1-linux-x86_64.tar.gz
tar -xzf sley-tools-release-candidate-*.tar.gz
CORE="$PWD/sley-2.1.1-linux-x86_64/bin/sley"
TOOLS="$PWD/sley-tools-local/bin/sley-tools"
"$TOOLS" --version
"$TOOLS" doctor --core "$CORE"
"$TOOLS" init my-program --template release-readiness --core "$CORE"
"$TOOLS" run --project my-program --input my-program/inputs/ready.json
"$TOOLS" test --project my-program
```

The accepted Sley graph computes the `run` result. `test` checks external assertions against native execution. The selected public core cannot yet admit or run core-owned native `TestCase`s. See [current native-test status](F0_NATIVE_TEST_STATUS_PUBLIC.md).

The optional Python, TypeScript, and MCP packages are separate assets. Install only what your application needs:

```sh
python3 -m venv app/.venv
app/.venv/bin/python -m pip install --no-index --no-deps ./sley_tools-0.0.1-py3-none-any.whl
mkdir -p app-node
npm install --offline --ignore-scripts --no-audit --no-fund \
  --prefix app-node ./sley-lang-tools-0.0.1.tgz
mkdir -p app-mcp
npm install --offline --ignore-scripts --no-audit --no-fund --no-package-lock \
  --prefix app-mcp ./sley-lang-tools-mcp-0.0.1.tgz
```

## Moving from RC6

RC7 uses the same Sley 2.1.1 core as RC6, so projects and exported artifacts created with RC6 work with RC7 unchanged. RC7 adds SDK and CLI reliability fixes; see the [changelog](../CHANGELOG.md).

## Moving from RC5

RC7 is pinned to the Sley 2.1.1 core; `doctor` reports any other core binary, including the 2.0.1 core used by RC5, as incompatible and names the release it found. Projects created with RC5 stay bound to the 2.0.1 core. To carry a program forward, export it with RC5 and import the artifact with RC7:

```sh
"$TOOLS" artifact import my-rc5-program.json my-program-2.1.1 --core "$CORE"
"$TOOLS" test --project my-program-2.1.1
```

Import accepts native artifacts exported for Sley 2.0.1 or 2.1.1, rebases them into a fresh workspace, and runs every declared case on the selected 2.1.1 core before accepting them.

These assets are release downloads, not PyPI or npm registry publications. The [RC6](INSTALL_RC6.md), [RC5](INSTALL_RC5.md) and [RC4](INSTALL_RC4.md) instructions remain available for those releases.
