#!/usr/bin/env python3
"""Run changed module cdc checks in parallel inside one GitHub job."""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from discover_compile_matrix import (
    expand_flist,
    is_global_trigger,
    read_changed_files,
    repo_rel,
)


HEARTBEAT_SECONDS = 60
LICENSE_WAIT_SECONDS = 60
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
LICENSE_WAIT_RE = re.compile(
    r"(?:"
    r"no\s+licenses?\s+available|"
    r"all\s+licenses?\s+are\s+in\s+use|"
    r"waiting\s+for\s+(?:a\s+)?license|"
    r"licensed\s+number\s+of\s+users\s+already\s+reached|"
    r"license\s+checkout\s+failed|"
    r"checkout\s+failed.*license|"
    r"unable\s+to\s+checkout.*license|"
    r"cannot\s+(?:check\s*out|checkout|obtain).*license|"
    r"flex(?:lm|net)\s+error\s*-?(?:4|15|96)"
    r")",
    re.IGNORECASE,
)
RTL_FLIST_FILE_JOIN_RE = re.compile(
    r"set\s+(?:rtl_flist|cdc_rtl_flist)\s+\[file\s+join\s+\$project_root\s+\"([^\"]+)\"\s*\]"
)
RTL_FLIST_QUOTED_RE = re.compile(r"set\s+(?:rtl_flist|cdc_rtl_flist)\s+\"([^\"]+)\"")
CDC_DEP_FILE_JOIN_RE = re.compile(
    r"set\s+(?:cdc_constraint_file|cdc_module_waiver_file)\s+\[file\s+join\s+\$project_root\s+\"([^\"]+)\"\s*\]"
)
CDC_DEP_QUOTED_RE = re.compile(
    r"set\s+(?:cdc_constraint_file|cdc_module_waiver_file)\s+\"([^\"]+)\""
)
MANAGEMENT_TOTAL_RE = re.compile(r"^\s*Total\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)(?:\s+(\d+))?\s*$")
TREE_TOTAL_RE = re.compile(r"^\s*Total\s+(\d+)\s+(\d+)\s*$")
VIOLATION_SECTION_RE = re.compile(
    r"^\s*\S.*\((\d+)\s+(fatal|error|warning|info)s?/\d+\s+waived"
    r"(?:/\d+\s+ignored)?\)\s*$"
)
FILENAME_RE = re.compile(r"^\s*FileName\s+:\s+(.+?)\s*$")
CDC_REPORT_NAME = "report_cdc.top"
CDC_GLOBAL_TRIGGER_EXACT = {
    "package/rtl-commons",
}


@dataclass(frozen=True)
class CDCCandidate:
    module: str
    cdc_tcl: Path
    rtl_flist: Path


@dataclass(frozen=True)
class CDCReport:
    path: Path
    fatals: int
    errors: int
    warnings: int
    infos: int
    waived: int
    reported_fatals: int
    reported_errors: int
    reported_warnings: int
    reported_infos: int
    ignored_external_violations: int
    summary_text: str

    @property
    def unwaived_violations(self) -> int:
        return self.fatals + self.errors + self.warnings + self.infos

    @property
    def reported_unwaived_violations(self) -> int:
        return self.reported_fatals + self.reported_errors + self.reported_warnings + self.reported_infos


@dataclass(frozen=True)
class CDCResult:
    candidate: CDCCandidate
    log_path: Path
    report_path: Path
    tool_returncode: int
    report: CDCReport | None
    returncode: int


def safe_name(value: str) -> str:
    return SAFE_NAME_RE.sub("_", value).strip("_") or "unnamed"


def warn(message: str, path: str | None = None) -> None:
    file_part = f" file={path}" if path else ""
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::warning{file_part},title=CDC Discovery::{escaped}")


