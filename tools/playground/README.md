# Sley Playground

Playground is an optional local browser interface for exploring three existing native examples. It can run typed inputs, show the accepted program, guide supported edits, and export a changed artifact. The decisions and calculations run through the Sley core on the local machine, not in the browser.

From the repository root, with the CLI built and `CORE` set to a verified Sley 2.0.1 binary:

```sh
target/debug/sley-tools playground --adapter apps/playground/server.mjs \
  --core "$CORE" --port 4177
```

Open `http://127.0.0.1:4177`. The [Playground setup and limits](../../apps/playground/README.md) explain its local session behavior and examples. Hosted deployment remains unqualified. Playground is not required to use the CLI, SDKs, or native program packages.
