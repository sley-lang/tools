#!/usr/bin/env python3
"""Build local packages and exercise them with the checkout hidden and network off."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1]
CORE_ARCHIVE_SHA256 = "b0898ba7404a1656db2f83f448ea96d2c1175346f35e50b135bedc96e3810bfd"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def call(*args: object, cwd: Path | None = None, env: dict[str, str] | None = None,
         timeout: int = 120) -> subprocess.CompletedProcess[str]:
    command = [str(item) for item in args]
    completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                               text=True, timeout=timeout, check=False)
    if completed.returncode:
        raise RuntimeError(f"{command[:3]} exited {completed.returncode}: "
                           f"{completed.stderr[-800:] or completed.stdout[-800:]}")
    return completed


def json_call(*args: object, cwd: Path | None = None,
              env: dict[str, str] | None = None) -> dict:
    return json.loads(call(*args, cwd=cwd, env=env).stdout)


def rejected_json(*args: object, cwd: Path, code: str) -> dict:
    process = subprocess.run([str(item) for item in args], cwd=cwd,
                             capture_output=True, text=True, timeout=120, check=False)
    try:
        diagnostic = json.loads(process.stderr)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"rejected command returned unreadable diagnostic: {process.stderr[-400:]}") from error
    if process.returncode != 2 or diagnostic.get("code") != code:
        raise RuntimeError(f"rejected command used wrong status or code: {diagnostic}")
    return diagnostic


def copy_bundle(source: Path, target: Path, tools: Path, builder: Path) -> None:
    (target / "bin").mkdir(parents=True)
    shutil.copy2(tools, target / "bin/sley-tools")
    shutil.copy2(builder, target / "bin/sley-tools-native-builder")
    for name in ("programs", "docs", "examples", "apps/repair-lab", "apps/playground"):
        shutil.copytree(source / name, target / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(source / "packages/mcp", target / "packages/mcp",
                    ignore=shutil.ignore_patterns("node_modules"))
    (target / "packages/typescript").mkdir()
    shutil.copytree(source / "packages/typescript/src", target / "packages/typescript/src")
    shutil.copy2(source / "packages/typescript/package.json",
                 target / "packages/typescript/package.json")
    for name in ("LICENSE", "NOTICE"):
        shutil.copy2(source / "packages/typescript" / name,
                     target / "packages/typescript" / name)
    shutil.copytree(source / "evidence/review/fixtures", target / "evidence/review/fixtures")
    (target / "scripts").mkdir()
    shutil.copy2(source / "scripts/probe_fresh_install.py", target / "scripts/probe_fresh_install.py")
    shutil.copy2(source / "scripts/probe_repair_lab.py", target / "scripts/probe_repair_lab.py")
    for name in ("probe_genesis.py", "probe_native_test_route.py",
                 "probe_authorized_native_test.py"):
        shutil.copy2(source / "scripts" / name, target / "scripts" / name)
    shutil.copy2(source / "scripts/probe_playground_local.mjs",
                 target / "scripts/probe_playground_local.mjs")
    (target / "schemas").mkdir()
    shutil.copy2(source / "schemas/program-v0.schema.json", target / "schemas/program-v0.schema.json")
    for name in ("README.md", "LICENSE", "NOTICE"):
        shutil.copy2(source / name, target / name)


def deterministic_tar(source: Path, output: Path) -> None:
    with output.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0,
                                                    filename="") as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for file in sorted(source.rglob("*")):
                if not file.is_file():
                    continue
                relative = file.relative_to(source)
                info = tarfile.TarInfo(f"sley-tools-local/{relative.as_posix()}")
                info.size = file.stat().st_size
                info.mode = 0o755 if os.access(file, os.X_OK) else 0o644
                info.mtime = 0
                with file.open("rb") as contents:
                    archive.addfile(info, contents)


def restricted_environment(stage: Path) -> dict[str, str]:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is needed to install the built Python wheel offline")
    return {
        "PATH": f"{Path(uv).parent}:/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "XDG_CONFIG_HOME": str(stage / "config"),
        "XDG_CACHE_HOME": str(stage / "cache"),
        "TMPDIR": str(stage / "tmp"),
        "NPM_CONFIG_CACHE": str(stage / "cache/npm"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def package_and_run(args: argparse.Namespace) -> dict:
    tools, builder, core_archive = (getattr(args, name).resolve() for name in
                                    ("tools", "builder", "core_archive"))
    if digest(core_archive) != CORE_ARCHIVE_SHA256:
        raise RuntimeError("core archive differs from the verified selected release")
    if not tools.is_file() or not builder.is_file():
        raise RuntimeError("tools binaries must be built before fresh-install acceptance")
    source_commit = call("git", "rev-parse", "HEAD", cwd=SOURCE).stdout.strip()
    source_dirty = bool(call("git", "status", "--porcelain", "--untracked-files=all",
                             cwd=SOURCE).stdout.strip())
    with tempfile.TemporaryDirectory(prefix="sley-fresh-install-") as temporary:
        stage = Path(temporary)
        for name in ("dist", "tmp", "cache", "config", "work", "payload"):
            (stage / name).mkdir()
        copy_bundle(SOURCE, stage / "payload", tools, builder)
        call("npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--prefer-offline",
             cwd=stage / "payload/packages/mcp", timeout=180)
        bundle = stage / "dist/sley-tools-local.tar.gz"
        deterministic_tar(stage / "payload", bundle)
        wheel_build = call("uv", "build", "--offline", "--wheel", SOURCE / "packages/python",
                           "--out-dir", stage / "dist", cwd=stage)
        wheels = list((stage / "dist").glob("sley_tools-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"expected one built Python wheel: {wheel_build.stdout[-300:]}")
        npm_report = json.loads(call("npm", "pack", "--json", "--ignore-scripts",
                                    "--pack-destination", stage / "dist",
                                    cwd=SOURCE / "packages/typescript").stdout)
        npm_package = stage / "dist" / npm_report[0]["filename"]
        mcp_package_report = json.loads(call(
            sys.executable, SOURCE / "scripts/package_mcp.py", "--output-dir", stage / "dist",
            "--report", stage / "dist/mcp-package.json", cwd=stage, timeout=240).stdout)
        mcp_package = stage / "dist" / mcp_package_report["filename"]
        if mcp_package_report["archive_sha256"] != digest(mcp_package):
            raise RuntimeError("standalone MCP package report lost its archive identity")
        shutil.copy2(core_archive, stage / "dist/sley-core.tar.gz")
        shutil.copy2(SOURCE / "scripts/probe_fresh_install.py", stage / "runner.py")
        environment = restricted_environment(stage)
        sandbox = ["bwrap", "--ro-bind", "/", "/", "--dev-bind", "/dev", "/dev",
                   "--proc", "/proc", "--tmpfs", "/tmp", "--bind", str(stage), str(stage),
                   "--tmpfs", str(SOURCE), "--unshare-net", "--chdir", str(stage),
                   "--", sys.executable, str(stage / "runner.py"), "--inside",
                   "--stage", str(stage), "--source-checkout", str(SOURCE)]
        sandboxed = call(*sandbox, cwd=stage, env=environment, timeout=900)
        observed = json.loads((stage / "result.json").read_text())
        if observed.get("result") != "PASS" or observed.get("source_checkout_hidden") is not True:
            raise RuntimeError(f"fresh-install scenario did not pass: {sandboxed.stdout[-500:]}")
        observed.update({
            "tools_source_commit": source_commit,
            "tools_source_dirty": source_dirty,
            "tools_binary_sha256": digest(tools), "builder_binary_sha256": digest(builder),
            "core_archive_sha256": digest(core_archive),
            "local_tools_package_sha256": digest(bundle),
            "python_wheel_sha256": digest(wheels[0]),
            "typescript_package_sha256": digest(npm_package),
            "standalone_mcp_package_sha256": digest(mcp_package),
            "mcp_packager_sha256": digest(SOURCE / "scripts/package_mcp.py"),
            "probe_sha256": digest(SOURCE / "scripts/probe_fresh_install.py"),
            "sandbox_stdout": sandboxed.stdout.strip(),
        })
        return observed


def copy_review_package(project: Path, destination: Path) -> Path:
    manifest = json.loads((project / "program.json").read_text())
    (destination / "artifact").mkdir(parents=True)
    shutil.copy2(project / "program.json", destination / "program.json")
    for binding in ("artifact", "imported_source_artifact"):
        if manifest.get(binding):
            relative = manifest[binding]["path"]
            shutil.copy2(project / relative, destination / relative)
    return destination / "program.json"


def exercise(stage: Path, source_checkout: Path) -> dict:
    if (source_checkout / "README.md").exists():
        raise RuntimeError("developer checkout remains visible in isolated scenario")
    bundle = stage / "dist/sley-tools-local.tar.gz"
    with tarfile.open(bundle, "r:gz") as archive:
        archive.extractall(stage / "install", filter="data")
    installed = stage / "install/sley-tools-local"
    with tarfile.open(stage / "dist/sley-core.tar.gz", "r:gz") as archive:
        archive.extractall(stage / "install", filter="data")
    core_root = stage / "install/sley-2.1.1-linux-x86_64"
    core = core_root / "bin/sley"
    tools = installed / "bin/sley-tools"
    manifest = json.loads((core_root / "MANIFEST.json").read_text())
    if (digest(core) != next(item["sha256"] for item in manifest["files"]
                              if item["path"] == "bin/sley")
            or digest(stage / "dist/sley-core.tar.gz") != CORE_ARCHIVE_SHA256):
        raise RuntimeError("installed core failed manifest or archive verification")
    wheel = next((stage / "dist").glob("sley_tools-*.whl"))
    venv = stage / "venv"
    call("uv", "venv", "--offline", "--python", sys.executable, venv, cwd=stage)
    call("uv", "pip", "install", "--offline", "--no-deps", "--python",
         venv / "bin/python", wheel, cwd=stage)
    python = venv / "bin/python"
    node_root = stage / "node"
    node_root.mkdir()
    npm_package = next((stage / "dist").glob("sley-lang-tools-[0-9]*.tgz"))
    call("npm", "install", "--offline", "--ignore-scripts", "--no-package-lock",
         "--prefix", node_root, npm_package, cwd=stage)
    if not (node_root / "node_modules/@sley-lang/tools/src/index.js").is_file():
        raise RuntimeError("TypeScript package was not installed from the produced archive")
    work = stage / "work"
    doctor = json_call(tools, "doctor", "--core", core, cwd=work)
    if doctor["status"] != "compatible":
        raise RuntimeError("installed binaries did not recognize selected core")

    project = work / "release-readiness"
    created = json_call(tools, "init", project, "--template", "release-readiness",
                        "--core", core, cwd=work)
    if json_call(tools, "test", "--project", project, cwd=work)["result"] != "PASS":
        raise RuntimeError("installed Starter did not pass native execution assertions")
    baseline = json_call(tools, "run", "--project", project,
                         "--input", project / "inputs/ready.json", cwd=work)
    if baseline["result"]["allow"] is not True:
        raise RuntimeError("installed Starter changed the ready decision")

    review_project = work / "review-project"
    json_call(tools, "artifact", "import", installed / "programs/release-readiness/artifact/native-program.json",
              review_project, "--core", core, cwd=work)
    json_call(tools, "artifact", "export", "--project", review_project,
              "--output", review_project / "artifact/before-review.json", "--bind-manifest", cwd=work)
    before = copy_review_package(review_project, work / "before")
    changed = json_call(tools, "edit", "--project", review_project,
                        "--max-blocking-findings", "2", cwd=work)
    if json_call(tools, "test", "--project", review_project, cwd=work)["result"] != "PASS":
        raise RuntimeError("installed native edit failed its external assertions")
    changed_input = work / "changed-input.json"
    changed_input.write_text(json.dumps({"test_run_completed": True, "tests_failed": 0,
                                         "artifact_verified": True, "blocking_findings": 1}))
    if json_call(tools, "run", "--project", review_project,
                 "--input", changed_input, cwd=work)["result"]["allow"] is not True:
        raise RuntimeError("installed native edit did not change behavior")
    json_call(tools, "artifact", "export", "--project", review_project,
              "--output", review_project / "artifact/after-review.json", "--bind-manifest", cwd=work)
    after = copy_review_package(review_project, work / "after")
    review_path = work / "review.json"
    review = json_call(tools, "review", "--before", before, "--after", after,
                       "--core", core, "--cases", installed / "programs/release-readiness/tests/bridge-cases.json",
                       "--output", review_path, cwd=work)
    if review["changed_case_count"] < 1:
        raise RuntimeError("installed Review did not detect the native behavior change")
    verified = json_call(tools, "review", "verify", review_path, "--before", before,
                         "--after", after, "--core", core, "--cases",
                         installed / "programs/release-readiness/tests/bridge-cases.json", cwd=work)
    if verified["result"] != "PASS":
        raise RuntimeError("installed Review report failed recomputation")
    replay = work / "replay"
    json_call(tools, "artifact", "import", review_project / "artifact/after-review.json",
              replay, "--core", core, cwd=work)
    if json_call(tools, "run", "--project", replay,
                 "--input", changed_input, cwd=work)["result"]["allow"] is not True:
        raise RuntimeError("edited artifact lost behavior after import")

    python_code = """import json,sys
