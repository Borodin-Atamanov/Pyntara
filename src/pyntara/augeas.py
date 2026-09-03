"""Generic augeas helpers: read, write and sync a drop-in config file.

Every function drives augtool --noautoload with a manual load entry for
one file and the given lens, so only that file is ever touched. The
drop-in model is a flat directive list (name and value); an optional
container groups the directives under one augeas node, which ssh_config
needs for the Host block. The lens and the ownership comment are
parameters, so any augeas lens can be driven the same way.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from pyntara.utils import (
    install_packages,
    package_is_installed,
    run_command,
)

# One node line of an augtool print listing.
AUGTOOL_VALUE_RE = re.compile(r'^(?P<node>.+) = "(?P<value>.*)"$')


def parse_augtool_print(
    output: str,
    base: str,
    *,
    skip_labels: frozenset[str] = frozenset(),
) -> tuple[dict[str, str], str | None]:
    """Parse an augtool print listing into (directives, first comment).

    Every line has the form /files<path>/<node> = "<value>"; comment
    nodes carry the # label and an optional [n] index. The label is the
    last path segment without the index; labels in skip_labels (the
    container node) are ignored, so the container itself never looks
    like a directive. The first comment is the ownership header, the
    rest are ignored.
    """

    directives: dict[str, str] = {}
    comment: str | None = None
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith(base + "/"):
            continue
        match = AUGTOOL_VALUE_RE.match(line)
        if match is None:
            continue
        node = match.group("node")
        label = node.rsplit("/", 1)[-1].split("[", 1)[0]
        value = match.group("value")
        if label.startswith("#"):
            if comment is None:
                comment = value
        elif label not in skip_labels:
            directives[label] = value
    return directives, comment


def read_dropin_state(
    dropin_path: Path,
    lens: str,
    timeout: float,
    *,
    skip_labels: frozenset[str] = frozenset(),
) -> tuple[dict[str, str], str | None]:
    """Current directive map and ownership comment of the drop-in.

    The tree comes from a single augtool print over a manual load entry,
    so only the drop-in file is parsed; a missing file yields an empty
    map and a None comment.
    """

    script = (
        "set /augeas/load/entry/lens " + lens + "\n"
        f"set /augeas/load/entry/incl {dropin_path}\n"
        "load\n"
        f"print /files{dropin_path}\n"
    )
    result = run_command(
        ["augtool", "--noautoload"],
        input=script,
        capture=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"augtool read failed: exit {result.returncode}: "
            f"{result.stderr.strip()}"
        )
    return parse_augtool_print(
        result.stdout, f"/files{dropin_path}", skip_labels=skip_labels
    )


def write_dropin(
    dropin_path: Path,
    directives: tuple[tuple[str, str], ...],
    stale_names: list[str],
    lens: str,
    header: str,
    timeout: float,
    *,
    container: str | None = None,
    container_value: str = "*",
) -> None:
    """Write the configured directives through augeas.

    The ownership comment is set first, so augeas places it at the top
    of a fresh file; the container node (if any) is created with its
    value, every configured directive is then set to its value and
    every stale directive is removed. augtool runs with --noautoload
    and a manual load entry, so only the drop-in file is touched.
    """

    dropin_path.parent.mkdir(parents=True, exist_ok=True)
    node = f"/files{dropin_path}"
    lines = [
        "set /augeas/load/entry/lens " + lens,
        f"set /augeas/load/entry/incl {dropin_path}",
        "load",
        f'set {node}/#comment "{header}"',
    ]
    if container is not None:
        lines.append(f"set {node}/{container}[last()] {container_value}")
    for name, value in directives:
        if container is not None:
            lines.append(
                f'set {node}/{container}[last()]/{name}[last()] "{value}"'
            )
        else:
            lines.append(f'set {node}/{name} "{value}"')
    for name in stale_names:
        if container is not None:
            lines.append(f"rm {node}/{container}/{name}")
        else:
            lines.append(f"rm {node}/{name}")
    lines.append("save")
    result = run_command(
        ["augtool", "--noautoload"],
        input="\n".join(lines) + "\n",
        capture=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"augtool write failed: exit {result.returncode}: "
            f"{result.stderr.strip()}"
        )


def sync_dropin(
    dropin_path: Path,
    directives: tuple[tuple[str, str], ...],
    mode: int,
    force: bool,
    lens: str,
    header: str,
    timeout: float,
    *,
    container: str | None = None,
    container_value: str = "*",
    port_directive: str | None = None,
) -> tuple[bool, bool]:
    """Align the drop-in with the configured directives; return (changed, port_changed).

    The current state is read through augeas and compared with the
    desired map; a matching file is left untouched. A difference, a
    missing ownership comment or force triggers a rewrite.
    port_changed reports whether the port_directive differs, because a
    port change needs a restart, not a reload; it is always False when
    port_directive is None. An empty directives list removes the
    drop-in.
    """

    if not directives:
        existed = dropin_path.exists()
        if existed:
            dropin_path.unlink()
        return existed, False
    skip_labels = frozenset({container}) if container else frozenset()
    current, comment = read_dropin_state(
        dropin_path, lens, timeout, skip_labels=skip_labels
    )
    desired = dict(directives)
    changed = force or current != desired or comment != header
    port_changed = (
        port_directive is not None
        and current.get(port_directive) != desired.get(port_directive)
    )
    if not changed:
        return False, False
    stale_names = [name for name in current if name not in desired]
    write_dropin(
        dropin_path,
        directives,
        stale_names,
        lens,
        header,
        timeout,
        container=container,
        container_value=container_value,
    )
    os.chmod(dropin_path, mode)
    apply_owner(dropin_path, 0, 0)
    return True, port_changed


def _read_text(path: Path) -> str | None:
    """Current content of a text file, or None when absent."""

    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def include_covers_dropin(config_path: Path, dropin_path: Path) -> bool:
    """True when the main config pulls the drop-in directory in.

    Every Include directive of the main config is matched against the
    drop-in path with fnmatch, which understands the glob patterns
    OpenSSH accepts; a relative pattern resolves against the directory
    of the main config. A missing file, an unreadable file or a
    directive that does not cover the drop-in all mean the rendered
    drop-in would be ignored, so a task must fail loudly instead of
    pretending the configuration is in place.
    """

    content = _read_text(config_path)
    if content is None:
        return False
    base_dir = config_path.parent
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        keyword, sep, pattern = stripped.partition(" ")
        if not sep or keyword.casefold() != "include":
            continue
        pattern = pattern.strip()
        if fnmatch.fnmatch(str(dropin_path), pattern):
            return True
        if not pattern.startswith("/"):
            relative = base_dir / pattern
            if fnmatch.fnmatch(str(dropin_path), str(relative)):
                return True
    return False


def apply_owner(path: Path, uid: int, gid: int) -> None:
    """Set the file owner when the process runs as root.

    The installer runs under sudo, so the ownership is applied on real
    machines; non-root test runs skip the chown, because it would fail
    without privileges.
    """

    if os.geteuid() == 0:
        os.chown(path, uid, gid)


def ensure_augtool(
    package_name: str,
    *,
    status_timeout: float,
    install_timeout: float,
    retries: int,
    skip_update: bool,
) -> str | None:
    """Error text when augtool cannot be ensured; None when ready.

    augtool comes from package_name, which the caller task installs
    itself, so the task never waits for another task to provide the
    tool. A package already in the installed state is left alone, so a
    configured system is not touched and a rerun stays quiet. The
    install goes through the shared apt helpers: the index is refreshed
    once unless skip_update is set, and a failed install is reported
    back to the caller.
    """

    if package_is_installed(package_name, status_timeout):
        return None
    _, failures, _ = install_packages(
        [package_name],
        install_timeout=install_timeout,
        update_timeout=install_timeout,
        retries=retries,
        skip_update=skip_update,
    )
    if failures:
        return f"cannot install {package_name}: {failures[0][1]}"
    return None
