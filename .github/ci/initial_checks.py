#!/usr/bin/env python3
"""Repository structure and RTL policy checks for PR CI."""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCAN_GLOBS = (
    "*.v",
    "*.sv",
    "*.flist*",
)

RTL_SOURCE_SUFFIXES = {".v", ".sv"}
RTL_FILELIST_SUFFIXES = {".f", ".flist"}
COMMON_RTL_PREFIX = "design/lib/"
SPICE_LFS_PROBES = (
    "design/rtl/__ci_probe__/pd_handoff/spice_files/probe.sp",
    "design/rtl/__ci_probe__/pd_handoff/spice_files/netlists/probe.sp",
)

SKIP_DIRS = {
    ".git",
    ".continue",
    ".github",
    "package",
    ".run_parallel_control",
    "__pycache__",
    "sim",
    "artifacts",
    "regression",
}

PATH_TOKEN_RE = re.compile(
    r"""
    (?P<path>
      (?:\$\{PROJECT_ROOT\}|\$PROJECT_ROOT|\$PROJ_ROOT)(?:/[A-Za-z0-9_.${}-]+)+
      |
      (?:\./|\.\./|/\./|/\.\./)(?:[A-Za-z0-9_.${}-]+/)*[A-Za-z0-9_.${}-]+
      |
      ~/(?:[A-Za-z0-9_.${}-]+/)+[A-Za-z0-9_.${}-]*
      |
      (?<![A-Za-z0-9_.${}/:-])/(?:[A-Za-z0-9_.${}-]+/)+[A-Za-z0-9_.${}-]*
      |
      [A-Za-z]:\\[^\s"'`<>]+
    )
    """,
    re.VERBOSE,
)

MODULE_TOKEN_RE = re.compile(
    r"\b(?:(module)\s+(?:automatic\s+)?([A-Za-z_][A-Za-z0-9_$]*)|(endmodule))\b"
)
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT_RE = re.compile(r"//.*?$", re.MULTILINE)
TSMC_PATH_RE = re.compile(r"(?:tsmc|tsmchome|sclib|tcbn|65msrf)", re.IGNORECASE)

ALLOWED_REPO_PREFIXES = ("${PROJECT_ROOT}", "$PROJECT_ROOT", "$PROJ_ROOT")
SANITY_MODE_RE = re.compile(r"^SANITY(?:$|_)", re.IGNORECASE)
LITEUVM_PATH = "package/rtl-liteuvm"
VCS_WAIVER_VALIDATOR = Path(
    "package/rtl-commons/simulation/bin/generate_vcs_message_config.py"
)
REPOSITORY_POLICY_VALIDATOR = Path(
    "package/rtl-commons/policy/bin/check_repository_policy.py"
)


@dataclass(frozen=True)
class Finding:
    check: str
    path: str
    message: str
    severity: str = "error"

    @property
    def is_error(self) -> bool:
        return self.severity == "error"


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def escape_annotation(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def annotation_location(path: str) -> str:
    if not path:
        return ""

    match = re.match(r"^(?P<file>.+):(?P<line>[1-9][0-9]*)$", path)
    if match:
        return f" file={match.group('file')},line={match.group('line')}"

    return f" file={path}"


def emit_annotation(finding: Finding) -> None:
    location = annotation_location(finding.path)
    level = "error" if finding.is_error else "warning"
    print(
        f"::{level}{location},title={escape_annotation(finding.check)}::"
        f"{escape_annotation(finding.message)}"
    )


def append_summary(summary: Path | None, findings: list[Finding]) -> None:
    if summary is None:
        return

    with summary.open("a", encoding="utf-8") as handle:
        handle.write("## Initial Checks\n\n")
        if not findings:
            handle.write("No initial-check findings found.\n\n")
            return

        handle.write("| Severity | Check | Path | Message |\n")
        handle.write("|---|---|---|---|\n")
        for finding in findings:
            escaped_message = finding.message.replace("|", "\\|")
            handle.write(
                f"| {finding.severity} | {finding.check} | `{finding.path}` | "
                f"{escaped_message} |\n"
            )
        handle.write("\n")


def module_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir())