def github_escape(message: str) -> str:
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def discover_cdc_candidates(repo: Path) -> list[CDCCandidate]:
    candidates: list[CDCCandidate] = []
    rtl_root = repo / "design" / "rtl"
    if not rtl_root.exists():
        return candidates

    for cdc_tcl in sorted(rtl_root.glob("*/code/cdc.tcl")):
        module = cdc_tcl.parents[1].name
        candidates.append(
            CDCCandidate(
                module=module,
                cdc_tcl=cdc_tcl,
                rtl_flist=parse_cdc_flist(cdc_tcl, repo, module),
            )
        )
    return candidates


def parse_cdc_flist(cdc_tcl: Path, repo: Path, module: str) -> Path:
    try:
        text = cdc_tcl.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        warn(f"Could not read cdc TCL: {exc}", repo_rel(cdc_tcl, repo))
        return default_cdc_flist(repo, module)

    file_join_match = RTL_FLIST_FILE_JOIN_RE.search(text)
    if file_join_match:
        return (repo / file_join_match.group(1)).resolve()

    quoted_match = RTL_FLIST_QUOTED_RE.search(text)
    if quoted_match:
        return (repo / quoted_match.group(1)).resolve()

    warn(
        "Could not derive rtl_flist from cdc.tcl; using default design flist path.",
        repo_rel(cdc_tcl, repo),
    )
    return default_cdc_flist(repo, module)


def default_cdc_flist(repo: Path, module: str) -> Path:
    return repo / "design" / "rtl" / module / "pd_handoff" / "rtl.flist"


def should_run_all(changed_files: set[str], all_mode: bool) -> bool:
    if all_mode:
        return True
    if is_global_trigger(changed_files):
        return True
    return any(changed in CDC_GLOBAL_TRIGGER_EXACT for changed in changed_files)


def implicit_dependencies(candidate: CDCCandidate, repo: Path) -> set[str]:
    deps: set[str] = set()
    paths = [
        candidate.cdc_tcl,
        candidate.rtl_flist,
        repo / "package" / "rtl-commons" / "static" / "cdc.tcl",
        repo / "package" / "rtl-commons" / "static" / "cdc_waiver.tcl",
    ]
    paths.extend(parse_optional_cdc_dependencies(candidate.cdc_tcl, repo))
    for path in paths:
        rel_path = repo_rel(path, repo)
        if rel_path:
            deps.add(rel_path)
    return deps


def parse_optional_cdc_dependencies(cdc_tcl: Path, repo: Path) -> list[Path]:
    try:
        text = cdc_tcl.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        warn(f"Could not read CDC dependency paths: {exc}", repo_rel(cdc_tcl, repo))
        return []

    paths: list[Path] = []
    for match in CDC_DEP_FILE_JOIN_RE.finditer(text):
        paths.append((repo / match.group(1)).resolve())
    for match in CDC_DEP_QUOTED_RE.finditer(text):
        path_text = match.group(1)
        if path_text:
            paths.append((repo / path_text).resolve())
    return paths


def discover_rows(
    repo: Path,
    changed_files_path: Path | None,
    all_mode: bool,
    summary: Path | None,
) -> list[CDCCandidate]:
    changed_files = read_changed_files(changed_files_path)
    run_all = should_run_all(changed_files, all_mode)
    rows: list[CDCCandidate] = []
    summary_rows: list[tuple[CDCCandidate, str, list[str], str]] = []

    for candidate in discover_cdc_candidates(repo):
        if not candidate.rtl_flist.exists():
            rel_flist = repo_rel(candidate.rtl_flist, repo) or candidate.rtl_flist.as_posix()
            warn(f"Skipping {candidate.module}: missing {rel_flist}", rel_flist)
            summary_rows.append((candidate, "skipped", [], f"Missing {rel_flist}"))
            continue

        dependencies = expand_flist(candidate.rtl_flist, repo)
        dependencies.update(implicit_dependencies(candidate, repo))
        hits = sorted(dependencies & changed_files)
        should_emit = run_all or bool(hits)

        if should_emit:
            rows.append(candidate)
            note = "manual --all" if all_mode else "global trigger" if run_all else "changed cdc flist dependency"
            summary_rows.append((candidate, "kept", hits, note))
        else:
            summary_rows.append((candidate, "skipped", [], "No changed cdc flist dependency"))

    append_discovery_summary(summary, summary_rows, run_all, all_mode)
    print(f"Discovered {len(rows)} cdc check(s).")
    return rows


