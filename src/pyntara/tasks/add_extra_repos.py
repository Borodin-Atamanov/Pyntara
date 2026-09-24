"""Task add_extra_repos: enable extra Ubuntu archive components.

A fresh Kubuntu enables only the main component of the Ubuntu archive in
/etc/apt/sources.list.d/ubuntu.sources; universe, restricted and multiverse
are off. This task appends the configured components to the Components line
of every deb822 section whose URIs point to an Ubuntu archive host, and to
every matching legacy line in /etc/apt/sources.list. Third-party sources
(chrome, vscode, onedrive and friends) are never touched, because the host
filter only matches the official archive domains. The goal is reached when
every Ubuntu section already lists every configured component; the task
then skips. Independent of the components work the task also keeps an apt
drop-in that stops apt and unattended-upgrades from deleting downloaded
.deb files after a successful install, writing it while the run asked to keep
the downloads (ctx.delete_packages_after_install is false) and removing it
while the run deletes them.
After a real change the apt index is refreshed once, unless
ctx.skip_apt_update is set (test or offline runs). A failure is reported
through TaskResult and never stops the run (task-model contract): the
runner continues with the remaining tasks and the summary shows the error.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import refresh_apt_index
from pyntara.values import add_extra_repos as values
from pyntara.values import engine as engine_values
from pyntara.values import missing_value_names


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

    return any(host in uri for host in values.UBUNTU_HOSTS)


def _process_deb822(text: str) -> _FileRewrite:
    """Rewrite Components lines of Ubuntu sections in a deb822 source file.

    Sections are separated by blank lines. A section is an Ubuntu archive
    section when its URIs field names an official Ubuntu host; a fallback
    scan of the whole section text catches URI values that span multiple
    continuation lines. Only the Components line of such a section is
    rewritten: missing configured components are appended in configured
    order, everything else in the file stays byte-identical. The names of
    the two fields come from the values module and are compared without
    case.
    """

    uris_key = values.URIS_FIELD_NAME.lower()
    components_key = values.COMPONENTS_FIELD_NAME.lower()

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
            if lower.startswith(uris_key):
                uris = stripped[len(uris_key) :].split()
                if any(_uri_is_ubuntu(uri) for uri in uris):
                    is_ubuntu = True
            elif lower.startswith(components_key):
                components_line = line_index
        if not is_ubuntu:
            section_text = "".join(lines[i] for i in section)
            if any(host in section_text for host in values.UBUNTU_HOSTS):
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
        line = lines[components_line]
        key_start = line.lower().find(components_key)
        key_text = line[: key_start + len(components_key)]
        existing = line[key_start + len(components_key) :].split()
        missing = [
            component for component in values.COMPONENTS if component not in existing
        ]
        if missing:
            satisfied = False
            newline = "\n" if line.endswith("\n") else ""
            new_line = f"{key_text} {' '.join(existing + missing)}{newline}"
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


def _process_legacy(text: str) -> _FileRewrite:
    """Append missing components to legacy Ubuntu deb lines.

    A legacy line has the shape deb [options] URI suite component...
    Components are the tokens after the suite. The trailing comment, if
    any, stays at the end of the line. The keywords that open such a line
    and the schemes that mark its URI token come from the values module, so
    the line format of another distribution is answered in one place.
    """

    lines = text.splitlines(keepends=True)
    has_ubuntu = False
    satisfied = True
    changed = False
    problems: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith(values.LEGACY_SOURCE_TYPE_KEYWORDS):
            continue
        if not any(host in line for host in values.UBUNTU_HOSTS):
            continue
        has_ubuntu = True
        body, comment = _split_trailing_comment(line)
        tokens = body.split()
        url_index = next(
            (
                i
                for i, token in enumerate(tokens)
                if token.startswith(values.SOURCE_URL_SCHEMES)
            ),
            None,
        )
        if url_index is None or url_index + 2 >= len(tokens):
            problems.append(f"cannot parse Ubuntu source line: {stripped}")
            satisfied = False
            continue
        components = tokens[url_index + 2 :]
        missing = [
            component for component in values.COMPONENTS if component not in components
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

    apt reads the legacy sources file and, in the sources directory, only
    lowercase files carrying the suffix of either format. Backup files
    (.bak) and other extensions are ignored by apt and by this task.
    """

    suffix = (values.LEGACY_SOURCE_SUFFIX, values.DEB822_SOURCE_SUFFIX)
    files: list[Path] = []
    if values.LEGACY_SOURCES_FILE.is_file():
        files.append(values.LEGACY_SOURCES_FILE)
    if values.SOURCES_LIST_D.is_dir():
        files.extend(
            sorted(
                path
                for path in values.SOURCES_LIST_D.iterdir()
                if path.suffix in suffix and path.name.islower()
            )
        )
    return files


def _process_file(path: Path) -> _FileRewrite:
    """Analyze and rewrite one source file in memory, by its format."""

    text = path.read_text(encoding="utf-8")
    if path.suffix == values.DEB822_SOURCE_SUFFIX:
        return _process_deb822(text)
    return _process_legacy(text)


def _keep_debs_state_note(keep_downloaded_debs: bool) -> str:
    """User note for the keep-debs state applied to the apt drop-in."""

    if keep_downloaded_debs:
        return "keep downloaded .deb files after install enabled"
    return "keep downloaded .deb files after install disabled"


def _keep_debs_dropin_content(keep_downloaded_debs: bool) -> str:
    """The body of the drop-in for the mode of the run.

    One body serves both modes: the same two option names carry the answer of
    the run, true while it keeps the downloaded packages and false while it
    deletes them. The file is therefore never absent, so nothing on the machine
    depends on the default of apt.
    """

    return values.KEEP_DEBS_DROPIN_TEMPLATE.format(
        value="true" if keep_downloaded_debs else "false"
    )