def validate_json_file(path: Path, findings: list[Finding], severity: str = "error") -> dict[str, Any] | None:
    if not path.exists():
        expected = rel(path)
        findings.append(
            Finding(
                "missing-sweep-json",
                expected,
                f"Missing sweep.json: expected `{expected}`",
                severity,
            )
        )
        return None

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        findings.append(
            Finding(
                "invalid-sweep-json",
                rel(path),
                f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
                severity,
            )
        )
        return None

    if not isinstance(payload, dict):
        findings.append(
            Finding(
                "invalid-sweep-json",
                rel(path),
                "Invalid sweep.json: expected a JSON object at the top level.",
                severity,
            )
        )
        return None

    return payload


def validate_sweep_modes(path: Path, payload: dict[str, Any], findings: list[Finding], severity: str = "error") -> None:
    path_rel = rel(path)
    scope = "this sweep.json"
    path_parts = path.parts
    if "top_level" in path_parts:
        scope = "top-level sweep.json"
    elif "rtl_test" in path_parts:
        try:
            test_dir = path.parents[1].name
            scope = f"RTL module `{test_dir}` sweep.json"
        except IndexError:
            scope = f"`{path_rel}`"
    elif "model_test" in path_parts:
        try:
            test_dir = path.parents[1].name
            scope = f"model `{test_dir}` sweep.json"
        except IndexError:
            scope = f"`{path_rel}`"
    else:
        scope = f"`{path_rel}`"

    modes = payload.get("modes")
    if not isinstance(modes, dict):
        findings.append(
            Finding(
                "missing-required-modes",
                path_rel,
                f"{scope} has no valid `modes` object; expected at least one SANITY mode.",
                severity,
            )
        )
        return

    mode_names = [str(name) for name in modes.keys()]
    has_sanity = any(SANITY_MODE_RE.match(name) for name in mode_names)
    if has_sanity:
        return

    findings.append(
        Finding(
            "missing-required-modes",
            path_rel,
            f"{scope} should define at least one SANITY mode.",
            severity,
        )
    )


def validate_required_file(path: Path, check: str, label: str, findings: list[Finding], severity: str = "error") -> None:
    if path.exists():
        return
    expected = rel(path)
    findings.append(
        Finding(
            check,
            expected,
            f"Missing {label}: expected `{expected}`",
            severity,
        )
    )


def validate_no_liteuvm_reference(path: Path, findings: list[Finding], severity: str = "error") -> None:
    if not path.exists():
        return

    text = path.read_text(encoding="utf-8", errors="ignore").replace("\\", "/")
    if LITEUVM_PATH not in text:
        return

    findings.append(
        Finding(
            "liteuvm-dependency",
            rel(path),
            f"`{rel(path)}` references `{LITEUVM_PATH}`; CI compile checks no longer initialize that submodule.",
            severity,
        )
    )


def run_structure_checks(repo: Path) -> list[Finding]:
    findings: list[Finding] = []

    design_rtl = repo / "design" / "rtl"
    rtl_tests = repo / "test" / "rtl_test"
    for module in module_dirs(design_rtl):
        test_dir = rtl_tests / module.name
        if not test_dir.exists():
            findings.append(
                Finding(
                    "missing-rtl-test",
                    rel(test_dir),
                    f"No test directory for design RTL module `{module.name}`",
                )
            )
            continue

        script_dir = test_dir / "script"
        sweep_payload = validate_json_file(script_dir / "sweep.json", findings)
        if sweep_payload is not None:
            validate_sweep_modes(script_dir / "sweep.json", sweep_payload, findings)
        validate_no_liteuvm_reference(script_dir / "rtl.flist", findings)
        lint_tcl = module / "code" / "lint.tcl"
        if not lint_tcl.exists():
            expected = rel(lint_tcl)
            findings.append(
                Finding(
                    "missing-lint-tcl",
                    expected,
                    f"Missing lint.tcl: expected `{expected}`",
                )
            )

    models_root = repo / "design" / "models"
    model_tests = repo / "test" / "model_test"
    if models_root.exists():
        for entry in sorted(models_root.iterdir()):
            if entry.is_dir():
                findings.append(
                    Finding(
                        "nested-model-directory",
                        rel(entry),
                        f"Model components must be files directly under design/models; nested directory `{rel(entry)}` is not allowed",
                    )
                )

    if model_tests.exists():
        for test_dir in sorted(model_tests.iterdir()):
            if test_dir.name == ".gitkeep":
                continue
            if not test_dir.is_dir():
                findings.append(
                    Finding(
                        "loose-model-test-entry",
                        rel(test_dir),
                        f"Model tests must be directories directly under test/model_test; `{rel(test_dir)}` is not allowed",
                    )
                )
                continue

            script_dir = test_dir / "script"
            sweep_path = script_dir / "sweep.json"
            sweep_payload = validate_json_file(sweep_path, findings)
            if sweep_payload is not None:
                validate_sweep_modes(sweep_path, sweep_payload, findings)
            validate_required_file(
                script_dir / "rtl.flist",
                "missing-model-test-rtl-flist",
                "rtl.flist",
                findings,
            )
            validate_no_liteuvm_reference(script_dir / "rtl.flist", findings)

    top_level_script = repo / "test" / "top_level" / "script"
    validate_required_file(
        top_level_script / "Makefile",
        "missing-top-level-makefile",
        "Makefile",
        findings,
        "warning",
    )
    validate_required_file(
        top_level_script / "rtl.flist",
        "missing-top-level-rtl-flist",
        "rtl.flist",
        findings,
        "warning",
    )
    validate_no_liteuvm_reference(top_level_script / "rtl.flist", findings, "warning")
    top_level_sweep = top_level_script / "sweep.json"
    sweep_payload = validate_json_file(top_level_sweep, findings, "warning")
    if sweep_payload is not None:
        validate_sweep_modes(top_level_sweep, sweep_payload, findings, "warning")

    return findings