from sley_tools import open_runtime
with open_runtime(tools=sys.argv[1],core=sys.argv[2]) as runtime:
 p=runtime.load(artifact=sys.argv[3])
 r=p.call('evaluate',inputs={'test_run_completed':True,'tests_failed':0,'artifact_verified':True,'blocking_findings':0})
 assert r.value['allow'] is True
with open_runtime(tools=sys.argv[1],core=sys.argv[2]) as runtime:
 w=runtime.load(artifact=sys.argv[4])
 v=w.call('evaluate',inputs={'requested_workers':1,'available_workers':1,'requested_memory_mib':9007199254740993,'available_memory_mib':9007199254740993})
 assert v.value['allow'] is True and v.value['requested_memory_mib']==9007199254740993
 print(json.dumps({'release_allow':r.value['allow'],'wide_memory':v.value['requested_memory_mib']}))
"""
    python_sdk = json.loads(call(python, "-c", python_code, tools, core,
                                 installed / "programs/release-readiness/artifact/native-program.json",
                                 installed / "programs/resource-admission/artifact/native-program.json",
                                 cwd=work).stdout)
    javascript = """import {openRuntime} from '@sley-lang/tools';
const a=openRuntime({tools:process.env.SLEY_TOOLS_BIN,core:process.env.SLEY_CORE_BIN});
let releaseAllow;
try {
 const p=await a.load({artifact:process.env.SLEY_RELEASE_ARTIFACT});
 const v=await p.call('evaluate',{inputs:{test_run_completed:true,tests_failed:0n,artifact_verified:true,blocking_findings:0n}});
 if(v.value.allow!==true) throw Error('release decision differs');
 releaseAllow=v.value.allow;
} finally { await a.close(); }
const b=openRuntime({tools:process.env.SLEY_TOOLS_BIN,core:process.env.SLEY_CORE_BIN});
try {
 const q=await b.load({artifact:process.env.SLEY_RESOURCE_ARTIFACT});
 const w=await q.call('evaluate',{inputs:{requested_workers:1n,available_workers:1n,requested_memory_mib:9007199254740993n,available_memory_mib:9007199254740993n}});
 if(w.value.allow!==true || w.value.requested_memory_mib!==9007199254740993n) throw Error('wide integer differs');
 console.log(JSON.stringify({release_allow:releaseAllow,wide_memory:String(w.value.requested_memory_mib)}));
} finally { await b.close(); }
"""
    node_env = dict(os.environ)
    node_env.update({"SLEY_TOOLS_BIN": str(tools), "SLEY_CORE_BIN": str(core),
                     "SLEY_RELEASE_ARTIFACT": str(installed / "programs/release-readiness/artifact/native-program.json"),
                     "SLEY_RESOURCE_ARTIFACT": str(installed / "programs/resource-admission/artifact/native-program.json")})
    javascript_sdk = json.loads(call("node", "--input-type=module", "-e", javascript,
                                     cwd=node_root, env=node_env).stdout)
    if (python_sdk["wide_memory"] != 9007199254740993
            or javascript_sdk["wide_memory"] != "9007199254740993"):
        raise RuntimeError("installed SDKs lost signed integer precision")

    opcode_spec = installed / "examples/graph/fixed-limit.json"
    opcode_initial = installed / "examples/graph/fixed-limit-cases.json"
    opcode_updated = installed / "examples/graph/fixed-limit-exclusive-cases.json"
    python_opcode_project, typescript_opcode_project = (
        work / "python-opcode", work / "typescript-opcode")
    for destination in (python_opcode_project, typescript_opcode_project):
        json_call(tools, "init", destination, "--graph", opcode_spec, "--tests",
                  opcode_initial, "--core", core, cwd=work)
    native_candidate = json_call(tools, "test", "native-candidate", "--project",
                                 python_opcode_project, "--case", "zero", cwd=work)
    if (native_candidate["contract"] != "sley-tools.native-test-candidate.v0"
            or native_candidate["phase"] != "STATICALLY_VALID"
            or native_candidate["native_test_execution"] != "NOT_RUN"
            or native_candidate["native_core_test_admission"] !=
               "NOT_RUN: production native executor unavailable"
            or native_candidate["declared_limits"]["memory_bytes"] >
               native_candidate["grant_ceilings"]["max_memory_bytes"]
            or native_candidate["native_plan_selected_test_count"] != 1
            or native_candidate["native_plan_preflight"] != "CORE_LIBRARY_ONLY_NO_EXECUTION"
            or native_candidate["accepted_root_unchanged"] is not True):
        raise RuntimeError("installed CLI misreported native TestCase candidate validation")
    rejected_json(tools, "test", "native-candidate", "--project", python_opcode_project,
                  "--case", "absent", cwd=work, code="ST_INPUT_INVALID")
    python_opcode_code = """import json,sys
