#!/usr/bin/env python3
"""Run changed module integration checks in parallel inside one GitHub job."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import itertools
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from discover_compile_matrix import (
    Candidate,
    discover_candidates,
    expand_flist,
    implicit_dependencies,
    is_global_trigger,
    load_sweep_modes,
    read_changed_files,
    repo_rel,
)


HEARTBEAT_SECONDS = 60
INTEGRATION_MODE_RE = re.compile(r"^(SANITY|MODULE_TESTS).*$")
INTEGRATION_MODE_PATTERNS = ("SANITY*", "MODULE_TESTS*")
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
VERDICT_RE = re.compile(r"\bverdict\s*=\s*(?:VERDICT_)?(PASS|FAIL)\b", re.IGNORECASE)
UNKNOWN_VERDICT_RE = re.compile(r"\bverdict\s*=\s*(\S+)", re.IGNORECASE)
REPLAY_KEY_RE = re.compile(r"\breplay(?:[_\s-]?key)?\s*[=:]\s*([^\s,;]+)", re.IGNORECASE)
RUN_LOG_TAIL_LINES = 200


@dataclass(frozen=True)
class IntegrationRow:
    module: str
    script_dir: Path
    mode: str
    index: int
    params: dict[str, Any]


@dataclass(frozen=True)
class IntegrationResult:
    row: IntegrationRow
    make_log: Path
    compile_log: Path
    sim_log: Path
    result_logs: list[Path]
    compile_returncode: int
    sim_returncode: int
    verdict_returncode: int
    replay_key: str | None


def safe_name(value: str) -> str:
    return SAFE_NAME_RE.sub("_", value).strip("_") or "unnamed"


def warn(message: str, path: str | None = None) -> None:
    file_part = f" file={path}" if path else ""
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::warning{file_part},title=Integration Discovery::{escaped}")


def github_escape(message: str) -> str:
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def extract_replay_key(row_data: dict[str, str], message: str) -> str | None:
    for key, value in row_data.items():
        normalized_key = re.sub(r"[^a-z0-9]+", "", key.lower())
        if normalized_key in {"replaykey", "replay"}:
            cleaned = value.strip()
            if cleaned:
                return cleaned

    replay_match = REPLAY_KEY_RE.search(message)
    if replay_match:
        replay_key = replay_match.group(1).strip()
        if replay_key:
            return replay_key

    return None


def load_sweep_config(script_dir: Path) -> dict[str, Any]:
    return json.loads((script_dir / "sweep.json").read_text(encoding="utf-8"))


def selected_candidates(
    repo: Path,
    changed_files_path: Path | None,
    all_mode: bool,
    summary: Path | None,
) -> list[Candidate]:
    changed_files = read_changed_files(changed_files_path)
    global_trigger = is_global_trigger(changed_files)
    candidates: list[Candidate] = []
    summary_rows: list[tuple[Candidate, str, list[str], str]] = []

    for candidate in discover_candidates(repo):
        if not candidate.rtl_flist.exists():
            rel_flist = repo_rel(candidate.rtl_flist, repo) or candidate.rtl_flist.as_posix()
            warn(f"Skipping {candidate.module}: missing {rel_flist}", rel_flist)
            summary_rows.append((candidate, "skipped", [], f"Missing {rel_flist}"))
            continue

        dependencies = expand_flist(candidate.rtl_flist, repo)
        dependencies.update(implicit_dependencies(candidate, repo))
        hits = sorted(dependencies & changed_files)
        should_emit = all_mode or global_trigger or bool(hits)

        if should_emit:
            candidates.append(candidate)
            note = "manual --all" if all_mode else "global trigger" if global_trigger else "changed compile dependency"
            summary_rows.append((candidate, "kept", hits, note))
        else:
            summary_rows.append((candidate, "skipped", [], "No changed compile dependency"))

    append_discovery_summary(summary, summary_rows, global_trigger, all_mode)
    return candidates


def append_discovery_summary(
    summary: Path | None,
    rows: list[tuple[Candidate, str, list[str], str]],
    global_trigger: bool,
    all_mode: bool,
) -> None:
    if summary is None:
        return

    with summary.open("a", encoding="utf-8") as handle:
        handle.write("## Integration Discovery\n\n")
        handle.write(f"- Global trigger: `{str(global_trigger).lower()}`\n")
        handle.write(f"- Manual all mode: `{str(all_mode).lower()}`\n\n")
        handle.write("| Module | Kind | Status | Hits | Note |\n")
        handle.write("|---|---|---|---|---|\n")
        for candidate, status, hits, note in rows:
            hit_text = ", ".join(f"`{hit}`" for hit in hits[:8])
            if len(hits) > 8:
                hit_text += f", ... (+{len(hits) - 8})"
            escaped_note = note.replace("|", "\\|")
            handle.write(
                f"| `{candidate.module}` | {candidate.kind} | {status} | {hit_text or '-'} | {escaped_note} |\n"
            )
        handle.write("\n")


def expand_rows(candidate: Candidate, repo: Path) -> list[IntegrationRow]:
    matching_modes = [
        mode_name
        for mode_name in load_sweep_modes(candidate.sweep_json, repo)
        if INTEGRATION_MODE_RE.match(mode_name)
    ]
    if not matching_modes:
        warn(
            f"Skipping {candidate.module}: no modes match SANITY* or MODULE_TESTS*",
            repo_rel(candidate.sweep_json, repo),
        )
        return []

    return [
        IntegrationRow(
            module=candidate.module,
            script_dir=candidate.script_dir,
            mode=mode_name,
            index=index,
            params={},
        )
        for index, mode_name in enumerate(matching_modes, start=1)
    ]


def expand_param_sets(params: dict[str, Any]) -> list[dict[str, Any]]:
    keys: list[str] = []
    value_sets: list[list[Any]] = []
    excludes = {
        key.removeprefix("Exclude_"): value
        for key, value in params.items()
        if key.startswith("Exclude_")
    }

    for key, value in params.items():
        if key.startswith("Exclude_"):
            continue
        keys.append(key)
        value_sets.append(expand_param_value(value))

    expanded: list[dict[str, Any]] = []
    for combo in itertools.product(*value_sets):
        row = dict(zip(keys, combo))
        if is_excluded(row, excludes):
            continue
        expanded.append(row)
    return expanded or [{}]


def expand_param_value(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value or [""]
    if isinstance(value, dict) and set(value.keys()) == {".."}:
        return expand_range(value[".."])
    return [value]


def expand_range(value: Any) -> list[Any]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"Unsupported sweep range: {value!r}")
    start, stop, step = value
    if step == 0:
        raise ValueError(f"Unsupported zero-step sweep range: {value!r}")

    values: list[Any] = []
    current = start
    if all(isinstance(item, int) for item in (start, stop, step)):
        compare = (lambda lhs, rhs: lhs <= rhs) if step > 0 else (lambda lhs, rhs: lhs >= rhs)
        while compare(current, stop):
            values.append(current)
            current += step
        return values

    current = float(start)
    stop_f = float(stop)
    step_f = float(step)
    compare_f = (lambda lhs, rhs: lhs <= rhs) if step_f > 0 else (lambda lhs, rhs: lhs >= rhs)
    while compare_f(current, stop_f):
        values.append(current)
        current += step_f
    return values


def is_excluded(row: dict[str, Any], excludes: dict[str, Any]) -> bool:
    for key, excluded_values in excludes.items():
        if key not in row:
            continue
        values = excluded_values if isinstance(excluded_values, list) else [excluded_values]
        if row[key] in values:
            return True
    return False


def sim_args(params: dict[str, Any]) -> str:
    args: list[str] = []
    for key, value in sorted(params.items()):
        if isinstance(value, bool):
            value = int(value)
        args.append(f"+{key}={shlex.quote(str(value))}")
    return " ".join(args)


def load_commands(script_dir: Path) -> tuple[Path, str, str]:
    config = load_sweep_config(script_dir)
    run_dir = script_dir / str(config.get("commandDir", "."))
    compile_cmd = str(config.get("compileCmd", "make compile"))
    run_cmd = str(config.get("runCmd", "make sim"))
    return run_dir.resolve(), compile_cmd, run_cmd


def row_build_tag(row: IntegrationRow) -> str:
    return "run_sim_integration"


def row_subdir(row: IntegrationRow) -> str:
    return f"run_sim_integration_{safe_name(row.mode)}"


def log_paths(row: IntegrationRow, repo: Path, log_dir: Path) -> tuple[Path, Path, Path]:
    safe_mode = safe_name(row.mode)

    make_log = repo / log_dir / f"{row.module}__{safe_mode}__run_sim.log"
    compile_log = make_log
    sim_log = make_log
    return make_log, compile_log, sim_log


def result_dir(row: IntegrationRow) -> Path:
    return row.script_dir / "sim" / row_subdir(row) / "results"


def candidate_run_dirs(row: IntegrationRow) -> list[Path]:
    sim_root = row.script_dir / "sim"
    run_prefix = f"{row_subdir(row)}*"
    candidates: list[Path] = [sim_root / row_subdir(row)]

    if sim_root.exists():
        candidates.extend(path for path in sim_root.glob(run_prefix) if path.is_dir())

    unique_dirs: list[Path] = []
    seen: set[Path] = set()
    for directory in sorted(candidates):
        resolved = directory.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_dirs.append(resolved)
    return unique_dirs


def candidate_result_dirs(row: IntegrationRow, run_started_at: float | None = None) -> list[Path]:
    direct_dir = result_dir(row)
    sim_root = row.script_dir / "sim"
    run_prefix = f"{row_subdir(row)}*"
    candidates: list[Path] = [direct_dir]

    if sim_root.exists():
        for run_dir in sorted(path for path in sim_root.glob(run_prefix) if path.is_dir()):
            candidates.append(run_dir / "results")
            candidates.extend(path for path in run_dir.glob("**/results") if path.is_dir())
        for results_dir in sorted(path for path in sim_root.glob("**/results") if path.is_dir()):
            if run_started_at is None:
                candidates.append(results_dir)
                continue
            try:
                if results_dir.stat().st_mtime >= run_started_at - 5:
                    candidates.append(results_dir)
                    continue
                has_recent_result = any(
                    result_file.is_file() and result_file.stat().st_mtime >= run_started_at - 5
                    for result_file in itertools.chain(
                        results_dir.glob("*_result.csv"),
                        results_dir.glob("*_fw_result.csv"),
                    )
                )
                if has_recent_result:
                    candidates.append(results_dir)
            except OSError:
                continue

    unique_dirs: list[Path] = []
    seen: set[Path] = set()
    for directory in candidates:
        resolved = directory.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_dirs.append(resolved)
    return unique_dirs


def discover_support_logs(row: IntegrationRow) -> list[Path]:
    logs: list[Path] = []
    for run_dir in candidate_run_dirs(row):
        logs.extend(path for path in run_dir.glob(".ci-logs/*.log") if path.is_file())
        logs.extend(path for path in run_dir.glob("logs/*.log") if path.is_file())

    unique_logs: list[Path] = []
    seen: set[Path] = set()
    for log_path in sorted(logs):
        resolved = log_path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_logs.append(resolved)
    return unique_logs


def discover_result_logs(row: IntegrationRow, run_started_at: float | None = None) -> list[Path]:
    logs: list[Path] = []
    for directory in candidate_result_dirs(row, run_started_at):
        if not directory.exists():
            continue
        logs.extend(path for path in directory.glob("*_fw_result.csv") if path.is_file())
        logs.extend(path for path in directory.glob("*_result.csv") if path.is_file())

    unique_logs: list[Path] = []
    seen: set[Path] = set()
    for log_path in sorted(logs):
        resolved = log_path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_logs.append(resolved)
    return unique_logs


def run_command(command: str, cwd: Path, env: dict[str, str], log_handle: Any, title: str) -> int:
    log_handle.write(f"## {title}\n")
    log_handle.write(f"Command: {command}\n")
    log_handle.flush()
    process = subprocess.run(
        ["bash", "-lc", command],
        cwd=cwd,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    log_handle.write(f"\nExit status: {process.returncode}\n")
    log_handle.flush()
    return process.returncode


def run_integration(row: IntegrationRow, repo: Path, log_dir: Path, run_sim_concurrency: int) -> IntegrationResult:
    run_dir = row.script_dir.resolve()
    make_log, compile_log, sim_log = log_paths(row, repo, log_dir)
    make_log.parent.mkdir(parents=True, exist_ok=True)
    run_sim_bin = os.environ.get("RUN_SIM_BIN", "run_sim")
    command_parts = [
        run_sim_bin,
        "--config",
        "sweep.json",
        "--mode",
        row.mode,
        "--makefile-mode",
        "rtl",
        "--concurrency",
        str(run_sim_concurrency),
    ]
    command = " ".join(shlex.quote(part) for part in command_parts)

    env = os.environ.copy()
    env.update(
        {
            "PROJECT_ROOT": repo.as_posix(),
            "PROJ_ROOT": repo.as_posix(),
            "SUBDIR": row_subdir(row),
        }
    )

    print(f"::group::Integration {row.module} {row.mode} run_sim")
    print(f"Run directory: {run_dir}")
    print(f"Output subdir: {row_subdir(row)}")
    print(f"Command: {command}")
    print(f"Log: {make_log}")
    print("::endgroup::", flush=True)

    run_started_at = time.time()
    with make_log.open("w", encoding="utf-8", errors="ignore") as log_handle:
        run_sim_returncode = run_command(command, run_dir, env, log_handle, "run_sim")

    result_logs = discover_result_logs(row, run_started_at)
    verdict_returncode, replay_key = validate_result_logs(
        row,
        result_logs,
        repo,
        run_sim_returncode=run_sim_returncode,
        run_log=make_log,
    )

    return IntegrationResult(
        row=row,
        make_log=make_log,
        compile_log=compile_log,
        sim_log=sim_log,
        result_logs=result_logs,
        compile_returncode=0,
        sim_returncode=run_sim_returncode,
        verdict_returncode=verdict_returncode,
        replay_key=replay_key,
    )


def integration_repro_command(row: IntegrationRow, repo: Path, run_sim_concurrency: int) -> str:
    script_dir = repo_rel(row.script_dir, repo) or row.script_dir.as_posix()
    command_parts = [
        "run_sim",
        "--mode",
        row.mode,
        "--makefile-mode",
        "rtl",
        "--concurrency",
        str(run_sim_concurrency),
    ]
    command = " ".join(shlex.quote(part) for part in command_parts)
    return f"cd {shlex.quote(script_dir)} && SUBDIR={shlex.quote(row_subdir(row))} {command}"


def integration_replay_command(row: IntegrationRow, repo: Path, replay_key: str | None = None) -> str:
    script_dir = repo_rel(row.script_dir, repo) or row.script_dir.as_posix()
    command_parts = [
        "run_sim",
        "--mode",
        row.mode,
        "--makefile-mode",
        "rtl",
        "--replay-key",
        replay_key or "<REPLAY_KEY_FROM_RESULTS>",
    ]
    command = " ".join(shlex.quote(part) for part in command_parts)
    return f"cd {shlex.quote(script_dir)} && SUBDIR={shlex.quote(row_subdir(row))} {command}"


def validate_result_logs(
    row: IntegrationRow,
    result_logs: list[Path],
    repo: Path,
    *,
    run_sim_returncode: int,
    run_log: Path,
) -> tuple[int, str | None]:
    if not result_logs:
        search_dirs = ", ".join(path.as_posix() for path in candidate_result_dirs(row))
        run_log_rel = repo_rel(run_log, repo) or run_log.as_posix()
        support_logs = discover_support_logs(row)
        support_log_rel_paths = [repo_rel(path, repo) or path.as_posix() for path in support_logs]
        support_log_hint = (
            f" related run logs: {', '.join(support_log_rel_paths[:8])}."
            if support_log_rel_paths
            else ""
        )
        print(
            f"::error title=Missing Integration Result Log::"
            f"{row.module} {row.mode} did not generate *_result.csv under any of: {search_dirs}. "
            f"run_sim exit status: {run_sim_returncode}. run_sim log: {run_log_rel}."
            f"{support_log_hint}"
        )
        return 1, None

    failed = False
    verdict_count = 0
    replay_key: str | None = None
    for result_log in result_logs:
        with result_log.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "message" not in reader.fieldnames:
                print(
                    f"::error file={repo_rel(result_log, repo)},title=Malformed Result Log::"
                    "Expected a CSV result log with a `message` column."
                )
                failed = True
                continue

            for line_number, row_data in enumerate(reader, start=2):
                message = row_data.get("message", "")
                if replay_key is None:
                    replay_key = extract_replay_key(row_data, message)
                unknown_match = UNKNOWN_VERDICT_RE.search(message)
                if not unknown_match:
                    continue

                verdict_count += 1
                verdict_match = VERDICT_RE.search(message)
                if not verdict_match:
                    print(
                        f"::error file={repo_rel(result_log, repo)},line={line_number},title=Invalid Verdict::"
                        f"Verdict must be PASS or FAIL: {message}"
                    )
                    failed = True
                    continue

                verdict = verdict_match.group(1).upper()
                if verdict == "FAIL":
                    print(
                        f"::error file={repo_rel(result_log, repo)},line={line_number},title=Integration Failed::"
                        f"{row.module} {row.mode} reported {message}"
                    )
                    failed = True

    if verdict_count == 0:
        print(
            f"::error title=Missing Integration Verdict::"
            f"{row.module} {row.mode} generated result logs but no VERDICT=PASS/FAIL row was found."
        )
        failed = True

    return (1 if failed else 0), replay_key


def print_result_logs(result: IntegrationResult, repo: Path) -> None:
    title = f"Integration result log {result.row.module} {result.row.mode}"
    print(f"::group::{title}")
    if not result.result_logs:
        print(f"No result logs found under {result_dir(result.row)}.")
        print("::endgroup::", flush=True)
        return

    for result_log in result.result_logs:
        rel_log = repo_rel(result_log, repo) or result_log.as_posix()
        print(f"### {rel_log}")
        contents = result_log.read_text(encoding="utf-8", errors="ignore").rstrip()
        print(contents or "[empty result log]")
        print()

    print("::endgroup::", flush=True)


def print_run_log_tail(result: IntegrationResult, repo: Path) -> None:
    title = f"Integration run_sim log tail {result.row.module} {result.row.mode}"
    print(f"::group::{title}")
    rel_log = repo_rel(result.make_log, repo) or result.make_log.as_posix()
    print(f"### {rel_log} (last {RUN_LOG_TAIL_LINES} lines)")
    if not result.make_log.exists():
        print("[missing run_sim log]")
        print("::endgroup::", flush=True)
        return

    contents = result.make_log.read_text(encoding="utf-8", errors="ignore").splitlines()
    tail_lines = contents[-RUN_LOG_TAIL_LINES:] if contents else []
    print("\n".join(tail_lines) if tail_lines else "[empty run_sim log]")
    print("::endgroup::", flush=True)


def failed_result_for_exception(
    row: IntegrationRow,
    repo: Path,
    log_dir: Path,
    exc: BaseException,
) -> IntegrationResult:
    make_log, compile_log, sim_log = log_paths(row, repo, log_dir)
    make_log.parent.mkdir(parents=True, exist_ok=True)
    make_log.write_text(f"Internal integration runner error:\n{exc}\n", encoding="utf-8")
    print(f"::error title=Integration Runner Failed::{row.module} {row.mode}: {exc}")
    return IntegrationResult(row, make_log, compile_log, sim_log, [], 1, 1, 1, None)


def append_parallel_summary(
    summary: Path | None,
    results: list[IntegrationResult],
    max_parallel: int,
    repo: Path,
) -> None:
    if summary is None:
        return

    failures = [
        result
        for result in results
        if result.compile_returncode or result.sim_returncode or result.verdict_returncode
    ]
    with summary.open("a", encoding="utf-8") as handle:
        handle.write("## Parallel Integration Summary\n\n")
        handle.write(f"- Integration checks: `{len(results)}`\n")
        handle.write(f"- run_sim concurrency: `{max_parallel}`\n")
        handle.write(f"- Failures: `{len(failures)}`\n\n")
        for result in sorted(results, key=lambda item: (item.row.module, item.row.mode)):
            handle.write(f"### Integration Result `{result.row.module}` `{result.row.mode}`\n\n")
            handle.write(f"- run_sim exit status: `{result.sim_returncode}`\n")
            handle.write(f"- Verdict status: `{result.verdict_returncode}`\n")
            if not result.result_logs:
                handle.write(f"- Result logs: none found under `{result_dir(result.row).as_posix()}`\n\n")
                continue

            handle.write("- Result logs:\n")
            for result_log in result.result_logs:
                rel_log = repo_rel(result_log, repo) or result_log.as_posix()
                handle.write(f"  - `{rel_log}`\n")
            handle.write("\n")

            for result_log in result.result_logs:
                rel_log = repo_rel(result_log, repo) or result_log.as_posix()
                handle.write(f"<details><summary>{rel_log}</summary>\n\n")
                handle.write("```csv\n")
                handle.write(result_log.read_text(encoding="utf-8", errors="ignore"))
                handle.write("\n```\n\n</details>\n\n")

        if results:
            handle.write("### Reproduce integration checks locally\n\n")
            for result in sorted(results, key=lambda item: (item.row.module, item.row.mode)):
                row = result.row
                status = (
                    "failed"
                    if result.compile_returncode or result.sim_returncode or result.verdict_returncode
                    else "passed"
                )
                handle.write(f"- `{row.module}` `{row.mode}` (`{status}`)\n")
                handle.write("```bash\n")
                handle.write(f"{integration_repro_command(row, repo, max_parallel)}\n")
                handle.write("```\n")
                handle.write("Failure replay:\n")
                handle.write("```bash\n")
                handle.write(f"{integration_replay_command(row, repo, result.replay_key)}\n")
                handle.write("```\n\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--changed-files", type=Path, default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--max-parallel", type=int, default=8)
    parser.add_argument("--log-dir", type=Path, default=Path(".ci-logs"))
    parser.add_argument("--summary", type=Path, default=None)
    args = parser.parse_args()

    repo = Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve()
    rows: list[IntegrationRow] = []
    for candidate in selected_candidates(repo, args.changed_files, args.all, args.summary):
        rows.extend(expand_rows(candidate, repo))

    print(f"Discovered {len(rows)} integration check(s).")
    if not rows:
        print("No integration checks to run.")
        return 0

    max_parallel = max(1, args.max_parallel)
    print(f"Running {len(rows)} integration check(s) with run_sim concurrency {max_parallel}.")

    results: list[IntegrationResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        pending = {
            executor.submit(run_integration, row, repo, args.log_dir, max_parallel): row
            for row in rows
        }
        while pending:
            done, _ = concurrent.futures.wait(
                pending,
                timeout=HEARTBEAT_SECONDS,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            if not done:
                print(f"Still running {len(pending)} integration check(s).", flush=True)
                continue

            for future in done:
                row = pending.pop(future)
                try:
                    result = future.result()
                except BaseException as exc:
                    result = failed_result_for_exception(row, repo, args.log_dir, exc)
                results.append(result)
                status = (
                    "failed"
                    if result.compile_returncode or result.sim_returncode or result.verdict_returncode
                    else "passed"
                )
                print(f"{result.row.module} {result.row.mode}: {status}", flush=True)
                repro = integration_repro_command(result.row, repo, max_parallel)
                replay = integration_replay_command(result.row, repo, result.replay_key)
                print(
                    "::notice title=Integration Repro Command::"
                    f"{github_escape(result.row.module)} {github_escape(result.row.mode)} => "
                    f"{github_escape(repro)}"
                )
                if status == "failed":
                    print(
                        "::notice title=Integration Failure Replay::"
                        f"{github_escape(result.row.module)} {github_escape(result.row.mode)} => "
                        f"{github_escape(replay)}"
                    )

    for result in sorted(results, key=lambda item: (item.row.module, item.row.mode)):
        print_result_logs(result, repo)
        if result.sim_returncode or not result.result_logs:
            print_run_log_tail(result, repo)

    append_parallel_summary(args.summary, results, max_parallel, repo)
    return 1 if any(
        result.compile_returncode or result.sim_returncode or result.verdict_returncode
        for result in results
    ) else 0


if __name__ == "__main__":
    sys.exit(main())