def load_allowlist(path: Path) -> list[str]:
    if not path.exists():
        return []
    prefixes: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                prefixes.append(stripped)
    return prefixes


def should_scan(path: Path) -> bool:
    parts = set(path.parts)
    if parts & SKIP_DIRS:
        return False
    return any(path.match(f"**/{pattern}") for pattern in SCAN_GLOBS)


def is_allowed_path(token: str, external_prefixes: list[str]) -> bool:
    if token.startswith(ALLOWED_REPO_PREFIXES):
        return True
    if token.startswith("//"):
        return True
    return any(token.startswith(prefix) for prefix in external_prefixes)


def is_relative_path_token(token: str) -> bool:
    return token.startswith(("./", "../", "/./", "/../"))


def is_verilog_concatenation_path_fragment(path: Path, line: str, match: re.Match[str]) -> bool:
    """Return whether a slash-prefixed string is only a Verilog path suffix.

    For example, in ``{test_data_path, "/reports/result.txt"}`` the quoted
    slash-prefixed value is a fragment of a runtime concatenation, not a
    complete absolute path.  Keep scanning complete literals and all filelist
    or Tcl paths normally.
    """
    if path.suffix not in RTL_SOURCE_SUFFIXES:
        return False

    token = match.group("path")
    if not token.startswith("/"):
        return False

    start = match.start("path")
    end = match.end("path")
    if start == 0 or end >= len(line):
        return False
    if line[start - 1] not in {"'", '"'} or line[end] not in {"'", '"'}:
        return False

    open_brace = line.rfind("{", 0, start)
    close_brace = line.rfind("}", 0, start)
    if open_brace <= close_brace:
        return False

    # A preceding concatenation element makes this quoted value a suffix. Do
    # not suppress a complete literal that merely happens to be in braces.
    return "," in line[open_brace + 1 : start] and "}" in line[end:]


def scan_external_paths(repo: Path, allowlist_path: Path) -> list[Finding]:
    findings: list[Finding] = []
    external_prefixes = load_allowlist(allowlist_path)

    for path in sorted(repo.rglob("*")):
        if not path.is_file() or not should_scan(path.relative_to(repo)):
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            findings.append(Finding("scan-error", rel(path.relative_to(repo)), str(exc)))
            continue

        for line_no, line in enumerate(text.splitlines(), start=1):
            line = strip_code_comments(line)
            for match in PATH_TOKEN_RE.finditer(line):
                token = match.group("path").rstrip("),];")
                if is_verilog_concatenation_path_fragment(path, line, match):
                    continue
                if is_allowed_path(token, external_prefixes):
                    continue
                if is_relative_path_token(token):
                    findings.append(
                        Finding(
                            "relative-path",
                            f"{rel(path.relative_to(repo))}:{line_no}",
                            f"Relative path `{token}` is used in `{rel(path.relative_to(repo))}:{line_no}`; prefer `${{PROJECT_ROOT}}/...` or an approved absolute external path.",
                            "warning",
                        )
                    )
                    continue
                findings.append(
                    Finding(
                        "external-path",
                        f"{rel(path.relative_to(repo))}:{line_no}",
                        f"Path `{token}` is not allowed in `{rel(path.relative_to(repo))}:{line_no}`; use paths inside the repo or approved external paths",
                    )
                )

    return findings


