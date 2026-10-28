import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path.cwd()
LOGS = Path(os.environ["RUNNER_TEMP"]) / "meetily-validation-logs"
LOGS.mkdir(parents=True, exist_ok=True)
ENV = os.environ.copy()
ENV["CI"] = "true"
ENV["NEXT_TELEMETRY_DISABLED"] = "1"
ENV["PYTHONUTF8"] = "1"
if sys.platform == "win32":
    llvm = Path(r"C:\Program Files\LLVM\bin")
    if llvm.exists():
        ENV["LIBCLANG_PATH"] = str(llvm)
    runtime = ROOT / "frontend/src-tauri/binaries/onnxruntime"
    ENV["PATH"] = str(runtime) + os.pathsep + ENV["PATH"]
RESULTS = []
SPECS = [
    (887, "codex/application-audio", ["audio::source::tests", "audio::clocked_mixer::tests"]),
    (889, "codex/recording-timeouts", ["audio_startup", "stalled_enumeration_cannot_block_recording_shutdown"]),
    (890, "codex/native-recording-sessions", ["session::", "shutdown_tests"]),
    (888, "codex/summary-editor-loading", None),
]

def run(pr, label, command, cwd=ROOT, limit=2400):
    path = LOGS / f"{pr}-{label}.log"
    print(f"START PR #{pr}: {label} ({' '.join(command)})", flush=True)
    started = time.monotonic()
    executable = command[0]
    if sys.platform == "win32" and executable == "pnpm":
        command = ["cmd", "/c", *command]
    try:
        with path.open("w", encoding="utf-8") as stream:
            result = subprocess.run(command, cwd=cwd, env=ENV, stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=limit)
        code = result.returncode
    except subprocess.TimeoutExpired:
        code = 124
    text = path.read_text(encoding="utf-8", errors="replace")
    tail = text.splitlines()[-(100 if code else 8):]
    print("\n".join(tail), flush=True)
    print(f"END PR #{pr}: {label}: exit={code}, seconds={time.monotonic()-started:.1f}", flush=True)
    return code

subprocess.run(["git", "fetch", "origin", *[branch for _, branch, _ in SPECS]], check=True)
for pr, branch, filters in SPECS:
    head = subprocess.check_output(["git", "rev-parse", f"origin/{branch}"], text=True).strip()
    subprocess.run(["git", "checkout", "--detach", head], check=True)
    record = {"pr": pr, "head": head, "os": sys.platform, "checks": {}}
    RESULTS.append(record)
    checks = [
        ("install", ["pnpm", "install", "--frozen-lockfile"]),
        ("types", ["pnpm", "exec", "tsc", "--noEmit", "--incremental", "false"]),
    ]
    for label, command in checks:
        code = run(pr, label, command, ROOT / "frontend")
        record["checks"][label] = code
        if code and label == "install":
            break
    if record["checks"]["install"] == 0:
        # Bun module mocks are process-wide. Isolate every upstream suite file
        # so OS-dependent file enumeration cannot leak mocks between suites.
        for index, test in enumerate(sorted((ROOT / "frontend/tests").rglob("*.test.*"))):
            if test.suffix not in (".ts", ".tsx", ".js", ".mjs"):
                continue
            label = f"frontend-test-{index}-{test.stem}"
            record["checks"][label] = run(pr, label, ["bun", "test", str(test)], ROOT / "frontend")
        record["checks"]["frontend-build"] = run(pr, "frontend-build", ["pnpm", "build"], ROOT / "frontend")
    if filters is not None and record["checks"].get("frontend-build") == 0:
        code = run(pr, "native-check", ["cargo", "check", "-p", "meetily", "--lib", "--bins", "--locked"])
        record["checks"]["native-check"] = code
        if not code:
            for index, test_filter in enumerate(filters):
                code = run(pr, f"native-tests-{index}", ["cargo", "test", "-p", "meetily",
                           "--lib", "--locked", test_filter, "--", "--nocapture"])
                record["checks"][test_filter] = code
    (LOGS / "results.json").write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
print(json.dumps(RESULTS, indent=2), flush=True)
sys.exit(1 if any(any(record["checks"].values()) for record in RESULTS) else 0)

