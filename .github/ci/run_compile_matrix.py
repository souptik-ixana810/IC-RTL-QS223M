#!/usr/bin/env python3
"""Run discovered compile checks in parallel inside one GitHub job."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
HEARTBEAT_SECONDS = 60


@dataclass(frozen=True)
class CompileRow:
    module: str
    script_dir: Path
    mode: str
    group: str
    defines: str


@dataclass(frozen=True)
class CompileResult:
    row: CompileRow
    make_log: Path
    compile_log: Path
    parse_log: Path
    compile_returncode: int
    parse_returncode: int


def safe_name(value: str) -> str:
    return SAFE_NAME_RE.sub("_", value).strip("_") or "unnamed"


def load_rows(path: Path, repo: Path) -> list[CompileRow]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("include", [])
    if not isinstance(rows, list):
        raise ValueError("compile matrix JSON must contain an `include` array")

    compile_rows: list[CompileRow] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"matrix row {index} is not an object")
        script_dir = Path(str(row["script_dir"]))
        if not script_dir.is_absolute():
            script_dir = repo / script_dir
        compile_rows.append(
            CompileRow(
                module=str(row["module"]),
                script_dir=script_dir,
                mode=str(row["mode"]),
                group=str(row["group"]),
                defines=str(row.get("defines", "")),
            )
        )
    return compile_rows


def parse_github_output(path: Path) -> dict[str, str]:
    outputs: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if "<<" not in line:
            if "=" in line:
                name, value = line.split("=", 1)
                outputs[name] = value
            index += 1
            continue

        name, delimiter = line.split("<<", 1)
        index += 1
        value_lines: list[str] = []
        while index < len(lines) and lines[index] != delimiter:
            value_lines.append(lines[index])
            index += 1
        outputs[name] = "\n".join(value_lines)
        index += 1
    return outputs


def discover_matrix(
    repo: Path,
    matrix_path: Path,
    changed_files: Path | None,
    all_mode: bool,
    summary: Path | None,
) -> None:
    output_path = matrix_path.with_suffix(".outputs")
    command = [
        sys.executable,
        str(repo / ".github" / "ci" / "discover_compile_matrix.py"),
    ]
    if all_mode:
        command.append("--all")
    elif changed_files is not None:
        command.extend(["--changed-files", str(changed_files)])
    if summary is not None:
        command.extend(["--summary", str(summary)])

    env = os.environ.copy()
    env["GITHUB_OUTPUT"] = str(output_path)
    matrix_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("", encoding="utf-8")

    subprocess.run(command, cwd=repo, env=env, check=True)
    outputs = parse_github_output(output_path)
    matrix = outputs.get("matrix")
    if matrix is None:
        raise ValueError("compile discovery did not emit a matrix output")
    matrix_path.write_text(f"{matrix}\n", encoding="utf-8")


def compile_switches(defines: str) -> str:
    switches: list[str] = []
    for raw_switch in defines.split(","):
        switch = raw_switch.strip()
        if not switch:
            continue
        if switch.startswith(("+", "-")):
            switches.append(switch)
        else:
            switches.append(f"+define+{switch}")
    return " ".join(switches)


def load_sweep_config(script_dir: Path) -> tuple[Path, str]:
    sweep_json = script_dir / "sweep.json"
    config = json.loads(sweep_json.read_text(encoding="utf-8"))
    run_dir = script_dir / str(config.get("commandDir", "."))
    compile_cmd = str(config.get("compileCmd", "make compile"))
    return run_dir.resolve(), compile_cmd


def log_paths(row: CompileRow, repo: Path, log_dir: Path) -> tuple[Path, Path]:
    safe_mode = safe_name(row.mode)
    safe_group = safe_name(row.group)
    build_tag = f"{row.mode}_{row.group}"
    subdir = f"rtl_{row.mode}_{build_tag}"

    make_log = repo / log_dir / f"{row.module}__{safe_mode}__{safe_group}__make.log"
    compile_log = row.script_dir / "sim" / subdir / log_dir / f"compile_{build_tag}.log"
    return make_log, compile_log


def run_compile(row: CompileRow, repo: Path, log_dir: Path) -> CompileResult:
    extra_defs = compile_switches(row.defines)
    run_dir, compile_cmd = load_sweep_config(row.script_dir)
    make_log, compile_log = log_paths(row, repo, log_dir)
    build_tag = f"{row.mode}_{row.group}"
    make_log.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "CI_COMPILE_ARGS": extra_defs,
            "MODE": "rtl",
            "TEST": row.mode,
            "BUILD_TAG": build_tag,
        }
    )

    print(f"::group::Compile {row.module} {row.mode} {row.group}")
    print(f"Run directory: {run_dir}")
    print(f"Command: {compile_cmd}")
    print(f"Make log: {make_log}")
    print(f"Expected VCS log: {compile_log}")
    print("::endgroup::", flush=True)

    with make_log.open("w", encoding="utf-8", errors="ignore") as log_handle:
        process = subprocess.run(
            ["bash", "-lc", compile_cmd],
            cwd=run_dir,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )

    return CompileResult(row, make_log, compile_log, compile_log, process.returncode, 0)


def compile_repro_command(row: CompileRow, repo: Path) -> str:
    extra_defs = compile_switches(row.defines)
    run_dir, compile_cmd = load_sweep_config(row.script_dir)
    build_tag = f"{row.mode}_{row.group}"
    run_dir_text = run_dir.as_posix()
    try:
        run_dir_text = run_dir.relative_to(repo).as_posix()
    except ValueError:
        pass

    env_parts = [
        f"MODE={shlex.quote('rtl')}",
        f"TEST={shlex.quote(row.mode)}",
        f"BUILD_TAG={shlex.quote(build_tag)}",
    ]
    if extra_defs:
        env_parts.append(f"CI_COMPILE_ARGS={shlex.quote(extra_defs)}")

    command = " ".join(env_parts + [compile_cmd])
    return f"cd {shlex.quote(run_dir_text)} && {command}"


def github_escape(message: str) -> str:
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def parse_result(
    result: CompileResult,
    repo: Path,
    warnings_as_errors: Path,
    summary: Path | None,
) -> CompileResult:
    parse_log = result.compile_log
    if not parse_log.exists():
        print(
            "::warning title=Missing VCS Compile Log::"
            f"Expected compile log was not found at {result.compile_log}; "
            "parsing make wrapper log instead."
        )
        parse_log = result.make_log

    command = [
        sys.executable,
        str(repo / ".github" / "ci" / "parse_vcs_log.py"),
        str(parse_log),
        "--module",
        result.row.module,
        "--mode",
        result.row.mode,
        "--group",
        result.row.group,
        "--compile-switches",
        compile_switches(result.row.defines),
        "--warnings-as-errors",
        str(warnings_as_errors),
        "--max-annotations",
        "50",
    ]
    if summary is not None:
        command.extend(["--summary", str(summary)])

    parse = subprocess.run(command, cwd=repo, check=False)
    parse_returncode = parse.returncode
    if result.compile_returncode != 0 and parse_returncode == 0:
        print(
            f"::error file={parse_log},title=Compile Command Failed::"
            f"Compile command exited with status {result.compile_returncode}, "
            "but no tagged VCS errors were found. Check the uploaded compile log for details."
        )
        parse_returncode = 1

    return CompileResult(
        result.row,
        result.make_log,
        result.compile_log,
        parse_log,
        result.compile_returncode,
        parse_returncode,
    )


def append_parallel_summary(summary: Path | None, results: list[CompileResult], max_parallel: int, repo: Path) -> None:
    if summary is None:
        return

    failures = [
        result
        for result in results
        if result.compile_returncode != 0 or result.parse_returncode != 0
    ]
    with summary.open("a", encoding="utf-8") as handle:
        handle.write("## Parallel Compile Summary\n\n")
        handle.write(f"- Compile checks: `{len(results)}`\n")
        handle.write(f"- Max parallel: `{max_parallel}`\n")
        handle.write(f"- Failures: `{len(failures)}`\n\n")
        if results:
            handle.write("### Reproduce compile checks locally\n\n")
            for result in sorted(results, key=lambda item: (item.row.module, item.row.mode, item.row.group)):
                row = result.row
                status = "failed" if result.compile_returncode or result.parse_returncode else "passed"
                handle.write(
                    f"- `{row.module}` `{row.mode}` `{row.group}` (`{status}`)\n"
                )
                handle.write("```bash\n")
                handle.write(f"{compile_repro_command(row, repo)}\n")
                handle.write("```\n\n")


def failed_result_for_exception(
    row: CompileRow,
    repo: Path,
    log_dir: Path,
    exc: BaseException,
) -> CompileResult:
    make_log, compile_log = log_paths(row, repo, log_dir)
    make_log.parent.mkdir(parents=True, exist_ok=True)
    make_log.write_text(f"Internal compile runner error:\n{exc}\n", encoding="utf-8")
    print(
        f"::error title=Compile Runner Failed::"
        f"{row.module} {row.mode} {row.group}: {exc}"
    )
    return CompileResult(row, make_log, compile_log, make_log, 1, 1)


def run_coverage_checks(repo: Path, log_dir: Path, summary: Path | None,
                        rows: list[CompileRow]) -> int:
    # Select by script location, not the display name (model tests can share names).
    selected: set[str] = set()
    test_root = (repo / "test/rtl_test").resolve()
    for row in rows:
        try:
            relative = row.script_dir.resolve().relative_to(test_root)
        except ValueError:
            continue
        if len(relative.parts) == 2 and relative.parts[1] == "script":
            if (test_root / relative.parts[0] / "coverage_baseline").is_dir():
                selected.add(relative.parts[0])
    if not selected:
        print("No selected compile tests with coverage baselines; coverage validation skipped.")
        return 0
    checker = repo / "package/rtl-commons/coverage/bin/check_coverage_baselines.py"
    command = [sys.executable, str(checker), "--project-root", str(repo),
               "--log-dir", str(log_dir / "coverage"), "--tests", *sorted(selected)]
    if summary is not None:
        command.extend(["--summary", str(summary)])
    return subprocess.run(command, cwd=repo, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, default=None)
    parser.add_argument("--changed-files", type=Path, default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--max-parallel", type=int, default=8)
    parser.add_argument("--log-dir", type=Path, default=Path(".ci-logs"))
    parser.add_argument("--warnings-as-errors", type=Path, required=True)
    parser.add_argument("--summary", type=Path, default=None)
    args = parser.parse_args()

    repo = Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve()
    matrix_path = args.matrix or repo / args.log_dir / "compile-matrix.json"
    if args.matrix is None:
        discover_matrix(repo, matrix_path, args.changed_files, args.all, args.summary)

    rows = load_rows(matrix_path, repo)
    if not rows:
        print("No compile rows to run.")
        return 0

    max_parallel = max(1, min(args.max_parallel, len(rows)))
    print(f"Running {len(rows)} compile check(s) with max parallelism {max_parallel}.")

    results: list[CompileResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as executor:
        pending = {
            executor.submit(run_compile, row, repo, args.log_dir): row
            for row in rows
        }
        while pending:
            done, _ = concurrent.futures.wait(
                pending,
                timeout=HEARTBEAT_SECONDS,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            if not done:
                print(f"Still running {len(pending)} compile check(s).", flush=True)
                continue

            for future in done:
                row = pending.pop(future)
                try:
                    result = future.result()
                except BaseException as exc:
                    result = failed_result_for_exception(row, repo, args.log_dir, exc)
                parsed = parse_result(result, repo, args.warnings_as_errors, args.summary)
                results.append(parsed)
                status = "failed" if parsed.compile_returncode or parsed.parse_returncode else "passed"
                print(f"{parsed.row.module} {parsed.row.mode} {parsed.row.group}: {status}", flush=True)
                repro = compile_repro_command(parsed.row, repo)
                print(
                    "::notice title=Compile Repro Command::"
                    f"{github_escape(parsed.row.module)} {github_escape(parsed.row.mode)} "
                    f"{github_escape(parsed.row.group)} => {github_escape(repro)}"
                )

    append_parallel_summary(args.summary, results, max_parallel, repo)
    coverage_failed = run_coverage_checks(repo, args.log_dir, args.summary, rows)
    return int(bool(coverage_failed or any(result.compile_returncode or result.parse_returncode for result in results)))


if __name__ == "__main__":
    sys.exit(main())
