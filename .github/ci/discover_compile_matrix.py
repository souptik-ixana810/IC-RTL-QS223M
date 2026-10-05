#!/usr/bin/env python3
"""Build the compile-check matrix for changed RTL/model tests."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


COMPILE_MODE_RE = re.compile(r"^COMPILE_CHECK.*$")
PROJECT_VAR_RE = re.compile(r"\$\{?PROJECT_ROOT\}?|\$PROJ_ROOT")

IGNORED_PREFIXES = (
    "+incdir+",
    "+define+",
    "+libext+",
    "+",
)

IGNORED_OPTIONS_WITH_ARG = {
    "-y",
    "-libmap",
    "-timescale",
}

GLOBAL_TRIGGER_EXACT = {
    ".gitmodules",
    "test/Makefile",
}

GLOBAL_TRIGGER_PREFIXES = (
    ".github/workflows/",
    ".github/actions/",
    ".github/ci/",
)

TOP_LEVEL_SCRIPT_DIR = Path("test/top_level/script")
TOP_LEVEL_RTL_FLIST = TOP_LEVEL_SCRIPT_DIR / "rtl.flist"
LITEUVM_PATH = "package/rtl-liteuvm"
LEGACY_TEST_SUFFIX = "_test"


@dataclass(frozen=True)
class Candidate:
    module: str
    kind: str
    script_dir: Path
    sweep_json: Path
    rtl_flist: Path


@dataclass
class DiscoveryResult:
    candidate: Candidate
    status: str
    hits: list[str]
    row_count: int
    note: str = ""


def repo_rel(path: Path, repo: Path) -> str | None:
    try:
        return path.resolve().relative_to(repo).as_posix()
    except ValueError:
        return None


def normalize_changed_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def read_changed_files(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    changed: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            normalized = normalize_changed_path(line)
            if normalized:
                changed.add(normalized)
    return changed


def is_global_trigger(changed_files: Iterable[str]) -> bool:
    for changed in changed_files:
        if changed in GLOBAL_TRIGGER_EXACT:
            return True
        if any(changed.startswith(prefix) for prefix in GLOBAL_TRIGGER_PREFIXES):
            return True
    return False


def warn(message: str, path: str | None = None) -> None:
    file_part = f" file={path}" if path else ""
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::warning{file_part},title=Compile Discovery::{escaped}")


def write_output(name: str, value: str) -> None:
    github_output = os.environ.get("GITHUB_OUTPUT")
    if not github_output:
        print(f"{name}={value}")
        return
    with Path(github_output).open("a", encoding="utf-8") as handle:
        handle.write(f"{name}<<__{name.upper()}__\n{value}\n__{name.upper()}__\n")


def append_summary(summary: Path | None, results: list[DiscoveryResult], global_trigger: bool, all_mode: bool) -> None:
    if summary is None:
        return
    with summary.open("a", encoding="utf-8") as handle:
        handle.write("## Compile Discovery\n\n")
        handle.write(f"- Global trigger: `{str(global_trigger).lower()}`\n")
        handle.write(f"- Manual all mode: `{str(all_mode).lower()}`\n\n")
        handle.write("| Module | Kind | Status | Hits | Rows | Note |\n")
        handle.write("|---|---|---|---|---:|---|\n")
        for result in results:
            hits = ", ".join(f"`{hit}`" for hit in result.hits[:8])
            if len(result.hits) > 8:
                hits += f", ... (+{len(result.hits) - 8})"
            note = result.note.replace("|", "\\|")
            handle.write(
                f"| `{result.candidate.module}` | {result.candidate.kind} | "
                f"{result.status} | {hits or '-'} | {result.row_count} | {note} |\n"
            )
        handle.write("\n")


def discover_candidates(repo: Path) -> list[Candidate]:
    candidates: list[Candidate] = []

    top_level_sweep = repo / TOP_LEVEL_SCRIPT_DIR / "sweep.json"
    if top_level_sweep.exists():
        candidates.append(
            Candidate(
                module="top_level",
                kind="top_level",
                script_dir=top_level_sweep.parent,
                sweep_json=top_level_sweep,
                rtl_flist=repo / TOP_LEVEL_RTL_FLIST,
            )
        )

    rtl_root = repo / "test" / "rtl_test"
    if rtl_root.exists():
        for sweep in sorted(rtl_root.glob("*/script/sweep.json")):
            module = sweep.parents[1].name
            if module.endswith(LEGACY_TEST_SUFFIX):
                warn(
                    f"Skipping {module}: module test directories must be named without the `{LEGACY_TEST_SUFFIX}` suffix.",
                    repo_rel(sweep, repo),
                )
                continue
            candidates.append(
                Candidate(
                    module=module,
                    kind="rtl",
                    script_dir=sweep.parent,
                    sweep_json=sweep,
                    rtl_flist=sweep.parent / "rtl.flist",
                )
            )

    model_root = repo / "test" / "model_test"
    if model_root.exists():
        for sweep in sorted(model_root.glob("*/script/sweep.json")):
            module = sweep.parents[1].name
            candidates.append(
                Candidate(
                    module=module,
                    kind="model",
                    script_dir=sweep.parent,
                    sweep_json=sweep,
                    rtl_flist=sweep.parent / "rtl.flist",
                )
            )

    return candidates


def strip_comment(line: str) -> str:
    if "#" not in line:
        return line.strip()
    return line.split("#", 1)[0].strip()


def substitute_project_root(token: str, repo: Path) -> str:
    return PROJECT_VAR_RE.sub(repo.as_posix(), token)


def resolve_token(token: str, base_dir: Path, repo: Path) -> Path:
    cleaned = substitute_project_root(token.strip().strip('"').strip("'"), repo)
    path = Path(cleaned)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def tokenize_flist_line(line: str) -> list[str]:
    try:
        return shlex.split(line, posix=True)
    except ValueError:
        return line.split()


def expand_flist(flist: Path, repo: Path, visited: set[Path] | None = None) -> set[str]:
    visited = visited or set()
    flist = flist.resolve()
    if flist in visited:
        return set()
    visited.add(flist)

    dependencies: set[str] = set()
    rel_flist = repo_rel(flist, repo)
    if rel_flist:
        dependencies.add(rel_flist)

    if not flist.exists():
        warn(f"Missing rtl.flist referenced by discovery: {flist}", rel_flist)
        return dependencies

    base_dir = flist.parent
    with flist.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            line = strip_comment(raw_line)
            if not line:
                continue

            tokens = tokenize_flist_line(line)
            index = 0
            while index < len(tokens):
                token = tokens[index]

                if token == "-f":
                    if index + 1 >= len(tokens):
                        warn("Found `-f` without a following flist path", rel_flist)
                        index += 1
                        continue
                    child = resolve_token(tokens[index + 1], base_dir, repo)
                    dependencies.update(expand_flist(child, repo, visited))
                    index += 2
                    continue

                if token.startswith("-f") and len(token) > 2:
                    child = resolve_token(token[2:], base_dir, repo)
                    dependencies.update(expand_flist(child, repo, visited))
                    index += 1
                    continue

                if token in IGNORED_OPTIONS_WITH_ARG:
                    index += 2
                    continue

                if token.startswith(IGNORED_PREFIXES) or token.startswith("-"):
                    index += 1
                    continue

                candidate = resolve_token(token, base_dir, repo)
                rel_candidate = repo_rel(candidate, repo)
                if rel_candidate and candidate.is_file():
                    dependencies.add(rel_candidate)
                index += 1

    return dependencies


def flist_references_liteuvm(flist: Path, repo: Path, visited: set[Path] | None = None) -> bool:
    visited = visited or set()
    flist = flist.resolve()
    if flist in visited:
        return False
    visited.add(flist)

    if not flist.exists():
        return False

    base_dir = flist.parent
    with flist.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            line = strip_comment(raw_line)
            if not line:
                continue

            normalized = substitute_project_root(line, repo).replace("\\", "/")
            if LITEUVM_PATH in normalized:
                return True

            tokens = tokenize_flist_line(line)
            index = 0
            while index < len(tokens):
                token = tokens[index]
                child_token: str | None = None
                if token == "-f" and index + 1 < len(tokens):
                    child_token = tokens[index + 1]
                    index += 2
                elif token.startswith("-f") and len(token) > 2:
                    child_token = token[2:]
                    index += 1
                else:
                    index += 1

                if child_token is None:
                    continue

                child = resolve_token(child_token, base_dir, repo)
                if flist_references_liteuvm(child, repo, visited):
                    return True

    return False


def implicit_dependencies(candidate: Candidate, repo: Path) -> set[str]:
    deps: set[str] = set()
    for path in (candidate.rtl_flist, candidate.script_dir / "Makefile", candidate.sweep_json):
        rel_path = repo_rel(path, repo)
        if rel_path:
            deps.add(rel_path)
    return deps


def load_sweep_modes(sweep_json: Path, repo: Path) -> dict[str, object]:
    try:
        with sweep_json.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        warn(f"Could not read {sweep_json}: {exc}", repo_rel(sweep_json, repo))
        return {}
    modes = config.get("modes", {})
    return modes if isinstance(modes, dict) else {}


def join_switches(values: object) -> str:
    if not isinstance(values, list):
        return ""
    return ",".join(str(value) for value in values)


def expand_mode_rows(candidate: Candidate, repo: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    modes = load_sweep_modes(candidate.sweep_json, repo)
    compile_modes = {
        name: spec
        for name, spec in modes.items()
        if COMPILE_MODE_RE.match(name) and isinstance(spec, dict)
    }

    if not compile_modes:
        return [
            {
                "module": candidate.module,
                "kind": candidate.kind,
                "script_dir": candidate.script_dir.as_posix(),
                "mode": "DEFAULT",
                "group": "BASE",
                "defines": "",
            }
        ]

    for mode_name, spec in compile_modes.items():
        groups = spec.get("compileSwitchGroups")
        if isinstance(groups, dict):
            for group_name, switches in groups.items():
                rows.append(
                    {
                        "module": candidate.module,
                        "kind": candidate.kind,
                        "script_dir": candidate.script_dir.as_posix(),
                        "mode": mode_name,
                        "group": str(group_name),
                        "defines": join_switches(switches),
                    }
                )
            continue

        rows.append(
            {
                "module": candidate.module,
                "kind": candidate.kind,
                "script_dir": candidate.script_dir.as_posix(),
                "mode": mode_name,
                "group": "BASE",
                "defines": join_switches(spec.get("compileSwitches")),
            }
        )

    return rows


def make_relative_rows(rows: list[dict[str, str]], repo: Path) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for row in rows:
        copied = dict(row)
        script_dir = Path(copied["script_dir"])
        if script_dir.is_absolute():
            rel_script = repo_rel(script_dir, repo)
            if rel_script:
                copied["script_dir"] = rel_script
        normalized.append(copied)
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--changed-files", type=Path, default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--summary", type=Path, default=None)
    args = parser.parse_args()

    repo = Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve()
    changed_files = read_changed_files(args.changed_files)
    global_trigger = is_global_trigger(changed_files)

    matrix_rows: list[dict[str, str]] = []
    results: list[DiscoveryResult] = []

    for candidate in discover_candidates(repo):
        if not candidate.rtl_flist.exists():
            rel_flist = repo_rel(candidate.rtl_flist, repo) or candidate.rtl_flist.as_posix()
            warn(f"Skipping {candidate.module}: missing {rel_flist}", rel_flist)
            results.append(
                DiscoveryResult(candidate, "skipped", [], 0, f"Missing {rel_flist}")
            )
            continue

        if flist_references_liteuvm(candidate.rtl_flist, repo):
            rel_flist = repo_rel(candidate.rtl_flist, repo) or candidate.rtl_flist.as_posix()
            warn(
                f"Skipping {candidate.module}: {rel_flist} references {LITEUVM_PATH}, "
                "and CI compile checks do not initialize that submodule.",
                rel_flist,
            )
            results.append(
                DiscoveryResult(candidate, "skipped", [], 0, "LiteUVM dependency")
            )
            continue

        dependencies = expand_flist(candidate.rtl_flist, repo)
        dependencies.update(implicit_dependencies(candidate, repo))
        hits = sorted(dependencies & changed_files)
        should_emit = args.all or global_trigger or bool(hits)

        if not should_emit:
            results.append(DiscoveryResult(candidate, "skipped", [], 0, "No changed flist dependency"))
            continue

        rows = make_relative_rows(expand_mode_rows(candidate, repo), repo)
        matrix_rows.extend(rows)
        note = "manual --all" if args.all else "global trigger" if global_trigger else "changed dependency"
        results.append(DiscoveryResult(candidate, "kept", hits, len(rows), note))

    matrix = {"include": matrix_rows}
    matrix_json = json.dumps(matrix, separators=(",", ":"))
    write_output("matrix", matrix_json)
    write_output("count", str(len(matrix_rows)))
    write_output("global_trigger", str(global_trigger).lower())

    append_summary(args.summary, results, global_trigger, args.all)
    print(f"Discovered {len(matrix_rows)} compile matrix row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
