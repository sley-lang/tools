# Install Sley Tools 0.0.1 RC4

RC4 is a Linux x86_64 prerelease. It runs native programs with the separately released Sley 2.0.1 core. No source checkout or model API key is required.

Download the core and Tools assets into a new empty directory:

```sh
gh release download v2.0.1 -R sley-lang/sley -p sley-2.0.1-linux-x86_64.tar.gz
gh release download v0.0.1-rc.4 -R sley-lang/tools
printf '%s  %s\n' \
  34b3b56957a9ddf9a4edc35c2a961c89928d8e78dc74c76b23f1f5378e975e44 \
  sley-2.0.1-linux-x86_64.tar.gz | sha256sum -c -
sha256sum -c SHA256SUMS.txt
```

Stop if a checksum fails. Unpack the two archives and run a native program:

```sh
tar -xzf sley-2.0.1-linux-x86_64.tar.gz
tar -xzf sley-tools-release-candidate-*.tar.gz
CORE="$PWD/sley-2.0.1-linux-x86_64/bin/sley"
TOOLS="$PWD/sley-tools-local/bin/sley-tools"
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

These assets are release downloads, not PyPI or npm registry publications. RC4 is a prerelease; compatibility with Sley 2.0.4 is not yet verified.