from sley_tools import open_runtime
fixture=json.load(open(sys.argv[4]))
cases=[{'id':r['id'],'inputs':{'count':int(r['input'][0])},'expected':r['expected']} for r in fixture['cases']]
with open_runtime(tools=sys.argv[1],core=sys.argv[2]) as runtime:
 p=runtime.open_project(sys.argv[3])
 q=p.propose_graph_opcode('entry','within','less_than',cases)
 p.validate(q['candidate_id'])
 c=p.commit(q['candidate_id'])
 assert c['changed_case_count']==1 and c['external_assertions']==len(cases)
 assert p.call('evaluate',inputs={'count':10}).value is False
 print(json.dumps({'accepted_root':c['new_root'],'changed_case_count':c['changed_case_count']}))
"""
    installed_python_opcode = json.loads(call(
        python, "-c", python_opcode_code, tools, core, python_opcode_project,
        opcode_updated, cwd=work).stdout)
    typescript_opcode_code = """import {readFile} from 'node:fs/promises';
import {openRuntime} from '@sley-lang/tools';
const fixture=JSON.parse(await readFile(process.env.SLEY_GRAPH_CASES,'utf8'));
const cases=fixture.cases.map(r=>({id:r.id,inputs:{count:Number(r.input[0])},expected:r.expected}));
const runtime=openRuntime({tools:process.env.SLEY_TOOLS_BIN,core:process.env.SLEY_CORE_BIN});
try {
 const p=await runtime.openProject({project:process.env.SLEY_GRAPH_PROJECT});
 const q=await p.proposeGraphOpcode('entry','within','less_than',cases);
 await p.validate(q.candidate_id);
 const c=await p.commit(q.candidate_id);
 const observed=await p.callGraph('evaluate',{inputs:{count:10}});
 if(c.changed_case_count!==1 || c.external_assertions!==cases.length || observed.value!==false) throw Error('installed opcode edit differs');
 console.log(JSON.stringify({accepted_root:c.new_root,changed_case_count:c.changed_case_count}));
} finally { await runtime.close(); }
"""
    opcode_env = dict(node_env)
    opcode_env.update({"SLEY_GRAPH_PROJECT": str(typescript_opcode_project),
                       "SLEY_GRAPH_CASES": str(opcode_updated)})
    installed_typescript_opcode = json.loads(call(
        "node", "--input-type=module", "-e", typescript_opcode_code,
        cwd=node_root, env=opcode_env).stdout)

    bench = []
    for program in ("benchmark-comparator", "benchmark-selector", "benchmark-summary-reducer"):
        destination = work / program
        json_call(tools, "artifact", "import", installed / f"programs/{program}/artifact/native-graph.json",
                  destination, "--core", core, cwd=work)
        bench.append(destination)
    pipe_output = work / "data-pipe.jsonl"
    call(tools, "pipe", "--adapter", venv / "bin/sley-data-pipe", "--core", core,
         "--comparator-project", bench[0], "--selector-project", bench[1],
         "--reducer-project", bench[2], "--input", installed / "examples/data-pipe/benchmark-v0.input.jsonl",
         "--output", pipe_output, cwd=work)
    if pipe_output.read_bytes() != (installed / "examples/data-pipe/benchmark-v0.expected.jsonl").read_bytes():
        raise RuntimeError("installed Data Pipe output differed from the published sample")

    playground_report_path = work / "playground.json"
    call("node", installed / "scripts/probe_playground_local.mjs", "--tools", tools,
         "--core", core, "--output", playground_report_path, cwd=installed, timeout=240)
    playground_report = json.loads(playground_report_path.read_text())
    if (playground_report.get("contract") != "sley-tools.playground-local-probe.v1"
            or playground_report.get("result") != "PASS"
            or playground_report.get("tools_binary_sha256") != digest(tools)
            or playground_report.get("core_binary_sha256") != digest(core)
            or playground_report.get("server_sha256")
                != digest(installed / "apps/playground/server.mjs")
            or playground_report.get("probe_script_sha256")
                != digest(installed / "scripts/probe_playground_local.mjs")
            or playground_report.get("unified_cli_launcher_verified") is not True
            or playground_report.get("hosted_ready") is not False):
        raise RuntimeError("installed Playground launcher omitted the local native browser flow")

    lab_report_path = work / "repair-lab-report.json"
    call(python, installed / "scripts/probe_repair_lab.py", "--tools", tools,
         "--core", core, "--output", lab_report_path, cwd=work, timeout=600)
    lab_report = json.loads(lab_report_path.read_text())
    exercise_ids = [f"rl{number:02d}" for number in range(1, 9)]
    if (lab_report.get("result") != "PASS"
            or lab_report.get("public_exercises_available") != exercise_ids
            or lab_report.get("remaining_exercises_pending") != []
            or lab_report.get("tools_binary_sha256") != digest(tools)
            or lab_report.get("core_binary_sha256") != digest(core)
            or any(not isinstance(lab_report.get(exercise), dict)
                   for exercise in exercise_ids)):
        raise RuntimeError("installed Repair Lab did not verify all eight exercises")

    schema_project = work / "schema-import"
    json_call(venv / "bin/sley-schema", "import", installed / "examples/schema/device-reading.schema.json",
              schema_project, "--tools", tools, "--core", core, cwd=work)
    schema_input = work / "schema-input.json"
    schema_input.write_text('{"active":true,"count":1.0}\n')
    schema_result = json_call(venv / "bin/sley-schema", "validate", "--project", schema_project,
                              "--input", schema_input, "--tools", tools, cwd=work)
    if schema_result["valid"] is not True:
        raise RuntimeError("installed Schema Importer rejected a valid raw record")
    schema_new_input = work / "schema-count-12.json"
    schema_new_input.write_text('{"active":true,"count":12}\n')
    schema_above_input = work / "schema-count-13.json"
    schema_above_input.write_text('{"active":true,"count":13}\n')
    schema_before = json_call(venv / "bin/sley-schema", "validate", "--project", schema_project,
                              "--input", schema_new_input, "--tools", tools, cwd=work)
    if schema_before["valid"] is not False:
        raise RuntimeError("installed schema validator began with the wrong maximum")
    schema_mapping = json.loads((schema_project / "schema/mapping.json").read_text())
    schema_maximum = next(entry["maximum_constant"] for entry in schema_mapping["properties"]
                          if entry["name"] == "count")
    schema_value = work / "schema-new-maximum.json"
    schema_value.write_text('"12"\n')
    schema_cases = []
    for case_id, value, valid in (("new-maximum", 12, True), ("above-maximum", 13, False)):
        facts = {"root_is_object": True, "extra_count": "0", "p0_present": True,
                 "p0_kind": True, "p0_value": True, "p1_present": True,
                 "p1_kind": True, "p1_fits": True, "p1_value": str(value)}
        expected = {name: False for name in schema_mapping["native_result_flags"]}
        expected["valid" if valid else "p1_out_of_bounds"] = True
        schema_cases.append({"id": case_id,
                             "input": [facts[item["name"]] for item in schema_mapping["envelope_parameters"]],
                             "expected": expected})
    schema_cases_file = work / "schema-edited-cases.json"
    schema_cases_file.write_text(json.dumps({"contract": "sley-tools.graph-cases.v0",
                                             "cases": schema_cases}))
    json_call(tools, "propose", "--project", schema_project, "--constant", schema_maximum,
              "--value-file", schema_value, "--tests", schema_cases_file, cwd=work)
    json_call(tools, "validate", "--project", schema_project, cwd=work)
    schema_commit = json_call(tools, "commit", "--project", schema_project, cwd=work)
    schema_tests = json_call(tools, "test", "--project", schema_project, cwd=work)
    schema_after = json_call(venv / "bin/sley-schema", "validate", "--project", schema_project,
                             "--input", schema_new_input, "--tools", tools, cwd=work)
    schema_above = json_call(venv / "bin/sley-schema", "validate", "--project", schema_project,
                             "--input", schema_above_input, "--tools", tools, cwd=work)
    if (schema_commit["changed_case_count"] != 1
            or schema_tests["external_assertions_against_native_execution"] != 2
            or schema_after["valid"] is not True
            or schema_after["source_schema_alignment"] != "native_edited"
            or schema_above["valid"] is not False
            or schema_commit["new_root"] == schema_before["accepted_state_root"]
            or schema_commit["new_root"] != schema_after["accepted_state_root"]):
        raise RuntimeError("installed schema native edit did not preserve the boundary cases")
    rejected_schema = work / "unsupported.schema.json"
    rejected_schema.write_text('{"type":"integer","minimum":0,"maximum":10,"pattern":"x"}\n')
    rejected_schema_project = work / "unsupported-schema-project"
    schema_diagnostic = rejected_json(venv / "bin/sley-schema", "import", rejected_schema,
                                      rejected_schema_project, "--tools", tools, "--core", core,
                                      cwd=work, code="ST_SCHEMA_UNSUPPORTED")
    if schema_diagnostic.get("schema_pointer") != "/pattern" or rejected_schema_project.exists():
        raise RuntimeError("installed schema importer published an unsupported constraint")

    python_project = work / "python-import"
    json_call(venv / "bin/sley-python", "import", installed / "examples/python-importer/quota.py",
              installed / "examples/python-importer/quota.domain.json", python_project,
              "--tools", tools, "--core", core, cwd=work)
    python_input = work / "python-input.json"
    python_input.write_text('{"capacity":"100","used":"37"}\n')
    import_result = json_call(venv / "bin/sley-python", "run", "--project", python_project,
                              "--input", python_input, "--tools", tools, cwd=work)
    if import_result["in_domain"] is not True or import_result["value"] != "63":
        raise RuntimeError("installed Python Function Importer changed quota semantics")
    tier_project = work / "python-tier"
    json_call(venv / "bin/sley-python", "import", installed / "examples/python-importer/tier.py",
              installed / "examples/python-importer/tier.domain.json", tier_project,
              "--tools", tools, "--core", core, cwd=work)
    tier_input = work / "tier-score-25.json"
    tier_input.write_text('{"score":"25"}\n')
    tier_boundary = work / "tier-score-26.json"
    tier_boundary.write_text('{"score":"26"}\n')
    tier_before = json_call(venv / "bin/sley-python", "run", "--project", tier_project,
                            "--input", tier_input, "--tools", tools, cwd=work)
    if tier_before["value"] != "1":
        raise RuntimeError("installed tier importer began with the wrong boundary")
    tier_mapping = json.loads((tier_project / "python-source/mapping.json").read_text())
    tier_constant = next(item["constant"] for item in tier_mapping["constant_spans"]
                         if item["line"] == 2)
    tier_cases = json.loads((tier_project / "tests/cases.json").read_text())
    next(item for item in tier_cases["cases"] if item["id"] == "second")["expected"]["value"] = {"ok": "0"}
    tier_cases_file = work / "tier-edited-cases.json"
    tier_cases_file.write_text(json.dumps(tier_cases))
    tier_value = work / "tier-new-threshold.json"
    tier_value.write_text('"26"\n')
    json_call(tools, "propose", "--project", tier_project, "--constant", tier_constant,
              "--value-file", tier_value, "--tests", tier_cases_file, cwd=work)
    json_call(tools, "validate", "--project", tier_project, cwd=work)
    tier_commit = json_call(tools, "commit", "--project", tier_project, cwd=work)
    tier_tests = json_call(tools, "test", "--project", tier_project, cwd=work)
    tier_after = json_call(venv / "bin/sley-python", "run", "--project", tier_project,
                           "--input", tier_input, "--tools", tools, cwd=work)
    tier_next = json_call(venv / "bin/sley-python", "run", "--project", tier_project,
                          "--input", tier_boundary, "--tools", tools, cwd=work)
    if (tier_commit["changed_case_count"] != 1
            or tier_tests["external_assertions_against_native_execution"] != 8
            or tier_after["value"] != "0" or tier_next["value"] != "1"
            or tier_after["source_alignment"] != "native_edited"
            or tier_commit["new_root"] == tier_before["accepted_state_root"]
            or tier_commit["new_root"] != tier_after["accepted_state_root"]):
        raise RuntimeError("installed Python native edit did not preserve tier boundaries")
    unsupported_python = work / "unsupported-tier.py"
    unsupported_python.write_text('def tier(score: int) -> int:\n    return abs(score)\n')
    rejected_python_project = work / "unsupported-python-project"
    python_diagnostic = rejected_json(venv / "bin/sley-python", "import", unsupported_python,
                                      installed / "examples/python-importer/tier.domain.json",
                                      rejected_python_project, "--tools", tools, "--core", core,
                                      cwd=work, code="ST_IMPORT_UNSUPPORTED")
    if rejected_python_project.exists():
        raise RuntimeError("installed Python importer published an unsupported function")

    mcp_package = json.loads((stage / "dist/mcp-package.json").read_text())
    mcp_archive = stage / "dist" / mcp_package["filename"]
    if mcp_package["archive_sha256"] != digest(mcp_archive):
        raise RuntimeError("standalone MCP archive differs from its build report")
    mcp_node = stage / "mcp-node"
    mcp_node.mkdir()
    call("npm", "install", "--offline", "--ignore-scripts", "--no-package-lock",
         "--no-audit", "--no-fund", "--prefix", mcp_node, mcp_archive, cwd=stage)
    standalone_server = mcp_node / "node_modules/@sley-lang/tools-mcp/src/server.mjs"
    standalone_sdk = mcp_node / "node_modules/@sley-lang/tools-mcp/src/sdk/index.js"
    if (digest(standalone_server) != mcp_package["standalone_server_sha256"]
            or digest(standalone_sdk) != mcp_package["sdk_source_sha256"]):
        raise RuntimeError("installed standalone MCP source differs from its package report")
    mcp_report_path = work / "mcp-reference.json"
    call("node", installed / "packages/mcp/tests/probe.mjs",
         f"tools={tools}", f"core={core}", f"server={standalone_server}",
         f"output={mcp_report_path}",
         cwd=installed, timeout=240)
    mcp_report = json.loads(mcp_report_path.read_text())
    if (mcp_report.get("result") != "PASS"
            or not mcp_report.get("runner_runtime", "").startswith("node ")
            or mcp_report.get("tools_binary_sha256") != digest(tools)
            or mcp_report.get("core_binary_sha256") != digest(core)
            or mcp_report.get("server_entry_sha256") != digest(standalone_server)
            or mcp_report.get("external_assertions") != 106
            or mcp_report.get("graph_opcode_edit") is not True
            or mcp_report.get("graph_operand_edit") is not True
            or mcp_report.get("graph_branch_edit") is not True
            or mcp_report.get("created_native_graph_project") is not True):
        raise RuntimeError("installed MCP reference client did not verify native agent edits")
    launcher_report_path = work / "mcp-launcher.json"
    call("node", installed / "packages/mcp/tests/launcher_probe.mjs",
         f"tools={tools}", f"core={core}", f"server={standalone_server}",
         f"output={launcher_report_path}", cwd=installed, timeout=120)
    launcher_report = json.loads(launcher_report_path.read_text())
    if (launcher_report.get("result") != "PASS"
            or launcher_report.get("tools_binary_sha256") != digest(tools)
            or launcher_report.get("core_binary_sha256") != digest(core)
            or launcher_report.get("adapter_sha256") != digest(standalone_server)
            or launcher_report.get("probe_entry_sha256")
                != digest(installed / "packages/mcp/tests/launcher_probe.mjs")
            or launcher_report.get("native_boundary_changed") is not True):
        raise RuntimeError("installed CLI MCP launcher did not carry a native edit")

    result = {
        "contract": "sley-tools.fresh-install-probe.v3", "result": "PASS",
        "profile": "local-linux-x86_64-sley-2.1.1",
        "isolation": "bubblewrap checkout-masked, private tmpfs, network namespace disabled, new workspace and caches",
        "source_checkout_hidden": True,
        "installed_core_binary_sha256": digest(core),
        "installed_tools_binary_sha256": digest(tools),
        "installed_python_package": "sley_tools-0.0.1 wheel",
        "installed_typescript_package": "@sley-lang/tools@0.0.1 tarball",
        "flagship_new_program_root": created["program"]["entrypoint"]["state_root"],
        "flagship_edited_root": changed["new_root"],
        "review_changed_cases": review["changed_case_count"],
        "review_verified": True, "artifact_replay_passed": True,
        "python_sdk": python_sdk, "typescript_sdk": javascript_sdk,
        "installed_python_graph_opcode": installed_python_opcode,
        "installed_typescript_graph_opcode": installed_typescript_opcode,
        "installed_native_test_candidate": native_candidate,
        "data_pipe_expected_output_sha256": digest(pipe_output),
        "data_pipe_unified_cli_launcher": True,
        "installed_playground": playground_report,
        "repair_lab_independent_native_cases": lab_report["rl01"]["independent_native_cases"],
        "installed_repair_lab": lab_report,
        "schema_import_valid": schema_result["valid"],
        "schema_native_edit": {"before_root": schema_before["accepted_state_root"],
                               "after_root": schema_commit["new_root"],
                               "before_count_12_valid": schema_before["valid"],
                               "after_count_12_valid": schema_after["valid"],
                               "after_count_13_valid": schema_above["valid"],
                               "changed_case_count": schema_commit["changed_case_count"],
                               "external_assertions": schema_tests["external_assertions_against_native_execution"],
                               "source_alignment": schema_after["source_schema_alignment"],
                               "unsupported_code": schema_diagnostic["code"],
                               "unsupported_pointer": schema_diagnostic["schema_pointer"]},
        "python_import_value": import_result["value"],
        "python_native_edit": {"before_root": tier_before["accepted_state_root"],
                               "after_root": tier_commit["new_root"],
                               "before_score_25": tier_before["value"],
                               "after_score_25": tier_after["value"],
                               "after_score_26": tier_next["value"],
                               "changed_case_count": tier_commit["changed_case_count"],
                               "external_assertions": tier_tests["external_assertions_against_native_execution"],
                               "source_alignment": tier_after["source_alignment"],
                               "unsupported_code": python_diagnostic["code"]},
        "installed_mcp_reference": mcp_report,
        "installed_mcp_launcher": launcher_report,
        "standalone_mcp_package": mcp_package,
        "native_core_test_admission": "NOT_RUN: production native executor unavailable",
        "live_agent_task": "NOT_RUN: no agent launched by fresh-install probe",
    }
    (stage / "result.json").write_text(json.dumps(result, sort_keys=True) + "\n")
    print("fresh-install local package scenario passed")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--stage", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--source-checkout", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--tools", type=Path)
    parser.add_argument("--builder", type=Path)
    parser.add_argument("--core-archive", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.inside:
        if args.stage is None or args.source_checkout is None:
            parser.error("inside scenario needs a stage and source checkout path")
        exercise(args.stage, args.source_checkout)
        return
    if any(getattr(args, name) is None for name in ("tools", "builder", "core_archive", "output")):
        parser.error("tools, builder, core archive, and output are required")
    evidence = package_and_run(args)
    destination = args.output.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"wrote {destination}: isolated local package install passed")


if __name__ == "__main__":
    main()