def append_discovery_summary(
    summary: Path | None,
    rows: list[tuple[CDCCandidate, str, list[str], str]],
    run_all: bool,
    all_mode: bool,
) -> None:
    if summary is None:
        return

    with summary.open("a", encoding="utf-8") as handle:
        handle.write("## CDC Discovery\n\n")
        handle.write(f"- Global trigger: `{str(run_all and not all_mode).lower()}`\n")
        handle.write(f"- Manual all mode: `{str(all_mode).lower()}`\n\n")
        handle.write("| Module | Status | Hits | Note |\n")
        handle.write("|---|---|---|---|\n")
        for candidate, status, hits, note in rows:
            hit_text = ", ".join(f"`{hit}`" for hit in hits[:8])
            if len(hits) > 8:
                hit_text += f", ... (+{len(hits) - 8})"
            escaped_note = note.replace("|", "\\|")
            handle.write(
                f"| `{candidate.module}` | {status} | {hit_text or '-'} | {escaped_note} |\n"
            )
        handle.write("\n")


def make_ci_cdc_tcl(candidate: CDCCandidate) -> Path:
    ci_tcl = candidate.cdc_tcl.with_name(".ci_cdc.tcl")
    lines = candidate.cdc_tcl.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
    # This must precede the module script: it sources the shared CDC runner,
    # which uses the flag to skip its interactive view_activity command.
    filtered: list[str] = ["set ::env(VC_STATIC_BATCH) 1\n"]
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("view_", "start_gui")):
            continue
        filtered.append(line)
        if stripped.startswith("set project_root "):
            filtered.append("set ::env(PROJECT_ROOT) $project_root\n")
            filtered.append("set ::env(PROJ_ROOT) $project_root\n")

    if filtered and not filtered[-1].endswith("\n"):
        filtered[-1] += "\n"
    filtered.append("\n# Added by CI wrapper to keep vc_static_shell non-interactive.\n")
    filtered.append("exit\n")
    ci_tcl.write_text("".join(filtered), encoding="utf-8")
    return ci_tcl


def cdc_report_path(candidate: CDCCandidate) -> Path:
    return candidate.cdc_tcl.parent / "cdc_run" / CDC_REPORT_NAME


def parse_cdc_report(report_path: Path, repo: Path) -> CDCReport | None:
    if not report_path.exists():
        return None

    report_text = report_path.read_text(encoding="utf-8", errors="ignore")
    reported_counts = parse_management_summary(report_text)
    if reported_counts is None:
        return None

    repo_counts, ignored_external_violations = count_repo_violations(report_text, repo)
    if sum(repo_counts) == 0 and ignored_external_violations == 0 and sum(reported_counts[:4]) > 0:
        repo_counts = reported_counts[:4]

    return CDCReport(
        path=report_path,
        fatals=repo_counts[0],
        errors=repo_counts[1],
        warnings=repo_counts[2],
        infos=repo_counts[3],
        waived=reported_counts[4],
        reported_fatals=reported_counts[0],
        reported_errors=reported_counts[1],
        reported_warnings=reported_counts[2],
        reported_infos=reported_counts[3],
        ignored_external_violations=ignored_external_violations,
        summary_text=extract_report_summary(report_text),
    )


def parse_management_summary(report_text: str) -> tuple[int, int, int, int, int] | None:
    in_management_summary = False
    for line in report_text.splitlines():
        stripped = line.strip()
        if stripped == "Management Summary":
            in_management_summary = True
            continue
        if not in_management_summary:
            continue

        match = MANAGEMENT_TOTAL_RE.match(line)
        if not match:
            continue

        fatals, errors, warnings, infos, waived = match.groups()
        return (
            int(fatals),
            int(errors),
            int(warnings),
            int(infos),
            int(waived or 0),
        )

    return None


