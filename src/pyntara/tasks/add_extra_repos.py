"""Task add_extra_repos: enable extra Ubuntu archive components.

A fresh Kubuntu enables only the main component of the Ubuntu archive in
/etc/apt/sources.list.d/ubuntu.sources; universe, restricted and multiverse
are off. This task appends the configured components to the Components line
of every deb822 section whose URIs point to an Ubuntu archive host, and to
every matching legacy line in /etc/apt/sources.list. Third-party sources
(chrome, vscode, onedrive and friends) are never touched, because the host
filter only matches the official archive domains. The goal is reached when
every Ubuntu section already lists every configured component; the task
then skips. After a real change the apt index is refreshed once, unless
ctx.skip_apt_update is set (test or offline runs). A failure is reported
through TaskResult and never stops the run (task-model contract): the
runner continues with the remaining tasks and the summary shows the error.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from pyntara.context import Context
from pyntara.models import TaskResult
from pyntara.utils import run_command

# apt must never ask questions; all package operations run noninteractive.
APT_EXTRA_ENV = {"DEBIAN_FRONTEND": "noninteractive"}

# Module-level path constants are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide).
LEGACY_SOURCES_FILE = Path("/etc/apt/sources.list")
SOURCES_LIST_D = Path("/etc/apt/sources.list.d")

# Only the official Ubuntu archive domains are managed. A mirror such as
# mirror.yandex.ru is outside the scope and reported as not found, so the
# task never guesses about unknown sources.
UBUNTU_HOSTS = (
    "archive.ubuntu.com",
    "security.ubuntu.com",
    "ports.ubuntu.com",
    "old-releases.ubuntu.com",
)

# The task name from the catalog; the module file name matches it
# (task-model contract), so the prefix is always correct.
TASK_NAME = __name__.rsplit(".", 1)[-1]


def _log(message: str) -> None:
    """Print one progress line for this task, flushed to stdout.

    inst.sh tees stdout into the install log, so every decision and action
    of the task is visible in the terminal and in the log.
    """

    print(f"[{TASK_NAME}] {message}", flush=True)


@dataclass(frozen=True)
class _FileRewrite:
    """Outcome of analyzing and possibly rewriting one apt source file."""

    text: str
    changed: bool
    has_ubuntu: bool
    satisfied: bool
    problems: tuple[str, ...]


def _uri_is_ubuntu(uri: str) -> bool:
    """True when the URI points to an official Ubuntu archive host."""

    return any(host in uri for host in UBUNTU_HOSTS)


def _process_deb822(text: str, configured: tuple[str, ...]) -> _FileRewrite:
    """Rewrite Components lines of Ubuntu sections in a deb822 source file.

    Sections are separated by blank lines. A section is an Ubuntu archive
    section when its URIs field names an official Ubuntu host; a fallback
    scan of the whole section text catches URI values that span multiple
    continuation lines. Only the Components line of such a section is
    rewritten: missing configured components are appended in configured
    order, everything else in the file stays byte-identical.
    """

    lines = text.splitlines(keepends=True)
    has_ubuntu = False
    satisfied = True
    changed = False
    problems: list[str] = []
    index = 0
    while index < len(lines):
        section: list[int] = []
        while index < len(lines) and lines[index].strip():
            section.append(index)
            index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
        if not section:
            continue
        is_ubuntu = False
        components_line: int | None = None
        for line_index in section:
            stripped = lines[line_index].strip()
            lower = stripped.lower()
            if lower.startswith("uris:"):
                uris = stripped.split(":", 1)[1].split()
                if any(_uri_is_ubuntu(uri) for uri in uris):
                    is_ubuntu = True
            elif lower.startswith("components:"):
                components_line = line_index
        if not is_ubuntu:
            section_text = "".join(lines[i] for i in section)
            if any(host in section_text for host in UBUNTU_HOSTS):
                is_ubuntu = True
        if not is_ubuntu:
            continue
        has_ubuntu = True
        if components_line is None:
            problems.append(
                f"Ubuntu section without a Components line (starts at line "
                f"{section[0] + 1})"
            )
            satisfied = False
            continue
        existing = lines[components_line].strip().split(":", 1)[1].split()
        missing = [
            component for component in configured if component not in existing
        ]
        if missing:
            satisfied = False
            key = lines[components_line][: lines[components_line].find(":")]
            new_line = f"{key}: {' '.join(existing + missing)}"
            if lines[components_line].endswith("\n"):
                new_line = f"{new_line}\n"
            lines[components_line] = new_line
            changed = True
    return _FileRewrite("".join(lines), changed, has_ubuntu, satisfied, tuple(problems))


def _split_trailing_comment(line: str) -> tuple[str, str]:
    """Split a legacy line into body and trailing comment, without newline."""

    content = line.rstrip("\n")
    comment_at = content.find("#")
    if comment_at < 0:
        return content.rstrip(), ""
    return content[:comment_at].rstrip(), content[comment_at:]


def _process_legacy(text: str, configured: tuple[str, ...]) -> _FileRewrite:
    """Append missing components to legacy Ubuntu deb lines.

    A legacy line has the shape deb [options] URI suite component...
    Components are the tokens after the suite. The trailing comment, if
    any, stays at the end of the line.
    """

    lines = text.splitlines(keepends=True)
    has_ubuntu = False
    satisfied = True
    changed = False
    problems: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith(("deb ", "deb-src ")):
            continue
        if not any(host in line for host in UBUNTU_HOSTS):
            continue
        has_ubuntu = True
        body, comment = _split_trailing_comment(line)
        tokens = body.split()
        url_index = next(
            (
                i
                for i, token in enumerate(tokens)
                if token.startswith(("http://", "https://"))
            ),
            None,
        )
        if url_index is None or url_index + 2 >= len(tokens):
            problems.append(f"cannot parse Ubuntu source line: {stripped}")
            satisfied = False
            continue
        components = tokens[url_index + 2 :]
        missing = [
            component for component in configured if component not in components
        ]
        if missing:
            satisfied = False
            joined = " ".join(missing)
            rebuilt = f"{body} {joined}"
            if comment:
                rebuilt = f"{rebuilt} {comment}"
            lines[index] = rebuilt + ("\n" if line.endswith("\n") else "")
            changed = True
    return _FileRewrite("".join(lines), changed, has_ubuntu, satisfied, tuple(problems))


def _collect_source_files() -> list[Path]:
    """The apt source files apt itself reads, legacy file first.

    apt reads /etc/apt/sources.list and, in sources.list.d, only lowercase
    files ending in .list or .sources. Backup files (.bak) and other
    extensions are ignored by apt and by this task.
    """

    files: list[Path] = []
    if LEGACY_SOURCES_FILE.is_file():
        files.append(LEGACY_SOURCES_FILE)
    if SOURCES_LIST_D.is_dir():
        files.extend(
            sorted(
                path
                for path in SOURCES_LIST_D.iterdir()
                if path.suffix in (".list", ".sources") and path.name.islower()
            )
        )
    return files


def _process_file(path: Path, configured: tuple[str, ...]) -> _FileRewrite:
    """Analyze and rewrite one source file in memory, by its format."""

    if path.suffix == ".sources":
        return _process_deb822(path.read_text(encoding="utf-8"), configured)
    return _process_legacy(path.read_text(encoding="utf-8"), configured)


def task(ctx: Context) -> TaskResult:
    """Ensure every Ubuntu archive section lists the configured components.

    The task skips when the goal is already reached. Otherwise it rewrites
    the Components lines, refreshes the apt index once (unless
    ctx.skip_apt_update), verifies the result by re-reading the files and
    reports the outcome. Every failure is returned as an error TaskResult:
    the runner continues with the remaining tasks and never stops here.
    """

    configured = ctx.config.add_extra_repos.components
    _log(f"configured components: {' '.join(configured)}")
    files = _collect_source_files()
    if not files:
        return TaskResult(success=False, error="no apt source files found")
    _log(f"apt source files found: {len(files)}")
    states: list[tuple[Path, _FileRewrite]] = []
    problems: list[str] = []
    has_ubuntu = False
    for path in files:
        try:
            state = _process_file(path, configured)
        except OSError as exc:
            problems.append(f"cannot read {path}: {exc}")
            continue
        states.append((path, state))
        has_ubuntu = has_ubuntu or state.has_ubuntu
        if state.has_ubuntu:
            status = "satisfied" if state.satisfied else "components missing"
            _log(f"reading {path}: ubuntu section found, {status}")
        else:
            _log(f"reading {path}: no ubuntu section")
    all_problems = problems + [
        problem for _, state in states for problem in state.problems
    ]
    if all_problems:
        return TaskResult(success=False, error="; ".join(all_problems))
    if not has_ubuntu:
        return TaskResult(
            success=False,
            error=(
                "no Ubuntu archive section found in the apt sources; "
                "add_extra_repos only manages Ubuntu archive components"
            ),
        )
    if all(state.satisfied for _, state in states):
        _log("target state already reached, skipping")
        return TaskResult(success=True, changed=False, message="already satisfied")
    changed_paths: list[Path] = []
    for path, state in states:
        if not state.changed:
            continue
        _log(f"writing {path}: appending missing components")
        try:
            path.write_text(state.text, encoding="utf-8")
        except OSError as exc:
            problems.append(f"cannot write {path}: {exc}")
            continue
        changed_paths.append(path)
    if problems:
        return TaskResult(
            success=False, changed=bool(changed_paths), error="; ".join(problems)
        )
    warnings: list[str] = []
    if changed_paths:
        if ctx.skip_apt_update:
            _log("apt index refresh skipped")
        else:
            _log("refreshing apt index: apt-get update")
            try:
                run_command(
                    ["apt-get", "update"],
                    extra_env=APT_EXTRA_ENV,
                    timeout=ctx.config.engine.command_timeout_seconds,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                warnings.append(f"apt index refresh: {exc}")
            else:
                _log("apt index refreshed")
    _log("verifying rewritten sources")
    verified: list[tuple[Path, _FileRewrite]] = []
    for path in files:
        try:
            verified.append((path, _process_file(path, configured)))
        except OSError as exc:
            problems.append(f"cannot read {path} for verification: {exc}")
    if problems:
        return TaskResult(
            success=False, changed=bool(changed_paths), error="; ".join(problems)
        )
    unsatisfied = [str(path) for path, state in verified if not state.satisfied]
    if unsatisfied:
        return TaskResult(
            success=False,
            changed=bool(changed_paths),
            error=f"components still missing after rewrite: {', '.join(unsatisfied)}",
        )
    _log(f"verification passed: {len(verified)} files satisfied")
    message = (
        f"components ensured in Ubuntu archive sections: {', '.join(configured)}"
    )
    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(success=True, changed=bool(changed_paths), message=message)
