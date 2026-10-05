#!/usr/bin/env python3
"""Parse VCS logs into grouped diagnostics and a job summary row."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


ERROR_RE = re.compile(r"^\s*(%?Error|Error-|Fatal-)")
WARNING_RE = re.compile(r"^\s*(%?Warning|Warning-)")
WARNING_TAG_RE = re.compile(r"Warning(?:-\[?|\[)([A-Za-z0-9_.$:+-]+)\]?")
UNTAGGED_ERROR_RE = re.compile(r"No options passed to vcs", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    severity: str
    line_no: int
    message: str
    tag: str | None = None


def escape_annotation_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def load_warning_tags(path: Path) -> set[str]:
    if not path.exists():
        return set()

    tags: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                tags.add(stripped)
    return tags


def extract_warning_tag(line: str) -> str | None:
    match = WARNING_TAG_RE.search(line)
    if not match:
        return None
    return match.group(1).strip("[]")


def is_diagnostic_start(line: str) -> bool:
    return bool(ERROR_RE.search(line) or WARNING_RE.search(line) or UNTAGGED_ERROR_RE.search(line))


def collect_message(lines: list[str], start_index: int, context_lines: int) -> str:
    message_lines = [lines[start_index].rstrip()]
    for offset in range(1, context_lines + 1):
        index = start_index + offset
        if index >= len(lines):
            break
        line = lines[index].rstrip()
        if is_diagnostic_start(line):
            break
        message_lines.append(line)
    return "\n".join(message_lines).rstrip()


def parse_log(path: Path, warnings_as_errors: set[str], context_lines: int) -> list[Finding]:
    findings: list[Finding] = []
    if not path.exists():
        return [
            Finding(
                severity="error",
                line_no=1,
                message=f"Log file does not exist: {path}",
            )
        ]

    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for index, line in enumerate(lines):
        if ERROR_RE.search(line):
            findings.append(Finding("error", index + 1, collect_message(lines, index, context_lines)))
            continue

        if UNTAGGED_ERROR_RE.search(line):
            findings.append(Finding("error", index + 1, collect_message(lines, index, context_lines)))
            continue

        if WARNING_RE.search(line):
            tag = extract_warning_tag(line)
            severity = "error" if tag and tag in warnings_as_errors else "warning"
            findings.append(
                Finding(severity, index + 1, collect_message(lines, index, context_lines), tag)
            )

    return findings


def format_context(module: str, mode: str, group: str, compile_switches: str) -> str:
    switches = compile_switches.strip() or "<none>"
    return f"module={module}, mode={mode}, group={group}, compile_switches={switches}"


def format_group_title(message: str, module: str, group: str, compile_switches: str) -> str:
    switches = compile_switches.strip() or "<none>"
    summary = next((line.strip() for line in message.splitlines() if line.strip()), "<empty diagnostic>")
    return f"{summary} module={module} group={group} compile_switches={switches}"


def emit_diagnostic_groups(
    findings: list[Finding],
    max_annotations: int,
    module: str,
    mode: str,
    group: str,
    compile_switches: str,
) -> None:
    context = format_context(module, mode, group, compile_switches)
    for finding in findings[:max_annotations]:
        group_title = format_group_title(finding.message, module, group, compile_switches)
        print(f"::group::{escape_annotation_data(group_title)}")
        print(context)
        print(finding.message)
        print("::endgroup::")


def append_summary(
    summary: Path | None,
    module: str,
    mode: str,
    group: str,
    log: Path,
    findings: list[Finding],
) -> None:
    if summary is None:
        return

    err_count = sum(1 for finding in findings if finding.severity == "error")
    warn_count = sum(1 for finding in findings if finding.severity == "warning")
    status = "failure" if err_count else "success"

    with summary.open("a", encoding="utf-8") as handle:
        handle.write("## VCS Compile Log\n\n")
        handle.write("| Module | Mode | Group | Status | Errors | Warnings | Log |\n")
        handle.write("|---|---|---|---|---:|---:|---|\n")
        handle.write(
            f"| `{module}` | `{mode}` | `{group}` | **{status}** | "
            f"{err_count} | {warn_count} | `{log.as_posix()}` |\n\n"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("--module", required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--group", required=True)
    parser.add_argument("--compile-switches", default="")
    parser.add_argument("--warnings-as-errors", type=Path, required=True)
    parser.add_argument("--max-annotations", type=int, default=50)
    parser.add_argument("--context-lines", type=int, default=8)
    parser.add_argument("--summary", type=Path, default=None)
    args = parser.parse_args()

    warnings_as_errors = load_warning_tags(args.warnings_as_errors)
    findings = parse_log(args.log, warnings_as_errors, args.context_lines)
    emit_diagnostic_groups(
        findings,
        args.max_annotations,
        args.module,
        args.mode,
        args.group,
        args.compile_switches,
    )
    append_summary(args.summary, args.module, args.mode, args.group, args.log, findings)

    return 1 if any(finding.severity == "error" for finding in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