def count_repo_violations(report_text: str, repo: Path) -> tuple[tuple[int, int, int, int], int]:
    severity_to_index = {"fatal": 0, "error": 1, "warning": 2, "info": 3}
    counts = [0, 0, 0, 0]
    ignored_external_violations = 0
    current_severity: str | None = None
    current_remaining = 0

    for line in report_text.splitlines():
        section_match = VIOLATION_SECTION_RE.match(line)
        if section_match:
            current_remaining = int(section_match.group(1))
            current_severity = section_match.group(2) if current_remaining > 0 else None
            continue

        filename_match = FILENAME_RE.match(line)
        if current_severity is None or current_remaining <= 0 or not filename_match:
            continue

        if is_repo_report_path(filename_match.group(1), repo):
            counts[severity_to_index[current_severity]] += 1
        else:
            ignored_external_violations += 1
        current_remaining -= 1

    return (counts[0], counts[1], counts[2], counts[3]), ignored_external_violations


def is_repo_report_path(path_text: str, repo: Path) -> bool:
    report_path = path_text.strip()
    if not report_path:
        return True

    normalized_report_path = report_path.replace("\\", "/")
    normalized_repo = repo.as_posix().rstrip("/")
    if normalized_report_path.startswith(f"{normalized_repo}/"):
        return True

    is_absolute = normalized_report_path.startswith("/") or re.match(r"^[A-Za-z]:/", normalized_report_path)
    if not is_absolute:
        return True

    return False


def extract_report_summary(report_text: str) -> str:
    lines = report_text.splitlines()
    start = 0
    for index, line in enumerate(lines):
        if line.strip() == "Report Summary":
            start = max(0, index - 1)
            break

    end = len(lines)
    in_tree_summary = False
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if stripped == "Tree Summary":
            in_tree_summary = True
            continue
        if in_tree_summary and TREE_TOTAL_RE.match(lines[index]):
            end = index + 1
            for next_index in range(index + 1, min(len(lines), index + 4)):
                next_line = lines[next_index].strip()
                if next_line and set(next_line) == {"-"}:
                    end = next_index + 1
                    break
            break

    return "\n".join(lines[start:end]).rstrip()


def run_cdc_once(
    candidate: CDCCandidate,
    repo: Path,
    log_path: Path,
    log_mode: str,
    attempt: int,
) -> tuple[int, str, bool]:
    ci_tcl = make_ci_cdc_tcl(candidate)
    command = ["vc_static_shell", "-f", str(ci_tcl)]
    output_lines: list[str] = []
    license_wait_seen = False

    with log_path.open(log_mode, encoding="utf-8", errors="ignore") as log_handle:
        try:
            env = os.environ.copy()
            env.update(
                {
                    "PROJECT_ROOT": repo.as_posix(),
                    "PROJ_ROOT": repo.as_posix(),
                }
            )
            process = subprocess.Popen(
                command,
                cwd=repo,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
            )
            assert process.stdout is not None
            for line in process.stdout:
                output_lines.append(line)
                log_handle.write(line)
                if not license_wait_seen and LICENSE_WAIT_RE.search(line):
                    license_wait_seen = True
                    print_license_wait_notice(candidate.module, attempt, retry=False)
            returncode = process.wait()
        finally:
            ci_tcl.unlink(missing_ok=True)

    return returncode, "".join(output_lines), license_wait_seen


def is_license_wait_failure(returncode: int, output_text: str, license_wait_seen: bool) -> bool:
    return returncode != 0 and (license_wait_seen or LICENSE_WAIT_RE.search(output_text) is not None)


def print_license_wait_notice(module: str, attempt: int, retry: bool) -> str:
    retry_text = f"; retrying in {LICENSE_WAIT_SECONDS}s" if retry else ""
    message = f"{module} cdc is waiting for a vc_static license{retry_text} (attempt {attempt})."
    print(message, flush=True)
    return message


