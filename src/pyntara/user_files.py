"""Writing one file of the desktop user from the root run.

A desktop configuration file must be owned by the desktop user, so the section
that writes one creates its directory as that user, writes the content as the
root process and then hands the file to the user with the declared mode. Two
sections do that (kde_settings and vocalinux_setup), so the sequence lives here
once and a section passes the relative path, the content and the mode.

A file that already holds the content is skipped, so a repeated run changes
nothing. A failure raises, and the caller decides whether it is a warning of one
step or an error of the task.
"""

from __future__ import annotations

from pathlib import Path

from pyntara.logger import log_progress as _log
from pyntara.utils import (
    as_user_command,
    home_environment,
    run_command,
    substituted_command,
)
from pyntara.values import common as common_values


def write_user_file(
    rel_path: str,
    content: str,
    *,
    file_mode: int,
    timeout: float,
    force: bool,
) -> bool:
    """Write one user-owned file as the desktop user; True when written.

    The directory is created as the desktop user, the content is written by the
    root process and then chowned and chmodded to that user, so the file keeps
    the ownership and the mode a desktop config file needs. file_mode is applied
    in its octal form.
    """

    target = Path(common_values.DESKTOP_HOME_DIR) / rel_path
    if not force and target.is_file():
        try:
            if target.read_text(encoding="utf-8") == content:
                return False
        except OSError:
            pass
    run_command(
        as_user_command(
            substituted_command(
                common_values.MKDIR_COMMAND, {"path": str(target.parent)}
            ),
        ),
        extra_env=home_environment(),
        timeout=timeout,
    )
    # The user mkdir above owns the directory; this direct creation is a no-op
    # when it succeeded and a fallback for a read-only fixture.
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    run_command(
        substituted_command(
            common_values.CHOWN_COMMAND,
            {
                "owner": (
                    f"{common_values.DESKTOP_USERNAME}:"
                    f"{common_values.DESKTOP_USERNAME}"
                ),
                "path": str(target),
            },
        ),
        timeout=timeout,
    )
    run_command(
        substituted_command(
            common_values.CHMOD_COMMAND,
            {"file_mode": f"{file_mode:o}", "path": str(target)},
        ),
        timeout=timeout,
    )
    _log(f"wrote {target}")
    return True
