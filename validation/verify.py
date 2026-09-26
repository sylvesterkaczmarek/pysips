import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

CONFIG = json.loads(Path(__file__).with_name("config.json").read_text())
SOURCE = Path(sys.argv[1]).resolve()
EVIDENCE = Path(sys.argv[2]).resolve()
EVIDENCE.mkdir(parents=True, exist_ok=True)
PYTHON = sys.executable
ENV = os.environ.copy()
ENV["PATH"] = str(Path(PYTHON).parent) + os.pathsep + ENV["PATH"]
ENV["MPLBACKEND"] = "Agg"
ENV["OMP_NUM_THREADS"] = "1"
ENV["OPENBLAS_NUM_THREADS"] = "1"


def run(label, command, expected=0, cwd=SOURCE):
    result = subprocess.run(command, cwd=cwd, env=ENV, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=600)
    (EVIDENCE / (label + ".log")).write_text(result.stdout)
    print(label, "exit", result.returncode, flush=True)
    if result.returncode != expected:
        print(result.stdout[-14000:], flush=True)
        raise RuntimeError(label + " returned an unexpected status")
    return result.stdout


def clear_bytecode():
    for cache in SOURCE.rglob("__pycache__"):
        shutil.rmtree(cache)


def pytest_run(label, paths, expected_counts, expected_status=0, coverage=False):
    report = EVIDENCE / (label + ".xml")
    args = [PYTHON, "-m", "pytest"]
    if coverage:
        ENV["COVERAGE_FILE"] = str(EVIDENCE / ".coverage")
        args = [PYTHON, "-m", "coverage", "run", "--branch", "--source=pysips", "-m", "pytest"]
    run(label, args + ["-q", *paths, "--tb=short", "--junitxml", str(report)], expected_status)
    suites = ET.parse(report).getroot().findall("testsuite")
    counts = tuple(sum(int(s.get(k, "0")) for s in suites) for k in ("tests", "failures", "errors", "skipped"))
    assert counts == tuple(expected_counts), (label, counts, expected_counts)
    print(label, "counts", counts, flush=True)


head = run("head", ["git", "rev-parse", "HEAD"]).strip()
assert head == CONFIG["source"], (head, CONFIG["source"])
print("TESTED SOURCE", head, flush=True)
main_file = SOURCE / CONFIG["files"][0]
fixed = main_file.read_bytes()

if CONFIG["name"] == "pysips":
    pytest_run("fixed-suite", ["tests"], (99, 0, 0, 0))
    pytest_run("unit-integration-coverage", ["tests/unit", "tests/integration"], (96, 0, 0, 0), coverage=True)
    run("coverage-json", [PYTHON, "-m", "coverage", "json", "-o", str(EVIDENCE / "coverage.json")])
    run("pylint", [PYTHON, "-m", "pylint", "pysips", "--fail-under=10"])
    run("new-test-lint", [PYTHON, "-m", "flake8", CONFIG["files"][1], "--max-line-length=88"])
else:
    modules = ["unit_tests.test_runner_exit_status", "unit_tests.test_bpv6_protocol",
               "unit_tests.test_bpv7_protocol", "unit_tests.test_tcpcl",
               "unit_tests.test_dtn_time", "unit_tests.test_bpsec", "unit_tests.test_storage"]
    text = run("offline-suite", [PYTHON, "-m", "unittest", "-v", *modules])
    assert "Ran 32 tests" in text and "\nOK\n" in text, text[-2000:]
    run("changed-file-lint", [PYTHON, "-m", "flake8", *CONFIG["files"], "--max-line-length=88"])

try:
    original = subprocess.check_output(["git", "show", CONFIG["base"] + ":" + CONFIG["files"][0]], cwd=SOURCE)
    main_file.write_bytes(original)
    clear_bytecode()
    if CONFIG["name"] == "pysips":
        pytest_run("original-new-regressions", [CONFIG["files"][1]], (19, 18, 0, 0), 1)
        pytest_run("original-existing-suite", ["tests", "--ignore=" + CONFIG["files"][1]], (80, 3, 0, 0), 1)
    else:
        pytest_run("original-new-regressions", [CONFIG["files"][1]], (11, 7, 0, 0), 1)
finally:
    main_file.write_bytes(fixed)
    clear_bytecode()
assert main_file.read_bytes() == fixed
run("source-restored", ["git", "diff", "--exit-code"])
if CONFIG["name"] == "pysips":
    pytest_run("restored-suite", ["tests"], (99, 0, 0, 0))
else:
    text = run("restored-offline-suite", [PYTHON, "-m", "unittest", "-v", *modules])
    assert "Ran 32 tests" in text and "\nOK\n" in text

run("build", [PYTHON, "-m", "build", "--outdir", str(EVIDENCE / "dist")])
if CONFIG["name"] == "pysips":
    wheel = next((EVIDENCE / "dist").glob("*.whl"))
    run("install-wheel", [PYTHON, "-m", "pip", "install", "--force-reinstall", "--no-deps", str(wheel)])
    with tempfile.TemporaryDirectory() as directory:
        path = run("installed-location", [PYTHON, "-c", "import pysips; print(pysips.__file__)"], cwd=directory).strip().splitlines()[-1]
        assert "site-packages" in path and str(SOURCE) not in path, path
        run("installed-regressions", [PYTHON, "-m", "pytest", "-q", str(SOURCE / CONFIG["files"][1]), "--import-mode=importlib"], cwd=directory)
run("dependency-check", [PYTHON, "-m", "pip", "check"])
run("dependency-versions", [PYTHON, "-m", "pip", "freeze"])
run("whitespace", ["git", "diff", "--check", CONFIG["base"], CONFIG["source"]])
print("VALIDATION COMPLETE", CONFIG["name"], head, flush=True)