def run_cdc(candidate: CDCCandidate, repo: Path, log_dir: Path) -> CDCResult:
    log_path = repo / log_dir / f"{candidate.module}__cdc.log"
    report_path = cdc_report_path(candidate)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    command = ["vc_static_shell", "-f", str(candidate.cdc_tcl.with_name(".ci_cdc.tcl"))]
    print(f"::group::CDC {candidate.module}")
    print(f"Command: {' '.join(command)}")
    print(f"Log: {log_path}")

    attempt = 1
    log_mode = "w"
    while True:
        returncode, output_text, license_wait_seen = run_cdc_once(candidate, repo, log_path, log_mode, attempt)
        if not is_license_wait_failure(returncode, output_text, license_wait_seen):
            break

        message = print_license_wait_notice(candidate.module, attempt, retry=True)
        with log_path.open("a", encoding="utf-8", errors="ignore") as log_handle:
            log_handle.write(f"\n{message} Previous attempt exited {returncode}.\n\n")
        time.sleep(LICENSE_WAIT_SECONDS)
        attempt += 1
        log_mode = "a"

    print("::endgroup::", flush=True)
    report = parse_cdc_report(report_path, repo)
    final_returncode = 1 if returncode != 0 or report is None or report.unwaived_violations else 0
    return CDCResult(candidate, log_path, report_path, returncode, report, final_returncode)


def cdc_repro_command(candidate: CDCCandidate, repo: Path) -> str:
    cdc_tcl = repo_rel(candidate.cdc_tcl, repo) or candidate.cdc_tcl.as_posix()
    return f"vc_static_shell -f {shlex.quote(cdc_tcl)}"


def print_cdc_report(result: CDCResult, repo: Path) -> None:
    print(f"::group::CDC Report {result.candidate.module}")
    print(f"Tool log: {result.log_path}")
    print(f"Report: {result.report_path}")
    if result.report is None:
        print("No parseable cdc report was generated.")
    else:
        print(result.report.summary_text)
    print("::endgroup::", flush=True)

    if result.tool_returncode != 0:
        print(
            f"::warning file={repo_rel(result.candidate.cdc_tcl, repo)},title=CDC Tool Failed::"
            f"{github_escape(result.candidate.module)} cdc exited with status {result.tool_returncode}."
        )
    elif result.report is None:
        print(
            f"::warning file={repo_rel(result.candidate.cdc_tcl, repo)},title=Missing CDC Report::"
            f"{github_escape(result.candidate.module)} did not generate a parseable {CDC_REPORT_NAME}."
        )
    elif result.report.unwaived_violations:
        report = result.report
        message = (
            f"{result.candidate.module} cdc reported {report.unwaived_violations} repo violation(s): "
            f"{report.fatals} fatal(s), {report.errors} error(s), "
            f"{report.warnings} warning(s), {report.infos} info(s); "
            f"{report.ignored_external_violations} external violation(s) ignored; {report.waived} waived."
        )
        print(
            f"::warning file={repo_rel(report.path, repo)},title=CDC Violations::"
            f"{github_escape(message)}"
        )


def failed_result_for_exception(
    candidate: CDCCandidate,
    repo: Path,
    log_dir: Path,
    exc: BaseException,
) -> CDCResult:
    log_path = repo / log_dir / f"{candidate.module}__cdc.log"
    report_path = cdc_report_path(candidate)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(f"Internal cdc runner error:\n{exc}\n", encoding="utf-8")
    print(f"::warning title=CDC Runner Failed::{candidate.module}: {exc}")
    return CDCResult(candidate, log_path, report_path, 1, None, 1)