def strip_code_comments(line: str) -> str:
    slash_index = line.find("//")
    hash_index = line.find("#")
    comment_indexes = [
        index
        for index in (slash_index, hash_index)
        if index != -1 and not line[:index].rstrip().endswith(":")
    ]
    if not comment_indexes:
        return line
    return line[: min(comment_indexes)]


def strip_verilog_comments(text: str) -> str:
    without_blocks = BLOCK_COMMENT_RE.sub(
        lambda match: "\n" * match.group(0).count("\n"),
        text,
    )
    return LINE_COMMENT_RE.sub("", without_blocks)


def first_outermost_module_decl(path: Path) -> tuple[str, int] | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    stripped = strip_verilog_comments(text)
    depth = 0
    for match in MODULE_TOKEN_RE.finditer(stripped):
        if match.group(3):
            depth = max(depth - 1, 0)
            continue

        module_name = match.group(2)
        if depth == 0:
            line_no = stripped[: match.start()].count("\n") + 1
            return module_name, line_no
        depth += 1

    return None


def check_file_module_names(repo: Path) -> list[Finding]:
    findings: list[Finding] = []
    source_roots = (repo / "design", repo / "test")

    for source_root in source_roots:
        if not source_root.exists():
            continue

        for path in sorted(source_root.rglob("*")):
            if not path.is_file() or path.suffix not in RTL_SOURCE_SUFFIXES:
                continue
            parts = path.relative_to(repo).parts
            # Vendor IP may declare multiple modules or use delivery filenames.
            # Preserve the existing pd_handoff exemption for delivered netlists.
            if "pd_handoff" in parts or parts[:3] == ("design", "lib", "IP"):
                continue

            declaration = first_outermost_module_decl(path)
            if declaration is None:
                continue

            module_name, line_no = declaration
            if path.stem == module_name:
                continue

            expected_name = f"{module_name}{path.suffix}"
            findings.append(
                Finding(
                    "file-module-name",
                    f"{path.relative_to(repo).as_posix()}:{line_no}",
                    f"File `{path.relative_to(repo).as_posix()}` must match first outermost module name `{module_name}`; rename the file to `{expected_name}` or rename the module.",
                    "error",
                )
            )

    return findings


def check_duplicate_verilog_basenames(repo: Path) -> list[Finding]:
    """Reject duplicate Verilog/SystemVerilog filenames anywhere in design or test."""
    paths_by_name: dict[str, list[Path]] = collections.defaultdict(list)

    for source_root in (repo / "design", repo / "test"):
        if not source_root.exists():
            continue

        for path in sorted(source_root.rglob("*")):
            if not path.is_file() or path.suffix not in RTL_SOURCE_SUFFIXES:
                continue
            if set(path.relative_to(repo).parts) & SKIP_DIRS:
                continue
            paths_by_name[path.name.casefold()].append(path)

    findings: list[Finding] = []
    for paths in sorted(paths_by_name.values(), key=lambda items: items[0].name.casefold()):
        if len(paths) < 2:
            continue

        locations = ", ".join(f"`{path.relative_to(repo).as_posix()}`" for path in paths)
        findings.append(
            Finding(
                "duplicate-verilog-basename",
                paths[0].name,
                f"Duplicate Verilog/SystemVerilog filename `{paths[0].name}` found at {locations}; filenames must be globally unique under `design/` and `test/`.",
                "error",
            )
        )

    return findings


def normalize_path_token(token: str) -> str:
    return token.strip().strip("\"'").rstrip("),];\\").replace("\\", "/")


def resolve_project_path(repo: Path, base: Path, token: str) -> Path | None:
    normalized = normalize_path_token(token)
    for prefix in ALLOWED_REPO_PREFIXES:
        if normalized.startswith(prefix + "/"):
            return repo / normalized[len(prefix) + 1 :]

    if normalized.startswith("/"):
        candidate = Path(normalized)
    else:
        candidate = base / normalized

    try:
        candidate.resolve().relative_to(repo.resolve())
    except ValueError:
        return None
    return candidate


def split_filelist_line(line: str) -> list[str]:
    line = strip_code_comments(line).strip()
    if not line:
        return []
    return line.split()