def _ensure_keep_debs_dropin(keep_downloaded_debs: bool) -> tuple[bool, str | None]:
    """Bring the apt keep-debs drop-in to the state the run asked for.

    The drop-in carries the same two option names in both modes and answers
    them with the value of this run: true while the run keeps the downloaded
    packages, false while it deletes them. Writing that answer explicitly is
    what makes apt free the packages it downloads instead of falling back to
    its own default, which is to keep every one of them. The current content
    is read before writing, so an exact match changes nothing (idempotency
    through read-back). Returns whether the file changed and an error string
    when the file could not be updated.
    """

    path = values.KEEP_DEBS_FILE
    content = _keep_debs_dropin_content(keep_downloaded_debs)
    try:
        if path.exists() and path.read_text(encoding="utf-8") == content:
            return False, None
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return False, f"cannot update {path}: {exc}"
    return True, None


def task(ctx: Context) -> TaskResult:
    """Ensure every Ubuntu archive section lists the configured components.

    The task skips when the goal is already reached. Otherwise it rewrites
    the Components lines, refreshes the apt index once (unless
    ctx.skip_apt_update), verifies the result by re-reading the files and
    reports the outcome. Every step that cannot run is reported as a
    warning of a completed task: a file that cannot be read or written is
    named, the other files are still handled, and the runner continues with
    the remaining tasks and never stops here.
    """

    absent = missing_value_names(values, values.READ_VALUE_NAMES)
    if absent:
        # A value that is not declared costs the task and never the run: the
        # names are reported in plain words and the runner carries on with the
        # remaining tasks. The guard stands above every read, so no value is
        # touched before the names are known.
        return TaskResult(
            success=True,
            message="the add_extra_repos values are not declared, nothing was changed",
            warnings=(
                "the add_extra_repos values are not declared: " + ", ".join(absent),
            ),
        )
    configured = values.COMPONENTS
    warnings: list[str] = []
    keep_downloaded_debs = not ctx.delete_packages_after_install
    _log(f"configured components: {' '.join(configured)}")
    keep_changed, keep_error = _ensure_keep_debs_dropin(keep_downloaded_debs)
    if keep_error:
        warnings.append(keep_error)
    if keep_changed:
        _log(
            f"updated {values.KEEP_DEBS_FILE}: "
            f"{_keep_debs_state_note(keep_downloaded_debs)}"
        )
    files = _collect_source_files()
    if not files:
        warning = "no apt source files found"
        warnings.append(warning)
        return TaskResult(
            success=True,
            changed=keep_changed,
            message="; ".join(warnings),
            warnings=tuple(warnings),
        )
    _log(f"apt source files found: {len(files)}")
    states: list[tuple[Path, _FileRewrite]] = []
    has_ubuntu = False
    for path in files:
        try:
            state = _process_file(path)
        except OSError as exc:
            warnings.append(f"cannot read {path}: {exc}")
            continue
        states.append((path, state))
        has_ubuntu = has_ubuntu or state.has_ubuntu
        if state.has_ubuntu:
            status = "satisfied" if state.satisfied else "components missing"
            _log(f"reading {path}: ubuntu section found, {status}")
        else:
            _log(f"reading {path}: no ubuntu section")
    warnings.extend(problem for _, state in states for problem in state.problems)
    if not has_ubuntu:
        warnings.append(
            "no Ubuntu archive section found in the apt sources; "
            "add_extra_repos only manages Ubuntu archive components"
        )
        if warnings:
            _log("; ".join(warnings))
        return TaskResult(
            success=True,
            changed=keep_changed,
            message="; ".join(warnings),
            warnings=tuple(warnings),
        )
    if all(state.satisfied for _, state in states):
        _log("target state already reached, skipping")
        message = "already satisfied"
        if keep_changed:
            message = f"{message}; {_keep_debs_state_note(keep_downloaded_debs)}"
        if warnings:
            message = f"{message}; warnings: {'; '.join(warnings)}"
        return TaskResult(
            success=True,
            changed=keep_changed,
            message=message,
            warnings=tuple(warnings),
        )
    changed_paths: list[Path] = []
    for path, state in states:
        if not state.changed:
            continue
        _log(f"writing {path}: appending missing components")
        try:
            path.write_text(state.text, encoding="utf-8")
        except OSError as exc:
            warnings.append(f"cannot write {path}: {exc}")
            continue
        changed_paths.append(path)
    if changed_paths:
        if ctx.skip_apt_update:
            _log("apt index refresh skipped")
        else:
            _log("refreshing apt index: apt-get update")
            try:
                refresh_apt_index(engine_values.COMMAND_TIMEOUT_SECONDS)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                warnings.append(f"apt index refresh: {exc}")
            else:
                _log("apt index refreshed")
    _log("verifying rewritten sources")
    verified: list[tuple[Path, _FileRewrite]] = []
    for path in files:
        try:
            verified.append((path, _process_file(path)))
        except OSError as exc:
            warnings.append(f"cannot read {path} for verification: {exc}")
    unsatisfied = [str(path) for path, state in verified if not state.satisfied]
    if unsatisfied:
        warnings.append(
            f"components still missing after rewrite: {', '.join(unsatisfied)}"
        )
    else:
        _log(f"verification passed: {len(verified)} files satisfied")
    message = f"components ensured in Ubuntu archive sections: {', '.join(configured)}"
    if keep_changed:
        message = f"{message}; {_keep_debs_state_note(keep_downloaded_debs)}"
    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True,
        changed=bool(changed_paths) or keep_changed,
        message=message,
        warnings=tuple(warnings),
    )