def append_parallel_summary(summary: Path | None, results: list[CDCResult], max_parallel: int, repo: Path) -> None:
    if summary is None:
        return

    failures = [result for result in results if result.returncode != 0]
    with summary.open("a", encoding="utf-8") as handle:
        handle.write("## Parallel CDC Summary\n\n")
        handle.write(f"- CDC checks: `{len(results)}`\n")
        handle.write(f"- Max parallel: `{max_parallel}`\n")
        handle.write(f"- Failures: `{len(failures)}`\n\n")
        for result in sorted(results, key=lambda item: item.candidate.module):
            handle.write(f"### CDC Report `{result.candidate.module}`\n\n")
            handle.write(f"- Tool exit status: `{result.tool_returncode}`\n")
            handle.write(f"- Report: `{repo_rel(result.report_path, repo) or result.report_path.as_posix()}`\n")
            if result.report is None:
                handle.write(f"- Status: `failed` - no parseable `{CDC_REPORT_NAME}` was generated.\n\n")
                continue

            report = result.report
            handle.write(f"- Repo violations: `{report.unwaived_violations}`\n")
            handle.write(f"- Fatals: `{report.fatals}`\n")
            handle.write(f"- Errors: `{report.errors}`\n")
            handle.write(f"- Warnings: `{report.warnings}`\n")
            handle.write(f"- Infos: `{report.infos}`\n")
            handle.write(f"- External violations ignored: `{report.ignored_external_violations}`\n")
            handle.write(f"- Reported unwaived violations: `{report.reported_unwaived_violations}`\n")
            handle.write(f"- Waived: `{report.waived}`\n\n")
            handle.write("<details><summary>Report summary</summary>\n\n")
            handle.write("```text\n")
            handle.write(report.summary_text)
            handle.write("\n```\n\n</details>\n\n")

        if results:
            handle.write("### Reproduce cdc checks locally\n\n")
            for result in sorted(results, key=lambda item: item.candidate.module):
                status = "failed" if result.returncode else "passed"
                handle.write(f"- `{result.candidate.module}` (`{status}`)\n")
                handle.write("```bash\n")
                handle.write(f"{cdc_repro_command(result.candidate, repo)}\n")
                handle.write("```\n\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--changed-files", type=Path, default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--max-parallel", type=int, default=8)
    parser.add_argument("--log-dir", type=Path, default=Path(".ci-logs"))
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument(
        "--fail-on-issues",
        action="store_true",
        help="Return non-zero exit code when cdc failures are detected.",
    )
    args = parser.parse_args()

    repo = Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve()
    rows = discover_rows(repo, args.changed_files, args.all, args.summary)
    if not rows:
        print("No cdc checks to run.")
        return 0

    max_parallel = max(1, min(args.max_parallel, len(rows)))
    print(f"Running {len(rows)} cdc check(s) with max parallelism {max_parallel}.")

    results: list[CDCResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as executor:
        next_row_index = 0
        pending: dict[concurrent.futures.Future[CDCResult], CDCCandidate] = {}

        def submit_next_cdc() -> None:
            nonlocal next_row_index
            if next_row_index >= len(rows):
                return
            row = rows[next_row_index]
            next_row_index += 1
            pending[executor.submit(run_cdc, row, repo, args.log_dir)] = row

        for _ in range(max_parallel):
            submit_next_cdc()

        while pending:
            done, _ = concurrent.futures.wait(
                pending,
                timeout=HEARTBEAT_SECONDS,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            if not done:
                queued = len(rows) - next_row_index
                queued_text = f"; {queued} queued" if queued else ""
                print(f"Still running {len(pending)} cdc check(s){queued_text}.", flush=True)
                continue

            for future in done:
                row = pending.pop(future)
                try:
                    result = future.result()
                except BaseException as exc:
                    result = failed_result_for_exception(row, repo, args.log_dir, exc)
                results.append(result)
                status = "failed" if result.returncode else "passed"
                print(f"{result.candidate.module} cdc: {status}", flush=True)
                repro = cdc_repro_command(result.candidate, repo)
                print(
                    "::notice title=CDC Repro Command::"
                    f"{github_escape(result.candidate.module)} => "
                    f"{github_escape(f'Run from repo root: {repro}')}"
                )
                submit_next_cdc()

    for result in sorted(results, key=lambda item: item.candidate.module):
        print_cdc_report(result, repo)

    append_parallel_summary(args.summary, results, max_parallel, repo)
    failures_detected = any(result.returncode != 0 for result in results)
    if failures_detected:
        print("::warning title=CDC Checks::CDC issues detected; reporting warning only.")
    if args.fail_on_issues and failures_detected:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