def parse_design_filelist(
    repo: Path,
    filelist: Path,
) -> set[Path]:
    sources: set[Path] = set()
    try:
        lines = filelist.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return sources

    for line in lines:
        tokens = split_filelist_line(line)
        index = 0
        while index < len(tokens):
            token = tokens[index]

            if token == "-f" and index + 1 < len(tokens):
                index += 2
                continue

            if token.startswith("-f") and len(token) > 2:
                index += 1
                continue

            if token.startswith("+incdir+") or token.startswith("-"):
                index += 1
                continue

            candidate = resolve_project_path(repo, filelist.parent, token)
            if candidate is not None and candidate.suffix in RTL_SOURCE_SUFFIXES:
                sources.add(candidate.resolve())

            index += 1

    return sources


def discover_design_filelists(repo: Path) -> list[Path]:
    design_rtl = repo / "design" / "rtl"
    if not design_rtl.exists():
        return []
    return sorted(
        path
        for path in design_rtl.rglob("*")
        if path.is_file() and path.suffix in RTL_FILELIST_SUFFIXES
    )


def check_design_filelist_location(repo: Path) -> list[Finding]:
    findings: list[Finding] = []
    design_rtl = repo / "design" / "rtl"
    if not design_rtl.exists():
        return findings

    for filelist in discover_design_filelists(repo):
        rel_path = filelist.relative_to(repo).as_posix()
        parts = filelist.relative_to(design_rtl).parts
        if len(parts) >= 4 and parts[1] == "pd_handoff" and parts[2] == "filelist":
            expected = f"design/rtl/{parts[0]}/pd_handoff/{filelist.name}"
            findings.append(
                Finding(
                    "design-filelist-location",
                    rel_path,
                    f"Design RTL filelist `{rel_path}` must live directly under `design/rtl/{parts[0]}/pd_handoff/`; move it to `{expected}`.",
                    "error",
                )
            )

    return findings


def check_common_design_filelist_reuse(repo: Path) -> list[Finding]:
    """Require design/lib only for sources directly listed by multiple modules.

    A module may include another module's rtl.flist; the included module owns
    the individual RTL source references in that case.
    """
    file_to_lists: dict[Path, set[str]] = collections.defaultdict(set)

    for filelist in discover_design_filelists(repo):
        filelist_rel = filelist.relative_to(repo).as_posix()
        for source in parse_design_filelist(repo, filelist):
            file_to_lists[source].add(filelist_rel)

    findings: list[Finding] = []
    for source, filelists in sorted(file_to_lists.items(), key=lambda item: item[0].as_posix()):
        if len(filelists) < 2:
            continue

        source_rel = source.relative_to(repo).as_posix()
        if source_rel.startswith(COMMON_RTL_PREFIX):
            continue

        locations = ", ".join(f"`{name}`" for name in sorted(filelists))
        findings.append(
            Finding(
                "shared-rtl-common-file",
                source_rel,
                f"RTL source `{source_rel}` is used by multiple design RTL filelists ({locations}); shared design RTL sources must live under `{COMMON_RTL_PREFIX}`.",
                "error",
            )
        )

    return findings


