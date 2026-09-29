#!/usr/bin/env python3
"""Differential and native-edit probe for the restricted Python importer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import resource
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/python"))
from sley_tools.python_importer import PythonImportError, import_project, run_project  # noqa: E402

REFERENCE = """import json,sys
payload=json.load(sys.stdin)
namespace={}
exec(compile(payload['source'], '<explicit-reference>', 'exec'), namespace)
function=namespace[payload['function']]
json.dump([function(*row) for row in payload['vectors']],sys.stdout)
"""


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(*command: str) -> dict:
    process = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if process.returncode:
        raise RuntimeError(f"{command[1]} failed: {process.stderr[:500]}")
    return json.loads(process.stdout)


def reference(source: Path, function: str, vectors: list[list[object]]) -> list[object]:
    payload = {"source": source.read_text(), "function": function, "vectors": vectors}

    def limits() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))

    process = subprocess.run(
        ["bwrap", "--unshare-net", "--die-with-parent", "--tmpfs", "/",
         "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib",
         "--ro-bind", "/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
         "--chdir", "/", "--", sys.executable, "-I", "-S", "-c", REFERENCE],
        input=json.dumps(payload), capture_output=True, text=True, timeout=20, preexec_fn=limits,
    )
    if process.returncode:
        raise RuntimeError(f"isolated Python reference failed: {process.stderr[:500]}")
    return json.loads(process.stdout)


def vectors_for(name: str, manifest: dict) -> list[list[object]]:
    if name == "range":
        return [[n] for n in range(-10, 11)]
    if name == "clamp":
        return [[n] for n in range(-20, 21)]
    if name == "tier":
        return [[n] for n in range(101)]
    seed = random.Random(20260927)
    pairs = {(capacity, used) for capacity in (0, 1, 20, 50, 99, 100)
             for used in (0, 1, 20, 50, 99, 100)}
    pairs.update((seed.randrange(101), seed.randrange(101)) for _ in range(20))
    return [list(pair) for pair in sorted(pairs)]


def native(tools: Path, project: Path, vector: list[object], scratch: Path) -> dict:
    input_file = scratch / "input.json"
    input_file.write_text(json.dumps([str(value) if type(value) is int else value for value in vector]))
    process = subprocess.run(
        [str(tools), "run", "--project", str(project), "--input", str(input_file)],
        capture_output=True, text=True, timeout=120,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
    )
    if process.returncode:
        raise RuntimeError(f"native execution without Python path failed: {process.stderr[:500]}")
    return json.loads(process.stdout)["result"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "core", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    tools, core = args.tools.resolve(), args.core.resolve()
    examples = Path(__file__).resolve().parents[1] / "examples/python-importer"
    rows = []
    with tempfile.TemporaryDirectory(prefix="sley-python-probe-") as temporary:
        root = Path(temporary)
        for name, function in (("range", "in_range"), ("clamp", "clamp"),
                               ("quota", "quota_remaining"), ("tier", "tier")):
            source, domain = examples / f"{name}.py", examples / f"{name}.domain.json"
            project = root / name
            imported = invoke(str(tools), "import", "python", str(source), str(domain), str(project),
                              "--core", str(core))
            if imported["contract"] != "sley-tools.python-import.v1":
                raise RuntimeError("Python importer contract differs")
            manifest = json.loads(domain.read_text())
            vectors = vectors_for(name, manifest)
            expected = reference(source, function, vectors)
            if len(expected) != len(vectors):
                raise RuntimeError("reference output length differs")
            for vector, answer in zip(vectors, expected, strict=True):
                result = native(tools, project, vector, root)
                if result["in_domain"] is not True or result["value"] != (
                    answer if type(answer) is bool else {"ok": str(answer)}
                ):
                    raise RuntimeError(f"{name} differs from reference at {vector}: {result} != {answer}")
            for index, parameter in enumerate(manifest["parameters"]):
                if parameter["type"] == "int":
                    lower, upper = int(parameter["minimum"]), int(parameter["maximum"])
                    for outside in (lower - 1, upper + 1):
                        if -(2**63) <= outside <= 2**63 - 1:
                            vector = vectors[0].copy()
                            vector[index] = outside
                            if native(tools, project, vector, root)["in_domain"] is not False:
                                raise RuntimeError(f"{name} failed native domain enforcement")
            mapping = json.loads((project / "python-source/mapping.json").read_text())
            if not mapping["operation_spans"] or not all(item["entity_id"] for item in mapping["operation_spans"]):
                raise RuntimeError(f"{name} source spans lack native operation entity IDs")
            generated = json.loads((project / "tests/cases.json").read_text())["cases"]
            generated_domain_cases = sum(case["id"].startswith("domain-") for case in generated)
            expected_domain_cases = 4 if name == "quota" else 2
            if len(generated) != len(manifest["cases"]) + expected_domain_cases or generated_domain_cases != expected_domain_cases:
                raise RuntimeError(f"{name} native project omitted generated domain-guard tests")
            rows.append({"name": name, "source_sha256": digest(source), "domain_sha256": digest(domain),
                         "accepted_root": imported["accepted_state_root"],
                         "artifact_sha256": imported["native_artifact_sha256"],
                         "differential_cases": len(vectors), "generated_domain_cases": generated_domain_cases,
                         "isolated_reference": "bubblewrap networkless runtime-only mounts"})

        # Import only parses; a source with a top-level side effect must fail without executing it.
        marker = root / "executed"
        source = root / "side-effect.py"
        source.write_text(f"open({str(marker)!r}, 'w').write('bad')\n" + (examples / "range.py").read_text())
        try:
            import_project(source, examples / "range.domain.json", root / "rejected-side-effect", tools, core)
        except PythonImportError:
            pass
        else:
            raise RuntimeError("top-level side effect imported")
        if marker.exists() or (root / "rejected-side-effect").exists():
            raise RuntimeError("rejected source executed or published")

        try:
            import_project(examples / "quota.py", examples / "quota-unsafe.domain.json",
                           root / "rejected-overflow", tools, core)
        except PythonImportError as error:
            if "overflow" not in str(error):
                raise RuntimeError("unsafe quota was rejected for another reason") from error
        else:
            raise RuntimeError("potentially overflowing quota imported")
        if (root / "rejected-overflow").exists():
            raise RuntimeError("unsafe quota published a project")

        small_domain = {
            "contract": "sley-tools.python-domain.v1", "profile": "python-pure-i64-v1",
            "parameters": [{"name": "x", "type": "int", "minimum": "-3", "maximum": "3"}],
            "cases": [{"id": "zero", "inputs": {"x": "0"}, "expected": "0"}],
        }
        small_domain_file = root / "small-domain.json"
        small_domain_file.write_text(json.dumps(small_domain))
        negative_sources = {
            "call": "def f(x: int) -> int:\n    return abs(x)\n",
            "truthiness": "def f(x: int) -> int:\n    if x:\n        return 1\n    return 0\n",
            "nested-arithmetic": "def f(x: int) -> int:\n    return (x + 1) * 2\n",
            "missing-return": "def f(x: int) -> int:\n    if x > 0:\n        return 1\n",
        }
        rejected = []
        for name, text in negative_sources.items():
            file = root / f"{name}.py"
            file.write_text(text)
            destination = root / f"rejected-{name}"
            try:
                import_project(file, small_domain_file, destination, tools, core)
            except PythonImportError as error:
                if error.code not in ("ST_IMPORT_UNSUPPORTED", "ST_INPUT_INVALID"):
                    raise RuntimeError(f"{name} failed with the wrong diagnostic {error.code}") from error
            else:
                raise RuntimeError(f"unsupported {name} source imported")
            if destination.exists():
                raise RuntimeError(f"unsupported {name} source published a project")
            rejected.append(name)
        missing_domain = root / "missing-domain.json"
        malformed = dict(small_domain)
        malformed["parameters"] = []
        missing_domain.write_text(json.dumps(malformed))
        try:
            import_project(root / "nested-arithmetic.py", missing_domain,
                           root / "rejected-missing-domain", tools, core)
        except PythonImportError as error:
            if error.code != "ST_INPUT_INVALID":
                raise RuntimeError("missing domain received the wrong diagnostic") from error
        else:
            raise RuntimeError("missing domain was accepted")

        positive_sources = {
            "boolean-branches": ("def f(x: int) -> bool:\n    return not (x < 0) or x > 2\n",
                                 True, [[-3], [0], [3]], [False, True, True]),
            "if-expression": ("def f(x: int) -> int:\n    return -1 if x < 0 else 2\n",
                              "2", [[-3], [0], [3]], [-1, 2, 2]),
            "multiply": ("def f(x: int) -> int:\n    return x * 3\n",
                         "0", [[-3], [0], [3]], [-9, 0, 9]),
            "negate": ("def f(x: int) -> int:\n    return -x\n",
                       "0", [[-3], [0], [3]], [3, 0, -3]),
        }
        for name, (text, expected_zero, vectors, answers) in positive_sources.items():
            file = root / f"{name}.py"
            file.write_text(text)
            domain = dict(small_domain)
            domain["cases"] = [{"id": "zero", "inputs": {"x": "0"}, "expected": expected_zero}]
            domain_file = root / f"{name}.domain.json"
            domain_file.write_text(json.dumps(domain))
            project = root / f"accepted-{name}"
            import_project(file, domain_file, project, tools, core)
            for vector, answer in zip(vectors, answers, strict=True):
                result = native(tools, project, vector, root)
                if result != {"in_domain": True, "value": answer if type(answer) is bool else {"ok": str(answer)}}:
                    raise RuntimeError(f"{name} native result differs at {vector}")

        tier = root / "tier"
        mapping = json.loads((tier / "python-source/mapping.json").read_text())
        recipe = json.loads((tier / "build/graph.json").read_text())
        threshold = next(item for item in mapping["constant_spans"] if item["line"] == 2)
        constant = threshold["constant"]
        if next(item for item in recipe["constants"] if item["name"] == constant)["value"] != "25":
            raise RuntimeError("tier source span mapped the wrong threshold")
        edited_cases = json.loads((tier / "tests/cases.json").read_text())
        next(item for item in edited_cases["cases"] if item["id"] == "second")["expected"]["value"] = {"ok": "0"}
        cases_file, value_file = root / "tier-after-cases.json", root / "new-threshold.json"
        cases_file.write_text(json.dumps(edited_cases))
        value_file.write_text('"26"\n')
        before = json.loads((tier / ".sley-tools/state.json").read_text())["state_root"]
        invoke(str(tools), "propose", "--project", str(tier), "--constant", constant,
               "--value-file", str(value_file), "--tests", str(cases_file))
        invoke(str(tools), "validate", "--project", str(tier))
        invoke(str(tools), "commit", "--project", str(tier))
        invoke(str(tools), "test", "--project", str(tier))
        after = json.loads((tier / ".sley-tools/state.json").read_text())["state_root"]
        if before == after or native(tools, tier, [25], root)["value"] != {"ok": "0"}:
            raise RuntimeError("native tier edit did not change the accepted behavior")
        artifact = tier / "artifact/native-graph-edited.json"
        invoke(str(tools), "artifact", "export", "--project", str(tier),
               "--output", str(artifact), "--bind-manifest")
        if not artifact.is_file():
            raise RuntimeError("edited artifact was not exported")
        edited_artifact_sha256 = digest(artifact)
        named_input = root / "named-input.json"
        named_input.write_text('{"score":"25"}\n')
        adapted = run_project(tier, named_input, tools)
        if adapted["source_alignment"] != "native_edited" or adapted["value"] != "0":
            raise RuntimeError("named input adapter did not report the native edit")
        stored_source = tier / "python-source/source.py"
        original_source = stored_source.read_bytes()
        stored_source.write_bytes(original_source + b"# tampered\n")
        try:
            run_project(tier, named_input, tools)
        except PythonImportError as error:
            if error.code != "ST_STALE_BASE":
                raise RuntimeError("tampered source received the wrong diagnostic") from error
        else:
            raise RuntimeError("tampered stored source was accepted")
        stored_source.write_bytes(original_source)

    report = {"contract": "sley-tools.python-importer-probe.v1", "result": "PASS",
              "tools_sha256": digest(tools), "core_sha256": digest(core),
              "importer_sha256": digest(Path(__file__).resolve().parents[1] / "packages/python/sley_tools/python_importer.py"),
              "probe_sha256": digest(Path(__file__).resolve()),
              "examples": rows, "differential_cases": sum(item["differential_cases"] for item in rows),
              "unsafe_arithmetic_rejected": True, "source_never_executed_during_import": True,
              "missing_domain_rejected": True, "unsupported_rejections": rejected,
              "additional_native_syntax_cases": sorted(positive_sources),
              "native_run_without_pythonpath": True,
              "named_adapter_native_edit_and_source_binding": True,
              "native_domain_guard_verified": True,
              "native_edit_without_reimport": {"before_root": before, "after_root": after,
                                               "artifact_sha256": edited_artifact_sha256},
              "native_core_test_admission": "NOT_RUN: production native executor unavailable"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