def check_synthesis_inputs(repo: Path) -> list[Finding]:
    validator = repo / "package/rtl-commons/synthesis/bin/synthesis_policy.py"
    if not validator.is_file():
        return [Finding("synthesis-policy", "design", "Shared synthesis validator is missing; update submodules.")]
    command = [sys.executable, str(validator), "--project-root", str(repo)]
    changed_files = repo / ".git/ci-changed-files.txt"
    if changed_files.is_file():
        command.extend(["--changed-files", str(changed_files)])
    result = subprocess.run(
        command,
        cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        errors = json.loads(result.stdout)
        if not isinstance(errors, list):
            raise ValueError("Expected a list of synthesis findings")
        findings = [Finding("synthesis-policy", item["path"], item["message"]) for item in errors]
    except (ValueError, TypeError, KeyError):
        return [Finding("synthesis-policy", "design", "Synthesis validator failed: " + (result.stderr or result.stdout).strip())]
    if result.returncode and not findings:
        findings.append(Finding("synthesis-policy", "design", "Synthesis validator exited unsuccessfully."))
    return findings


def load_exact_paths(path: Path) -> list[str]:
    if not path.exists():
        return []

    paths: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                paths.append(normalize_path_token(stripped))
    return paths


def tsmc_scan_file(path: Path) -> bool:
    parts = set(path.parts)
    if parts & SKIP_DIRS:
        return False
    return path.suffix in {".v", ".sv", ".f", ".flist", ".tcl", ".sdc", ".sdf", ".lib"}


def check_tsmc_paths(repo: Path, allowlist_path: Path, mapping_path: Path) -> list[Finding]:
    findings: list[Finding] = []
    external_prefixes = load_allowlist(allowlist_path)
    mapped_paths = load_exact_paths(mapping_path)
    mapped_path_set = set(mapped_paths)
    mapped_by_name: dict[str, list[str]] = collections.defaultdict(list)
    warned_names: set[tuple[str, str]] = set()

    if not mapping_path.exists():
        findings.append(
            Finding(
                "missing-tsmc-map",
                mapping_path.relative_to(repo).as_posix(),
                f"Missing TSMC path map `{mapping_path.relative_to(repo).as_posix()}`.",
                "error",
            )
        )

    for mapped_path in mapped_paths:
        mapped_by_name[Path(mapped_path).name].append(mapped_path)

    for path in sorted(repo.rglob("*")):
        if not path.is_file() or not tsmc_scan_file(path.relative_to(repo)):
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            findings.append(Finding("scan-error", path.relative_to(repo).as_posix(), str(exc)))
            continue

        for line_no, line in enumerate(text.splitlines(), start=1):
            line = strip_code_comments(line)
            for match in PATH_TOKEN_RE.finditer(line):
                token = normalize_path_token(match.group("path"))
                if not TSMC_PATH_RE.search(token):
                    continue

                token_name = Path(token).name
                canonical_paths = mapped_by_name.get(token_name, [])
                if canonical_paths and token not in canonical_paths:
                    warn_key = (path.relative_to(repo).as_posix(), token)
                    if warn_key not in warned_names:
                        findings.append(
                            Finding(
                                "tsmc-path-mismatch",
                                f"{path.relative_to(repo).as_posix()}:{line_no}",
                                f"TSMC file `{token_name}` is referenced from `{token}`; expected path: {', '.join(f'`{candidate}`' for candidate in canonical_paths)}.",
                            )
                        )
                        warned_names.add(warn_key)

                if token in mapped_path_set or is_allowed_path(token, external_prefixes):
                    continue

                findings.append(
                    Finding(
                        "tsmc-path-not-allowed",
                        f"{path.relative_to(repo).as_posix()}:{line_no}",
                        f"TSMC-related path `{token}` is not allowed; add the approved prefix to `{allowlist_path.relative_to(repo).as_posix()}` or add the exact file path to `{mapping_path.relative_to(repo).as_posix()}`.",
                        "error",
                    )
                )

    return findings


def is_module_pd_handoff_spice(path: Path) -> bool:
    parts = path.parts
    return (
        len(parts) >= 6
        and parts[0] == "design"
        and parts[1] == "rtl"
        and parts[3] == "pd_handoff"
        and parts[4] == "spice_files"
        and path.suffix.lower() == ".sp"
    )


def git_lfs_attrs(repo: Path, rel_path: str) -> dict[str, str]:
    result = subprocess.run(
        ["git", "check-attr", "filter", "diff", "merge", "text", "--", rel_path],
        cwd=repo,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        return {}

    attrs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        try:
            _, name, value = line.split(": ", 2)
        except ValueError:
            continue
        attrs[name] = value
    return attrs


def has_lfs_spice_attrs(repo: Path, rel_path: str) -> bool:
    attrs = git_lfs_attrs(repo, rel_path)
    return (
        attrs.get("filter") == "lfs"
        and attrs.get("diff") == "lfs"
        and attrs.get("merge") == "lfs"
        and attrs.get("text") == "unset"
    )


def check_spice_file_handling(repo: Path) -> list[Finding]:
    findings: list[Finding] = []

    for probe in SPICE_LFS_PROBES:
        if has_lfs_spice_attrs(repo, probe):
            continue

        findings.append(
            Finding(
                "spice-lfs-configuration",
                ".gitattributes",
                f"Git LFS must track large SPICE files under module pd_handoff/spice_files folders; expected `{probe}` to resolve to `filter=lfs diff=lfs merge=lfs -text`.",
                "error",
            )
        )

    for path in sorted(repo.rglob("*")):
        if not path.is_file() or path.suffix.lower() != ".sp":
            continue

        rel_path = path.relative_to(repo)
        rel_posix = rel_path.as_posix()
        if not is_module_pd_handoff_spice(rel_path):
            findings.append(
                Finding(
                    "spice-file-location",
                    rel_posix,
                    f"SPICE file `{rel_posix}` must live under `design/rtl/<module>/pd_handoff/spice_files/` so large SPICE handoff files can be stored with Git LFS.",
                    "error",
                )
            )
            continue

        if has_lfs_spice_attrs(repo, rel_posix):
            continue

        findings.append(
            Finding(
                "spice-lfs-attribute",
                rel_posix,
                f"SPICE file `{rel_posix}` is under pd_handoff/spice_files but is not covered by Git LFS attributes; add a matching `.gitattributes` rule.",
                "error",
            )
        )

    return findings


def check_vcs_error_waivers(repo: Path) -> list[Finding]:
    manifests = sorted(
        path
        for root in (repo / "design" / "models",)
        if root.is_dir()
        for path in root.rglob("vcs_error_waivers.json")
    )
    if not manifests:
        return []

    validator = repo / VCS_WAIVER_VALIDATOR
    if not validator.is_file():
        return [
            Finding(
                "vcs-error-waiver-structure",
                VCS_WAIVER_VALIDATOR.as_posix(),
                "Cannot validate VCS error waivers because the RTL Commons validator is missing.",
            )
        ]

    result = subprocess.run(
        [
            sys.executable,
            str(validator),
            "validate",
            "--project-root",
            str(repo),
        ],
        cwd=repo,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode == 0:
        return []

    details = [
        line.removeprefix("error: ").strip()
        for line in (result.stderr or result.stdout).splitlines()
        if line.strip()
    ]
    message = details[0] if details else "VCS error waiver validation failed."
    manifest_path = next(
        (rel(path) for path in manifests if rel(path) in message),
        rel(manifests[0]) if len(manifests) == 1 else "design",
    )
    return [Finding("vcs-error-waiver-structure", manifest_path, message)]


def check_repository_policy(repo: Path) -> list[Finding]:
    validator = repo / REPOSITORY_POLICY_VALIDATOR
    if not validator.is_file():
        return [
            Finding(
                "repository-policy",
                REPOSITORY_POLICY_VALIDATOR.as_posix(),
                "Cannot enforce repository policy because the RTL Commons validator is missing.",
            )
        ]
    command = [
        sys.executable,
        str(validator),
        "--project-root",
        str(repo),
        "--format",
        "json",
    ]
    changed_files = repo / ".git" / "ci-changed-files.txt"
    if changed_files.is_file():
        command.extend(["--changed-files", str(changed_files)])
    result = subprocess.run(
        command,
        cwd=repo,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        details = (result.stderr or result.stdout).strip()
        return [Finding("repository-policy", rel(validator), details or "Policy validator failed.")]
    return [
        Finding(
            item["check"],
            f"{item['path']}:{item['line']}",
            item["message"],
            item.get("severity", "error"),
        )
        for item in payload
    ]


def run_policy_checks(repo: Path, allowlist_path: Path, mapping_path: Path) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(check_file_module_names(repo))
    findings.extend(check_duplicate_verilog_basenames(repo))
    findings.extend(check_design_filelist_location(repo))
    findings.extend(check_common_design_filelist_reuse(repo))
    findings.extend(check_synthesis_inputs(repo))
    findings.extend(check_tsmc_paths(repo, allowlist_path, mapping_path))
    findings.extend(check_spice_file_handling(repo))
    findings.extend(check_vcs_error_waivers(repo))
    findings.extend(check_repository_policy(repo))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on findings")
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=Path(".github/ci/external_paths.txt"),
    )
    parser.add_argument(
        "--tsmc-map",
        type=Path,
        default=Path(".github/ci/tsmc_file_paths.txt"),
    )
    args = parser.parse_args()

    repo = Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve()
    findings = run_structure_checks(repo)
    findings.extend(scan_external_paths(repo, repo / args.allowlist))
    findings.extend(run_policy_checks(repo, repo / args.allowlist, repo / args.tsmc_map))

    for finding in findings:
        emit_annotation(finding)
    append_summary(args.summary, findings)

    if any(finding.is_error for finding in findings):
        return 1
    if findings and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
